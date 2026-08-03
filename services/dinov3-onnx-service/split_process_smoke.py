from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import statistics
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np


DEFAULT_SPLIT_DIR = Path("/mnt/sda1/ymk/dinov3_split_fp16")
MIDPOINT_NAME = "/backbone/layer.19/Add_1_output_0"


def _worker(
    connection: Any,
    model_path: str,
    physical_device_id: int,
    stage: int,
) -> None:
    try:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(physical_device_id)
        import onnxruntime as ort

        if hasattr(ort, "preload_dlls"):
            ort.preload_dlls()
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.enable_cpu_mem_arena = False
        providers = [
            (
                "CUDAExecutionProvider",
                {
                    "device_id": "0",
                    "arena_extend_strategy": "kSameAsRequested",
                    "do_copy_in_default_stream": "1",
                    "use_ep_level_unified_stream": "1",
                },
            ),
            "CPUExecutionProvider",
        ]
        started = time.monotonic()
        session = ort.InferenceSession(
            model_path,
            sess_options=options,
            providers=providers,
        )
        active = session.get_providers()
        if "CUDAExecutionProvider" not in active:
            raise RuntimeError(f"CUDA provider did not initialize: {active}")
        connection.send(
            {
                "event": "ready",
                "stage": stage,
                "physicalDeviceId": physical_device_id,
                "loadSeconds": round(time.monotonic() - started, 3),
                "providers": active,
            }
        )

        while True:
            command, payload = connection.recv()
            if command == "stop":
                return
            if command != "run":
                raise ValueError(f"unknown command: {command}")
            if stage == 1:
                output = session.run(
                    [MIDPOINT_NAME],
                    {"pixel_values": payload},
                )[0]
            else:
                hidden, image = payload
                output = session.run(
                    ["logits"],
                    {MIDPOINT_NAME: hidden, "pixel_values": image},
                )[0]
            connection.send(("ok", output))
    except BaseException as exc:
        connection.send(
            (
                "error",
                {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )
        )
    finally:
        connection.close()


def _receive(connection: Any) -> Any:
    status, payload = connection.recv()
    if status == "error":
        raise RuntimeError(json.dumps(payload, ensure_ascii=True))
    if status != "ok":
        raise RuntimeError(f"unexpected worker status: {status}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split-dir", type=Path, default=DEFAULT_SPLIT_DIR)
    parser.add_argument("--stage1-device", type=int, default=1)
    parser.add_argument("--stage2-device", type=int, default=0)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--hold-seconds", type=float, default=0)
    parser.add_argument("--image", type=Path)
    parser.add_argument(
        "--package-dir",
        type=Path,
        default=Path("/home/ymk/model_registry/dinov3_vit7b16_linear_onnx_fp16"),
    )
    args = parser.parse_args()

    context = mp.get_context("spawn")
    parent1, child1 = context.Pipe()
    parent2, child2 = context.Pipe()
    process1 = context.Process(
        target=_worker,
        args=(child1, str(args.split_dir / "stage1.onnx"), args.stage1_device, 1),
    )
    process2 = context.Process(
        target=_worker,
        args=(child2, str(args.split_dir / "stage2.onnx"), args.stage2_device, 2),
    )
    process1.start()
    process2.start()
    child1.close()
    child2.close()

    try:
        for connection in (parent1, parent2):
            message = connection.recv()
            if not isinstance(message, dict) or message.get("event") != "ready":
                raise RuntimeError(json.dumps(message, ensure_ascii=True))
            print(json.dumps(message, ensure_ascii=True), flush=True)

        if args.hold_seconds > 0:
            print(json.dumps({"event": "holding", "seconds": args.hold_seconds}), flush=True)
            time.sleep(args.hold_seconds)

        if args.image:
            from PIL import Image

            sys.path.insert(0, str(args.package_dir))
            from preprocess import preprocess_pil

            with Image.open(args.image) as source:
                image = preprocess_pil(source.convert("RGB"), add_batch_dim=True)
            image = np.ascontiguousarray(image, dtype=np.float32)
        else:
            image = np.zeros((1, 3, 224, 224), dtype=np.float32)
        timings: list[float] = []
        logits = None
        hidden = None
        for iteration in range(max(1, args.iterations)):
            started = time.monotonic()
            parent1.send(("run", image))
            hidden = _receive(parent1)
            stage1_seconds = time.monotonic() - started

            started = time.monotonic()
            parent2.send(("run", (hidden, image)))
            logits = _receive(parent2)
            stage2_seconds = time.monotonic() - started
            total = stage1_seconds + stage2_seconds
            timings.append(total)
            print(
                json.dumps(
                    {
                        "event": "iteration_complete",
                        "iteration": iteration + 1,
                        "stage1Seconds": round(stage1_seconds, 3),
                        "stage2Seconds": round(stage2_seconds, 3),
                        "totalSeconds": round(total, 3),
                    }
                ),
                flush=True,
            )

        assert hidden is not None and logits is not None
        print(
            json.dumps(
                {
                    "event": "benchmark_complete",
                    "iterations": len(timings),
                    "transferBytes": int(hidden.nbytes),
                    "medianSeconds": round(statistics.median(timings), 3),
                    "minSeconds": round(min(timings), 3),
                    "maxSeconds": round(max(timings), 3),
                    "logits": np.asarray(logits).tolist(),
                },
                ensure_ascii=True,
            ),
            flush=True,
        )
    finally:
        for connection in (parent1, parent2):
            try:
                connection.send(("stop", None))
            except (BrokenPipeError, EOFError):
                pass
            connection.close()
        for process in (process1, process2):
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()


if __name__ == "__main__":
    main()
