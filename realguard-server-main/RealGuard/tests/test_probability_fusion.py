from imagedetection.views import probability_fusion


def _expert(expert_id, score, *, weight=1.0, **extra):
    return {
        "id": expert_id,
        "status": "success",
        "score": score,
        "weight": weight,
        **extra,
    }


def test_missing_metadata_is_neutral_to_pixel_baseline():
    experts = [
        _expert("primary", 0.76, weight=0.72),
        _expert("metadata", 0.56, weight=0.18),
    ]
    result = probability_fusion.fuse(experts)
    assert result["pixelBaseline"] == 0.76
    assert result["posterior"] == 0.76


def test_known_watermark_and_integrity_clash_override_dilution():
    experts = [
        _expert("primary", 0.2, weight=0.72),
        {
            "id": "visible_watermark",
            "status": "success",
            "score": None,
            "weight": 0.0,
            "probabilityModel": {
                "version": probability_fusion.MODEL_VERSION,
                "effectiveLikelihoodRatio": 1440.0,
                "decisive": True,
                "corroborated": True,
                "factors": [
                    {"kind": "known_visible_ai_watermark", "group": "known_watermark", "label": "已知 AI 平台水印"},
                    {"kind": "metadata_integrity_clash", "group": "integrity", "label": "元数据完整性冲突"},
                ],
            },
        },
    ]
    result = probability_fusion.fuse(experts)
    assert result["posterior"] > 0.99
    assert result["pixelBaseline"] == 0.2


def test_generic_logo_probability_model_is_neutral():
    experts = [
        _expert("primary", 0.31),
        {
            "id": "visible_watermark",
            "status": "success",
            "score": None,
            "probabilityModel": {
                "effectiveLikelihoodRatio": 1.0,
                "decisive": False,
                "corroborated": False,
                "factors": [],
            },
        },
    ]
    result = probability_fusion.fuse(experts)
    assert result["posterior"] == 0.31


def test_known_invisible_watermark_increases_risk_monotonically():
    baseline = [_expert("primary", 0.35)]
    with_watermark = [
        *baseline,
        _expert(
            "watermark",
            0.96,
            provenance_kind="watermark",
            details={"attribution": "Stable Diffusion XL"},
        ),
    ]
    assert probability_fusion.fuse(with_watermark)["posterior"] > probability_fusion.fuse(baseline)["posterior"]


def test_high_metadata_score_without_verified_generator_is_neutral():
    experts = [
        _expert("primary", 0.28),
        _expert("metadata", 0.95, details={"verifiedAiMetadata": False}),
    ]
    result = probability_fusion.fuse(experts)
    assert result["posterior"] == 0.28
    assert result["factors"] == []


def test_editable_generator_metadata_is_probability_neutral():
    experts = [
        _expert("primary", 0.28),
        _expert(
            "metadata",
            0.9,
            details={"verifiedAiMetadata": False, "editableAiMetadata": True, "aiMarkers": ["PNG:Parameters = Stable Diffusion"]},
        ),
    ]
    result = probability_fusion.fuse(experts)
    assert result["posterior"] == 0.28
    assert result["factors"] == []


def test_tamper_and_recapture_scores_do_not_raise_ai_generation_probability():
    experts = [
        _expert("primary", 0.2, weight=0.7),
        _expert("aliyun_ps", 0.94, weight=0.2),
        _expert("aliyun_recap", 0.88, weight=0.1),
    ]

    result = probability_fusion.fuse(experts)

    assert result["posterior"] == 0.2
    assert result["baselineExperts"] == ["primary"]
    assert result["riskVector"] == {
        "aiGenerated": 0.2,
        "tampered": 0.94,
        "recaptured": 0.88,
    }


def test_non_aigc_experts_cannot_publish_ai_conclusion_without_source_evidence():
    result = probability_fusion.fuse([
        _expert("aliyun_ps", 0.94),
        _expert("aliyun_recap", 0.88),
    ])

    assert result["baselineExperts"] == []
    assert result["publishable"] is False
