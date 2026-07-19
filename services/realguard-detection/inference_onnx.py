import hashlib
import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import onnxruntime as ort
import torch
from PIL import Image
from torchvision import transforms
from torchvision.transforms import InterpolationMode

try:
    from .image_preprocessing import downsample_for_analysis, normalize_orientation
    from .model_decision_policy import model_decision_policy
except ImportError:  # Standalone deployment keeps both files in one directory.
    from image_preprocessing import downsample_for_analysis, normalize_orientation
    from model_decision_policy import model_decision_policy


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


DEPLOYMENT_DIR = os.environ.get("REALGUARD_V2_DEPLOYMENT_DIR", "/home/ymk/realguard_new_deployment")
ONNX_PATH = os.environ.get(
    "REALGUARD_V2_ONNX_PATH",
    os.path.join(DEPLOYMENT_DIR, "artifacts", "realguardv2.int8.onnx"),
)
TILE_SIZE = int(os.environ.get("REALGUARD_V2_TILE_SIZE", "224"))
MAX_TILES = int(os.environ.get("REALGUARD_V2_MAX_TILES", "16"))
TOP_K = int(os.environ.get("REALGUARD_V2_TOP_K", "3"))
MAX_ANALYSIS_SIDE = max(256, int(os.environ.get("REALGUARD_V2_MAX_ANALYSIS_SIDE", "2048")))
DEVICE = os.environ.get("REALGUARD_V2_DEVICE", "cuda").strip().lower()
CUDA_DEVICE_ID = int(os.environ.get("REALGUARD_V2_CUDA_DEVICE_ID", "0"))
REQUIRE_CUDA = _env_bool("REALGUARD_V2_REQUIRE_CUDA", False)
REQUIRE_MODEL_IDENTITY = _env_bool(
    "REALGUARD_V2_REQUIRE_MODEL_IDENTITY", REQUIRE_CUDA
)
MODEL_REVISION = os.environ.get("REALGUARD_V2_MODEL_REVISION", "").strip()
EXPECTED_MODEL_SHA256 = os.environ.get("REALGUARD_V2_MODEL_SHA256", "").strip().lower()
RUNTIME_LOCK_PATH = os.environ.get(
    "REALGUARD_V2_RUNTIME_LOCK_PATH",
    "/home/ymk/realguard-detection-releases/current/model/runtime.lock",
).strip()
EAGER_LOAD = _env_bool("REALGUARD_V2_EAGER_LOAD", False)
WARMUP_ON_INIT = _env_bool("REALGUARD_V2_WARMUP", True)
WARMUP_VIEWS = max(1, int(os.environ.get("REALGUARD_V2_WARMUP_VIEWS", "26")))
MAX_CONCURRENT_INFERENCES = max(
    1,
    int(os.environ.get("REALGUARD_V2_MAX_CONCURRENT_INFERENCES", "1")),
)
DEPLOYMENT_COMMIT_PATH = os.environ.get(
    "REALGUARD_DEPLOYMENT_COMMIT_PATH",
    "/home/ymk/realguard-detection-releases/current/DEPLOYED_COMMIT",
)
INFERENCE_QUEUE_TIMEOUT_SECONDS = max(
    0.1,
    float(os.environ.get("REALGUARD_V2_INFERENCE_QUEUE_TIMEOUT_SECONDS", "20")),
)
MAX_SOURCE_PIXELS = max(
    1024 * 1024,
    int(os.environ.get("REALGUARD_V2_MAX_SOURCE_PIXELS", "24000000")),
)
MAX_CHUNKS = max(1, int(os.environ.get("REALGUARD_V2_MAX_CHUNKS", "64")))


class InferenceBusyError(RuntimeError):
    pass


class ImagePixelsTooLargeError(ValueError):
    pass


class UnsupportedAnimatedImageError(ValueError):
    pass


session = None
input_name = None
preprocess_transform = None
_model_state: Dict[str, Any] = {
    "initialized": False,
    "requestedDevice": DEVICE,
    "cudaDeviceId": CUDA_DEVICE_ID,
}
_init_lock = threading.Lock()
_inference_slots = threading.BoundedSemaphore(MAX_CONCURRENT_INFERENCES)


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _deployment_commit() -> str | None:
    try:
        value = Path(DEPLOYMENT_COMMIT_PATH).read_text(encoding="ascii").strip().lower()
    except OSError:
        return None
    return value if 7 <= len(value) <= 40 and all(char in "0123456789abcdef" for char in value) else None


def _runtime_decision_contract(
    model_sha256: str | None,
    *,
    max_tiles: int | None = None,
    top_k: int | None = None,
    chunk_size: int | None = None,
) -> Dict[str, Any]:
    selected_max_tiles = int(max_tiles or MAX_TILES)
    side_tiles = max(1, int(round(math.sqrt(float(selected_max_tiles)))))
    derived_chunk_size = 256 * side_tiles
    preprocessing_impl = os.path.join(os.path.dirname(__file__), "image_preprocessing.py")
    decision_policy_impl = os.path.join(os.path.dirname(__file__), "model_decision_policy.py")
    preprocessing = {
        "schema": "cn.huijian.image-preprocessing-v1",
        "implementationSha256": _sha256_file(preprocessing_impl),
        "orientation": "PIL.ImageOps.exif_transpose",
        "colorMode": "RGB",
        "resize": {"size": TILE_SIZE, "interpolation": "bicubic"},
        "centerCrop": TILE_SIZE,
        "normalizeMean": [0.48145466, 0.4578275, 0.40821073],
        "normalizeStd": [0.26862954, 0.26130258, 0.27577711],
    }
    return {
        "schema": "cn.huijian.runtime-inference-contract-v1",
        "modelSha256": str(model_sha256 or "").strip().lower(),
        "inferenceImplementationSha256": _sha256_file(__file__),
        "decisionPolicyImplementationSha256": _sha256_file(decision_policy_impl),
        "runtimeLockSha256": _sha256_file(RUNTIME_LOCK_PATH),
        "classMapping": {"0": "real", "1": "fake"},
        "preprocessing": preprocessing,
        "inferenceParameters": {
            "tileSize": TILE_SIZE,
            "requestedMaxTiles": selected_max_tiles,
            "requestedTopK": int(top_k or TOP_K),
            "requestedChunkSize": int(chunk_size or derived_chunk_size),
            "maxAnalysisSide": MAX_ANALYSIS_SIDE,
            "maxSourcePixels": MAX_SOURCE_PIXELS,
            "maxChunks": MAX_CHUNKS,
            "aggregation": "top_k_mean_fusion_probability",
            "outputMapping": {
                "level1Logits": 0,
                "level2Logits": 1,
                "fusionLogits": 2,
                "fakeClassIndex": 1,
            },
        },
    }


def _provider_specs():
    available = set(ort.get_available_providers())
    if DEVICE not in {"auto", "cuda", "gpu", "cpu"}:
        raise ValueError(f"Unsupported REALGUARD_V2_DEVICE: {DEVICE}")

    wants_cuda = DEVICE in {"auto", "cuda", "gpu"}
    if wants_cuda and "CUDAExecutionProvider" in available:
        cuda_options = {
            "device_id": str(CUDA_DEVICE_ID),
            "arena_extend_strategy": "kNextPowerOfTwo",
            "cudnn_conv_algo_search": "EXHAUSTIVE",
            "do_copy_in_default_stream": "1",
        }
        return [("CUDAExecutionProvider", cuda_options), "CPUExecutionProvider"]

    if wants_cuda:
        message = (
            "CUDAExecutionProvider is unavailable; available providers: "
            + ", ".join(sorted(available))
        )
        if REQUIRE_CUDA:
            raise RuntimeError(message)
        print(f"  [模型加载] 警告: {message}; 回退到 CPU。")

    return ["CPUExecutionProvider"]


def _session_options():
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.enable_mem_pattern = True
    options.enable_cpu_mem_arena = True
    options.log_severity_level = int(os.environ.get("REALGUARD_V2_ORT_LOG_LEVEL", "2"))
    intra_op_threads = int(os.environ.get("REALGUARD_V2_INTRA_OP_THREADS", "0"))
    if intra_op_threads > 0:
        options.intra_op_num_threads = intra_op_threads
    return options


def _preprocess_pipeline():
    return transforms.Compose([
        transforms.Resize(TILE_SIZE, interpolation=InterpolationMode.BICUBIC),
        transforms.CenterCrop(TILE_SIZE),
        lambda image: image.convert("RGB"),
        transforms.ToTensor(),
        transforms.Normalize(
            (0.48145466, 0.4578275, 0.40821073),
            (0.26862954, 0.26130258, 0.27577711),
        ),
    ])


def initialize_model(warmup=None) -> Dict[str, Any]:
    """Create one process-wide ONNX session and optionally warm its CUDA kernels."""
    global session, input_name, preprocess_transform, _model_state
    if session is not None:
        return get_model_status()

    with _init_lock:
        if session is not None:
            return get_model_status()
        if not os.path.isfile(ONNX_PATH):
            raise FileNotFoundError(f"RealGuard v2 ONNX artifact not found: {ONNX_PATH}")
        actual_model_sha256 = _sha256_file(ONNX_PATH)
        if REQUIRE_MODEL_IDENTITY and (
            not MODEL_REVISION or len(EXPECTED_MODEL_SHA256) != 64
        ):
            raise RuntimeError(
                "RealGuard v2 model identity is required but revision/SHA-256 is missing"
            )
        if EXPECTED_MODEL_SHA256 and actual_model_sha256 != EXPECTED_MODEL_SHA256:
            raise RuntimeError(
                "RealGuard v2 ONNX SHA-256 mismatch: "
                f"{actual_model_sha256} != {EXPECTED_MODEL_SHA256}"
            )

        should_warmup = WARMUP_ON_INIT if warmup is None else bool(warmup)
        providers = _provider_specs()
        print(
            f"  [模型加载] 正在加载 RealGuard v2 ONNX: {ONNX_PATH} "
            f"(device={DEVICE}, cuda={CUDA_DEVICE_ID})"
        )
        init_started = time.perf_counter()
        model_session = ort.InferenceSession(
            ONNX_PATH,
            sess_options=_session_options(),
            providers=providers,
        )
        actual_providers = model_session.get_providers()
        if REQUIRE_CUDA and (
            not actual_providers or actual_providers[0] != "CUDAExecutionProvider"
        ):
            raise RuntimeError(
                "RealGuard v2 requires CUDA but ONNX Runtime activated: "
                + ", ".join(actual_providers)
            )

        model_input_name = model_session.get_inputs()[0].name
        warmup_ms = 0.0
        if should_warmup:
            warmup_started = time.perf_counter()
            warmup_input = np.zeros(
                (1, WARMUP_VIEWS, 3, TILE_SIZE, TILE_SIZE),
                dtype=np.float32,
            )
            model_session.run(None, {model_input_name: warmup_input})
            warmup_ms = (time.perf_counter() - warmup_started) * 1000.0

        session = model_session
        input_name = model_input_name
        preprocess_transform = _preprocess_pipeline()
        init_ms = (time.perf_counter() - init_started) * 1000.0
        _model_state = {
            "initialized": True,
            "requestedDevice": DEVICE,
            "providers": list(actual_providers),
            "activeProvider": actual_providers[0] if actual_providers else "unknown",
            "cudaDeviceId": CUDA_DEVICE_ID if "CUDAExecutionProvider" in actual_providers else None,
            "initMs": round(init_ms, 2),
            "warmupMs": round(warmup_ms, 2),
            "warmupViews": WARMUP_VIEWS if should_warmup else 0,
            "maxConcurrentInferences": MAX_CONCURRENT_INFERENCES,
            "maxAnalysisSide": MAX_ANALYSIS_SIDE,
            "modelRevision": MODEL_REVISION or "unversioned",
            "modelSha256": actual_model_sha256,
        }
        print(
            "  [模型加载] RealGuard v2 ONNX 已常驻 "
            f"{_model_state['activeProvider']}，初始化 {init_ms:.0f}ms，"
            f"预热 {warmup_ms:.0f}ms。"
        )
        return get_model_status()


def get_model_status() -> Dict[str, Any]:
    runtime_contract = _runtime_decision_contract(_model_state.get("modelSha256"))
    runtime_contract_sha256 = hashlib.sha256(
        json.dumps(
            runtime_contract,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        **_model_state,
        "deploymentCommit": _deployment_commit(),
        "runtimeDecisionContract": runtime_contract,
        "runtimeDecisionContractSha256": runtime_contract_sha256,
        "modelDecisionPolicy": model_decision_policy(
            model_sha256=_model_state.get("modelSha256"),
            runtime_contract=runtime_contract,
        ),
    }


def _lazy_init():
    if session is None:
        initialize_model()


def _start_positions(image_length: int, block_size: int, bias: int = 0) -> List[int]:
    if image_length <= block_size:
        return [bias]
    num_blocks = image_length // block_size + (image_length % block_size > 0)
    if num_blocks == 1:
        return [bias]
    total_stride = (image_length - block_size) / (num_blocks - 1)
    positions = [int(round(i * total_stride) + bias) for i in range(num_blocks - 1)]
    positions.append(image_length - block_size + bias)
    return positions


def _crop_image_to_tiles(image: Image.Image, block_size: int) -> List[Image.Image]:
    width, height = image.size
    tiles = []
    for y in _start_positions(height, block_size):
        for x in _start_positions(width, block_size):
            tiles.append(image.crop((x, y, x + block_size, y + block_size)))
    return tiles


def _max_tiles_to_chunk_size(max_tiles: int) -> Tuple[int, int]:
    side_tiles = max(1, int(round(math.sqrt(float(max_tiles)))))
    effective_tiles = side_tiles * side_tiles
    return 256 * side_tiles, effective_tiles


def _split_image_into_chunks_with_boxes(image: Image.Image, chunk_size: int):
    image = image.convert("RGB")
    width, height = image.size
    chunks = []
    for top in range(0, height, chunk_size):
        for left in range(0, width, chunk_size):
            right = min(left + chunk_size, width)
            bottom = min(top + chunk_size, height)
            chunks.append((image.crop((left, top, right, bottom)), (left, top, right, bottom)))
    return chunks


def _split_image_into_chunks(image: Image.Image, chunk_size: int):
    return [chunk for chunk, _ in _split_image_into_chunks_with_boxes(image, chunk_size)]


def _prepare_analysis_chunks(img_path, chunk_size, max_tiles):
    with Image.open(img_path) as source_image:
        width, height = source_image.size
        if width <= 0 or height <= 0 or width * height > MAX_SOURCE_PIXELS:
            raise ImagePixelsTooLargeError(
                f"image pixels exceed limit {MAX_SOURCE_PIXELS}: {width}x{height}"
            )
        if bool(getattr(source_image, "is_animated", False)) and int(getattr(source_image, "n_frames", 1)) > 1:
            raise UnsupportedAnimatedImageError("animated images are not supported; upload a static frame")
        normalized, orientation_meta = normalize_orientation(source_image)
        image = normalized.convert("RGB")
    image, resize_meta = downsample_for_analysis(image, MAX_ANALYSIS_SIDE)
    resize_meta["orientation"] = orientation_meta
    requested_max_tiles = int(max_tiles or MAX_TILES)
    derived_chunk_size, effective_tiles = _max_tiles_to_chunk_size(requested_max_tiles)
    requested_chunk_size = int(chunk_size or derived_chunk_size)
    if requested_chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    actual_chunk_size = requested_chunk_size
    width, height = image.size
    while math.ceil(width / actual_chunk_size) * math.ceil(height / actual_chunk_size) > MAX_CHUNKS:
        actual_chunk_size += max(1, actual_chunk_size // 4)
    chunks = _split_image_into_chunks_with_boxes(image, chunk_size=actual_chunk_size)
    if not chunks:
        raise ValueError("empty image chunks")
    return (
        image,
        resize_meta,
        requested_max_tiles,
        derived_chunk_size,
        effective_tiles,
        requested_chunk_size,
        actual_chunk_size,
        chunks,
    )


def _build_input_tensor(image: Image.Image) -> Tuple[np.ndarray, Dict[str, int]]:
    global preprocess_transform
    image = image.convert("RGB")
    global_img = preprocess_transform(image)
    tiles = _crop_image_to_tiles(image, TILE_SIZE)
    views = [global_img] + [preprocess_transform(tile) for tile in tiles]
    x = torch.stack(views, dim=0).unsqueeze(0)
    meta = {
        "tile_size": int(TILE_SIZE),
        "tile_count": int(len(tiles)),
        "model_inputs": int(x.shape[1]),
    }
    return x.numpy().astype(np.float32), meta


def _fake_prob_from_logits(logits: np.ndarray) -> float:
    logits = logits.astype(np.float32)
    logits = logits - np.max(logits, axis=1, keepdims=True)
    probs = np.exp(logits) / np.sum(np.exp(logits), axis=1, keepdims=True)
    return float(probs[0, 1])


def _topk_mean(scores: List[float], k: int) -> float:
    if not scores:
        return 0.0
    arr = np.asarray(scores, dtype=np.float32)
    kk = max(1, min(int(k), int(arr.shape[0])))
    idx = np.argsort(-arr)[:kk]
    return float(arr[idx].mean())


def _calibrated_probability(raw_score: float, calibration: Dict[str, Any] | None) -> float:
    if not isinstance(calibration, dict) or calibration.get("method") != "temperature_scaling":
        raise ValueError("probability calibration is unavailable")
    temperature = float(calibration.get("temperature"))
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise ValueError("probability calibration temperature is invalid")
    clipped = min(max(float(raw_score), 1e-7), 1.0 - 1e-7)
    calibrated_logit = math.log(clipped / (1.0 - clipped)) / temperature
    return 1.0 / (1.0 + math.exp(-calibrated_logit))


def acquire_inference_slot() -> float:
    """Reserve GPU capacity before any parallel auxiliary work is started."""
    _lazy_init()
    queue_started = time.perf_counter()
    acquired = _inference_slots.acquire(timeout=INFERENCE_QUEUE_TIMEOUT_SECONDS)
    if not acquired:
        raise InferenceBusyError("GPU inference queue is full")
    return (time.perf_counter() - queue_started) * 1000.0


def release_inference_slot() -> None:
    _inference_slots.release()


def analyze_image(
    img_path,
    chunk_size=None,
    max_tiles=None,
    top_k=None,
    admission_queue_wait_ms=None,
) -> Dict[str, Any]:
    analysis_started = time.perf_counter()
    requested_top_k = int(top_k or TOP_K)
    owns_slot = admission_queue_wait_ms is None
    queue_wait_ms = (
        acquire_inference_slot()
        if owns_slot
        else max(0.0, float(admission_queue_wait_ms))
    )
    try:
        (
            image,
            resize_meta,
            requested_max_tiles,
            derived_chunk_size,
            effective_tiles,
            requested_chunk_size,
            actual_chunk_size,
            chunks,
        ) = _prepare_analysis_chunks(img_path, chunk_size, max_tiles)
        chunk_results = []
        fusion_scores = []
        level1_scores = []
        level2_scores = []
        preprocess_ms = 0.0
        inference_ms = 0.0
        for index, (chunk, box) in enumerate(chunks):
            preprocess_started = time.perf_counter()
            x_np, input_meta = _build_input_tensor(chunk)
            preprocess_ms += (time.perf_counter() - preprocess_started) * 1000.0

            inference_started = time.perf_counter()
            outputs = session.run(None, {input_name: x_np})
            inference_ms += (time.perf_counter() - inference_started) * 1000.0

            level1_prob = _fake_prob_from_logits(outputs[0])
            level2_prob = _fake_prob_from_logits(outputs[1])
            fusion_prob = _fake_prob_from_logits(outputs[2])
            level1_scores.append(level1_prob)
            level2_scores.append(level2_prob)
            fusion_scores.append(fusion_prob)
            left, top, right, bottom = box
            chunk_results.append({
                "index": index,
                "bbox": {
                    "left": int(left),
                    "top": int(top),
                    "right": int(right),
                    "bottom": int(bottom),
                    "width": int(right - left),
                    "height": int(bottom - top),
                },
                "tileSize": int(TILE_SIZE),
                "tileCount": int(input_meta["tile_count"]),
                "modelInputs": int(input_meta["model_inputs"]),
                "level1Probability": round(level1_prob, 6),
                "level2Probability": round(level2_prob, 6),
                "fusionProbability": round(fusion_prob, 6),
            })
    finally:
        if owns_slot:
            release_inference_slot()

    arr = np.asarray(fusion_scores, dtype=np.float32)
    used_k = max(1, min(requested_top_k, int(arr.shape[0])))
    topk_indices = np.argsort(-arr)[:used_k].tolist()
    fake_probability = float(arr[topk_indices].mean())
    runtime_contract = _runtime_decision_contract(
        _model_state.get("modelSha256"),
        max_tiles=requested_max_tiles,
        top_k=requested_top_k,
        chunk_size=requested_chunk_size,
    )
    decision_policy = model_decision_policy(
        model_sha256=_model_state.get("modelSha256"),
        runtime_contract=runtime_contract,
    )
    decision_ready = decision_policy["ready"] is True
    published_probability = (
        _calibrated_probability(fake_probability, decision_policy.get("probabilityCalibration"))
        if decision_ready else 0.5
    )
    final_label = (
        "AI生成图像" if published_probability >= decision_policy["aiThreshold"] else "真实图像"
    ) if decision_ready else "需人工复核"
    for item in chunk_results:
        item["selectedByTopK"] = item["index"] in topk_indices

    return {
        "model": "RealGuard v2 INT8 ONNX",
        "onnxPath": ONNX_PATH,
        "originalSize": resize_meta["originalSize"],
        "processedSize": resize_meta["processedSize"],
        "downsample": resize_meta,
        "parameters": {
            "chunkSize": int(actual_chunk_size),
            "requestedChunkSize": int(requested_chunk_size),
            "maxChunks": int(MAX_CHUNKS),
            "derivedChunkSize": int(derived_chunk_size),
            "maxTiles": int(requested_max_tiles),
            "effectiveTiles": int(effective_tiles),
            "tileSize": int(TILE_SIZE),
            "topK": int(requested_top_k),
            "usedTopK": int(used_k),
            "maxAnalysisSide": int(MAX_ANALYSIS_SIDE),
        },
        "processing": [
            (
                f"原图最长边超过 {MAX_ANALYSIS_SIDE} 像素，已按比例缩放至 "
                f"{image.width}x{image.height} 后切块。"
                if resize_meta["downsampled"]
                else f"原图最长边未超过 {MAX_ANALYSIS_SIDE} 像素，不进行整体缩放。"
            ),
            "每个 chunk 会生成 1 张全局视图，并按 tileSize 生成局部 tile 视图。",
            "所有视图进入 RealGuard v2 ONNX，输出 level1、level2 和 fusion 三组 logits。",
            "Top-K fusion 分数先按签名校准清单执行概率校准，再应用发布阈值。",
        ],
        "chunkCount": int(len(chunk_results)),
        "topKChunkIndices": [int(i) for i in topk_indices],
        "fakeProbability": round(published_probability, 6),
        "realProbability": round(1.0 - published_probability, 6),
        "rawModelScore": round(fake_probability, 6),
        "finalLabel": final_label,
        "modelDecision": {
            **decision_policy,
            "rawModelScore": round(fake_probability, 6),
            "publishedProbability": round(published_probability, 6),
            "finalLabel": final_label,
        },
        "chunks": chunk_results,
        "runtime": {
            "activeProvider": _model_state.get("activeProvider", "unknown"),
            "cudaDeviceId": _model_state.get("cudaDeviceId"),
            "modelRevision": _model_state.get("modelRevision", "unversioned"),
            "modelSha256": _model_state.get("modelSha256"),
            "deploymentCommit": _deployment_commit(),
            "runtimeContractSha256": decision_policy.get("runtimeContractSha256"),
            "queueWaitMs": round(queue_wait_ms, 2),
            "preprocessMs": round(preprocess_ms, 2),
            "inferenceMs": round(inference_ms, 2),
            "totalMs": round((time.perf_counter() - analysis_started) * 1000.0, 2),
        },
    }


def predict(img_path):
    return float(analyze_image(img_path)["fakeProbability"])


if EAGER_LOAD:
    initialize_model()



if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("usage: python inference_onnx.py /path/to/image")
    print(f"RealGuard v2 fake probability: {predict(sys.argv[1]):.6f}")
