from imagedetection.views import detection


def test_photographer_copyright_and_negative_aigc_scan_are_not_ai_metadata():
    result = detection._swarm_metadata_expert({
        "all_metadata": {
            "EXIF_Artist": "DREAM-UP Light / Brice Leclert",
            "EXIF_Copyright": "http://www.dreamuplight.com",
            "AIGC_潜在指纹检测": "未检测到明显文本标记",
            "Info_adobe": "100",
        }
    })

    assert result["score"] == 0.5
    assert result["details"]["verifiedAiMetadata"] is False
    assert result["details"]["aiMarkers"] == []


def test_named_generator_in_parameters_is_verified_ai_metadata():
    result = detection._swarm_metadata_expert({
        "all_metadata": {
            "PNG:Parameters": "Stable Diffusion, Steps: 30, Sampler: Euler",
        }
    })

    assert result["score"] == 0.9
    assert result["details"]["verifiedAiMetadata"] is True
    assert "Stable Diffusion" in result["details"]["aiMarkers"][0]


def test_photoshop_alone_is_not_treated_as_ai_generator():
    result = detection._swarm_metadata_expert({
        "all_metadata": {
            "XMP:CreatorTool": "Adobe Photoshop 25.5",
        }
    })

    assert result["score"] == 0.5
    assert result["details"]["verifiedAiMetadata"] is False
