# Huijian AI Image API Contract

## Endpoints

Base URL defaults to `https://www.rrreal.cn`.

- `POST /api/openapi/v1/image-detections`
- `GET /api/openapi/v1/image-detections/{task_id}`
- `GET /api/openapi/v1/image-detections/{task_id}/report`

Authenticate every request with `Authorization: Bearer <API_KEY>`.

## Create Request

Send `multipart/form-data` with:

- `image`: JPEG, PNG, WebP, BMP, or GIF, up to 25 MB.
- `mode`: `fast` or `swarm`.

Optionally send an `Idempotency-Key` header of at most 128 characters. Reusing it with the same account, mode, and exact file returns the original task. Reusing it with different input returns `409`.

## Task Lifecycle

The create endpoint normally returns `202` and a task object. Poll `links.self` until:

- `success`: result is available; quota reservation is settled.
- `failed`: model execution did not deliver a result; quota reservation is released.
- `rejected`: the task was not dispatched because quota or billing was unavailable.

Intermediate states are `queued` and `running`. Billing states are `reserved`, `settled`, and `released`.

## Result Fields

The `result` object can include:

- `final_label`: human-readable conclusion.
- `probability`: probability-like fake score in the `[0, 1]` range.
- `detector_probability`: primary detector score before evidence fusion.
- `confidence`: calibrated confidence label.
- `explanation`: evidence-aware explanation.
- `visual_issues`: reviewable visual findings.
- `visibleWatermark`: detection status, confidence, provider evidence, and normalized boxes.
- `probabilityModel`: evidence-fusion details.
- `swarm`: expert counts, consensus, disagreement, and public expert summaries in Swarm mode.

The service may add fields without a version change. Ignore unknown fields.

## Quota And Ownership

All API keys under one login account share the account quota and ledger. Rotating or recreating a key does not reset free calls. Only successful tasks settle one detection call. A new active key under the same account can retrieve prior API tasks and reports; keys from another account receive `404`.
