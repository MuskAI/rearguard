from pathlib import Path
from io import BytesIO
import py_compile
import sys

from PIL import Image


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from image_preprocessing import downsample_for_analysis, fit_within, normalize_orientation


def test_production_inference_module_compiles():
    py_compile.compile(
        str(Path(__file__).with_name("inference_onnx.py")),
        doraise=True,
    )


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


def test_exif_orientation_is_normalized_before_analysis():
    encoded = Image.new("RGB", (4, 2), "white")
    exif = Image.Exif()
    exif[274] = 6
    output = BytesIO()
    encoded.save(output, format="JPEG", exif=exif)

    with Image.open(BytesIO(output.getvalue())) as source:
        normalized, metadata = normalize_orientation(source)

    assert normalized.size == (2, 4)
    assert metadata["exifOrientation"] == 6
    assert metadata["orientationApplied"] is True
    assert metadata["encodedSize"] == {"width": 4, "height": 2}
    assert metadata["displaySize"] == {"width": 2, "height": 4}
