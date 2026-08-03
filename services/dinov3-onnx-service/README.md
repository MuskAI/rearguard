# DINOv3 ViT-7B candidate service

This adapter exposes the packaged DINOv3 ONNX detector through the legacy
RealGuard model contract without copying or modifying the 13 GB model bundle.

## Server layout

- Model package: `/home/ymk/model_registry/dinov3_vit7b16_linear_onnx_fp16`
- Service source: `/home/ymk/services/dinov3-onnx-service`
- Local endpoint: `http://127.0.0.1:5071`
- Public-server tunnel endpoint: `http://127.0.0.1:15002`
- Admin model id: `dinov3-vit7b16-linear-fp16`

The two Titan Xp cards each have 12 GB VRAM. ONNX Runtime cannot shard this
graph across both cards, while the FP16 backbone alone is about 13 GB.
Production therefore runs this candidate with `CPUExecutionProvider`; moving
it to CUDA requires one GPU with at least 24 GB VRAM or a separately exported
model-parallel/quantized artifact. ONNX session construction has a large
temporary host-memory peak, so the service is isolated with a 64 GB soft limit
and an 80 GB hard limit.

The service returns a binary model label and raw fake score, but intentionally
marks the decision as `review_only` until an independently signed production
calibration manifest is available. Watermark, metadata, and provenance policy
remain owned by the public RealGuard application.
