#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import tempfile
from urllib.request import urlopen


BASE_URL = (
    "https://huggingface.co/twn39/"
    "TinyCLIP-ViT-8M-16-Text-3M-YFCC15M-ONNX/resolve/main"
)
FILES = {
    "model_int8.onnx": (
        "onnx/model_int8.onnx",
        "844d1a46ab18acf50c989e541b12fe3b6dc7f8d6004725b4e992d142788e0600",
    ),
    "tokenizer.json": (
        "tokenizer.json",
        "6d9109cc838977f3ca94a379eec36aecc7c807e1785cd729660ca2fc0171fb35",
    ),
    "preprocessor_config.json": (
        "preprocessor_config.json",
        "5df7e578c37e907a431daf47fd592fc49fa50d23ed4c41285a0a34a58a9d2e06",
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, destination: Path, expected_sha256: str) -> None:
    if destination.is_file() and _sha256(destination) == expected_sha256:
        print(f"ready  {destination.name}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix=f".{destination.name}.", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
        with urlopen(url, timeout=60) as response:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                temporary.write(chunk)
    actual = _sha256(temporary_path)
    if actual != expected_sha256:
        temporary_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"checksum mismatch for {destination.name}: expected {expected_sha256}, got {actual}"
        )
    temporary_path.replace(destination)
    print(f"saved  {destination.name}")


def main() -> None:
    default = Path(__file__).resolve().parents[1] / "models" / "document-router" / "tinyclip-int8"
    parser = argparse.ArgumentParser(description="Download the TinyCLIP document-router runtime")
    parser.add_argument("--output", type=Path, default=default)
    args = parser.parse_args()
    for filename, (remote_path, sha256) in FILES.items():
        _download(f"{BASE_URL}/{remote_path}", args.output / filename, sha256)
    print(f"TinyCLIP router model is ready at {args.output}")


if __name__ == "__main__":
    main()
