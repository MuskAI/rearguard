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
DEFAULT_STATE_PATH = PROJECT_ROOT / "admin_state.json"
STATE_PATH = Path(os.environ.get("REALGUARD_ADMIN_STATE_PATH", str(DEFAULT_STATE_PATH)))
_STATE_THREAD_LOCK = threading.RLock()


def _default_state():
    return {
        "version": 1,
        "alerts": {
            "enabled": False,
            "webhookUrl": "",
            "smsPhones": "",
            "rules": {
                "v1Offline": True,
                "artifactMissing": True,
                "fallbackEnabled": True,
                "probeFailed": True,
            },
            "notes": "告警配置仅保存后台策略；接入企业微信/短信网关后即可投递。",
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
        if STATE_PATH.exists():
            return _merge_defaults(json.loads(STATE_PATH.read_text(encoding="utf-8")))
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
        tmp_path.replace(STATE_PATH)
    finally:
        tmp_path.unlink(missing_ok=True)
    return data


@contextmanager
def _state_write_lock():
    with _STATE_THREAD_LOCK:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        lock_path = STATE_PATH.with_name(f".{STATE_PATH.name}.lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
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
        for key in ("enabled", "webhookUrl", "smsPhones", "notes"):
            if key in updates:
                current[key] = updates.get(key)
        if isinstance(updates.get("rules"), dict):
            rules = current.setdefault("rules", {})
            for key in ("v1Offline", "artifactMissing", "fallbackEnabled", "probeFailed"):
                if key in updates["rules"]:
                    rules[key] = bool(updates["rules"][key])
        return deepcopy(current)

    return _update_state(mutate)


def _db_state_enabled():
    return str(os.environ.get("REALGUARD_ADMIN_STATE_DB", "auto")).strip().lower() not in ("0", "false", "off", "json")


def _system_sql(sql, params=None, fetch=True):
    if not _db_state_enabled():
        return None
    try:
        from imagedetection.views.utils import excute_sql
        return excute_sql(sql, params, fetch=fetch)
    except Exception as exc:
        print(f"[ADMIN STATE DB ERROR] {exc}")
        return None


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
    if rows is not None:
        return [_audit_entry_from_row(row) for row in rows]
    audit = load_state().get("audit", [])
    return audit[:limit]


def append_audit(actor, action, target, before=None, after=None, meta=None):
    actor = actor or {}
    entry = {
        "id": f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}",
        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "actor": {
            "id": actor.get("Userid"),
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


def list_detection_jobs(limit=100):
    jobs = list(load_state().get("detectionJobs", {}).values())
    jobs.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = 100
    return jobs[:limit]


def get_api_key_quota(key_id):
    quotas = load_state().get("apiKeyQuotas", {})
    return quotas.get(str(key_id), {"dailyLimit": None, "rateLimitPerMinute": None, "scopes": ""})


def set_api_key_quota(key_id, quota):
    def mutate(state):
        quotas = state.setdefault("apiKeyQuotas", {})
        key = str(key_id)
        existing = quotas.get(key, {})
        for field in ("dailyLimit", "rateLimitPerMinute", "scopes", "notes"):
            if field in quota:
                existing[field] = quota.get(field)
        quotas[key] = existing
        return deepcopy(existing)

    return _update_state(mutate)
