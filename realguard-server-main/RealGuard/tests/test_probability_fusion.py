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


def test_verified_generator_metadata_increases_risk():
    experts = [
        _expert("primary", 0.28),
        _expert(
            "metadata",
            0.9,
            details={"verifiedAiMetadata": True, "aiMarkers": ["PNG:Parameters = Stable Diffusion"]},
        ),
    ]
    result = probability_fusion.fuse(experts)
    assert result["posterior"] > 0.9
    assert result["factors"][0]["kind"] == "ai_generation_metadata"
