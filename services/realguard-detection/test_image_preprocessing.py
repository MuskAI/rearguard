from pathlib import Path
import sys

from PIL import Image


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from image_preprocessing import downsample_for_analysis, fit_within


def test_fit_within_preserves_aspect_ratio_for_large_image():
    assert fit_within(4096, 3072, 2048) == (2048, 1536)


def test_large_image_is_downsampled_to_2k_longest_side():
    image = Image.new("RGB", (4096, 3072), "white")

    processed, metadata = downsample_for_analysis(image, 2048)

    assert processed.size == (2048, 1536)
    assert metadata == {
        "downsampled": True,
        "maxAnalysisSide": 2048,
        "originalSize": {"width": 4096, "height": 3072},
        "processedSize": {"width": 2048, "height": 1536},
        "scale": 0.5,
    }


def test_small_image_is_not_upscaled():
    image = Image.new("RGB", (1600, 1200), "white")

    processed, metadata = downsample_for_analysis(image, 2048)

    assert processed is image
    assert metadata["downsampled"] is False
    assert metadata["processedSize"] == {"width": 1600, "height": 1200}
