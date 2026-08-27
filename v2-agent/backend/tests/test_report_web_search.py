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
    monkeypatch.setattr(report_web_search, "_collect_verified_evidence", lambda _claim, _sources: {
        1: {
            "contentStatus": "verified",
            "evidenceRole": "satire_origin",
            "evidenceQuote": "该说法属于网络搞笑戏仿",
            "evidenceReason": "正文明确标注为搞笑戏仿",
            "evidenceBasis": "page",
        },
        2: {
            "contentStatus": "verified",
            "evidenceRole": "background_only",
            "evidenceQuote": "特朗普与高市早苗出席公开活动",
            "evidenceReason": "正文只记录公开活动",
            "evidenceBasis": "page",
        },
    })
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
    assert "正文明确标注为搞笑戏仿" not in result["summary"]
    assert "该说法属于网络搞笑戏仿" in result["summary"]
    assert "特朗普与高市早苗出席公开活动" in result["summary"]
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


def test_internal_recall_pool_can_exceed_public_source_limit(monkeypatch):
    monkeypatch.setattr(report_web_search, "WEB_SEARCH_MAX_SOURCES", 2)
    rows = [
        {"title": f"公开主张来源 {index}", "url": f"https://source{index}.example/story"}
        for index in range(1, 6)
    ]

    public_sized = report_web_search._normalize_sources(rows, claim="公开主张")
    internal_pool = report_web_search._normalize_sources(rows, claim="公开主张", limit=5)

    assert len(public_sized) == 2
    assert len(internal_pool) == 5


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


def test_evidence_candidates_require_entities_and_core_relation():
    sources = report_web_search._normalize_sources(
        [
            {"index": 1, "title": "特朗普与高市早苗举行会谈", "url": "https://news.example.com/meeting"},
            {"index": 2, "title": "特朗普与高市早苗的新 CP 搞笑视频", "url": "https://video.example.org/parody"},
            {"index": 3, "title": "特朗普恋爱史", "url": "https://history.example.net/history"},
        ],
        claim="特朗普爱上高市早苗",
        queries=["特朗普 高市早苗 恋爱"],
    )

    candidates = report_web_search._evidence_candidates("特朗普爱上高市早苗", sources)

    assert candidates[0]["title"] == "特朗普与高市早苗的新 CP 搞笑视频"
    assert {source["title"] for source in candidates} == {
        "特朗普与高市早苗的新 CP 搞笑视频",
        "特朗普与高市早苗举行会谈",
    }


def test_distinct_platform_origin_pages_can_both_be_verified():
    sources = report_web_search._normalize_sources(
        [
            {
                "title": "特朗普与高市早苗的爱情故事",
                "url": "https://www.bilibili.com/video/BV1eP7y6FEYo/",
                "lane": "origin",
            },
            {
                "title": "特朗普高市早苗新 CP 搞笑视频",
                "url": "https://www.bilibili.com/video/BV1qcybBnEST/",
                "lane": "origin",
            },
        ],
        claim="特朗普爱上高市早苗",
        queries=["特朗普 高市早苗 恋爱"],
    )

    candidates = report_web_search._evidence_candidates("特朗普爱上高市早苗", sources)

    assert len(candidates) == 2


def test_multilingual_origin_can_enter_body_verification_from_generated_query():
    sources = report_web_search._normalize_sources(
        [{
            "title": "Trump and Takaichi: The Unexpected Love Affair",
            "url": "https://example.com/love-affair",
            "lane": "origin",
        }],
        claim="特朗普爱上高市早苗",
        queries=["Trump Sanae Takaichi romance rumor"],
    )

    candidates = report_web_search._evidence_candidates("特朗普爱上高市早苗", sources)

    assert candidates[0]["title"] == "Trump and Takaichi: The Unexpected Love Affair"


def test_extractor_output_uses_evidence_section_and_ignores_summary():
    url = "https://example.com/story"
    parsed = report_web_search._parse_extractor_output(
        f"""The useful information in {url} for user goal 核验 as follows:

Evidence in page:
正文明确写着这是特朗普与高市早苗的新 CP 搞笑段子。

Summary:
这里是模型自己生成的错误总结。
"""
    )

    assert parsed[url]["available"] is True
    assert "新 CP 搞笑段子" in parsed[url]["text"]
    assert "错误总结" not in parsed[url]["text"]


def test_bilibili_official_metadata_is_verified_as_entertainment_origin(monkeypatch):
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {
            "code": 0,
            "data": {
                "title": "【特朗普x高市早苗】特高磕 好喜欢你",
                "owner": {"name": "测试发布者"},
                "desc": "-",
                "argue_info": {"argue_msg": "作者声明：该内容仅供娱乐，请勿过分解读"},
            },
        },
    )
    monkeypatch.setattr(report_web_search.httpx, "get", lambda *_args, **_kwargs: response)
    monkeypatch.setattr(report_web_search.detector, "_get_client", lambda: None)
    url = "https://www.bilibili.com/video/BV1qcybBnEST/"

    page = report_web_search._bilibili_metadata_page(url)
    result = report_web_search._classify_page_evidence(
        "特朗普爱上高市早苗",
        [{"index": 1, "title": "相关视频", "url": url}],
        {url: page},
    )

    assert page["basis"] == "platform_metadata"
    assert "仅供娱乐" in page["text"]
    assert result[1]["evidenceRole"] == "satire_origin"
    assert result[1]["evidenceBasis"] == "platform_metadata"
    assert "平台公开信息" in result[1]["evidenceReason"]


def test_platform_title_alone_cannot_become_factual_support(monkeypatch):
    monkeypatch.setattr(report_web_search.detector, "_get_client", lambda: None)
    url = "https://www.bilibili.com/video/BV1qcybBnEST/"
    page = {
        "available": True,
        "basis": "platform_metadata",
        "text": "平台公开元数据：视频标题：特朗普爱上高市早苗",
    }

    result = report_web_search._classify_page_evidence(
        "特朗普爱上高市早苗",
        [{"index": 1, "title": "相关视频", "url": url}],
        {url: page},
    )

    assert result[1]["evidenceRole"] == "background_only"
    assert "不能证明主张属实" in result[1]["evidenceReason"]


def test_platform_ai_warning_is_preserved_but_does_not_alone_set_verdict(monkeypatch):
    monkeypatch.setattr(report_web_search.detector, "_get_client", lambda: None)
    url = "https://www.bilibili.com/video/BV1eP7y6FEYo/"
    page = {
        "available": True,
        "basis": "platform_metadata",
        "text": (
            "平台公开元数据：视频标题：特朗普与高市早苗的爱情故事；"
            "平台内容提示：该内容疑似使用AI技术合成，请谨慎甄别"
        ),
    }

    result = report_web_search._classify_page_evidence(
        "特朗普爱上高市早苗",
        [{"index": 1, "title": "相关视频", "url": url}],
        {url: page},
    )
    source = {
        "index": 1,
        "domain": "www.bilibili.com",
        "quality": "other",
        "matchLevel": "direct",
        **result[1],
    }

    assert result[1]["evidenceRole"] == "misleading_origin"
    assert "AI 合成" in result[1]["evidenceReason"]
    assert report_web_search._derive_supported_verdicts("", [source]) == []


def test_direct_refute_requires_quote_to_deny_the_claim_relation():
    page = "特朗普曾在会谈中警告高市早苗不要添乱。"

    assert report_web_search._validated_evidence_role(
        "direct_refute",
        page,
        page,
        "特朗普爱上高市早苗",
    ) == "background_only"


def test_classifier_rewrites_reason_when_model_role_fails_evidence_gate(monkeypatch):
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=(
        '{"items":[{"index":1,"role":"satire_origin",'
        '"quote":"真的会被特朗普和高市早苗的梗图笑死",'
        '"reason":"这足以证明爱情说法是戏仿"}]}'
    )))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **_kwargs: response,
    )))
    monkeypatch.setattr(report_web_search.detector, "_get_client", lambda: client)
    page_text = "真的会被特朗普和高市早苗的梗图笑死。"

    result = report_web_search._classify_page_evidence(
        "特朗普爱上高市早苗",
        [{"index": 1, "title": "相关梗图", "url": "https://example.com/meme"}],
        {"https://example.com/meme": {"available": True, "text": page_text}},
    )

    assert result[1]["evidenceRole"] == "background_only"
    assert result[1]["evidenceReason"] == "正文只提供人物或事件背景，没有直接核验该主张"


def test_title_only_parody_and_reliable_background_cannot_support_satire():
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
    ) == []


def test_verified_page_satire_can_support_satire_verdict():
    sources = [{
        "index": 1,
        "title": "特朗普高市早苗新CP",
        "url": "https://example.com/parody",
        "domain": "example.com",
        "quality": "other",
        "matchLevel": "direct",
        "contentStatus": "verified",
        "evidenceRole": "satire_origin",
        "evidenceQuote": "这是一个特朗普与高市早苗的新 CP 搞笑段子",
        "evidenceBasis": "page",
    }]

    assert report_web_search._derive_supported_verdicts("", sources) == ["satire_likely"]


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
    monkeypatch.setattr(report_web_search, "_collect_verified_evidence", lambda _claim, _sources: {
        1: {
            "contentStatus": "verified",
            "evidenceRole": "satire_origin",
            "evidenceQuote": "特朗普高市早苗新 CP 搞笑视频",
            "evidenceReason": "正文明确使用搞笑和 CP 表达",
            "evidenceBasis": "page",
        },
    })
    monkeypatch.setattr(report_web_search, "WEB_SEARCH_CACHE_SECONDS", 0)
    monkeypatch.setattr(report_web_search, "WEB_SEARCH_STRATEGY", "agent")
    result = report_web_search.search_claim({"claim": "特朗普爱上高市早苗", "queries": []})

    options = captured["payload"]["parameters"]["search_options"]
    assert captured["strategy"] == "agent"
    assert captured["payload"]["parameters"]["incremental_output"] is True
    assert "enable_citation" not in options
    assert result["strategy"] == "agent"
    assert result["directSourceCount"] == 1
    assert result["matchedSourceCount"] == 1
    assert result["verifiedSourceCount"] == 1
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
    monkeypatch.setattr(report_web_search, "_collect_verified_evidence", lambda _claim, _sources: {
        1: {
            "contentStatus": "verified",
            "evidenceRole": "direct_support",
            "evidenceQuote": "公开主张获得官方确认",
            "evidenceReason": "正文直接确认该主张",
            "evidenceBasis": "page",
        },
    })
    monkeypatch.setattr(report_web_search, "WEB_SEARCH_CACHE_SECONDS", 0)
    monkeypatch.setattr(report_web_search, "WEB_SEARCH_STRATEGY", "agent")
    monkeypatch.setattr(report_web_search, "WEB_SEARCH_FALLBACK_STRATEGY", "max")
    result = report_web_search.search_claim({"claim": "公开主张", "queries": []})

    assert strategies == ["agent", "max"]
    assert result["strategy"] == "max"
    assert result["status"] == "success"


def test_multi_provider_sources_are_canonicalized_merged_and_attributed():
    sources = report_web_search._normalize_sources(
        [
            {
                "title": "特朗普爱上高市早苗",
                "url": "https://www.example.com/story?utm_source=test&id=7",
                "provider": "qwen_native",
                "lane": "exact",
            },
            {
                "title": "事实核查：特朗普爱上高市早苗",
                "url": "https://example.com/story?id=7&utm_medium=social",
                "provider": "qwen_responses",
                "lane": "fact_check",
            },
        ],
        claim="特朗普爱上高市早苗",
        queries=["特朗普 高市早苗 爱上"],
    )

    assert len(sources) == 1
    assert sources[0]["url"] == "https://www.example.com/story?id=7"
    assert sources[0]["providers"] == ["qwen_native", "qwen_responses"]
    assert sources[0]["lane"] == "fact_check"


def test_direct_evidence_requires_the_same_subject_object_and_relation():
    wrong_people = "拜登爱上高市早苗，这段文字明确讨论两人的恋情。"

    assert report_web_search._validated_evidence_role(
        "direct_support",
        wrong_people,
        wrong_people,
        "特朗普爱上高市早苗",
    ) == "background_only"


def test_structured_factcheck_record_maps_rating_without_using_search_snippets():
    source = {
        "factCheckClaim": "特朗普爱上高市早苗",
        "factCheckRating": "False",
        "factCheckPublisher": "Example Fact Check",
    }

    evidence = report_web_search._factcheck_record_evidence("特朗普爱上高市早苗", source)

    assert evidence["evidenceRole"] == "direct_refute"
    assert evidence["evidenceBasis"] == "fact_check_record"
    assert "公开评级：False" in evidence["evidenceQuote"]


def test_responses_ranker_only_accepts_urls_returned_by_search_tool():
    selected = report_web_search._parse_ranked_recall_lines(
        '\n'.join([
            '1. {"url":"https://example.com/real","title":"真实候选","lane":"exact"}',
            '{"url":"https://invented.example/fake","title":"模型编造地址","lane":"fact_check"}',
        ]),
        [{"url": "https://example.com/real", "title": "工具标题"}],
    )

    assert selected == [{
        "url": "https://example.com/real",
        "title": "真实候选",
        "site_name": "",
        "lane": "exact",
        "recall_basis": "tool_source",
    }]


def test_responses_ranker_can_fall_back_to_safe_candidates_when_provider_omits_source_list():
    selected = report_web_search._parse_ranked_recall_lines(
        '\n'.join([
            '{"url":"https://www.bilibili.com/video/BV1qcybBnEST/","title":"原始视频","lane":"origin"}',
            '{"url":"http://127.0.0.1/admin","title":"内网地址","lane":"official"}',
            '{"url":"javascript:alert(1)","title":"脚本地址","lane":"official"}',
        ]),
        [],
    )

    assert selected == [{
        "url": "https://www.bilibili.com/video/BV1qcybBnEST/",
        "title": "原始视频",
        "site_name": "",
        "lane": "origin",
        "recall_basis": "model_candidate",
    }]


def test_recall_providers_run_as_independent_lanes(monkeypatch):
    monkeypatch.setattr(report_web_search.detector, "API_KEY", "test-key")
    monkeypatch.setattr(report_web_search, "QWEN_RESPONSES_RECALL_ENABLED", True)
    monkeypatch.setattr(report_web_search, "GOOGLE_FACTCHECK_API_KEY", "")
    monkeypatch.setattr(report_web_search, "BRAVE_SEARCH_API_KEY", "")
    monkeypatch.setattr(report_web_search, "_native_recall", lambda *_args: {
        "provider": "qwen_native",
        "sources": [{"title": "原始出处", "url": "https://example.com/origin"}],
    })
    monkeypatch.setattr(report_web_search, "_qwen_responses_recall", lambda *_args: {
        "provider": "qwen_responses",
        "sources": [{"title": "事实核查", "url": "https://factcheck.org/check"}],
    })

    results, attempted = report_web_search._collect_recall_results("公开主张", ["公开主张"])

    assert [result["provider"] for result in results] == ["qwen_native", "qwen_responses"]
    assert attempted == ["qwen_native", "qwen_responses"]
