import base64
from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import report_web_search  # noqa: E402


def test_auto_search_only_triggers_for_public_fact_questions(monkeypatch):
    monkeypatch.setattr(report_web_search, "WEB_SEARCH_ENABLED", True)

    assert report_web_search.should_search("请联网查一下这条新闻是真的吗", "auto") is True
    assert report_web_search.should_search("特朗普爱上高市早苗是真的吗？", "auto") is True
    assert report_web_search.should_search("为什么图像模型判断为假？", "auto") is False
    assert report_web_search.should_search("请联网查一下", "off") is False
    assert report_web_search.should_search("核验公开信息", "on") is True


def test_image_preview_accepts_bounded_raster_data_only(monkeypatch):
    monkeypatch.setattr(report_web_search, "WEB_SEARCH_MAX_PREVIEW_BYTES", 100_000)
    png = b"\x89PNG\r\n\x1a\n" + b"safe-image"
    valid = "data:image/png;base64," + base64.b64encode(png).decode()

    assert report_web_search.validate_image_preview(valid) == valid
    assert report_web_search.validate_image_preview("data:text/plain;base64,SGVsbG8=") is None
    assert report_web_search.validate_image_preview(
        "data:image/png;base64," + base64.b64encode(b"not-a-png").decode()
    ) is None
    assert report_web_search.validate_image_preview("https://example.com/image.png") is None


def test_search_claim_uses_native_dashscope_sources(monkeypatch):
    captured = {}

    def fake_post(payload):
        captured.update(payload)
        return {
            "output": {
                "choices": [{
                    "message": {
                        "content": "未找到可靠报道证实该说法，更接近网络戏仿。[1][3]",
                    },
                }],
                "search_info": {
                    "search_results": [
                        {
                            "index": 1,
                            "title": "特朗普与高市早苗公开活动记录",
                            "url": "https://www.kantei.go.jp/example",
                            "site_name": "日本首相官邸",
                        },
                        {
                            "index": 2,
                            "title": "不安全来源",
                            "url": "javascript:alert(1)",
                            "site_name": "bad",
                        },
                        {
                            "index": 3,
                            "title": "事实核查：特朗普爱上高市早苗属于网络戏仿",
                            "url": "https://www.reuters.com/fact-check/example",
                            "site_name": "Reuters",
                        },
                    ],
                },
            },
            "usage": {
                "input_tokens": 800,
                "output_tokens": 120,
                "total_tokens": 920,
                "plugins": {"search": {"count": 1}},
            },
        }

    monkeypatch.setattr(report_web_search, "_post_search", fake_post)
    monkeypatch.setattr(report_web_search, "WEB_SEARCH_CACHE_SECONDS", 0)
    monkeypatch.setattr(report_web_search, "WEB_SEARCH_STRATEGY", "max")
    result = report_web_search.search_claim({
        "claim": "特朗普爱上高市早苗",
        "queries": ["特朗普 高市早苗 恋爱 新闻", "特朗普 高市早苗 辟谣"],
    })

    options = captured["parameters"]["search_options"]
    assert captured["model"] == report_web_search.WEB_SEARCH_MODEL
    assert options["forced_search"] is True
    assert options["enable_source"] is True
    assert options["enable_citation"] is True
    assert result["status"] == "success"
    assert result["used"] is True
    assert [source["quality"] for source in result["sources"]] == ["major", "primary"]
    assert "事实核查：特朗普爱上高市早苗属于网络戏仿》[1]" in result["summary"]
    assert "特朗普与高市早苗公开活动记录》[2]" in result["summary"]
    assert "未找到可靠报道" not in result["summary"]
    assert result["usage"]["searchCount"] == 1
    assert result["usage"]["totalTokens"] == 920


def test_claim_extractor_can_read_a_preview_for_generic_question(monkeypatch):
    png = b"\x89PNG\r\n\x1a\n" + b"preview"
    preview = "data:image/png;base64," + base64.b64encode(png).decode()
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=(
                    '{"searchable":true,"claim":"特朗普爱上高市早苗",'
                    '"queries":["特朗普 高市早苗 恋爱 新闻","特朗普 高市早苗 恶搞"]}'
                )))],
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    monkeypatch.setattr(report_web_search.detector, "_get_client", lambda: client)

    result = report_web_search.extract_claim(
        {"verdict": "real", "explanation": "图像像素更偏向真实"},
        "这是真的吗？",
        preview,
    )

    assert result["searchable"] is True
    assert result["claim"] == "特朗普爱上高市早苗"
    assert len(result["queries"]) == 2
    content = calls[0]["messages"][1]["content"]
    assert isinstance(content, list)
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_explicit_claim_skips_redundant_image_understanding(monkeypatch):
    png = b"\x89PNG\r\n\x1a\n" + b"preview"
    preview = "data:image/png;base64," + base64.b64encode(png).decode()

    def unexpected_client():
        raise AssertionError("explicit claims should not invoke the vision model")

    monkeypatch.setattr(report_web_search.detector, "_get_client", unexpected_client)
    result = report_web_search.extract_claim(
        {"verdict": "real"},
        "请联网核验：特朗普爱上高市早苗是真的吗？",
        preview,
    )

    assert result["searchable"] is True
    assert result["claim"] == "特朗普爱上高市早苗"


def test_generic_image_fact_check_request_still_needs_image_context():
    assert report_web_search._explicit_question_claim("请联网核验图片里的事件是否属实") is None


def test_generic_request_does_not_search_detector_explanation_when_claim_extraction_is_unavailable(monkeypatch):
    monkeypatch.setattr(report_web_search.detector, "_get_client", lambda: None)

    result = report_web_search.extract_claim(
        {"explanation": "图像模型的综合风险分为 80%"},
        "请联网核验图片里的事件是否属实",
    )

    assert result == {"searchable": False, "claim": "", "queries": []}


def test_public_result_never_returns_private_or_script_urls():
    result = report_web_search.public_result({
        "attempted": True,
        "used": True,
        "status": "success",
        "claim": "公开主张",
        "sources": [
            {"title": "公网来源", "url": "https://example.com/news", "siteName": "Example"},
            {"title": "本机来源", "url": "http://127.0.0.1/admin", "siteName": "Local"},
            {"title": "脚本来源", "url": "javascript:alert(1)", "siteName": "Bad"},
        ],
    })

    assert result["used"] is True
    assert [source["title"] for source in result["sources"]] == ["公网来源"]


def test_source_quality_requires_a_real_domain_boundary():
    assert report_web_search._source_quality("www.whitehouse.gov") == "primary"
    assert report_web_search._source_quality("whitehouse.gov.example.com") == "other"
    assert report_web_search._source_quality("notreuters.com") == "other"


def test_sources_prioritize_reliable_domains_and_limit_repeated_sites(monkeypatch):
    monkeypatch.setattr(report_web_search, "WEB_SEARCH_MAX_SOURCES", 4)
    sources = report_web_search._normalize_sources([
        {"index": 1, "title": "转载一", "url": "https://example.com/a"},
        {"index": 2, "title": "转载二", "url": "https://example.com/b"},
        {"index": 3, "title": "转载三", "url": "https://example.com/c"},
        {"index": 4, "title": "官方说明", "url": "https://www.kantei.go.jp/notice"},
        {"index": 5, "title": "通讯社核查", "url": "https://www.reuters.com/fact-check/story"},
    ])

    assert [source["title"] for source in sources] == ["官方说明", "通讯社核查", "转载一", "转载二"]


def test_stream_events_are_assembled_into_a_normal_response():
    result = report_web_search._aggregate_stream_events([
        {
            "request_id": "req-1",
            "output": {
                "choices": [{"message": {"content": "先核对原始出处。"}}],
                "search_info": {"search_results": [{
                    "index": 1,
                    "title": "原始出处",
                    "url": "https://example.com/origin",
                }]},
            },
        },
        {
            "request_id": "req-1",
            "output": {"choices": [{"message": {"content": "再核对权威报道。[1]"}}]},
            "usage": {"total_tokens": 321, "plugins": {"search": {"count": 2}}},
        },
    ])

    assert result["output"]["choices"][0]["message"]["content"] == "先核对原始出处。再核对权威报道。[1]"
    assert result["output"]["search_info"]["search_results"][0]["title"] == "原始出处"
    assert result["usage"]["total_tokens"] == 321


def test_source_ranking_separates_direct_context_and_weak_matches():
    sources = report_web_search._normalize_sources(
        [
            {"index": 1, "title": "特朗普与习近平讨论对台军售", "url": "https://www.reuters.com/a"},
            {"index": 2, "title": "特朗普高市早苗新CP？搞笑视频", "url": "https://example.com/parody"},
            {"index": 3, "title": "特朗普与高市早苗举行会谈", "url": "https://www.bbc.com/news/b"},
        ],
        claim="特朗普爱上高市早苗",
        queries=["特朗普 高市早苗 恋爱"],
    )

    assert sources[0]["title"] == "特朗普高市早苗新CP？搞笑视频"
    assert sources[0]["matchLevel"] == "direct"
    assert next(source for source in sources if "举行会谈" in source["title"])["matchLevel"] == "context"
    assert next(source for source in sources if "对台军售" in source["title"])["matchLevel"] == "weak"


def test_satire_can_use_a_direct_parody_origin_plus_reliable_context():
    sources = report_web_search._normalize_sources(
        [
            {"index": 1, "title": "特朗普高市早苗新CP？搞笑视频", "url": "https://example.com/parody"},
            {
                "index": 2,
                "title": "高市早苗：特朗普确认日美密切关系并肯定友谊",
                "url": "https://www.bbc.com/news/b",
            },
        ],
        claim="特朗普爱上高市早苗",
        queries=["特朗普 高市早苗 恋爱"],
    )

    assert report_web_search._derive_supported_verdicts(
        "相关说法来自社交媒体调侃和二次创作。[1]特朗普确认日美密切关系并肯定友谊。[2]",
        sources,
    ) == ["satire_likely"]


def test_agent_search_uses_streaming_payload_and_keeps_match_metadata(monkeypatch):
    captured = {}

    def fake_request(payload, strategy):
        captured["payload"] = payload
        captured["strategy"] = strategy
        return {
            "output": {
                "choices": [{"message": {"content": "该说法来自搞笑视频。[1]公开报道仅能提供背景。[2]"}}],
                "search_info": {"search_results": [
                    {
                        "index": 1,
                        "title": "特朗普高市早苗新CP？搞笑视频",
                        "url": "https://example.com/parody",
                    },
                    {
                        "index": 2,
                        "title": "特朗普与高市早苗举行会谈",
                        "url": "https://www.bbc.com/news/context",
                    },
                ]},
            },
            "usage": {"total_tokens": 500, "plugins": {"search": {"count": 2}}},
        }

    monkeypatch.setattr(report_web_search, "_request_search", fake_request)
    monkeypatch.setattr(report_web_search, "WEB_SEARCH_CACHE_SECONDS", 0)
    monkeypatch.setattr(report_web_search, "WEB_SEARCH_STRATEGY", "agent")
    result = report_web_search.search_claim({"claim": "特朗普爱上高市早苗", "queries": []})

    options = captured["payload"]["parameters"]["search_options"]
    assert captured["strategy"] == "agent"
    assert captured["payload"]["parameters"]["incremental_output"] is True
    assert "enable_citation" not in options
    assert result["strategy"] == "agent"
    assert result["directSourceCount"] == 1
    assert result["matchedSourceCount"] == 2
    assert result["supportedVerdicts"] == ["satire_likely"]


def test_agent_search_falls_back_to_max_when_streaming_is_unavailable(monkeypatch):
    strategies = []

    def fake_request(_payload, strategy):
        strategies.append(strategy)
        if strategy == "agent":
            raise report_web_search.WebSearchUnavailableError("agent unavailable")
        return {
            "output": {
                "choices": [{"message": {"content": "公开记录确认该主张。[1]"}}],
                "search_info": {"search_results": [{
                    "index": 1,
                    "title": "公开主张获得官方确认",
                    "url": "https://www.reuters.com/fact-check/claim",
                }]},
            },
            "usage": {"total_tokens": 100, "plugins": {"search": {"count": 1}}},
        }

    monkeypatch.setattr(report_web_search, "_request_search", fake_request)
    monkeypatch.setattr(report_web_search, "WEB_SEARCH_CACHE_SECONDS", 0)
    monkeypatch.setattr(report_web_search, "WEB_SEARCH_STRATEGY", "agent")
    monkeypatch.setattr(report_web_search, "WEB_SEARCH_FALLBACK_STRATEGY", "max")
    result = report_web_search.search_claim({"claim": "公开主张", "queries": []})

    assert strategies == ["agent", "max"]
    assert result["strategy"] == "max"
    assert result["status"] == "success"
