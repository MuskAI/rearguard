# Standalone Watermark Review Lab

This is a separate manual-review entry for validating explicit watermark
detection before it is exposed in `rrreal.cn`. It proxies uploads to the
existing 66-server precheck endpoint and keeps the upstream token server-side.

## Entry

- Service: `realguard-watermark-lab.service`
- Port: `5070`
- Route: `/`
- API proxy: `POST /api/analyze` for images
- Video API: `POST /api/analyze-video` with `file` and optional `sample_count` (3-24)

The UI overlays each fused hit on the original image and shows type, platform,
confidence, OCR confidence, retrieval similarity, position and fusion reasons.
The pipeline monitor consumes `watermark_pipeline_trace_v1` and exposes eight
clickable stages: decode/normalize, metadata, registry, YOLO localization, OCR,
FAISS retrieval, rule fusion, and final verdict. Each stage includes actual
elapsed time and structured input/output. Retrieval details include the
platform threshold, inter-platform margin, rejection reason, reference source,
and Top-K matches.
For videos, it samples frames uniformly, sends up to four frames in parallel to
the existing image watermark pipeline, and shows each frame's timestamp,
verdict, source and bounding boxes before producing a persistence-aware summary.
Selecting a frame switches the monitor to that frame's pipeline trace.
It does not write a detection record and does not alter the uploaded media.
