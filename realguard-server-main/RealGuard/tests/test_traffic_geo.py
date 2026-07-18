from datetime import datetime, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection.views import traffic_geo  # noqa: E402


NOW = datetime(2026, 7, 18, 18, 0, 0, tzinfo=timezone.utc)


def log_line(ip, path="/", status=200, agent="Mozilla/5.0 Chrome/126.0", timestamp="18/Jul/2026:17:30:00 +0000"):
    return f'{ip} - - [{timestamp}] "GET {path} HTTP/1.1" {status} 1188 "-" "{agent}"'


def test_parse_access_line_accepts_public_document_request():
    parsed = traffic_geo.parse_access_line(log_line("8.8.8.8", "/image-detection"))

    assert parsed["ip"] == "8.8.8.8"
    assert parsed["path"] == "/image-detection"


def test_parse_access_line_rejects_private_static_bot_and_error_requests():
    assert traffic_geo.parse_access_line(log_line("10.0.0.8")) is None
    assert traffic_geo.parse_access_line(log_line("8.8.8.8", "/static/app.js")) is None
    assert traffic_geo.parse_access_line(log_line("8.8.8.8", agent="ApacheBench/2.3")) is None
    assert traffic_geo.parse_access_line(log_line("8.8.8.8", agent="Googlebot/2.1")) is None
    assert traffic_geo.parse_access_line(log_line("8.8.8.8", status=429)) is None


def test_aggregate_access_lines_returns_only_anonymous_province_counts():
    locations = {
        "8.8.8.8": {"country": "中国", "province": "浙江省", "city": "杭州市", "isoCode": "CN"},
        "1.1.1.1": {"country": "中国", "province": "浙江", "city": "杭州市", "isoCode": "CN"},
        "9.9.9.9": {"country": "美国", "province": "", "city": "", "isoCode": "US"},
    }
    lines = [
        log_line("8.8.8.8"),
        log_line("8.8.8.8", "/agent"),
        log_line("1.1.1.1", "/developer"),
        log_line("9.9.9.9"),
        log_line("7.7.7.7", timestamp="16/Jul/2026:17:30:00 +0000"),
    ]

    payload = traffic_geo.aggregate_access_lines(
        lines,
        now=NOW,
        resolver=lambda ip: locations.get(ip, {}),
    )

    assert payload["uniqueVisitors"] == 3
    assert payload["requests"] == 4
    assert payload["domesticVisitors"] == 2
    assert payload["overseasVisitors"] == 1
    assert payload["coveragePercent"] == 100.0
    assert payload["provinces"] == [{
        "name": "浙江",
        "visitors": 2,
        "requests": 3,
        "share": 66.7,
        "cities": [{"name": "杭州市", "visitors": 2}],
        "visitorDetails": [{
            "maskedIp": "8.8.*.*",
            "city": "杭州市",
            "network": "未知网络",
            "device": "桌面端",
            "browser": "Chrome",
            "requests": 2,
            "pages": 2,
            "firstSeen": "07-18 17:30",
            "lastSeen": "07-18 17:30",
            "label": "访客 01",
        }, {
            "maskedIp": "1.1.*.*",
            "city": "杭州市",
            "network": "未知网络",
            "device": "桌面端",
            "browser": "Chrome",
            "requests": 1,
            "pages": 1,
            "firstSeen": "07-18 17:30",
            "lastSeen": "07-18 17:30",
            "label": "访客 02",
        }],
    }]
    assert payload["privacy"] == {"rawIpsIncluded": False, "granularity": "province_with_masked_visitor_detail"}
    assert "8.8.8.8" not in str(payload)


def test_traffic_summary_degrades_without_log_or_database(monkeypatch):
    monkeypatch.setenv("REALGUARD_ACCESS_LOG_PATHS", "/missing/access.log")
    monkeypatch.setattr(traffic_geo, "_load_searcher", lambda: None)

    payload = traffic_geo.traffic_summary()

    assert payload["ready"] is False
    assert payload["uniqueVisitors"] == 0
    assert payload["source"]["databaseReady"] is False


def test_single_visitor_city_is_hidden_and_ip_is_masked():
    payload = traffic_geo.aggregate_access_lines(
        [log_line("8.8.8.8", agent="Mozilla/5.0 (iPhone) Safari/605.1")],
        now=NOW,
        resolver=lambda _ip: {
            "country": "中国",
            "province": "四川省",
            "city": "成都市",
            "isp": "示例网络",
            "isoCode": "CN",
        },
    )

    visitor = payload["provinces"][0]["visitorDetails"][0]
    assert visitor["maskedIp"] == "8.8.*.*"
    assert visitor["city"] == "省内其他地区"
    assert visitor["device"] == "移动端"
    assert visitor["browser"] == "Safari"
    assert "8.8.8.8" not in str(payload)
