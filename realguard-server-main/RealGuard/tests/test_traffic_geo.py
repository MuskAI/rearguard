from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection.views import traffic_geo  # noqa: E402


NOW = datetime(2026, 7, 18, 18, 0, 0, tzinfo=timezone.utc)


def log_line(ip, path="/", status=200, agent="Mozilla/5.0 Chrome/126.0", timestamp="18/Jul/2026:17:30:00 +0000", referer="-"):
    return f'{ip} - - [{timestamp}] "GET {path} HTTP/1.1" {status} 1188 "{referer}" "{agent}"'


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
    assert payload["homepage"] == {"pageViews": 2, "uniqueVisitors": 2}
    assert payload["site"] == {"pageViews": 4, "uniqueVisitors": 3}
    assert payload["onlineVisitors"] == 0
    assert payload["onlineWindowMinutes"] == 5
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


def test_traffic_summary_uses_confirmed_browser_store_without_access_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("REALGUARD_ACCESS_LOG_PATHS", "/missing/access.log")
    monkeypatch.setenv("REALGUARD_TRAFFIC_CUMULATIVE_DB", str(tmp_path / "traffic.sqlite3"))

    payload = traffic_geo.traffic_summary()

    assert payload["ready"] is True
    assert payload["uniqueVisitors"] == 0
    assert payload["source"]["kind"] == "confirmed-browser-pageview"


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


def test_online_visitors_are_deduplicated_within_activity_window():
    lines = [
        log_line("8.8.8.8", "/", timestamp="18/Jul/2026:17:58:00 +0000"),
        log_line("8.8.8.8", "/developer", timestamp="18/Jul/2026:17:59:00 +0000"),
        log_line("1.1.1.1", "/", timestamp="18/Jul/2026:17:54:00 +0000"),
    ]

    payload = traffic_geo.aggregate_access_lines(
        lines,
        now=NOW,
        resolver=lambda _ip: {},
        online_window_minutes=5,
    )

    assert payload["onlineVisitors"] == 1
    assert payload["homepage"] == {"pageViews": 2, "uniqueVisitors": 2}
    assert payload["site"] == {"pageViews": 3, "uniqueVisitors": 2}


def test_confirmed_pageviews_persist_deduplicate_and_drive_all_windows(tmp_path, monkeypatch):
    monkeypatch.setenv("REALGUARD_TRAFFIC_CUMULATIVE_DB", str(tmp_path / "traffic.sqlite3"))
    locations = {
        "8.8.8.8": {"country": "中国", "province": "浙江省", "city": "杭州市", "isoCode": "CN"},
        "1.1.1.1": {"country": "中国", "province": "四川省", "city": "成都市", "isoCode": "CN"},
    }
    resolver = lambda ip: locations.get(ip, {})
    common = {"agent": "Mozilla/5.0 Chrome/126.0", "resolver": resolver}

    assert traffic_geo.record_confirmed_pageview(
        ip="8.8.8.8", visitor_id="visitor-00000001", event_id="event-00000000001",
        page="home", occurred_at=NOW - timedelta(minutes=2), **common,
    )
    assert traffic_geo.record_confirmed_pageview(
        ip="8.8.8.8", visitor_id="visitor-00000001", event_id="event-00000000001",
        page="home", occurred_at=NOW - timedelta(minutes=2), **common,
    )
    assert traffic_geo.record_confirmed_pageview(
        ip="8.8.8.8", visitor_id="visitor-00000001", event_id="event-00000000002",
        page="image", occurred_at=NOW - timedelta(minutes=1), **common,
    )
    assert traffic_geo.record_confirmed_pageview(
        ip="1.1.1.1", visitor_id="visitor-00000002", event_id="event-00000000003",
        page="home", occurred_at=NOW - timedelta(minutes=10), **common,
    )

    payload = traffic_geo.confirmed_traffic_summary(now=NOW)
    cumulative = payload["cumulative"]

    assert payload["site"] == {"pageViews": 3, "uniqueVisitors": 2}
    assert payload["homepage"] == {"pageViews": 2, "uniqueVisitors": 2}
    assert payload["onlineVisitors"] == 1
    assert cumulative["site"] == payload["site"]
    assert [item["name"] for item in cumulative["provinces"]] == ["浙江", "四川"]
    assert "8.8.8.8" not in str(payload)


def test_confirmed_pageview_rejects_automation_and_invalid_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("REALGUARD_TRAFFIC_CUMULATIVE_DB", str(tmp_path / "traffic.sqlite3"))
    common = {
        "ip": "8.8.8.8",
        "visitor_id": "visitor-00000001",
        "event_id": "event-00000000001",
        "page": "home",
        "resolver": lambda _ip: {},
    }

    assert not traffic_geo.record_confirmed_pageview(agent="HeadlessChrome/126.0", **common)
    assert not traffic_geo.record_confirmed_pageview(agent="Mozilla/5.0", **{**common, "page": "admin"})


def test_confirmed_pageview_tracks_workspace_and_developer_as_distinct_surfaces(tmp_path, monkeypatch):
    monkeypatch.setenv("REALGUARD_TRAFFIC_CUMULATIVE_DB", str(tmp_path / "traffic.sqlite3"))
    common = {
        "ip": "8.8.8.8",
        "agent": "Mozilla/5.0 Chrome/126.0",
        "visitor_id": "visitor-00000001",
        "resolver": lambda _ip: {"country": "中国", "province": "浙江省", "isoCode": "CN"},
    }

    assert traffic_geo.record_confirmed_pageview(
        event_id="event-workspace-001", page="workspace", occurred_at=NOW, **common,
    )
    assert traffic_geo.record_confirmed_pageview(
        event_id="event-developer-001", page="developer", occurred_at=NOW, **common,
    )

    payload = traffic_geo.confirmed_traffic_summary(now=NOW)
    assert payload["site"] == {"pageViews": 2, "uniqueVisitors": 1}
    assert payload["homepage"] == {"pageViews": 0, "uniqueVisitors": 0}
    assert payload["provinces"][0]["visitorDetails"][0]["pages"] == 2


def test_confirmed_pageview_accepts_about_and_playground_surfaces(tmp_path, monkeypatch):
    monkeypatch.setenv("REALGUARD_TRAFFIC_CUMULATIVE_DB", str(tmp_path / "traffic.sqlite3"))
    common = {
        "ip": "8.8.8.8",
        "agent": "Mozilla/5.0 Chrome/126.0",
        "visitor_id": "visitor-00000001",
        "resolver": lambda _ip: {"country": "中国", "province": "浙江省", "isoCode": "CN"},
        "occurred_at": NOW,
    }
    assert traffic_geo.record_confirmed_pageview(
        event_id="event-about-0000001", page="about", **common,
    )
    assert traffic_geo.record_confirmed_pageview(
        event_id="event-playground-001", page="playground", **common,
    )
    assert traffic_geo.confirmed_traffic_summary(now=NOW)["site"]["pageViews"] == 2


def test_registered_account_activity_is_separate_from_anonymous_big_screen_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("REALGUARD_TRAFFIC_CUMULATIVE_DB", str(tmp_path / "traffic.sqlite3"))
    account_uuid = "11111111-1111-4111-8111-111111111111"
    other_uuid = "22222222-2222-4222-8222-222222222222"
    common = {
        "ip": "8.8.8.8",
        "agent": "Mozilla/5.0 Chrome/126.0",
        "visitor_id": "visitor-00000001",
        "resolver": lambda _ip: {
            "country": "中国", "province": "浙江省", "city": "杭州市", "isoCode": "CN",
        },
    }
    assert traffic_geo.record_confirmed_pageview(
        event_id="event-account-00001", page="home", account_uuid=account_uuid,
        occurred_at=NOW - timedelta(minutes=2), **common,
    )
    assert traffic_geo.record_confirmed_pageview(
        event_id="event-account-00002", page="workspace", account_uuid=account_uuid,
        occurred_at=NOW - timedelta(minutes=1), **common,
    )
    assert traffic_geo.record_confirmed_pageview(
        event_id="event-anonymous-0001", page="developer", account_uuid="",
        occurred_at=NOW, **common,
    )

    activity = traffic_geo.registered_account_activity(
        [account_uuid, other_uuid], province="浙江省", now=NOW,
    )
    public_payload = traffic_geo.confirmed_traffic_summary(now=NOW)

    assert activity["total"] == 1
    assert activity["accounts"][0]["accountUuid"] == account_uuid
    assert activity["accounts"][0]["requests"] == 2
    assert activity["accounts"][0]["pages"] == 2
    assert activity["accounts"][0]["city"] == "杭州市"
    assert account_uuid not in str(public_payload)
    assert "accountUuid" not in str(public_payload)
    assert public_payload["site"] == {"pageViews": 3, "uniqueVisitors": 1}


def test_registered_account_activity_keeps_event_province_when_visitor_moves(tmp_path, monkeypatch):
    monkeypatch.setenv("REALGUARD_TRAFFIC_CUMULATIVE_DB", str(tmp_path / "traffic.sqlite3"))
    account_uuid = "11111111-1111-4111-8111-111111111111"
    locations = {
        "8.8.8.8": {"country": "中国", "province": "浙江省", "city": "杭州市", "isoCode": "CN"},
        "1.1.1.1": {"country": "中国", "province": "四川省", "city": "成都市", "isoCode": "CN"},
    }
    common = {
        "agent": "Mozilla/5.0 Safari/605.1",
        "visitor_id": "visitor-moving-0001",
        "account_uuid": account_uuid,
        "resolver": lambda ip: locations[ip],
    }
    assert traffic_geo.record_confirmed_pageview(
        ip="8.8.8.8", event_id="event-moving-00001", page="home",
        occurred_at=NOW - timedelta(hours=1), **common,
    )
    assert traffic_geo.record_confirmed_pageview(
        ip="1.1.1.1", event_id="event-moving-00002", page="workspace",
        occurred_at=NOW, **common,
    )

    zhejiang = traffic_geo.registered_account_activity(
        [account_uuid], province="浙江", scope="cumulative", now=NOW,
    )
    sichuan = traffic_geo.registered_account_activity(
        [account_uuid], province="四川", scope="cumulative", now=NOW,
    )

    assert zhejiang["accounts"][0]["requests"] == 1
    assert zhejiang["accounts"][0]["city"] == "杭州市"
    assert sichuan["accounts"][0]["requests"] == 1
    assert sichuan["accounts"][0]["city"] == "成都市"


def test_existing_traffic_database_migrates_account_hash_column(tmp_path):
    database = tmp_path / "legacy-traffic.sqlite3"
    connection = traffic_geo.sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE traffic_events (event_hash TEXT PRIMARY KEY, occurred_at INTEGER NOT NULL)"
    )
    connection.commit()
    connection.close()

    migrated = traffic_geo._open_cumulative_db(str(database))
    columns = {
        row["name"] for row in migrated.execute("PRAGMA table_info(traffic_events)").fetchall()
    }
    indexes = {
        row["name"] for row in migrated.execute("PRAGMA index_list(traffic_events)").fetchall()
    }
    migrated.close()

    assert "account_hash" in columns
    assert {"province", "city", "isp", "agent"}.issubset(columns)
    assert "idx_traffic_events_account_time" in indexes
    assert "idx_traffic_events_province_account_time" in indexes


def test_account_link_expires_while_anonymous_pageview_remains(tmp_path, monkeypatch):
    database = tmp_path / "traffic.sqlite3"
    monkeypatch.setenv("REALGUARD_TRAFFIC_CUMULATIVE_DB", str(database))
    monkeypatch.setenv("REALGUARD_TRAFFIC_ACCOUNT_LINK_RETENTION_DAYS", "180")
    current = datetime.now().astimezone()
    account_uuid = "11111111-1111-4111-8111-111111111111"
    assert traffic_geo.record_confirmed_pageview(
        ip="8.8.8.8",
        agent="Mozilla/5.0 Chrome/126.0",
        visitor_id="visitor-retention-01",
        event_id="event-retention-0001",
        page="home",
        account_uuid=account_uuid,
        resolver=lambda _ip: {"country": "中国", "province": "浙江省", "isoCode": "CN"},
        occurred_at=current,
    )
    connection = traffic_geo.sqlite3.connect(database)
    connection.execute(
        "UPDATE traffic_events SET occurred_at = ?",
        (int((current - timedelta(days=181)).timestamp()),),
    )
    connection.execute("DELETE FROM traffic_metadata WHERE key = 'account_link_cleanup'")
    connection.commit()
    connection.close()

    activity = traffic_geo.registered_account_activity(
        [account_uuid], province="浙江", scope="cumulative", now=current,
    )
    public_payload = traffic_geo.confirmed_traffic_summary(now=current)

    assert activity["total"] == 0
    assert public_payload["cumulative"]["site"] == {"pageViews": 1, "uniqueVisitors": 1}


def test_historical_referer_recognizes_current_spa_surfaces():
    allowed = {"www.rrreal.cn"}

    assert traffic_geo._historical_page_from_referer(
        "https://www.rrreal.cn/?workspace=1", allowed,
    ) == "workspace"
    assert traffic_geo._historical_page_from_referer(
        "https://www.rrreal.cn/?developer=1&developerTab=docs", allowed,
    ) == "developer"
    assert traffic_geo._historical_page_from_referer(
        "https://www.rrreal.cn/?page=image", allowed,
    ) == "image"


def test_historical_import_recovers_only_same_site_browser_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("REALGUARD_TRAFFIC_CUMULATIVE_DB", str(tmp_path / "traffic.sqlite3"))
    browser = "Mozilla/5.0 Chrome/126.0"
    valid = log_line(
        "8.8.8.8",
        "/api/me",
        status=401,
        agent=browser,
        referer="https://www.rrreal.cn/?page=image",
    )
    lines = [
        valid,
        valid,
        log_line("1.1.1.1", "/api/me", agent=browser, referer="https://www.rrreal.cn/"),
        log_line("9.9.9.9", "/", agent=browser, referer="https://www.rrreal.cn/"),
        log_line("7.7.7.7", "/api/me", agent="curl/8.7.1", referer="https://www.rrreal.cn/"),
        log_line("6.6.6.6", "/api/me", agent=browser, referer="https://attacker.example/"),
        log_line("5.5.5.5", "/api/me", agent=browser, referer="https://www.rrreal.cn/admin"),
    ]

    result = traffic_geo.import_historical_browser_sessions(
        lines,
        resolver=lambda ip: {
            "country": "中国",
            "province": "浙江省" if ip == "8.8.8.8" else "四川省",
            "isoCode": "CN",
        },
    )
    payload = traffic_geo.confirmed_traffic_summary(now=NOW)

    assert result == {"ready": True, "imported": 2, "duplicates": 1, "rejected": 4}
    assert payload["site"] == {"pageViews": 2, "uniqueVisitors": 2}
    assert payload["homepage"] == {"pageViews": 1, "uniqueVisitors": 1}
    assert [item["name"] for item in payload["provinces"]] == ["四川", "浙江"]
    assert "8.8.8.8" not in str(payload)
