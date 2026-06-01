from io import BytesIO
from pathlib import Path
import json
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection import creat_app  # noqa: E402
from imagedetection.views import api, detection, historical_record, retrieve  # noqa: E402


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.fixture
def client():
    app = creat_app()
    app.config.update(TESTING=True)
    return app.test_client()


def _login_session(client, phone="13800000000"):
    with client.session_transaction() as sess:
        sess["user_info"] = {
            "Userid": 1,
            "username": "tester",
            "phone": phone,
            "openid": "openid-1",
        }


def test_api_password_login_sets_session(client, monkeypatch):
    monkeypatch.setattr(
        api,
        "_authenticate_password_user",
        lambda phone, secret: {
            "Userid": 7,
            "username": "tester",
            "phone": phone,
            "openid": "openid-1",
        },
    )
    monkeypatch.setattr(api, "_sync_detection_user", lambda *args, **kwargs: None)

    response = client.post("/api/login/password", json={"phone": "13800000000", "secret": "hashed-pass"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["user"]["Userid"] == 7
    with client.session_transaction() as sess:
        assert sess["user_info"]["phone"] == "13800000000"


def test_guest_image_detect_returns_rewritten_url_and_then_blocks(client, monkeypatch):
    monkeypatch.setattr(detection, "_metadata_for_item", lambda itemid: {})
    monkeypatch.setattr(
        detection,
        "_backend_post",
        lambda url, **kwargs: _FakeResponse(
            {
                "code": 200,
                "data": {
                    "data_itemid": 11,
                    "fake_percentage": 64.0,
                    "final_label": "AI生成图像",
                    "confidence": "高",
                    "image_url": "http://10.1.20.66:5000/static/uploads/guest/image/demo.png",
                    "filename": "demo.png",
                    "file_size": "12KB",
                    "img_format": "png",
                    "resolution": "256x256",
                },
            }
        ),
    )

    first = client.post(
        "/image_upload/detect",
        data={"image": (BytesIO(b"fake-image"), "demo.png")},
        content_type="multipart/form-data",
    )

    assert first.status_code == 200
    payload = first.get_json()
    assert payload["result"]["image_url"] == "/detection-static/uploads/guest/image/demo.png"
    with client.session_transaction() as sess:
        assert sess[detection.GUEST_DETECTION_SESSION_KEY] == 1

    second = client.post(
        "/image_upload/detect",
        data={"image": (BytesIO(b"fake-image"), "demo.png")},
        content_type="multipart/form-data",
    )

    assert second.status_code == 401
    assert "请登录后继续检测" in second.get_json()["message"]


def test_video_detect_logged_in_builds_public_media_url(client, monkeypatch):
    _login_session(client)
    monkeypatch.setattr(
        detection,
        "_backend_post",
        lambda url, **kwargs: _FakeResponse(
            {
                "code": 200,
                "data": {
                    "data_itemid": 21,
                    "fake_percentage": 83.0,
                    "real_percentage": 17.0,
                    "confidence": 0.91,
                    "final_label": "fake",
                    "frame_count": 48,
                    "encoder": "h264",
                    "meta": {
                        "file_size": "2.5MB",
                        "duration": "8s",
                        "resolution": "1280x720",
                        "video_format": "mp4",
                    },
                },
            }
        ),
    )

    monkeypatch.setattr(
        detection,
        "excute_detection_sql",
        lambda sql, params=None, fetch=True: [{
            "filename": "video.mp4",
            "openid": "openid-1",
            "phone": "13800000000",
        }] if sql == "SELECT * FROM video_data WHERE itemid = %s" else [],
    )

    response = client.post(
        "/video_upload/detect",
        data={"video_url": "https://example.com/video.mp4", "fast_mode": "1"},
    )

    assert response.status_code == 200
    payload = response.get_json()["result"]
    assert payload["final_label"] == "AI生成视频"
    assert payload["confidence"] == "高"
    assert payload["video_url"] == "/detection-static/uploads/openid-1/video/video.mp4"


def test_guest_image_report_downloads_attachment(client, monkeypatch):
    with client.session_transaction() as sess:
        sess["guest_openid"] = "guest-123"

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == "SELECT * FROM data WHERE itemid = %s AND openid = %s LIMIT 1":
            assert params == ("31", "guest-123")
            return [{
                "itemid": 31,
                "filename": "guest.png",
                "fake": 61.0,
                "clarity": "中",
                "file_size": "8KB",
                "img_format": "png",
                "resolution": "320x320",
                "openid": "guest-123",
                "createtime": "2026-05-27 14:00:00",
            }]
        if sql == "SELECT all_metadata FROM exif WHERE data_itemid = %s LIMIT 1":
            return []
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(detection, "excute_detection_sql", fake_detection_sql)

    response = client.get("/image_upload/report?itemid=31")

    assert response.status_code == 200
    assert "attachment;" in response.headers["Content-Disposition"]
    assert "图像鉴伪报告" in response.get_data(as_text=True)
    assert "guest.png" in response.get_data(as_text=True)


def test_guest_history_returns_guest_image_records(client, monkeypatch):
    with client.session_transaction() as sess:
        sess["guest_openid"] = "guest-abc"

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == "SELECT * FROM data WHERE openid = %s ORDER BY createtime DESC":
            assert params == ("guest-abc",)
            return [{
                "itemid": 35,
                "filename": "guest-history.png",
                "fake": 58.0,
                "clarity": "中",
                "openid": "guest-abc",
                "phone": "",
                "createtime": "2026-05-27 16:00:00",
                "explantation": "视觉可疑点\n- 边缘过度平滑",
            }]
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(api, "excute_detection_sql", fake_detection_sql)
    monkeypatch.setattr(api, "_has_detection_metadata", lambda itemid: itemid == 35)

    response = client.get("/api/history/image-detections")

    assert response.status_code == 200
    record = response.get_json()["records"][0]
    assert record["itemid"] == 35
    assert record["is_guest_record"] is True
    assert record["report_url"] == "/image_upload/report?itemid=35"
    assert record["has_metadata"] is True
    assert record["has_visual_issues"] is True
    assert record["visual_issue_count"] == 1


def test_history_endpoints_return_empty_for_fresh_guest(client):
    image_response = client.get("/api/history/image-detections")
    video_response = client.get("/api/history/video-detections")
    retrieve_response = client.get("/api/history/retrievals?search_type=image")

    assert image_response.status_code == 200
    assert image_response.get_json()["records"] == []
    assert video_response.status_code == 200
    assert video_response.get_json()["records"] == []
    assert retrieve_response.status_code == 200
    assert retrieve_response.get_json()["records"] == []


def test_retrieval_history_supports_hit_filters(client, monkeypatch):
    _login_session(client, phone="13900000000")

    def fake_sql(sql, params=None, fetch=True):
        if sql == "SELECT * FROM retrieve_data WHERE phone = %s AND search_type = %s ORDER BY createtime DESC":
            assert params == ("13900000000", "image")
            return [
                {
                    "itemid": 1,
                    "filename": "hit.png",
                    "result_count": 3,
                    "top_k": 5,
                    "file_size": "12KB",
                    "createtime": "2026-06-01 10:00:00",
                    "results_json": "[]",
                },
                {
                    "itemid": 2,
                    "filename": "empty.png",
                    "result_count": 0,
                    "top_k": 5,
                    "file_size": "10KB",
                    "createtime": "2026-06-01 09:00:00",
                    "results_json": "[]",
                },
            ]
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(api, "excute_sql", fake_sql)

    listing = client.get("/api/history/retrievals?search_type=image")
    hits_only = client.get("/api/history/retrievals?search_type=image&filter=hits")
    empty_only = client.get("/api/history/retrievals?search_type=image&filter=empty")

    assert listing.status_code == 200
    payload = listing.get_json()
    assert payload["filter_counts"]["all"] == 2
    assert payload["filter_counts"]["hits"] == 1
    assert payload["filter_counts"]["empty"] == 1
    assert hits_only.status_code == 200
    assert len(hits_only.get_json()["records"]) == 1
    assert hits_only.get_json()["records"][0]["filename"] == "hit.png"
    assert empty_only.status_code == 200
    assert len(empty_only.get_json()["records"]) == 1
    assert empty_only.get_json()["records"][0]["filename"] == "empty.png"


def test_guest_thumbnail_uses_guest_openid_lookup(client, monkeypatch, tmp_path):
    with client.session_transaction() as sess:
        sess["guest_openid"] = "guest-thumb"

    thumb_path = tmp_path / "thumb.webp"
    thumb_path.write_bytes(b"fake-webp")

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == "SELECT * FROM data WHERE itemid = %s AND openid = %s LIMIT 1":
            assert params == (91, "guest-thumb")
            return [{
                "itemid": 91,
                "filename": "guest-thumb.png",
                "openid": "guest-thumb",
                "phone": "",
                "createtime": "2026-05-27 16:05:00",
            }]
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(api, "excute_detection_sql", fake_detection_sql)
    monkeypatch.setattr(api, "_thumbnail_cache_path", lambda item: thumb_path)

    response = client.get("/api/media/thumbnail/image/91")

    assert response.status_code == 200
    assert response.data == b"fake-webp"


def test_video_report_downloads_attachment_for_logged_user(client, monkeypatch):
    _login_session(client)

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == "SELECT * FROM video_data WHERE itemid = %s AND (phone = %s OR openid = %s) LIMIT 1":
            assert params == ("41", "13800000000", "openid-1")
            return [{
                "itemid": 41,
                "filename": "clip.mp4",
                "fake": 78.5,
                "final_label": "AI生成视频",
                "confidence": "高",
                "explanation": "检测到明显生成痕迹。",
                "duration": "10s",
                "resolution": "1280x720",
                "file_size": "4MB",
                "encoder": "h264",
                "frame_count": 55,
                "openid": "openid-1",
                "phone": "13800000000",
                "createtime": "2026-05-27 14:05:00",
            }]
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(detection, "excute_detection_sql", fake_detection_sql)

    response = client.get("/video_upload/report?itemid=41")

    assert response.status_code == 200
    assert "attachment;" in response.headers["Content-Disposition"]
    assert "视频鉴伪报告" in response.get_data(as_text=True)
    assert "clip.mp4" in response.get_data(as_text=True)


def test_retrieve_search_uses_selected_library_and_persists_history(client, monkeypatch, tmp_path):
    _login_session(client)
    insert_calls = []
    monkeypatch.setattr(retrieve, "current_dir", str(tmp_path))
    monkeypatch.setattr(retrieve, "list_retrieve_libraries", lambda search_type: ["libA"])
    monkeypatch.setattr(
        retrieve,
        "_build_local_retrieve_results",
        lambda **kwargs: [
            {
                "id": "libA/gallery/case1.png",
                "score": 0.87,
                "product": {"product_images": "libA/gallery/case1.png"},
            }
        ],
    )
    monkeypatch.setattr(retrieve, "get_now_str", lambda: "2026-05-27 13:00:00")
    monkeypatch.setattr(retrieve, "get_file_size_str", lambda path: "1KB")

    def fake_execute(sql, params=None, fetch=True):
        if "INSERT INTO retrieve_data" in sql:
            insert_calls.append((sql, params))
            return 1
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(retrieve, "excute_sql", fake_execute)

    response = client.post(
        "/retrieve/search",
        data={
            "image": (BytesIO(b"image-binary"), "query.png"),
            "search_type": "image",
            "dataset": "libA",
            "top_k": "5",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["dataset"] == "libA"
    assert payload["base_url"] == "/retrieve/library-file/image/"
    assert payload["results"][0]["id"].startswith("libA/")
    assert insert_calls
    _, params = insert_calls[0]
    assert params[2] == "image"
    assert json.loads(params[8])[0]["id"].startswith("libA/")


def test_history_detection_records_include_report_urls(client, monkeypatch):
    _login_session(client)

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == "SELECT * FROM data WHERE phone = %s OR openid = %s ORDER BY createtime DESC":
            assert params == ("13800000000", "openid-1")
            return [{
                "itemid": 51,
                "filename": "img.png",
                "fake": 51.5,
                "clarity": "中",
                "openid": "openid-1",
                "phone": "13800000000",
                "createtime": "2026-05-27 15:00:00",
                "explantation": "视觉可疑点\n- 背景纹理重复",
            }]
        if sql == "SELECT * FROM video_data WHERE phone = %s OR openid = %s ORDER BY createtime DESC":
            assert params == ("13800000000", "openid-1")
            return [{
                "itemid": 61,
                "filename": "vid.mp4",
                "fake": 78.2,
                "final_label": "AI生成视频",
                "confidence": "高",
                "openid": "openid-1",
                "phone": "13800000000",
                "createtime": "2026-05-27 15:02:00",
            }]
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(api, "excute_detection_sql", fake_detection_sql)
    monkeypatch.setattr(api, "_has_detection_metadata", lambda itemid: itemid == 51)

    image_response = client.get("/api/history/image-detections")
    video_response = client.get("/api/history/video-detections")

    assert image_response.status_code == 200
    assert video_response.status_code == 200
    assert image_response.get_json()["records"][0]["report_url"] == "/image_upload/report?itemid=51"
    assert image_response.get_json()["records"][0]["has_metadata"] is True
    assert image_response.get_json()["records"][0]["has_visual_issues"] is True
    assert image_response.get_json()["records"][0]["visual_issue_count"] == 1
    assert video_response.get_json()["records"][0]["report_url"] == "/video_upload/report?itemid=61"


def test_history_endpoints_support_query_filter_and_limit(client, monkeypatch):
    _login_session(client)

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == "SELECT * FROM data WHERE phone = %s OR openid = %s ORDER BY createtime DESC":
            return [
                {
                    "itemid": 101,
                    "filename": "meta-hit.png",
                    "fake": 42.0,
                    "clarity": "高",
                    "openid": "openid-1",
                    "phone": "13800000000",
                    "createtime": "2026-05-27 18:00:00",
                    "explantation": "视觉可疑点\n- 边缘纹理断裂",
                },
                {
                    "itemid": 102,
                    "filename": "plain.png",
                    "fake": 12.0,
                    "clarity": "低",
                    "openid": "openid-1",
                    "phone": "13800000000",
                    "createtime": "2026-05-27 18:01:00",
                    "explantation": "",
                },
            ]
        if sql == "SELECT * FROM video_data WHERE phone = %s OR openid = %s ORDER BY createtime DESC":
            return [
                {
                    "itemid": 201,
                    "filename": "ai-video.mp4",
                    "fake": 88.0,
                    "final_label": "AI生成视频",
                    "confidence": "高",
                    "openid": "openid-1",
                    "phone": "13800000000",
                    "createtime": "2026-05-27 18:10:00",
                },
                {
                    "itemid": 202,
                    "filename": "real-video.mp4",
                    "fake": 18.0,
                    "final_label": "真实视频",
                    "confidence": "中",
                    "openid": "openid-1",
                    "phone": "13800000000",
                    "createtime": "2026-05-27 18:11:00",
                },
            ]
        raise AssertionError(f"unexpected SQL: {sql}")

    def fake_execute(sql, params=None, fetch=True):
        assert sql == "SELECT * FROM retrieve_data WHERE phone = %s AND search_type = %s ORDER BY createtime DESC"
        return [
            {
                "itemid": 301,
                "filename": "query-a.png",
                "search_type": params[1],
                "result_count": 3,
                "top_k": 10,
                "file_size": "3KB",
                "createtime": "2026-05-27 18:20:00",
                "results_json": "[]",
            },
            {
                "itemid": 302,
                "filename": "query-b.png",
                "search_type": params[1],
                "result_count": 1,
                "top_k": 5,
                "file_size": "2KB",
                "createtime": "2026-05-27 18:21:00",
                "results_json": "[]",
            },
        ]

    monkeypatch.setattr(api, "excute_detection_sql", fake_detection_sql)
    monkeypatch.setattr(api, "excute_sql", fake_execute)
    monkeypatch.setattr(api, "_has_detection_metadata", lambda itemid: itemid == 101)

    image_response = client.get("/api/history/image-detections?filter=metadata&query=元数据&limit=1")
    video_response = client.get("/api/history/video-detections?filter=ai&query=AI结论&limit=1")
    retrieve_response = client.get("/api/history/retrievals?search_type=image&query=query-a&limit=1")

    assert image_response.status_code == 200
    image_payload = image_response.get_json()
    assert image_payload["total"] == 1
    assert len(image_payload["records"]) == 1
    assert image_payload["records"][0]["itemid"] == 101

    assert video_response.status_code == 200
    video_payload = video_response.get_json()
    assert video_payload["total"] == 1
    assert len(video_payload["records"]) == 1
    assert video_payload["records"][0]["itemid"] == 201

    assert retrieve_response.status_code == 200
    retrieve_payload = retrieve_response.get_json()
    assert retrieve_payload["total"] == 1
    assert len(retrieve_payload["records"]) == 1
    assert retrieve_payload["records"][0]["itemid"] == 301


def test_history_endpoints_support_offset_pagination(client, monkeypatch):
    _login_session(client)

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == "SELECT * FROM data WHERE phone = %s OR openid = %s ORDER BY createtime DESC":
            return [
                {
                    "itemid": 401,
                    "filename": "first.png",
                    "fake": 62.0,
                    "clarity": "高",
                    "openid": "openid-1",
                    "phone": "13800000000",
                    "createtime": "2026-05-27 19:00:00",
                    "explantation": "",
                },
                {
                    "itemid": 402,
                    "filename": "second.png",
                    "fake": 22.0,
                    "clarity": "中",
                    "openid": "openid-1",
                    "phone": "13800000000",
                    "createtime": "2026-05-27 19:01:00",
                    "explantation": "",
                },
            ]
        raise AssertionError(f"unexpected SQL: {sql}")

    def fake_execute(sql, params=None, fetch=True):
        assert sql == "SELECT * FROM retrieve_data WHERE phone = %s AND search_type = %s ORDER BY createtime DESC"
        return [
            {
                "itemid": 501,
                "filename": "retrieve-a.png",
                "search_type": params[1],
                "result_count": 4,
                "top_k": 10,
                "file_size": "2KB",
                "createtime": "2026-05-27 19:10:00",
                "results_json": "[]",
            },
            {
                "itemid": 502,
                "filename": "retrieve-b.png",
                "search_type": params[1],
                "result_count": 2,
                "top_k": 5,
                "file_size": "2KB",
                "createtime": "2026-05-27 19:11:00",
                "results_json": "[]",
            },
        ]

    monkeypatch.setattr(api, "excute_detection_sql", fake_detection_sql)
    monkeypatch.setattr(api, "excute_sql", fake_execute)
    monkeypatch.setattr(api, "_has_detection_metadata", lambda itemid: False)

    image_response = client.get("/api/history/image-detections?limit=1&offset=1")
    retrieve_response = client.get("/api/history/retrievals?search_type=image&limit=1&offset=1")

    assert image_response.status_code == 200
    image_payload = image_response.get_json()
    assert image_payload["total"] == 2
    assert len(image_payload["records"]) == 1
    assert image_payload["records"][0]["itemid"] == 402

    assert retrieve_response.status_code == 200
    retrieve_payload = retrieve_response.get_json()
    assert retrieve_payload["total"] == 2
    assert len(retrieve_payload["records"]) == 1
    assert retrieve_payload["records"][0]["itemid"] == 502


def test_retrieval_history_records_include_report_urls(client, monkeypatch):
    _login_session(client)

    def fake_execute(sql, params=None, fetch=True):
        assert sql == "SELECT * FROM retrieve_data WHERE phone = %s AND search_type = %s ORDER BY createtime DESC"
        return [{
            "itemid": 71,
            "filename": "query.png",
            "search_type": params[1],
            "result_count": 4,
            "top_k": 10,
            "file_size": "2KB",
            "createtime": "2026-05-27 15:30:00",
        }]

    monkeypatch.setattr(api, "excute_sql", fake_execute)

    image_response = client.get("/api/history/retrievals?search_type=image")
    video_response = client.get("/api/history/retrievals?search_type=video")

    assert image_response.status_code == 200
    assert video_response.status_code == 200
    assert image_response.get_json()["records"][0]["report_url"] == "/history_retrieve/report?itemid=71"
    assert video_response.get_json()["records"][0]["report_url"] == "/history_retrieve/report?itemid=71"


def test_history_endpoints_reject_invalid_filter_or_limit(client):
    bad_image_limit = client.get("/api/history/image-detections?limit=0")
    bad_image_offset = client.get("/api/history/image-detections?offset=-1")
    bad_image_filter = client.get("/api/history/image-detections?filter=bad")
    bad_video_filter = client.get("/api/history/video-detections?filter=oops")
    bad_retrieval_limit = client.get("/api/history/retrievals?search_type=image&limit=abc")
    bad_retrieval_offset = client.get("/api/history/retrievals?search_type=image&offset=oops")

    assert bad_image_limit.status_code == 400
    assert bad_image_offset.status_code == 400
    assert bad_image_filter.status_code == 400
    assert bad_video_filter.status_code == 400
    assert bad_retrieval_limit.status_code == 400
    assert bad_retrieval_offset.status_code == 400


def test_retrieval_report_downloads_attachment(client, monkeypatch):
    _login_session(client)

    def fake_execute(sql, params=None, fetch=True):
        if sql == "SELECT * FROM retrieve_data WHERE itemid = %s AND phone = %s":
            assert params == ("81", "13800000000")
            return [{
                "itemid": 81,
                "filename": "query.png",
                "search_type": "image",
                "result_count": 2,
                "top_k": 5,
                "file_size": "3KB",
                "createtime": "2026-05-27 15:40:00",
                "results_json": json.dumps([
                    {"id": "libA/a.png", "score": 0.92, "product": {"product_images": "libA/a.png"}},
                    {"id": "libA/b.png", "score": 0.88, "product": {"product_images": "libA/b.png"}},
                ]),
            }]
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(api, "excute_sql", fake_execute)
    monkeypatch.setattr(historical_record, "excute_sql", fake_execute)

    response = client.get("/history_retrieve/report?itemid=81")

    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "attachment;" in response.headers["Content-Disposition"]
    assert "检索报告" in text
    assert "libA/a.png" in text
