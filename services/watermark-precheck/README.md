# Watermark Precheck Service

Detection-only service deployed on `10.1.20.66`. It uses the Apache-2.0
`wiltodelta/remove-ai-watermarks` `identify` and visible-mark registry APIs.
No watermark removal, inpainting, metadata stripping, or diffusion code is
called by this service.

## Pipeline

1. Read C2PA, TC260, EXIF/XMP, and embedded generation parameters from the
   untouched original file on the public backend.
2. Short-circuit locally when the original contains an explicit AI source type
   or high-confidence AI metadata. Invalid C2PA signatures are downgraded to a
   review-required `suspected_fake` result instead of being presented as
   cryptographically verified.
3. For files larger than 1.5 MB, create a 1536 px, high-quality scan copy and
   send only that copy to server 66 for visible-mark matching. The original is
   retained for provenance reporting and is never rewritten.
4. Detect known visible AI marks on server 66: Gemini, Doubao, Jimeng/Dreamina,
   and Samsung Galaxy AI. A provider-specific location gate and confidence
   threshold prevent arbitrary corner logos from becoming direct evidence.
5. Run the pinned YOLO11x detector as an internal candidate-region localizer.
   A YOLO box is retained only when it spatially overlaps a known AI-platform
   mark from the registry. Unmatched trademarks, site badges, and other logos
   are discarded and are never returned to the frontend or authenticity vote.
6. If a compact scan is non-decisive but the original contains a C2PA or
   metadata hint that needs deeper parsing, run one original-file metadata pass
   through the server 66 engine.
7. Skip the statistical model only for a decisive known visible mark, explicit
   AI C2PA source type, or high-confidence AI metadata. Otherwise return
   `modelRequired: true` and continue to the existing model.

Precedence is therefore:

```text
AI C2PA / high-confidence metadata
  -> known provider visible mark
  -> existing image model
  -> manual review for conflicts or low confidence
```

The precheck is fail-open for availability: a timeout or service outage never
creates an AI verdict; it records the failure and continues to the model.

## Public Watermark Contract

The web backends expose only confirmed AI-platform marks in `visibleWatermark`:

- `method=remove_ai_watermarks_registry`, `evidenceRole=provenance`: a known
  AI-platform mark. A decisive match may contribute to provenance probability.
  `localizationConfirmed=true` indicates that an overlapping YOLO candidate
  independently corroborated the region.
- Raw `yolo11x_object_detection` candidates are internal-only. They are not
  included in `visibleWatermark.hits`, reports, or frontend annotations.

`visibleWatermark.detector.engines` exposes availability, model/version, hit
count, and role for both engines. The YOLO engine count means corroborated
platform regions, not all generic Logo candidates.

The service binds to `127.0.0.1:5066`. Production reaches it through a
loopback-only reverse SSH tunnel at `127.0.0.1:15066` on the public web server.
The tunnel key is restricted with `permitlisten="127.0.0.1:15066"` on the
public host and cannot open an interactive shell.

## Configuration

```dotenv
WATERMARK_PRECHECK_TOKEN=replace-with-a-long-random-secret
WATERMARK_PRECHECK_MAX_BYTES=31457280
YOLO_WATERMARK_URL=http://127.0.0.1:5067/v1/detect
YOLO_WATERMARK_HEALTH_URL=http://127.0.0.1:5067/health
YOLO_WATERMARK_TOKEN=replace-with-the-yolo-service-token
YOLO_WATERMARK_TIMEOUT_SECONDS=20
```

Public backend transport settings:

```dotenv
JIANZHEN_PROVENANCE_PRECHECK_URL=http://127.0.0.1:15066
JIANZHEN_PROVENANCE_PRECHECK_TOKEN=replace-with-the-shared-precheck-token
JIANZHEN_PROVENANCE_PRECHECK_TIMEOUT=8
JIANZHEN_PROVENANCE_PRECHECK_ORIGINAL_TIMEOUT=20
JIANZHEN_PROVENANCE_PRECHECK_DIRECT_UPLOAD_MAX_BYTES=1572864
JIANZHEN_PROVENANCE_PRECHECK_SCAN_MAX_SIDE=1536
JIANZHEN_PROVENANCE_PRECHECK_SCAN_QUALITY=94
```

## Local Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
WATERMARK_PRECHECK_TOKEN=dev-token .venv/bin/flask --app yolo_adapter run --port 5066
```
