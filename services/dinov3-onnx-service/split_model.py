from __future__ import annotations

import argparse
import time
from pathlib import Path

import onnx.utils


MIDPOINT_NAME = "/backbone/layer.19/Add_1_output_0"
CAPTURED_CONSTANT = "802"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Split the 40-block DINOv3 ONNX model into two validated stages."
    )
    parser.add_argument("model", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    source = args.model.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    onnx.utils.extract_model(
        source,
        output_dir / "stage1.onnx",
        ["pixel_values"],
        [MIDPOINT_NAME, CAPTURED_CONSTANT],
        check_model=True,
        infer_shapes=True,
    )
    print(f"stage 1 completed in {time.monotonic() - started:.3f}s", flush=True)

    started = time.monotonic()
    onnx.utils.extract_model(
        source,
        output_dir / "stage2.onnx",
        [MIDPOINT_NAME, "pixel_values"],
        ["logits", CAPTURED_CONSTANT],
        check_model=True,
        infer_shapes=True,
    )
    print(f"stage 2 completed in {time.monotonic() - started:.3f}s", flush=True)


if __name__ == "__main__":
    main()
