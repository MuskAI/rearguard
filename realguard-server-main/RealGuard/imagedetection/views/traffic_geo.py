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
DEFAULT_TAIL_BYTES = 4 * 1024 * 1024

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
    return {"ip": data["ip"], "timestamp": timestamp, "path": data["path"], "status": status}


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
) -> dict:
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.astimezone()
    cutoff = now - timedelta(hours=max(1, window_hours))
    requests_by_ip = defaultdict(int)

    for line in lines:
        item = parse_access_line(line)
        if not item or item["timestamp"] < cutoff or item["timestamp"] > now + timedelta(minutes=5):
            continue
        requests_by_ip[item["ip"]] += 1

    province_data = defaultdict(lambda: {"ips": set(), "requests": 0, "cities": defaultdict(set)})
    country_data = defaultdict(lambda: {"ips": set(), "requests": 0})
    located = domestic = overseas = unknown = 0

    for ip, request_count in requests_by_ip.items():
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
        else:
            overseas += 1

    unique_visitors = len(requests_by_ip)
    total_requests = sum(requests_by_ip.values())
    provinces = []
    for name, data in province_data.items():
        visitors = len(data["ips"])
        cities = [
            {"name": city, "visitors": len(ips)}
            for city, ips in data["cities"].items()
            if len(ips) >= 2
        ]
        cities.sort(key=lambda item: (-item["visitors"], item["name"]))
        provinces.append({
            "name": name,
            "visitors": visitors,
            "requests": data["requests"],
            "share": round(visitors * 100 / unique_visitors, 1) if unique_visitors else 0.0,
            "cities": cities[:5],
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
        "locatedVisitors": located,
        "coveragePercent": round(located * 100 / unique_visitors, 1) if unique_visitors else 0.0,
        "domesticVisitors": domestic,
        "overseasVisitors": overseas,
        "unknownVisitors": unknown,
        "provinces": provinces,
        "countries": countries[:8],
        "privacy": {"rawIpsIncluded": False, "granularity": "province"},
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
    except ValueError:
        max_bytes, window_hours = DEFAULT_TAIL_BYTES, DEFAULT_WINDOW_HOURS
    readable_paths = [path for path in paths if os.access(path, os.R_OK)]
    lines = []
    for path in readable_paths:
        lines.extend(_tail_lines(path, max_bytes))

    database_ready = _load_searcher() is not None
    payload = aggregate_access_lines(lines, window_hours=window_hours)
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
