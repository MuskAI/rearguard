import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback for local development
    fcntl = None


CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parents[1]
LEGACY_STATE_PATH = PROJECT_ROOT / "admin_state.json"
DEFAULT_STATE_PATH = Path(
    os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
) / "realguard" / "admin_state.json"
STATE_PATH = Path(os.environ.get("REALGUARD_ADMIN_STATE_PATH", str(DEFAULT_STATE_PATH)))
_STATE_THREAD_LOCK = threading.RLock()
_API_KEY_QUOTA_TABLES_READY = False


def _default_state():
    return {
        "version": 1,
        "alerts": {
            "enabled": False,
            "webhookUrl": "",
            "smsPhones": "",
            "cooldownSeconds": 900,
            "rules": {
                "v1Offline": True,
                "artifactMissing": True,
                "fallbackEnabled": True,
                "probeFailed": True,
            },
            "notes": "告警通过 HTTPS Webhook 投递，事件会去重并在恢复时发送通知。",
            "runtime": {"events": {}},
            "deliveryHistory": [],
        },
        "audit": [],
        "modelRuns": [],
        "apiKeyQuotas": {},
        "detectionJobs": {},
    }


def _merge_defaults(saved):
    state = _default_state()
    if not isinstance(saved, dict):
        return state
    state.update({key: saved.get(key, state[key]) for key in ("version", "audit", "modelRuns", "apiKeyQuotas", "detectionJobs")})
    if isinstance(saved.get("alerts"), dict):
        state["alerts"].update(saved["alerts"])
        if isinstance(saved["alerts"].get("rules"), dict):
            state["alerts"]["rules"].update(saved["alerts"]["rules"])
        if not isinstance(state["alerts"].get("runtime"), dict):
            state["alerts"]["runtime"] = {"events": {}}
        if not isinstance(state["alerts"].get("deliveryHistory"), list):
            state["alerts"]["deliveryHistory"] = []
    if not isinstance(state.get("audit"), list):
        state["audit"] = []
    if not isinstance(state.get("modelRuns"), list):
        state["modelRuns"] = []
    if not isinstance(state.get("apiKeyQuotas"), dict):
        state["apiKeyQuotas"] = {}
    if not isinstance(state.get("detectionJobs"), dict):
        state["detectionJobs"] = {}
    return state


def _load_state_unlocked():
    try:
        source_path = STATE_PATH
        if (
            not source_path.exists()
            and STATE_PATH == DEFAULT_STATE_PATH
            and LEGACY_STATE_PATH.exists()
        ):
            source_path = LEGACY_STATE_PATH
        if source_path.exists():
            os.chmod(source_path, 0o600)
            return _merge_defaults(json.loads(source_path.read_text(encoding="utf-8")))
    except Exception as exc:
        print(f"[ADMIN STATE ERROR] load failed: {exc}")
    return _default_state()


def load_state():
    with _STATE_THREAD_LOCK:
        return _load_state_unlocked()


def _save_state_unlocked(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = _merge_defaults(deepcopy(state))
    tmp_path = STATE_PATH.with_name(
        f".{STATE_PATH.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(STATE_PATH)
        os.chmod(STATE_PATH, 0o600)
    finally:
        tmp_path.unlink(missing_ok=True)
    return data


@contextmanager
def _state_write_lock():
    with _STATE_THREAD_LOCK:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        lock_path = STATE_PATH.with_name(f".{STATE_PATH.name}.lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            os.chmod(lock_path, 0o600)
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def save_state(state):
    with _state_write_lock():
        return _save_state_unlocked(state)


def _update_state(mutator):
    with _state_write_lock():
        state = _load_state_unlocked()
        result = mutator(state)
        _save_state_unlocked(state)
        return result


def alerts():
    return load_state().get("alerts", _default_state()["alerts"])


def update_alerts(updates):
    def mutate(state):
        current = state.setdefault("alerts", _default_state()["alerts"])
        for key in ("enabled", "webhookUrl", "smsPhones", "notes", "cooldownSeconds"):
            if key in updates:
                current[key] = updates.get(key)
        if isinstance(updates.get("rules"), dict):
            rules = current.setdefault("rules", {})
            for key in ("v1Offline", "artifactMissing", "fallbackEnabled", "probeFailed"):
                if key in updates["rules"]:
                    rules[key] = bool(updates["rules"][key])
                    if not rules[key]:
                        event = current.setdefault("runtime", {"events": {}}).setdefault("events", {}).setdefault(key, {})
                        event["active"] = False
                        event["suppressedAtEpoch"] = int(time.time())
        return deepcopy(current)

    return _update_state(mutate)


def claim_alert_event(event_id, active, title, message, level="warning", force=False):
    now = int(time.time())

    def mutate(state):
        config = state.setdefault("alerts", _default_state()["alerts"])
        runtime = config.setdefault("runtime", {"events": {}})
        events = runtime.setdefault("events", {})
        event = events.setdefault(str(event_id), {})
        was_active = bool(event.get("active"))
        last_attempt = int(event.get("lastAttemptAtEpoch") or 0)
        last_recovery_attempt = int(event.get("lastRecoveryAttemptAtEpoch") or 0)
        try:
            cooldown = max(60, min(int(config.get("cooldownSeconds") or 900), 86400))
        except (TypeError, ValueError):
            cooldown = 900
        should_send = bool(force)
        kind = "test" if force else "alert"
        if not force and active:
            should_send = not was_active or now - last_attempt >= cooldown
        elif not force and not active and was_active:
            kind = "recovery"
            should_send = now - last_recovery_attempt >= max(60, min(cooldown, 300))
        event.update({
            "active": bool(active),
            "title": str(title or ""),
            "message": str(message or ""),
            "level": str(level or "warning"),
            "updatedAtEpoch": now,
        })
        if was_active != bool(active):
            event["changedAtEpoch"] = now
        if should_send:
            event["lastAttemptAtEpoch"] = now
            if kind == "recovery":
                event["lastRecoveryAttemptAtEpoch"] = now
        events[str(event_id)] = event
        runtime["events"] = events
        config["runtime"] = runtime
        state["alerts"] = config
        if not should_send:
            return None
        return {
            "id": f"alert_{uuid.uuid4().hex[:20]}",
            "eventId": str(event_id),
            "kind": kind,
            "active": bool(active),
            "level": "success" if kind == "recovery" else str(level or "warning"),
            "title": f"{title}已恢复" if kind == "recovery" else str(title or "系统告警"),
            "message": "对应异常状态已恢复。" if kind == "recovery" else str(message or ""),
            "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    return _update_state(mutate)


def suppress_alert_event(event_id):
    def mutate(state):
        config = state.setdefault("alerts", _default_state()["alerts"])
        event = config.setdefault("runtime", {"events": {}}).setdefault("events", {}).setdefault(str(event_id), {})
        event["active"] = False
        event["suppressedAtEpoch"] = int(time.time())
        state["alerts"] = config

    _update_state(mutate)


def record_alert_delivery(claim, ok, status_code=None, error="", attempts=1):
    entry = {
        **(claim or {}),
        "ok": bool(ok),
        "statusCode": status_code,
        "error": str(error or "")[:500],
        "attempts": int(attempts or 1),
        "deliveredAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    def mutate(state):
        config = state.setdefault("alerts", _default_state()["alerts"])
        history = config.setdefault("deliveryHistory", [])
        history.insert(0, entry)
        config["deliveryHistory"] = history[:200]
        event = config.setdefault("runtime", {"events": {}}).setdefault("events", {}).setdefault(
            str(entry.get("eventId") or "unknown"), {}
        )
        if ok:
            event["lastSentAtEpoch"] = int(time.time())
        elif entry.get("kind") == "recovery":
            event["active"] = True
        event["lastDeliveryOk"] = bool(ok)
        event["lastError"] = entry["error"]
        state["alerts"] = config
        return deepcopy(entry)

    return _update_state(mutate)


def alert_delivery_history(limit=50):
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 50
    return alerts().get("deliveryHistory", [])[:limit]


def _db_state_enabled():
    return str(os.environ.get("REALGUARD_ADMIN_STATE_DB", "auto")).strip().lower() not in ("0", "false", "off", "json")


def _db_state_required():
    return str(os.environ.get("REALGUARD_ADMIN_STATE_DB", "auto")).strip().lower() in (
        "1", "true", "on", "db", "required",
    )


def _system_sql(sql, params=None, fetch=True):
    if not _db_state_enabled():
        return None
    try:
        from imagedetection.views.utils import excute_sql
        return excute_sql(sql, params, fetch=fetch)
    except Exception as exc:
        print(f"[ADMIN STATE DB ERROR] {exc}")
        return None


def ensure_api_key_quota_storage():
    """Create legacy key quota tables and account-authoritative quota tables."""
    global _API_KEY_QUOTA_TABLES_READY
    if _API_KEY_QUOTA_TABLES_READY:
        return True
    quota_result = _system_sql(
        """
        CREATE TABLE IF NOT EXISTS developer_api_key_quotas (
          key_id BIGINT NOT NULL,
          daily_limit INT NULL,
          rate_limit_per_minute INT NULL,
          scopes VARCHAR(255) NULL,
          notes TEXT NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (key_id),
          CONSTRAINT fk_developer_api_key_quotas_key
            FOREIGN KEY (key_id) REFERENCES developer_api_keys(id)
            ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        fetch=False,
    )
    usage_result = _system_sql(
        """
        CREATE TABLE IF NOT EXISTS developer_api_key_quota_usage (
          key_id BIGINT NOT NULL,
          day_bucket DATE NOT NULL,
          daily_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
          minute_bucket DATETIME NOT NULL,
          minute_count INT UNSIGNED NOT NULL DEFAULT 0,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (key_id),
          CONSTRAINT fk_developer_api_key_quota_usage_key
            FOREIGN KEY (key_id) REFERENCES developer_api_keys(id)
            ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        fetch=False,
    )
    account_quota_result = _system_sql(
        """
        CREATE TABLE IF NOT EXISTS developer_api_account_quotas (
          user_id INT NOT NULL,
          daily_limit INT NULL,
          rate_limit_per_minute INT NULL,
          scopes VARCHAR(255) NULL,
          notes TEXT NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (user_id),
          CONSTRAINT fk_developer_api_account_quotas_user
            FOREIGN KEY (user_id) REFERENCES `user`(Userid)
            ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        fetch=False,
    )
    account_usage_result = _system_sql(
        """
        CREATE TABLE IF NOT EXISTS developer_api_account_quota_usage (
          user_id INT NOT NULL,
          day_bucket DATE NOT NULL,
          daily_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
          minute_bucket DATETIME NOT NULL,
          minute_count INT UNSIGNED NOT NULL DEFAULT 0,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (user_id),
          CONSTRAINT fk_developer_api_account_quota_usage_user
            FOREIGN KEY (user_id) REFERENCES `user`(Userid)
            ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        fetch=False,
    )
    _API_KEY_QUOTA_TABLES_READY = all(
        result is not None
        for result in (quota_result, usage_result, account_quota_result, account_usage_result)
    )
    return _API_KEY_QUOTA_TABLES_READY


def _quota_payload(value=None):
    value = value if isinstance(value, dict) else {}

    def optional_nonnegative_int(field):
        raw = value.get(field)
        if raw in (None, ""):
            return None
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return None

    return {
        "dailyLimit": optional_nonnegative_int("dailyLimit"),
        "rateLimitPerMinute": optional_nonnegative_int("rateLimitPerMinute"),
        "scopes": str(value.get("scopes") or "").strip(),
        "notes": str(value.get("notes") or "").strip(),
    }


def _quota_from_row(row):
    return _quota_payload({
        "dailyLimit": row.get("daily_limit"),
        "rateLimitPerMinute": row.get("rate_limit_per_minute"),
        "scopes": row.get("scopes"),
        "notes": row.get("notes"),
    })


def _write_api_key_quota_db(key_id, quota, *, insert_only=False):
    if not ensure_api_key_quota_storage():
        return False
    quota = _quota_payload(quota)
    update_clause = "key_id = VALUES(key_id)" if insert_only else """
        daily_limit = VALUES(daily_limit),
        rate_limit_per_minute = VALUES(rate_limit_per_minute),
        scopes = VALUES(scopes),
        notes = VALUES(notes)
    """
    result = _system_sql(
        f"""
        INSERT INTO developer_api_key_quotas
            (key_id, daily_limit, rate_limit_per_minute, scopes, notes)
        SELECT %s, %s, %s, %s, %s
        FROM developer_api_keys
        WHERE id = %s
        ON DUPLICATE KEY UPDATE {update_clause}
        """,
        (
            int(key_id),
            quota["dailyLimit"],
            quota["rateLimitPerMinute"],
            quota["scopes"],
            quota["notes"],
            int(key_id),
        ),
        fetch=False,
    )
    return result is not None


def _api_key_user_id(key_id):
    rows = _system_sql(
        "SELECT user_id FROM developer_api_keys WHERE id = %s LIMIT 1",
        (int(key_id),),
    )
    if not rows:
        return None
    try:
        return int(rows[0].get("user_id"))
    except (TypeError, ValueError):
        return None


def _write_api_account_quota_db(user_id, quota, *, insert_only=False):
    if not ensure_api_key_quota_storage():
        return False
    quota = _quota_payload(quota)
    update_clause = "user_id = VALUES(user_id)" if insert_only else """
        daily_limit = VALUES(daily_limit),
        rate_limit_per_minute = VALUES(rate_limit_per_minute),
        scopes = VALUES(scopes),
        notes = VALUES(notes)
    """
    result = _system_sql(
        f"""
        INSERT INTO developer_api_account_quotas
            (user_id, daily_limit, rate_limit_per_minute, scopes, notes)
        SELECT %s, %s, %s, %s, %s
        FROM `user`
        WHERE Userid = %s
        ON DUPLICATE KEY UPDATE {update_clause}
        """,
        (
            int(user_id),
            quota["dailyLimit"],
            quota["rateLimitPerMinute"],
            quota["scopes"],
            quota["notes"],
            int(user_id),
        ),
        fetch=False,
    )
    return result is not None


def _migrate_key_quotas_to_accounts():
    """Import the strictest legacy key limit once, without replacing account policy."""
    result = _system_sql(
        """
        INSERT IGNORE INTO developer_api_account_quotas
            (user_id, daily_limit, rate_limit_per_minute, scopes, notes)
        SELECT k.user_id, MIN(q.daily_limit), MIN(q.rate_limit_per_minute),
               MAX(q.scopes), MAX(q.notes)
        FROM developer_api_key_quotas q
        JOIN developer_api_keys k ON k.id = q.key_id
        GROUP BY k.user_id
        """,
        fetch=False,
    )
    return result is not None


def sync_api_key_quotas_to_db():
    """Import legacy JSON quotas without overwriting DB-authoritative values."""
    if not ensure_api_key_quota_storage():
        return False
    quotas = load_state().get("apiKeyQuotas", {})
    for key_id, quota in quotas.items():
        try:
            if not _write_api_key_quota_db(int(key_id), quota, insert_only=True):
                return False
        except (TypeError, ValueError):
            continue
    return _migrate_key_quotas_to_accounts()


def _json_dumps(value):
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return "{}"


def _json_loads(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _audit_entry_from_row(row):
    return {
        "id": str(row.get("id") or ""),
        "createdAt": str(row.get("created_at") or row.get("createdAt") or ""),
        "actor": {
            "id": row.get("actor_id"),
            "username": row.get("actor_username") or "",
            "phone": row.get("actor_phone") or "",
        },
        "action": row.get("action") or "",
        "target": row.get("target") or "",
        "before": _json_loads(row.get("before_json"), None),
        "after": _json_loads(row.get("after_json"), None),
        "meta": _json_loads(row.get("meta_json"), {}),
    }


def list_audit(limit=100):
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = 100
    rows = _system_sql(
        """
        SELECT id, created_at, actor_id, actor_username, actor_phone, action, target,
               before_json, after_json, meta_json
        FROM admin_audit_logs
        ORDER BY id DESC
        LIMIT %s
        """,
        (limit,),
    )
    fallback = load_state().get("audit", [])
    if rows is None:
        return fallback[:limit]
    combined = [_audit_entry_from_row(row) for row in rows]
    seen = {
        (entry.get("createdAt"), entry.get("action"), entry.get("target"), (entry.get("actor") or {}).get("id"))
        for entry in combined
    }
    for entry in fallback:
        fingerprint = (
            entry.get("createdAt"),
            entry.get("action"),
            entry.get("target"),
            (entry.get("actor") or {}).get("id"),
        )
        if fingerprint not in seen:
            combined.append(entry)
            seen.add(fingerprint)
    combined.sort(key=lambda entry: str(entry.get("createdAt") or ""), reverse=True)
    return combined[:limit]


def append_audit(actor, action, target, before=None, after=None, meta=None):
    actor = actor or {}
    entry = {
        "id": f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}",
        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "actor": {
            "id": actor.get("Userid"),
            "account_uuid": actor.get("account_uuid") or "",
            "username": actor.get("username") or "",
            "phone": actor.get("phone") or "",
        },
        "action": action,
        "target": target,
        "before": before,
        "after": after,
        "meta": meta or {},
    }
    inserted = _system_sql(
        """
        INSERT INTO admin_audit_logs
            (actor_id, actor_username, actor_phone, action, target, before_json, after_json, meta_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            str(entry["actor"]["id"] or ""),
            entry["actor"]["username"],
            entry["actor"]["phone"],
            action,
            str(target or ""),
            _json_dumps(before),
            _json_dumps(after),
            _json_dumps(meta or {}),
        ),
        fetch=False,
    )
    if inserted is not None:
        return entry

    def mutate(state):
        audit = state.setdefault("audit", [])
        audit.insert(0, entry)
        state["audit"] = audit[:500]

    _update_state(mutate)
    return entry


def _model_run_from_row(row):
    return {
        "id": row.get("run_id") or str(row.get("id") or ""),
        "createdAt": str(row.get("created_at") or row.get("createdAt") or ""),
        "itemid": row.get("itemid"),
        "route": row.get("route") or "primary",
        "status": row.get("status") or "success",
        "model": {
            "id": row.get("model_id") or "",
            "name": row.get("model_name") or row.get("model_id") or "",
            "runtime": row.get("model_runtime") or "",
            "endpoint": row.get("model_endpoint") or "",
            "version": row.get("model_version") or "",
        },
        "actor": {
            "id": row.get("actor_id"),
            "username": row.get("actor_username") or "",
            "phone": row.get("actor_phone") or "",
        },
        "meta": _json_loads(row.get("meta_json"), {}),
    }


def append_model_run(itemid, model, route="primary", status="success", actor=None, meta=None):
    model = model or {}
    actor = actor or {}
    entry = {
        "id": f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}",
        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "itemid": itemid,
        "route": route,
        "status": status,
        "model": {
            "id": model.get("id") or "",
            "name": model.get("name") or model.get("id") or "",
            "runtime": model.get("runtime") or "",
            "endpoint": model.get("endpoint") or "",
            "version": model.get("version") or "",
        },
        "actor": {
            "id": actor.get("Userid"),
            "username": actor.get("username") or "",
            "phone": actor.get("phone") or "",
        },
        "meta": meta or {},
    }
    inserted = _system_sql(
        """
        INSERT INTO admin_model_runs
            (run_id, itemid, route, status, model_id, model_name, model_runtime, model_endpoint,
             model_version, actor_id, actor_username, actor_phone, meta_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            entry["id"],
            itemid,
            entry["route"],
            entry["status"],
            entry["model"]["id"],
            entry["model"]["name"],
            entry["model"]["runtime"],
            entry["model"]["endpoint"],
            entry["model"]["version"],
            str(entry["actor"]["id"] or ""),
            entry["actor"]["username"],
            entry["actor"]["phone"],
            _json_dumps(meta or {}),
        ),
        fetch=False,
    )
    if inserted is not None:
        return entry

    def mutate(state):
        runs = state.setdefault("modelRuns", [])
        runs.insert(0, entry)
        state["modelRuns"] = runs[:1000]

    _update_state(mutate)
    return entry


def list_model_runs(limit=1000):
    try:
        limit = max(1, min(int(limit), 2000))
    except (TypeError, ValueError):
        limit = 1000
    rows = _system_sql(
        """
        SELECT id, run_id, created_at, itemid, route, status, model_id, model_name,
               model_runtime, model_endpoint, model_version, actor_id, actor_username,
               actor_phone, meta_json
        FROM admin_model_runs
        ORDER BY id DESC
        LIMIT %s
        """,
        (limit,),
    )
    if rows is not None:
        return [_model_run_from_row(row) for row in rows]
    return load_state().get("modelRuns", [])[:limit]


def model_runs_by_itemids(itemids):
    wanted = {str(itemid) for itemid in itemids if itemid not in (None, "")}
    if not wanted:
        return {}
    placeholders = ", ".join(["%s"] * len(wanted))
    rows = _system_sql(
        f"""
        SELECT id, run_id, created_at, itemid, route, status, model_id, model_name,
               model_runtime, model_endpoint, model_version, actor_id, actor_username,
               actor_phone, meta_json
        FROM admin_model_runs
        WHERE itemid IN ({placeholders})
        ORDER BY id DESC
        """,
        tuple(wanted),
    )
    if rows is not None:
        found = {}
        for row in rows:
            key = str(row.get("itemid") or "")
            if key in wanted and key not in found:
                found[key] = _model_run_from_row(row)
        return found
    found = {}
    for item in load_state().get("modelRuns", []):
        key = str(item.get("itemid") or "")
        if key in wanted and key not in found:
            found[key] = item
        if len(found) == len(wanted):
            break
    return found


def create_detection_job(actor, filename, kind="image", mode=None, experts=None):
    actor = actor or {}
    job_id = f"job_{uuid.uuid4().hex[:20]}"
    entry = {
        "id": job_id,
        "kind": kind,
        "filename": filename,
        "status": "queued",
        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "actor": {
            "id": actor.get("Userid"),
            "account_uuid": actor.get("account_uuid") or "",
            "username": actor.get("username") or "",
            "phone": actor.get("phone") or "",
            "openid": actor.get("openid") or "",
        },
        "result": None,
        "error": "",
        "mode": mode or kind,
        "progress": 0,
        "experts": experts or [],
        "summary": "",
    }
    def mutate(state):
        state.setdefault("detectionJobs", {})[job_id] = entry

    _update_state(mutate)
    return entry


def update_detection_job(job_id, updates):
    def mutate(state):
        jobs = state.setdefault("detectionJobs", {})
        entry = jobs.get(str(job_id))
        if not entry:
            return None
        for key in ("status", "result", "error", "mode", "progress", "experts", "summary"):
            if key in updates:
                entry[key] = updates.get(key)
        entry["updatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
        jobs[str(job_id)] = entry
        state["detectionJobs"] = dict(list(jobs.items())[-500:])
        return deepcopy(entry)

    return _update_state(mutate)


def get_detection_job(job_id):
    return load_state().get("detectionJobs", {}).get(str(job_id))


def restore_detection_job(entry):
    """Restore a durable job into the progress cache after a Web restart."""
    if not isinstance(entry, dict) or not str(entry.get("id") or "").strip():
        return None
    restored = deepcopy(entry)
    job_id = str(restored["id"])

    def mutate(state):
        jobs = state.setdefault("detectionJobs", {})
        jobs[job_id] = restored
        state["detectionJobs"] = dict(list(jobs.items())[-500:])
        return deepcopy(restored)

    return _update_state(mutate)


def list_detection_jobs(limit=100):
    jobs = list(load_state().get("detectionJobs", {}).values())
    jobs.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = 100
    return jobs[:limit]


def reconcile_interrupted_detection_jobs(preserve_ids=None):
    """Fail jobs whose in-process executor disappeared during a controlled restart."""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    preserved = {str(job_id) for job_id in (preserve_ids or set())}

    def mutate(state):
        changed = 0
        jobs = state.setdefault("detectionJobs", {})
        for job_id, entry in list(jobs.items()):
            if str(job_id) in preserved:
                continue
            if str(entry.get("status") or "").lower() not in {"queued", "running"}:
                continue
            entry["status"] = "failed"
            entry["error"] = "服务发布或重启中断了本次任务，请重新提交原文件"
            entry["summary"] = "任务执行进程已退出，系统未生成检测结论，也不会将其计为成功任务"
            entry["updatedAt"] = now
            jobs[str(job_id)] = entry
            changed += 1
        state["detectionJobs"] = dict(list(jobs.items())[-500:])
        return changed

    return int(_update_state(mutate) or 0)


def get_api_key_quota(key_id):
    default = _quota_payload()
    if ensure_api_key_quota_storage():
        user_id = _api_key_user_id(key_id)
        if user_id is None:
            quotas = load_state().get("apiKeyQuotas", {})
            return _quota_payload(quotas.get(str(key_id)))
        rows = _system_sql(
            """
            SELECT daily_limit, rate_limit_per_minute, scopes, notes
            FROM developer_api_account_quotas
            WHERE user_id = %s
            LIMIT 1
            """,
            (user_id,),
        )
        if rows is None:
            quotas = load_state().get("apiKeyQuotas", {})
            return _quota_payload(quotas.get(str(key_id)))
        if rows:
            return _quota_from_row(rows[0])

        legacy_rows = _system_sql(
            """
            SELECT daily_limit, rate_limit_per_minute, scopes, notes
            FROM developer_api_key_quotas
            WHERE key_id = %s
            LIMIT 1
            """,
            (int(key_id),),
        )
        legacy = _quota_from_row(legacy_rows[0]) if legacy_rows else None
        if legacy is None:
            legacy = load_state().get("apiKeyQuotas", {}).get(str(key_id))
        if legacy is not None and _write_api_account_quota_db(user_id, legacy, insert_only=True):
            rows = _system_sql(
                """
                SELECT daily_limit, rate_limit_per_minute, scopes, notes
                FROM developer_api_account_quotas
                WHERE user_id = %s
                LIMIT 1
                """,
                (user_id,),
            )
            if rows is None:
                return _quota_payload(legacy)
            if rows:
                return _quota_from_row(rows[0])
        return default

    quotas = load_state().get("apiKeyQuotas", {})
    return _quota_payload(quotas.get(str(key_id)))


def set_api_key_quota(key_id, quota):
    existing = get_api_key_quota(key_id)
    db_authoritative = _API_KEY_QUOTA_TABLES_READY or _db_state_required()
    user_id = _api_key_user_id(key_id) if db_authoritative else None
    if db_authoritative:
        if user_id is None:
            return None
        rows = _system_sql(
            """
            SELECT daily_limit, rate_limit_per_minute, scopes, notes
            FROM developer_api_account_quotas
            WHERE user_id = %s
            LIMIT 1
            """,
            (user_id,),
        )
        if rows is None:
            return None
        if rows:
            existing = _quota_from_row(rows[0])
    merged = dict(existing)
    for field in ("dailyLimit", "rateLimitPerMinute", "scopes", "notes"):
        if field in quota:
            merged[field] = quota.get(field)
    merged = _quota_payload(merged)
    if user_id is None:
        user_id = _api_key_user_id(key_id)
    persisted_to_db = bool(user_id is not None and _write_api_account_quota_db(user_id, merged))
    if not persisted_to_db and (_API_KEY_QUOTA_TABLES_READY or _db_state_required()):
        return None

    def mutate(state):
        quotas = state.setdefault("apiKeyQuotas", {})
        key = str(key_id)
        quotas[key] = deepcopy(merged)
        return deepcopy(merged)

    try:
        return _update_state(mutate)
    except Exception:
        if persisted_to_db:
            return merged
        raise
