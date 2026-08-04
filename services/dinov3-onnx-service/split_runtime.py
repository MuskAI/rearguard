from __future__ import annotations

import atexit
import json
import multiprocessing as mp
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


MIDPOINT_NAME = "/backbone/layer.19/Add_1_output_0"


def _gpu_worker(
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
        active = list(session.get_providers())
        if "CUDAExecutionProvider" not in active:
            raise RuntimeError(f"CUDA provider did not initialize: {active}")
        connection.send(
            {
                "status": "ready",
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
                raise ValueError(f"unknown worker command: {command}")
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
        try:
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
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


class SplitCudaDetector:
    """Run the two halves of DINOv3 in isolated processes on separate GPUs."""

    def __init__(
        self,
        split_dir: str | Path,
        *,
        package_dir: str | Path,
        stage1_device: int = 1,
        stage2_device: int = 0,
        startup_timeout: float = 180.0,
    ) -> None:
        self.split_dir = Path(split_dir).expanduser().resolve()
        self.package_dir = Path(package_dir).expanduser().resolve()
        self.stage1_path = self.split_dir / "stage1.onnx"
        self.stage2_path = self.split_dir / "stage2.onnx"
        for path in (self.stage1_path, self.stage2_path):
            if not path.is_file():
                raise FileNotFoundError(f"split ONNX model not found: {path}")

        self._context = mp.get_context("spawn")
        self._lock = threading.Lock()
        self._closed = False
        self._connections: list[Any] = []
        self._processes: list[Any] = []
        self.stage_status: list[dict[str, Any]] = []

        for stage, model_path, device_id in (
            (1, self.stage1_path, stage1_device),
            (2, self.stage2_path, stage2_device),
        ):
            parent, child = self._context.Pipe()
            process = self._context.Process(
                target=_gpu_worker,
                args=(child, str(model_path), int(device_id), stage),
                name=f"dinov3-stage-{stage}-gpu-{device_id}",
            )
            process.start()
            child.close()
            self._connections.append(parent)
            self._processes.append(process)

        try:
            for connection in self._connections:
                if not connection.poll(startup_timeout):
                    raise TimeoutError("timed out while loading split CUDA model")
                message = connection.recv()
                if not isinstance(message, dict) or message.get("status") != "ready":
                    raise RuntimeError(
                        "split CUDA worker failed to start: "
                        + json.dumps(message, ensure_ascii=True)
                    )
                self.stage_status.append(message)
        except BaseException:
            self.close()
            raise

        self.active_providers = [
            f"CUDAExecutionProvider:GPU{item['physicalDeviceId']}"
            for item in self.stage_status
        ]
        atexit.register(self.close)

    @staticmethod
    def _receive(connection: Any) -> np.ndarray:
        if not connection.poll(120):
            raise TimeoutError("split CUDA inference timed out")
        status, payload = connection.recv()
        if status == "error":
            raise RuntimeError(json.dumps(payload, ensure_ascii=True))
        if status != "ok":
            raise RuntimeError(f"unexpected split worker status: {status}")
        return np.asarray(payload)

    def predict_pil(self, image: Image.Image) -> dict[str, Any]:
        import sys

        package = str(self.package_dir)
        if package not in sys.path:
            sys.path.insert(0, package)
        from preprocess import preprocess_pil

        array = preprocess_pil(image, add_batch_dim=True)
        array = np.ascontiguousarray(array, dtype=np.float32)
        with self._lock:
            if self._closed:
                raise RuntimeError("split CUDA detector is closed")
            stage1, stage2 = self._connections
            stage1.send(("run", array))
            hidden = self._receive(stage1)
            stage2.send(("run", (hidden, array)))
            logits = self._receive(stage2)

        raw_values = np.asarray(logits, dtype=np.float64)
        values = raw_values - np.max(raw_values, axis=1, keepdims=True)
        probabilities = np.exp(values)
        probabilities /= np.sum(probabilities, axis=1, keepdims=True)
        return {
            "real_probability": float(probabilities[0, 0]),
            "fake_probability": float(probabilities[0, 1]),
            "logits": [float(value) for value in raw_values[0].tolist()],
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for connection in self._connections:
            try:
                connection.send(("stop", None))
            except (BrokenPipeError, EOFError, OSError):
                pass
            connection.close()
        for process in self._processes:
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
