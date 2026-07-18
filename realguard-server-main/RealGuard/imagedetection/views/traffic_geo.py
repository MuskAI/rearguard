"""Privacy-preserving visitor geography aggregation for the admin screen."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import ipaddress
import os
from pathlib import Path
import re
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
    return payload
