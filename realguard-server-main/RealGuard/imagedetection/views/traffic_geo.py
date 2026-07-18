"""Privacy-preserving visitor geography aggregation for the admin screen."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import glob
import gzip
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
from typing import Callable, Iterable

try:
    import ip2region.searcher as ip2_searcher
    import ip2region.util as ip2_util
except ImportError:  # Optional during local development and unit tests.
    ip2_searcher = None
    ip2_util = None


DEFAULT_XDB_PATH = "/opt/realguard-data/ip2region_v4.xdb"
DEFAULT_ACCESS_LOGS = "/var/log/nginx/access.log,/var/log/nginx/access.log.1"
DEFAULT_ACCESS_LOG_GLOB = "/var/log/nginx/access.log*"
DEFAULT_CUMULATIVE_DB_PATH = "/opt/realguard-data/traffic-cumulative.sqlite3"
DEFAULT_WINDOW_HOURS = 24
DEFAULT_ONLINE_WINDOW_MINUTES = 5
DEFAULT_TAIL_BYTES = 4 * 1024 * 1024
DEFAULT_VISITOR_DETAIL_LIMIT = 20
HOMEPAGE_PATHS = {"/", "/index.html"}

LOG_PATTERN = re.compile(
    r'^(?P<ip>\S+)\s+\S+\s+\S+\s+\[(?P<time>[^]]+)]\s+'
    r'"(?P<method>\S+)\s+(?P<path>\S+)(?:\s+HTTP/[^\"]+)?"\s+'
    r'(?P<status>\d{3})\s+\S+\s+"[^"]*"\s+"(?P<agent>[^"]*)"'
)
BOT_PATTERN = re.compile(
    r"apachebench|\bcurl\b|python-requests|uptimerobot|healthcheck|go-http-client|"
    r"bot|spider|crawler|headlesschrome",
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


def _tail_lines(path: str, max_bytes: int) -> list[str]:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - max_bytes)
            handle.seek(start)
            data = handle.read()
    except (FileNotFoundError, PermissionError, OSError):
        return []
    if start:
        first_newline = data.find(b"\n")
        data = data[first_newline + 1 :] if first_newline >= 0 else b""
    return data.decode("utf-8", errors="replace").splitlines()


def _cumulative_db_path() -> str:
    return os.getenv("REALGUARD_TRAFFIC_CUMULATIVE_DB", DEFAULT_CUMULATIVE_DB_PATH).strip() or DEFAULT_CUMULATIVE_DB_PATH


def _cumulative_log_paths() -> list[str]:
    pattern = os.getenv("REALGUARD_ACCESS_LOG_GLOB", DEFAULT_ACCESS_LOG_GLOB).strip() or DEFAULT_ACCESS_LOG_GLOB
    return sorted(
        (path for path in glob.glob(pattern) if Path(path).is_file()),
        key=lambda path: (Path(path).stat().st_mtime_ns, path),
    )


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
        CREATE TABLE IF NOT EXISTS traffic_sources (
            source_id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            offset_bytes INTEGER NOT NULL DEFAULT 0,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            mtime_ns INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS traffic_events (
            event_hash TEXT PRIMARY KEY,
            occurred_at INTEGER NOT NULL
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
    connection.commit()
    return connection


def _metadata_value(connection: sqlite3.Connection, key: str, factory: Callable[[], str]) -> str:
    row = connection.execute("SELECT value FROM traffic_metadata WHERE key = ?", (key,)).fetchone()
    if row:
        return str(row["value"])
    value = factory()
    connection.execute("INSERT INTO traffic_metadata (key, value) VALUES (?, ?)", (key, value))
    return value


def _lines_with_offsets(data: bytes, start: int = 0) -> list[tuple[str, int]]:
    result = []
    offset = start
    for raw_line in data.splitlines(keepends=True):
        result.append((raw_line.rstrip(b"\r\n").decode("utf-8", errors="replace"), offset))
        offset += len(raw_line)
    return result


def _read_unprocessed_source(connection: sqlite3.Connection, path: str) -> tuple[list[tuple[str, int]], str, int, os.stat_result]:
    stat = os.stat(path)
    source_id = f"{stat.st_dev}:{stat.st_ino}"
    row = connection.execute(
        "SELECT offset_bytes FROM traffic_sources WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    previous_offset = int(row["offset_bytes"]) if row else 0
    if path.endswith(".gz"):
        if row and previous_offset == stat.st_size:
            return [], source_id, stat.st_size, stat
        try:
            with gzip.open(path, "rb") as handle:
                return _lines_with_offsets(handle.read()), source_id, stat.st_size, stat
        except (gzip.BadGzipFile, EOFError, OSError):
            return [], source_id, previous_offset, stat

    start = previous_offset if stat.st_size >= previous_offset else 0
    if stat.st_size == start:
        return [], source_id, start, stat
    with open(path, "rb") as handle:
        handle.seek(start)
        data = handle.read()
    last_newline = data.rfind(b"\n")
    if last_newline < 0:
        return [], source_id, start, stat
    consumed = data[: last_newline + 1]
    return _lines_with_offsets(consumed, start), source_id, start + last_newline + 1, stat


def _update_cumulative_store(
    connection: sqlite3.Connection,
    paths: Iterable[str],
    *,
    resolver: Callable[[str], dict] = resolve_ip,
) -> int:
    salt = _metadata_value(connection, "visitor_salt", lambda: secrets.token_hex(32))
    inserted = 0
    for path in paths:
        try:
            lines, source_id, next_offset, stat = _read_unprocessed_source(connection, path)
        except (FileNotFoundError, PermissionError, OSError):
            continue
        for line, line_offset in lines:
            item = parse_access_line(line)
            if not item:
                continue
            event_hash = hashlib.sha256(
                f"{salt}\0{line_offset}\0{line}".encode("utf-8", errors="replace")
            ).hexdigest()
            occurred_at = int(item["timestamp"].timestamp())
            cursor = connection.execute(
                "INSERT OR IGNORE INTO traffic_events (event_hash, occurred_at) VALUES (?, ?)",
                (event_hash, occurred_at),
            )
            if cursor.rowcount != 1:
                continue
            ip = item["ip"]
            clean_path = item["path"].split("?", 1)[0]
            is_homepage = clean_path in HOMEPAGE_PATHS
            visitor_hash = hashlib.sha256(f"{salt}\0{ip}".encode("utf-8")).hexdigest()
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
                    country = CASE WHEN excluded.country != '' THEN excluded.country ELSE traffic_visitors.country END,
                    province = CASE WHEN excluded.province != '' THEN excluded.province ELSE traffic_visitors.province END,
                    city = CASE WHEN excluded.city != '' THEN excluded.city ELSE traffic_visitors.city END,
                    isp = CASE WHEN excluded.isp != '' THEN excluded.isp ELSE traffic_visitors.isp END,
                    iso_code = CASE WHEN excluded.iso_code != '' THEN excluded.iso_code ELSE traffic_visitors.iso_code END,
                    agent = CASE WHEN excluded.agent != '' THEN excluded.agent ELSE traffic_visitors.agent END
                """,
                (
                    visitor_hash,
                    _masked_ip(ip),
                    occurred_at,
                    occurred_at,
                    int(is_homepage),
                    country,
                    province,
                    city,
                    isp,
                    iso_code,
                    item["agent"],
                ),
            )
            connection.execute(
                "INSERT OR IGNORE INTO traffic_visitor_paths (visitor_hash, path) VALUES (?, ?)",
                (visitor_hash, clean_path),
            )
            inserted += 1
        connection.execute(
            """
            INSERT INTO traffic_sources (source_id, path, offset_bytes, size_bytes, mtime_ns, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                path = excluded.path,
                offset_bytes = MAX(traffic_sources.offset_bytes, excluded.offset_bytes),
                size_bytes = excluded.size_bytes,
                mtime_ns = excluded.mtime_ns,
                updated_at = excluded.updated_at
            """,
            (source_id, path, next_offset, stat.st_size, stat.st_mtime_ns, int(datetime.now().timestamp())),
        )
    retention_cutoff = int((datetime.now().astimezone() - timedelta(days=30)).timestamp())
    connection.execute("DELETE FROM traffic_events WHERE occurred_at < ?", (retention_cutoff,))
    connection.commit()
    return inserted


def _cumulative_payload(connection: sqlite3.Connection, visitor_detail_limit: int) -> dict:
    rows = connection.execute(
        """
        SELECT v.*, COUNT(p.path) AS pages
        FROM traffic_visitors v
        LEFT JOIN traffic_visitor_paths p ON p.visitor_hash = v.visitor_hash
        GROUP BY v.visitor_hash
        ORDER BY v.last_seen DESC
        """
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
        "scope": "cumulative",
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
        "provinces": provinces,
        "countries": countries[:8],
        "privacy": {"rawIpsIncluded": False, "granularity": "province_with_masked_visitor_detail"},
    }


def cumulative_traffic_summary(
    *,
    visitor_detail_limit: int = DEFAULT_VISITOR_DETAIL_LIMIT,
    resolver: Callable[[str], dict] = resolve_ip,
) -> dict:
    paths = _cumulative_log_paths()
    try:
        with _CUMULATIVE_LOCK:
            connection = _open_cumulative_db(_cumulative_db_path())
            try:
                _update_cumulative_store(connection, paths, resolver=resolver)
                payload = _cumulative_payload(connection, visitor_detail_limit)
            finally:
                connection.close()
    except (OSError, sqlite3.Error) as exc:
        return {
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
    payload["source"] = {
        "kind": "persistent-nginx-access-log",
        "message": "累计数据已持久化；初始数据来自服务器现存轮转日志。",
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
    paths = [
        path.strip()
        for path in os.getenv("REALGUARD_ACCESS_LOG_PATHS", DEFAULT_ACCESS_LOGS).split(",")
        if path.strip()
    ]
    try:
        max_bytes = max(64 * 1024, int(os.getenv("REALGUARD_ACCESS_LOG_TAIL_BYTES", DEFAULT_TAIL_BYTES)))
        window_hours = max(1, int(os.getenv("REALGUARD_TRAFFIC_WINDOW_HOURS", DEFAULT_WINDOW_HOURS)))
        online_window_minutes = max(1, int(os.getenv("REALGUARD_TRAFFIC_ONLINE_MINUTES", DEFAULT_ONLINE_WINDOW_MINUTES)))
        visitor_detail_limit = max(1, min(50, int(os.getenv("REALGUARD_TRAFFIC_VISITOR_DETAIL_LIMIT", DEFAULT_VISITOR_DETAIL_LIMIT))))
    except ValueError:
        max_bytes = DEFAULT_TAIL_BYTES
        window_hours = DEFAULT_WINDOW_HOURS
        online_window_minutes = DEFAULT_ONLINE_WINDOW_MINUTES
        visitor_detail_limit = DEFAULT_VISITOR_DETAIL_LIMIT
    readable_paths = [path for path in paths if os.access(path, os.R_OK)]
    lines = []
    for path in readable_paths:
        lines.extend(_tail_lines(path, max_bytes))

    database_ready = _load_searcher() is not None
    payload = aggregate_access_lines(
        lines,
        window_hours=window_hours,
        online_window_minutes=online_window_minutes,
        visitor_detail_limit=visitor_detail_limit,
    )
    payload["ready"] = bool(readable_paths and database_ready)
    payload["source"] = {
        "kind": "nginx-access-log",
        "geoProvider": "ip2region",
        "databaseReady": database_ready,
        "message": (
            "访问地域按省级聚合，原始 IP 不进入接口响应。"
            if readable_paths and database_ready
            else "访问日志或离线 IP 归属数据库尚未就绪。"
        ),
    }
    payload["cumulative"] = cumulative_traffic_summary(visitor_detail_limit=visitor_detail_limit)
    return payload
