"""Privacy-preserving visitor geography aggregation for the admin screen."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
from typing import Callable, Iterable
from urllib.parse import parse_qs, urlsplit

try:
    import ip2region.searcher as ip2_searcher
    import ip2region.util as ip2_util
except ImportError:  # Optional during local development and unit tests.
    ip2_searcher = None
    ip2_util = None


DEFAULT_XDB_PATH = "/opt/realguard-data/ip2region_v4.xdb"
DEFAULT_CUMULATIVE_DB_PATH = "/opt/realguard-data/traffic-cumulative.sqlite3"
DEFAULT_WINDOW_HOURS = 24
DEFAULT_ONLINE_WINDOW_MINUTES = 5
DEFAULT_VISITOR_DETAIL_LIMIT = 20
HOMEPAGE_PATHS = {"/", "/index.html"}

LOG_PATTERN = re.compile(
    r'^(?P<ip>\S+)\s+\S+\s+\S+\s+\[(?P<time>[^]]+)]\s+'
    r'"(?P<method>\S+)\s+(?P<path>\S+)(?:\s+HTTP/[^\"]+)?"\s+'
    r'(?P<status>\d{3})\s+\S+\s+"(?P<referer>[^"]*)"\s+"(?P<agent>[^"]*)"'
)
BOT_PATTERN = re.compile(
    r"apachebench|\bcurl\b|python-requests|uptimerobot|healthcheck|go-http-client|"
    r"bot|spider|crawler|headlesschrome|censys|zgrab|nmap|pathscan|infrawatch|"
    r"palo alto|checkhost|internetmeasurement|visionheight|libredtail|masscan|"
    r"netsystemsresearch|researchscan|securitytrails|semrush|ahrefs|bytespider|"
    r"petalbot|facebookexternalhit",
    re.IGNORECASE,
)
ASSET_PATTERN = re.compile(
    r"\.(?:avif|css|gif|ico|jpe?g|js|json|map|mp4|pdf|png|svg|webm|webp|woff2?|ttf)(?:\?|$)",
    re.IGNORECASE,
)
IGNORED_PREFIXES = (
    "/admin",
    "/api/",
    "/assets/",
    "/static/",
    "/v2-api/",
    "/health",
    "/favicon",
    "/robots.txt",
    "/sitemap.xml",
)

PROVINCE_ALIASES = {
    "北京市": "北京",
    "天津市": "天津",
    "上海市": "上海",
    "重庆市": "重庆",
    "内蒙古自治区": "内蒙古",
    "广西壮族自治区": "广西",
    "西藏自治区": "西藏",
    "宁夏回族自治区": "宁夏",
    "新疆维吾尔自治区": "新疆",
    "香港特别行政区": "香港",
    "澳门特别行政区": "澳门",
}

_SEARCHER = None
_SEARCHER_PATH = None
_SEARCHER_LOCK = threading.Lock()
_CUMULATIVE_LOCK = threading.Lock()


def _clean_region_part(value: str) -> str:
    value = str(value or "").strip()
    return "" if value in {"0", "-", "null", "None"} else value


def normalize_province(value: str) -> str:
    value = _clean_region_part(value)
    if not value:
        return ""
    if value in PROVINCE_ALIASES:
        return PROVINCE_ALIASES[value]
    for suffix in ("省", "市", "壮族自治区", "回族自治区", "维吾尔自治区", "自治区", "特别行政区"):
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def _xdb_path() -> str:
    return os.getenv("REALGUARD_IP2REGION_XDB", DEFAULT_XDB_PATH).strip() or DEFAULT_XDB_PATH


def _load_searcher():
    global _SEARCHER, _SEARCHER_PATH
    path = _xdb_path()
    if ip2_searcher is None or ip2_util is None or not Path(path).is_file():
        return None
    if _SEARCHER is not None and _SEARCHER_PATH == path:
        return _SEARCHER
    with _SEARCHER_LOCK:
        if _SEARCHER is None or _SEARCHER_PATH != path:
            content = ip2_util.load_content_from_file(path)
            _SEARCHER = ip2_searcher.new_with_buffer(ip2_util.IPv4, content)
            _SEARCHER_PATH = path
    return _SEARCHER


def resolve_ip(ip: str) -> dict:
    searcher = _load_searcher()
    if searcher is None:
        return {}
    try:
        result = searcher.search(ip)
    except (ValueError, OSError, IndexError):
        return {}
    parts = [_clean_region_part(part) for part in str(result or "").split("|")]
    parts += [""] * max(0, 5 - len(parts))
    return {
        "country": parts[0],
        "province": normalize_province(parts[1]),
        "city": parts[2],
        "isp": parts[3],
        "isoCode": parts[4],
    }


def _is_public_ipv4(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.version == 4 and address.is_global


def _is_document_request(method: str, path: str, status: int, agent: str) -> bool:
    clean_path = path.split("?", 1)[0].lower()
    if method not in {"GET", "HEAD"} or not 200 <= status < 400:
        return False
    if BOT_PATTERN.search(agent or "") or ASSET_PATTERN.search(clean_path):
        return False
    return not any(clean_path.startswith(prefix) for prefix in IGNORED_PREFIXES)


def _masked_ip(value: str) -> str:
    parts = str(value or "").split(".")
    return f"{parts[0]}.{parts[1]}.*.*" if len(parts) == 4 else "已脱敏"


def _device_label(agent: str) -> str:
    agent = str(agent or "").lower()
    if any(token in agent for token in ("ipad", "tablet", "android 3", "android 4")):
        return "平板"
    if any(token in agent for token in ("mobile", "iphone", "android")):
        return "移动端"
    return "桌面端"


def _browser_label(agent: str) -> str:
    agent = str(agent or "").lower()
    if "edg/" in agent:
        return "Edge"
    if "firefox/" in agent:
        return "Firefox"
    if "chrome/" in agent or "crios/" in agent:
        return "Chrome"
    if "safari/" in agent:
        return "Safari"
    return "其他浏览器"


def parse_access_line(line: str) -> dict | None:
    match = LOG_PATTERN.match(line.strip())
    if not match:
        return None
    data = match.groupdict()
    try:
        status = int(data["status"])
        timestamp = datetime.strptime(data["time"], "%d/%b/%Y:%H:%M:%S %z")
    except (TypeError, ValueError):
        return None
    if not _is_public_ipv4(data["ip"]):
        return None
    if not _is_document_request(data["method"], data["path"], status, data["agent"]):
        return None
    return {
        "ip": data["ip"],
        "timestamp": timestamp,
        "path": data["path"],
        "status": status,
        "agent": data["agent"],
    }


def _cumulative_db_path() -> str:
    return os.getenv("REALGUARD_TRAFFIC_CUMULATIVE_DB", DEFAULT_CUMULATIVE_DB_PATH).strip() or DEFAULT_CUMULATIVE_DB_PATH


def _open_cumulative_db(path: str) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        CREATE TABLE IF NOT EXISTS traffic_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS traffic_events (
            event_hash TEXT PRIMARY KEY,
            occurred_at INTEGER NOT NULL,
            visitor_hash TEXT NOT NULL DEFAULT '',
            path TEXT NOT NULL DEFAULT '',
            is_homepage INTEGER NOT NULL DEFAULT 0,
            confirmed INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_traffic_events_occurred_at
            ON traffic_events(occurred_at);
        CREATE TABLE IF NOT EXISTS traffic_visitors (
            visitor_hash TEXT PRIMARY KEY,
            masked_ip TEXT NOT NULL,
            first_seen INTEGER NOT NULL,
            last_seen INTEGER NOT NULL,
            site_requests INTEGER NOT NULL DEFAULT 0,
            homepage_requests INTEGER NOT NULL DEFAULT 0,
            country TEXT NOT NULL DEFAULT '',
            province TEXT NOT NULL DEFAULT '',
            city TEXT NOT NULL DEFAULT '',
            isp TEXT NOT NULL DEFAULT '',
            iso_code TEXT NOT NULL DEFAULT '',
            agent TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_traffic_visitors_province
            ON traffic_visitors(province);
        CREATE TABLE IF NOT EXISTS traffic_visitor_paths (
            visitor_hash TEXT NOT NULL,
            path TEXT NOT NULL,
            PRIMARY KEY (visitor_hash, path)
        );
        """
    )
    event_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(traffic_events)").fetchall()
    }
    for column, definition in (
        ("visitor_hash", "TEXT NOT NULL DEFAULT ''"),
        ("path", "TEXT NOT NULL DEFAULT ''"),
        ("is_homepage", "INTEGER NOT NULL DEFAULT 0"),
        ("confirmed", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if column not in event_columns:
            connection.execute(f"ALTER TABLE traffic_events ADD COLUMN {column} {definition}")
    connection.commit()
    return connection


def _metadata_value(connection: sqlite3.Connection, key: str, factory: Callable[[], str]) -> str:
    row = connection.execute("SELECT value FROM traffic_metadata WHERE key = ?", (key,)).fetchone()
    if row:
        return str(row["value"])
    value = factory()
    connection.execute("INSERT INTO traffic_metadata (key, value) VALUES (?, ?)", (key, value))
    return value


def record_confirmed_pageview(
    *,
    ip: str,
    agent: str,
    visitor_id: str,
    event_id: str,
    page: str,
    resolver: Callable[[str], dict] = resolve_ip,
    occurred_at: datetime | None = None,
) -> bool:
    visitor_id = str(visitor_id or "").strip()
    event_id = str(event_id or "").strip()
    page = str(page or "").strip().lower()
    agent = str(agent or "").strip()
    if (
        not _is_public_ipv4(ip)
        or not re.fullmatch(r"[A-Za-z0-9_-]{16,96}", visitor_id)
        or not re.fullmatch(r"[A-Za-z0-9_-]{16,96}", event_id)
        or page not in {"home", "image", "video", "history"}
        or not agent
        or BOT_PATTERN.search(agent)
    ):
        return False
    timestamp = occurred_at or datetime.now().astimezone()
    if timestamp.tzinfo is None:
        timestamp = timestamp.astimezone()
    epoch = int(timestamp.timestamp())
    clean_path = "/" if page == "home" else f"/?page={page}"
    is_homepage = page == "home"
    try:
        with _CUMULATIVE_LOCK:
            connection = _open_cumulative_db(_cumulative_db_path())
            try:
                salt = _metadata_value(connection, "visitor_salt", lambda: secrets.token_hex(32))
                visitor_hash = hashlib.sha256(f"{salt}\0{visitor_id}".encode("utf-8")).hexdigest()
                event_hash = hashlib.sha256(f"{salt}\0{event_id}".encode("utf-8")).hexdigest()
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO traffic_events (
                        event_hash, occurred_at, visitor_hash, path, is_homepage, confirmed
                    ) VALUES (?, ?, ?, ?, ?, 1)
                    """,
                    (event_hash, epoch, visitor_hash, clean_path, int(is_homepage)),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    return True
                location = resolver(ip) or {}
                country = _clean_region_part(location.get("country", ""))
                province = normalize_province(location.get("province", ""))
                city = _clean_region_part(location.get("city", ""))
                isp = _clean_region_part(location.get("isp", ""))
                iso_code = _clean_region_part(location.get("isoCode", ""))
                connection.execute(
                    """
                    INSERT INTO traffic_visitors (
                        visitor_hash, masked_ip, first_seen, last_seen, site_requests,
                        homepage_requests, country, province, city, isp, iso_code, agent
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(visitor_hash) DO UPDATE SET
                        first_seen = MIN(traffic_visitors.first_seen, excluded.first_seen),
                        last_seen = MAX(traffic_visitors.last_seen, excluded.last_seen),
                        site_requests = traffic_visitors.site_requests + 1,
                        homepage_requests = traffic_visitors.homepage_requests + excluded.homepage_requests,
                        masked_ip = excluded.masked_ip,
                        country = CASE WHEN excluded.country != '' THEN excluded.country ELSE traffic_visitors.country END,
                        province = CASE WHEN excluded.province != '' THEN excluded.province ELSE traffic_visitors.province END,
                        city = CASE WHEN excluded.city != '' THEN excluded.city ELSE traffic_visitors.city END,
                        isp = CASE WHEN excluded.isp != '' THEN excluded.isp ELSE traffic_visitors.isp END,
                        iso_code = CASE WHEN excluded.iso_code != '' THEN excluded.iso_code ELSE traffic_visitors.iso_code END,
                        agent = excluded.agent
                    """,
                    (
                        visitor_hash,
                        _masked_ip(ip),
                        epoch,
                        epoch,
                        int(is_homepage),
                        country,
                        province,
                        city,
                        isp,
                        iso_code,
                        agent,
                    ),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO traffic_visitor_paths (visitor_hash, path) VALUES (?, ?)",
                    (visitor_hash, clean_path),
                )
                connection.commit()
                return True
            finally:
                connection.close()
    except (OSError, sqlite3.Error):
        return False


def _historical_page_from_referer(referer: str, allowed_hosts: set[str]) -> str | None:
    try:
        parsed = urlsplit(str(referer or ""))
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() not in allowed_hosts:
        return None
    if parsed.path.startswith("/admin"):
        return None
    page = parse_qs(parsed.query).get("page", [""])[0].lower()
    return page if page in {"image", "video", "history"} else "home"


def import_historical_browser_sessions(
    lines: Iterable[str],
    *,
    resolver: Callable[[str], dict] = resolve_ip,
    allowed_hosts: Iterable[str] | None = None,
) -> dict:
    """Recover high-confidence SPA visits from historical /api/me requests.

    A same-site referer and a normal browser user agent are required. This keeps
    raw document scans, load tools, admin traffic, and server probes out of the
    cumulative visitor figures.
    """
    hosts = {
        str(host).strip().lower()
        for host in (
            allowed_hosts
            or os.getenv(
                "REALGUARD_PUBLIC_HOSTS",
                "rrreal.cn,www.rrreal.cn,124.221.92.85",
            ).split(",")
        )
        if str(host).strip()
    }
    sessions = []
    rejected = 0
    for line in lines:
        match = LOG_PATTERN.match(str(line).strip())
        if not match:
            continue
        data = match.groupdict()
        clean_path = data["path"].split("?", 1)[0]
        agent = str(data.get("agent") or "").strip()
        page = _historical_page_from_referer(data.get("referer", ""), hosts)
        try:
            status = int(data["status"])
            timestamp = datetime.strptime(data["time"], "%d/%b/%Y:%H:%M:%S %z")
        except (TypeError, ValueError):
            rejected += 1
            continue
        if (
            data["method"] != "GET"
            or clean_path != "/api/me"
            or status not in {200, 401}
            or not _is_public_ipv4(data["ip"])
            or not agent.startswith("Mozilla/")
            or BOT_PATTERN.search(agent)
            or page is None
        ):
            rejected += 1
            continue
        sessions.append((str(line).strip(), data["ip"], agent, timestamp, page))

    imported = duplicates = 0
    located_by_ip = {}
    try:
        with _CUMULATIVE_LOCK:
            connection = _open_cumulative_db(_cumulative_db_path())
            try:
                salt = _metadata_value(connection, "visitor_salt", lambda: secrets.token_hex(32))
                for raw_line, ip, agent, timestamp, page in sessions:
                    visitor_hash = hashlib.sha256(
                        f"{salt}\0historical\0{ip}\0{agent}".encode("utf-8")
                    ).hexdigest()
                    event_hash = hashlib.sha256(
                        f"{salt}\0historical\0{raw_line}".encode("utf-8")
                    ).hexdigest()
                    is_homepage = page == "home"
                    clean_page_path = "/" if is_homepage else f"/?page={page}"
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO traffic_events (
                            event_hash, occurred_at, visitor_hash, path, is_homepage, confirmed
                        ) VALUES (?, ?, ?, ?, ?, 1)
                        """,
                        (
                            event_hash,
                            int(timestamp.timestamp()),
                            visitor_hash,
                            clean_page_path,
                            int(is_homepage),
                        ),
                    )
                    if cursor.rowcount != 1:
                        duplicates += 1
                        continue
                    location = located_by_ip.setdefault(ip, resolver(ip) or {})
                    country = _clean_region_part(location.get("country", ""))
                    province = normalize_province(location.get("province", ""))
                    city = _clean_region_part(location.get("city", ""))
                    isp = _clean_region_part(location.get("isp", ""))
                    iso_code = _clean_region_part(location.get("isoCode", ""))
                    epoch = int(timestamp.timestamp())
                    connection.execute(
                        """
                        INSERT INTO traffic_visitors (
                            visitor_hash, masked_ip, first_seen, last_seen, site_requests,
                            homepage_requests, country, province, city, isp, iso_code, agent
                        ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(visitor_hash) DO UPDATE SET
                            first_seen = MIN(traffic_visitors.first_seen, excluded.first_seen),
                            last_seen = MAX(traffic_visitors.last_seen, excluded.last_seen),
                            site_requests = traffic_visitors.site_requests + 1,
                            homepage_requests = traffic_visitors.homepage_requests + excluded.homepage_requests
                        """,
                        (
                            visitor_hash,
                            _masked_ip(ip),
                            epoch,
                            epoch,
                            int(is_homepage),
                            country,
                            province,
                            city,
                            isp,
                            iso_code,
                            agent,
                        ),
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO traffic_visitor_paths (visitor_hash, path) VALUES (?, ?)",
                        (visitor_hash, clean_page_path),
                    )
                    imported += 1
                connection.execute(
                    "INSERT OR REPLACE INTO traffic_metadata (key, value) VALUES (?, ?)",
                    ("historical_browser_sessions_imported_at", datetime.now().astimezone().isoformat()),
                )
                connection.commit()
            finally:
                connection.close()
    except (OSError, sqlite3.Error) as exc:
        return {"ready": False, "imported": imported, "duplicates": duplicates, "rejected": rejected, "error": str(exc)}
    return {"ready": True, "imported": imported, "duplicates": duplicates, "rejected": rejected}


def _stored_payload(
    connection: sqlite3.Connection,
    visitor_detail_limit: int,
    *,
    since_epoch: int | None = None,
    scope: str = "cumulative",
    online_cutoff: int | None = None,
) -> dict:
    where = "WHERE e.confirmed = 1"
    params = []
    if since_epoch is not None:
        where += " AND e.occurred_at >= ?"
        params.append(int(since_epoch))
    rows = connection.execute(
        f"""
        SELECT
            v.visitor_hash, v.masked_ip, v.country, v.province, v.city, v.isp,
            v.iso_code, v.agent,
            MIN(e.occurred_at) AS first_seen,
            MAX(e.occurred_at) AS last_seen,
            COUNT(*) AS site_requests,
            SUM(e.is_homepage) AS homepage_requests,
            COUNT(DISTINCT e.path) AS pages
        FROM traffic_events e
        JOIN traffic_visitors v ON v.visitor_hash = e.visitor_hash
        {where}
        GROUP BY v.visitor_hash
        ORDER BY last_seen DESC
        """,
        tuple(params),
    ).fetchall()
    unique_visitors = len(rows)
    total_requests = sum(int(row["site_requests"]) for row in rows)
    homepage_requests = sum(int(row["homepage_requests"]) for row in rows)
    homepage_visitors = sum(1 for row in rows if int(row["homepage_requests"]) > 0)
    located_rows = [row for row in rows if row["country"]]
    domestic_rows = [row for row in located_rows if _is_domestic(row["country"], row["iso_code"])]
    overseas_rows = [row for row in located_rows if not _is_domestic(row["country"], row["iso_code"])]

    province_rows = defaultdict(list)
    country_rows = defaultdict(list)
    for row in rows:
        if row["country"]:
            country_rows[row["country"]].append(row)
        if row["province"] and _is_domestic(row["country"], row["iso_code"]):
            province_rows[row["province"]].append(row)

    provinces = []
    for name, visitors in province_rows.items():
        city_visitors = defaultdict(int)
        for visitor in visitors:
            if visitor["city"]:
                city_visitors[visitor["city"]] += 1
        cities = [
            {"name": city, "visitors": count}
            for city, count in city_visitors.items()
            if count >= 2
        ]
        cities.sort(key=lambda item: (-item["visitors"], item["name"]))
        visible_cities = {item["name"] for item in cities}
        details = []
        for index, visitor in enumerate(visitors[:max(1, visitor_detail_limit)], start=1):
            last_seen = datetime.fromtimestamp(int(visitor["last_seen"])).astimezone()
            first_seen = datetime.fromtimestamp(int(visitor["first_seen"])).astimezone()
            details.append({
                "maskedIp": visitor["masked_ip"],
                "city": visitor["city"] if visitor["city"] in visible_cities else "省内其他地区",
                "network": visitor["isp"] or "未知网络",
                "device": _device_label(visitor["agent"]),
                "browser": _browser_label(visitor["agent"]),
                "requests": int(visitor["site_requests"]),
                "pages": int(visitor["pages"]),
                "firstSeen": first_seen.strftime("%m-%d %H:%M"),
                "lastSeen": last_seen.strftime("%m-%d %H:%M"),
                "label": f"访客 {index:02d}",
            })
        request_count = sum(int(visitor["site_requests"]) for visitor in visitors)
        provinces.append({
            "name": name,
            "visitors": len(visitors),
            "requests": request_count,
            "share": round(len(visitors) * 100 / unique_visitors, 1) if unique_visitors else 0.0,
            "cities": cities[:5],
            "visitorDetails": details,
        })
    provinces.sort(key=lambda item: (-item["visitors"], -item["requests"], item["name"]))
    countries = [
        {
            "name": name,
            "visitors": len(visitors),
            "requests": sum(int(visitor["site_requests"]) for visitor in visitors),
        }
        for name, visitors in country_rows.items()
    ]
    countries.sort(key=lambda item: (-item["visitors"], -item["requests"], item["name"]))
    first_seen = min((int(row["first_seen"]) for row in rows), default=0)
    return {
        "ready": True,
        "scope": scope,
        "since": datetime.fromtimestamp(first_seen).astimezone().strftime("%Y-%m-%d") if first_seen else "--",
        "requests": total_requests,
        "uniqueVisitors": unique_visitors,
        "homepage": {"pageViews": homepage_requests, "uniqueVisitors": homepage_visitors},
        "site": {"pageViews": total_requests, "uniqueVisitors": unique_visitors},
        "locatedVisitors": len(located_rows),
        "coveragePercent": round(len(located_rows) * 100 / unique_visitors, 1) if unique_visitors else 0.0,
        "domesticVisitors": len(domestic_rows),
        "overseasVisitors": len(overseas_rows),
        "unknownVisitors": unique_visitors - len(located_rows),
        "onlineVisitors": (
            sum(1 for row in rows if int(row["last_seen"]) >= int(online_cutoff))
            if online_cutoff is not None
            else 0
        ),
        "provinces": provinces,
        "countries": countries[:8],
        "privacy": {"rawIpsIncluded": False, "granularity": "province_with_masked_visitor_detail"},
    }


def _cumulative_payload(connection: sqlite3.Connection, visitor_detail_limit: int) -> dict:
    return _stored_payload(connection, visitor_detail_limit, scope="cumulative")


def cumulative_traffic_summary(
    *,
    visitor_detail_limit: int = DEFAULT_VISITOR_DETAIL_LIMIT,
    resolver: Callable[[str], dict] = resolve_ip,
) -> dict:
    del resolver
    return confirmed_traffic_summary(visitor_detail_limit=visitor_detail_limit)["cumulative"]


def confirmed_traffic_summary(
    *,
    visitor_detail_limit: int = DEFAULT_VISITOR_DETAIL_LIMIT,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    online_window_minutes: int = DEFAULT_ONLINE_WINDOW_MINUTES,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.astimezone()
    cutoff = int((now - timedelta(hours=max(1, window_hours))).timestamp())
    online_cutoff = int((now - timedelta(minutes=max(1, online_window_minutes))).timestamp())
    try:
        with _CUMULATIVE_LOCK:
            connection = _open_cumulative_db(_cumulative_db_path())
            try:
                payload = _stored_payload(
                    connection,
                    visitor_detail_limit,
                    since_epoch=cutoff,
                    scope="recent",
                    online_cutoff=online_cutoff,
                )
                cumulative = _cumulative_payload(connection, visitor_detail_limit)
            finally:
                connection.close()
    except (OSError, sqlite3.Error) as exc:
        cumulative = {
            "ready": False,
            "scope": "cumulative",
            "since": "--",
            "requests": 0,
            "uniqueVisitors": 0,
            "homepage": {"pageViews": 0, "uniqueVisitors": 0},
            "site": {"pageViews": 0, "uniqueVisitors": 0},
            "provinces": [],
            "countries": [],
            "source": {"message": f"累计访问统计暂不可用：{exc}"},
            "privacy": {"rawIpsIncluded": False, "granularity": "province_with_masked_visitor_detail"},
        }
        return {
            "ready": False,
            "windowHours": max(1, window_hours),
            "requests": 0,
            "uniqueVisitors": 0,
            "homepage": {"pageViews": 0, "uniqueVisitors": 0},
            "site": {"pageViews": 0, "uniqueVisitors": 0},
            "onlineVisitors": 0,
            "onlineWindowMinutes": max(1, online_window_minutes),
            "provinces": [],
            "countries": [],
            "cumulative": cumulative,
            "source": {"message": f"浏览器确认访问统计暂不可用：{exc}"},
            "privacy": {"rawIpsIncluded": False, "granularity": "province_with_masked_visitor_detail"},
        }
    payload["windowHours"] = max(1, window_hours)
    payload["onlineWindowMinutes"] = max(1, online_window_minutes)
    payload["cumulative"] = cumulative
    payload["source"] = {
        "kind": "confirmed-browser-pageview",
        "message": "仅统计前端实际运行后上报的匿名页面访问，压测、自动化与扫描流量不计入。",
    }
    return payload


def _is_domestic(country: str, iso_code: str) -> bool:
    country = str(country or "").lower()
    iso_code = str(iso_code or "").upper()
    return iso_code == "CN" or country in {"中国", "china", "中国大陆"}


def aggregate_access_lines(
    lines: Iterable[str],
    *,
    now: datetime | None = None,
    resolver: Callable[[str], dict] = resolve_ip,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    online_window_minutes: int = DEFAULT_ONLINE_WINDOW_MINUTES,
    visitor_detail_limit: int = DEFAULT_VISITOR_DETAIL_LIMIT,
) -> dict:
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.astimezone()
    cutoff = now - timedelta(hours=max(1, window_hours))
    online_cutoff = now - timedelta(minutes=max(1, online_window_minutes))
    activity_by_ip = {}

    for line in lines:
        item = parse_access_line(line)
        if not item or item["timestamp"] < cutoff or item["timestamp"] > now + timedelta(minutes=5):
            continue
        activity = activity_by_ip.setdefault(item["ip"], {
            "requests": 0,
            "firstSeen": item["timestamp"],
            "lastSeen": item["timestamp"],
            "paths": set(),
            "pathCounts": defaultdict(int),
            "agent": item["agent"],
        })
        activity["requests"] += 1
        activity["firstSeen"] = min(activity["firstSeen"], item["timestamp"])
        activity["lastSeen"] = max(activity["lastSeen"], item["timestamp"])
        clean_path = item["path"].split("?", 1)[0]
        activity["paths"].add(clean_path)
        activity["pathCounts"][clean_path] += 1
        if item["agent"]:
            activity["agent"] = item["agent"]

    province_data = defaultdict(lambda: {"ips": set(), "requests": 0, "cities": defaultdict(set), "visitors": []})
    country_data = defaultdict(lambda: {"ips": set(), "requests": 0})
    located = domestic = overseas = unknown = 0

    for ip, activity in activity_by_ip.items():
        request_count = activity["requests"]
        location = resolver(ip) or {}
        country = _clean_region_part(location.get("country", ""))
        province = normalize_province(location.get("province", ""))
        city = _clean_region_part(location.get("city", ""))
        iso_code = _clean_region_part(location.get("isoCode", ""))
        if not country:
            unknown += 1
            continue
        located += 1
        country_data[country]["ips"].add(ip)
        country_data[country]["requests"] += request_count
        if _is_domestic(country, iso_code):
            domestic += 1
            if province:
                province_data[province]["ips"].add(ip)
                province_data[province]["requests"] += request_count
                if city:
                    province_data[province]["cities"][city].add(ip)
                province_data[province]["visitors"].append({
                    "maskedIp": _masked_ip(ip),
                    "city": city,
                    "network": _clean_region_part(location.get("isp", "")) or "未知网络",
                    "device": _device_label(activity["agent"]),
                    "browser": _browser_label(activity["agent"]),
                    "requests": request_count,
                    "pages": len(activity["paths"]),
                    "firstSeen": activity["firstSeen"].strftime("%m-%d %H:%M"),
                    "lastSeen": activity["lastSeen"].strftime("%m-%d %H:%M"),
                    "_lastSeen": activity["lastSeen"],
                })
        else:
            overseas += 1

    unique_visitors = len(activity_by_ip)
    total_requests = sum(activity["requests"] for activity in activity_by_ip.values())
    homepage_visitors = sum(
        1 for activity in activity_by_ip.values()
        if any(path in HOMEPAGE_PATHS for path in activity["paths"])
    )
    homepage_page_views = sum(
        sum(count for path, count in activity["pathCounts"].items() if path in HOMEPAGE_PATHS)
        for activity in activity_by_ip.values()
    )
    online_visitors = sum(
        1 for activity in activity_by_ip.values()
        if activity["lastSeen"] >= online_cutoff
    )
    provinces = []
    for name, data in province_data.items():
        visitors = len(data["ips"])
        cities = [
            {"name": city, "visitors": len(ips)}
            for city, ips in data["cities"].items()
            if len(ips) >= 2
        ]
        cities.sort(key=lambda item: (-item["visitors"], item["name"]))
        visible_cities = {item["name"] for item in cities}
        visitor_details = sorted(
            data["visitors"],
            key=lambda item: (-item["_lastSeen"].timestamp(), -item["requests"], item["maskedIp"]),
        )[:max(1, visitor_detail_limit)]
        for index, visitor in enumerate(visitor_details, start=1):
            visitor.pop("_lastSeen", None)
            visitor["label"] = f"访客 {index:02d}"
            if visitor["city"] not in visible_cities:
                visitor["city"] = "省内其他地区"
        provinces.append({
            "name": name,
            "visitors": visitors,
            "requests": data["requests"],
            "share": round(visitors * 100 / unique_visitors, 1) if unique_visitors else 0.0,
            "cities": cities[:5],
            "visitorDetails": visitor_details,
        })
    provinces.sort(key=lambda item: (-item["visitors"], -item["requests"], item["name"]))

    countries = [
        {"name": name, "visitors": len(data["ips"]), "requests": data["requests"]}
        for name, data in country_data.items()
    ]
    countries.sort(key=lambda item: (-item["visitors"], -item["requests"], item["name"]))

    return {
        "windowHours": max(1, window_hours),
        "requests": total_requests,
        "uniqueVisitors": unique_visitors,
        "homepage": {
            "pageViews": homepage_page_views,
            "uniqueVisitors": homepage_visitors,
        },
        "site": {
            "pageViews": total_requests,
            "uniqueVisitors": unique_visitors,
        },
        "onlineVisitors": online_visitors,
        "onlineWindowMinutes": max(1, online_window_minutes),
        "locatedVisitors": located,
        "coveragePercent": round(located * 100 / unique_visitors, 1) if unique_visitors else 0.0,
        "domesticVisitors": domestic,
        "overseasVisitors": overseas,
        "unknownVisitors": unknown,
        "provinces": provinces,
        "countries": countries[:8],
        "privacy": {"rawIpsIncluded": False, "granularity": "province_with_masked_visitor_detail"},
    }


def traffic_summary() -> dict:
    try:
        window_hours = max(1, int(os.getenv("REALGUARD_TRAFFIC_WINDOW_HOURS", DEFAULT_WINDOW_HOURS)))
        online_window_minutes = max(1, int(os.getenv("REALGUARD_TRAFFIC_ONLINE_MINUTES", DEFAULT_ONLINE_WINDOW_MINUTES)))
        visitor_detail_limit = max(1, min(50, int(os.getenv("REALGUARD_TRAFFIC_VISITOR_DETAIL_LIMIT", DEFAULT_VISITOR_DETAIL_LIMIT))))
    except ValueError:
        window_hours = DEFAULT_WINDOW_HOURS
        online_window_minutes = DEFAULT_ONLINE_WINDOW_MINUTES
        visitor_detail_limit = DEFAULT_VISITOR_DETAIL_LIMIT
    return confirmed_traffic_summary(
        visitor_detail_limit=visitor_detail_limit,
        window_hours=window_hours,
        online_window_minutes=online_window_minutes,
    )
