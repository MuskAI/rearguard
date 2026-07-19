# RealGuard GPU inference

This directory tracks the production RealGuard v2 ONNX inference module and
the systemd GPU configuration used on the `10.1.20.66` detection server.

The systemd drop-in exposes only physical GPU 1 to the detection process. It
loads one ONNX Runtime CUDA session during process startup, warms the dynamic
26-view input shape, and allows up to two in-flight requests on physical GPU 1.
The independent YOLO watermark service remains on physical GPU 0, so visible
watermark localization does not consume the main model's GPU queue.

Images whose longest side exceeds 2048 pixels are downsampled proportionally
before chunk generation. The response preserves `originalSize`, adds
`processedSize` and `downsample`. Visible-watermark branches share one
EXIF-normalized display image and return `coordinateSpace=display_normalized_v1`
so browser overlays, registry matches, and YOLO boxes use the same coordinates.
Uploads above 24 million source pixels are rejected before RGB decoding. A
request that waits more than 20 seconds for a GPU slot receives HTTP 429 with
a `Retry-After` header instead of occupying memory indefinitely.

## Deployment targets

- `inference_onnx.py` -> `/home/ymk/RealGuard/AIGC_image_detection_system/imagedetection/Agent/tools/AIGC_Detection/inference_onnx.py`
- `image_preprocessing.py` -> `/home/ymk/RealGuard/AIGC_image_detection_system/imagedetection/Agent/tools/AIGC_Detection/image_preprocessing.py`
- `model_decision_policy.py` -> `/home/ymk/RealGuard/AIGC_image_detection_system/imagedetection/Agent/tools/AIGC_Detection/model_decision_policy.py`
- `remote_inference.py` -> `/home/ymk/RealGuard/AIGC_image_detection_system/imagedetection/views/remote_inference.py`
- `realguard-detection.service` -> `/etc/systemd/system/realguard-detection.service`
- `realguard-detection-gpu.conf` -> `/etc/systemd/system/realguard-detection.service.d/gpu.conf`
- `realguard-detection-shared-precheck.conf` -> `/etc/systemd/system/realguard-detection.service.d/shared-precheck.conf`
- `realguard-web-tunnel.service` -> `/etc/systemd/system/realguard-web-tunnel.service`
- `public-detector-remote.conf` -> `/etc/systemd/system/realguard-detector-backend.service.d/remote.conf` on the public server

Register `model_inference_blueprint` in the detection server app factory. The
public detector calls `/internal/model/predict` through a reverse SSH tunnel,
while user history and metadata remain owned by the public application server.

The untracked `/etc/realguard/model-inference.env` file must contain the same
`REALGUARD_MODEL_INTERNAL_TOKEN` and a separate 64-hex
`REALGUARD_MODEL_RESPONSE_HMAC_KEY` on both servers. Give the response key a
stable `REALGUARD_MODEL_RESPONSE_HMAC_KEY_ID`; the public server may retain old
verification keys in `REALGUARD_MODEL_RESPONSE_HMAC_KEYS_JSON`. The HMAC key is never sent
with a request; it binds each GPU response to a fresh nonce, the uploaded image,
and the complete response body. On the public server the file also defines
`REALGUARD_REMOTE_INFERENCE_URL` as the tunnel-local internal endpoint.
Database credentials belong in `/etc/realguard/detection-db.env`, owned by
`root:root` with mode `0600`; they must never be embedded in the world-readable
systemd unit.

Rotate without downtime in this order: add the next response key to the public
verification keyring, deploy/restart the public verifier, then switch the GPU
active key and key ID. Remove the retired key only after the maximum response
age has elapsed. For request-token rotation, place the old token temporarily in
`REALGUARD_MODEL_INTERNAL_TOKEN_PREVIOUS` on the GPU, switch public clients to
the new active token, then remove the previous token. Never leave a previous
token configured indefinitely.
The production service uses one Gunicorn worker with four threads. Keeping a
single worker prevents duplicate ONNX sessions from consuming GPU memory, while
the inference semaphore controls concurrent CUDA execution inside that worker.

## Commercial decision gate

The ONNX softmax is retained as `rawModelScore` for diagnostics, but is not
published as an AI probability until an independent held-out calibration has
passed the configured FPR/FNR gates. Without a complete calibration record the
service returns `finalLabel=需人工复核`, `fakeProbability=0.5`, and
`modelDecision.mode=review_only`. Confirmed AI-platform provenance can still
form an independent decisive result.

The calibration record is an Ed25519-signed JSON manifest at
`/etc/realguard/model-calibration.json`. The server only receives the public
key at `/etc/realguard/model-calibration-ed25519.pub`; the private signing key
must remain in the independent evaluation environment. Both files must be
root-owned and not writable by group or other users.

The manifest uses schema `cn.huijian.model-calibration-v2` and binds all of the
following fields under one signature:

- ONNX SHA-256 and dataset SHA-256
- the exact runtime contract returned by the protected model health endpoint,
  including preprocessing, class mapping, chunking and Top-K aggregation
- runtime-contract and preprocessing SHA-256 values
- evaluation code revision, sample counts, FPR/FNR, threshold and expiry

Supplying a threshold or environment variables alone never opens automatic
verdicts. A missing, expired, tampered or runtime-mismatched manifest keeps the
service in `review_only`. Custom chunk/Top-K parameters also require a matching
calibration contract.

`runtime.lock` records the full production Python package snapshot. Activation
compares every package and runs `pip check` before stopping the current service.
The watermark and YOLO services carry equivalent locks in their directories.

The public detector still performs local metadata extraction before calling
the GPU model and therefore requires ExifTool:

```bash
sudo apt-get install libimage-exiftool-perl
```

## Shared-upload precheck

`remote_inference.py` starts the local visible-watermark precheck while the
resident GPU model processes the same request body. The compact precheck result
is returned as `visibleWatermarkPrecheck`, so the public Swarm orchestrator does
not upload the image to the private network a second time.

The watermark service environment file is loaded by
`realguard-detection-shared-precheck.conf`. Keep the precheck URL loopback-only:

```text
REALGUARD_MODEL_VISIBLE_PRECHECK_URL=http://127.0.0.1:5066/v1/precheck
REALGUARD_MODEL_VISIBLE_PRECHECK_TIMEOUT=12
REALGUARD_MODEL_VISIBLE_PRECHECK_WORKERS=4
```

The precheck service runs provenance reporting, known-platform matching, and
YOLO localization concurrently. Its production unit uses four request threads;
the YOLO unit uses two single-threaded worker processes on physical GPU 0.
Deploy `realguard-watermark-precheck-yolo.conf` to the precheck unit's
`yolo.conf` drop-in so the server-specific adapter override keeps the same
four-thread limit.

On the public server, the persisted `v1-legacy-tunnel` model route must use
`http://127.0.0.1:15001/image` with health endpoint
`http://127.0.0.1:15001/health`. A remote endpoint saved in
`model_registry.json` overrides the environment default and bypasses shared
evidence reuse.

Swarm starts the V2 network expert after an upload-size-aware delay to avoid
making two large uploads compete for the same private-network link. Defaults:

```text
REALGUARD_SWARM_V2_STAGGER_BYTES_PER_SECOND=800000
REALGUARD_SWARM_V2_MAX_STAGGER_SECONDS=8
```

Set either value only after an end-to-end benchmark with production-size
images. Starting both uploads immediately increased the main model latency in
the current deployment.

Deploy these files as a versioned release with compile, CUDA warmup, a real
prediction probe, and automatic rollback:

```bash
./scripts/deploy_detection_service.sh
```

The startup journal must contain `CUDAExecutionProvider` before the service is
accepted as healthy. With `REALGUARD_V2_REQUIRE_CUDA=1`, the service refuses to
start instead of silently falling back to CPU.
