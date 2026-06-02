---
title: RealGuard Developer API
base_url: http://124.222.3.205/v2-api
v1_base_url: http://124.222.3.205/api/developer/v1
skill_url: http://124.222.3.205/skills/realguard-forensics/SKILL.md
console_url: http://124.222.3.205/?page=developer
---

# RealGuard Developer API

RealGuard Developer API is the public integration surface for external agents, automation tools, and business systems that need AI-generated content detection, image forensics, content credential checks, and report downloads.

## Getting Started

V2 Base URL:

```text
http://124.222.3.205/v2-api
```

V1 Base URL:

```text
http://124.222.3.205/api/developer/v1
```

Public Skill:

```text
http://124.222.3.205/skills/realguard-forensics/SKILL.md
```

Online API Console:

```text
http://124.222.3.205/?page=developer
```

One-sentence handoff for OpenClaw or another agent:

```text
Use $realguard-forensics; read http://124.222.3.205/skills/realguard-forensics/SKILL.md; prefer V2 by calling POST http://124.222.3.205/v2-api/detect with multipart field file and X-RealGuard-Key for multimodal forensic output and tokenUsage, or use V1 by calling POST http://124.222.3.205/api/developer/v1/detect with multipart field file for the legacy image model; then return verdict, confidence, evidence, model/source, usage or call-count note, and report/item id.
```

The public Skill is required because external agents cannot access your local repository path. They need a stable public URL that documents the API, required fields, output constraints, report fields, and interpretation boundaries.

## Authentication

Register or log in on the Developer Console, then create a personal API Key in the **My API Key** section:

```text
http://124.222.3.205/?page=developer
```

Use that key for developer API calls:

```http
X-RealGuard-Key: rg_sk_xxx
Authorization: Bearer rg_sk_xxx
```

Full API Keys are shown only once when created. The platform stores only a hash, a preview, status, and usage timestamps. Revoked keys are rejected immediately by protected developer endpoints.

`X-Jianzhen-Token` is reserved for internal admin endpoints such as `/admin/health`, `/history`, and `/metrics`. Do not distribute it to external developers or agents.

### API Key lifecycle

The web platform exposes authenticated key-management endpoints for logged-in users:

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/developer/keys` | List current user's key previews, status, scopes, created time, and last-used time. |
| `POST` | `/api/developer/keys` | Create a new key. The full `apiKey` is returned only once. |
| `DELETE` | `/api/developer/keys/{keyId}` | Revoke one active key owned by the current user. |
| `GET` | `/api/developer/usage?days=30` | Show the current user's V1/V2 call count, token usage, cache hits, and endpoint/model breakdown. |

External agents should never call these management endpoints directly. They should receive a generated `rg_sk_...` key from the account owner and use it only in API requests.

Usage is tracked for cost control and auditability. V1 and V2 both count calls. V2 also reports prompt, completion, and total token usage. Cache hits count as requests but consume `0` tokens because the model is not called again.

## API Reference

V2 endpoints below are relative to:

```text
http://124.222.3.205/v2-api
```

V1 image endpoints are relative to:

```text
http://124.222.3.205/api/developer/v1
```

### GET /health

Returns public service availability, coarse capability status, upload limit, and access-protection status. It does not expose internal paths or calibration thresholds.

```bash
curl -fsS http://124.222.3.205/v2-api/health
```

### GET /admin/health

Returns detailed diagnostics. This endpoint is protected when `JIANZHEN_ACCESS_TOKEN` is configured.

```bash
curl -fsS http://124.222.3.205/v2-api/admin/health \
  -H "X-Jianzhen-Token: <token>"
```

### POST /detect

V2 core detection endpoint. Upload a file and receive a forensic verdict, confidence score, evidence summary, model version, cache version, token usage, task ID, and report ID.

Request content type:

```text
multipart/form-data
```

Request parameters:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `file` | File | yes | File to analyze. Supports images, videos, audio, and documents. |
| `fileType` | string | no | Type hint. Supported values: `image`, `video`, `audio`, `document`. |

Default upload limit: `25MB`.

Example:

```bash
curl -fsS -X POST http://124.222.3.205/v2-api/detect \
  -H "X-RealGuard-Key: <your-api-key>" \
  -F "file=@/path/to/file.png" \
  -F "fileType=image"
```

Important response fields:

| Field | Description |
| --- | --- |
| `agentSummary` | Agent-oriented concise summary when available. |
| `verdict` | Final forensic verdict, such as `real`, `suspected`, `likely_ai_generated`, or `unknown`. |
| `confidence` | Confidence score from `0` to `1`. |
| `taskId` | Detection task identifier. |
| `reportId` | Report identifier for download and audit. |
| `source` | Detection source, such as `vlm`, `mock`, `heuristic`, or another fallback chain. |
| `modelVersion` | Model or rule version. |
| `cacheVersion` | Analysis cache version. |
| `cacheHit` | Whether the result came from cache. |
| `tokenUsage` | Prompt, completion, and total token usage for this request. Cache hits return zero token usage. |
| `explanation` | Human-readable evidence and reasons. |
| `synthid` | SynthID evidence when applicable. |
| `visibleWatermark` | Visible watermark evidence when applicable. |

Example response:

```json
{
  "taskId": "rj-20260602-0001",
  "reportId": "RJ-RPT-20260602-0001",
  "verdict": "real",
  "confidence": 0.95,
  "source": "vlm",
  "modelVersion": "qwen3-vl-flash",
  "cacheVersion": "v6-low-ela-weight",
  "tokenUsage": {
    "promptTokens": 1240,
    "completionTokens": 210,
    "totalTokens": 1450
  }
}
```

### POST /api/developer/v1/detect

V1 image detection endpoint. Use this when an agent or business workflow must reuse the legacy RealGuard image model. It accepts multipart image upload through the `file` field and requires the same `X-RealGuard-Key`.

Request content type:

```text
multipart/form-data
```

Request parameters:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `file` | File | yes | Image file to analyze with the V1 image model. |

Example:

```bash
curl -fsS -X POST http://124.222.3.205/api/developer/v1/detect \
  -H "X-RealGuard-Key: <your-api-key>" \
  -F "file=@/path/to/image.png"
```

Important response fields:

| Field | Description |
| --- | --- |
| `result.final_label` | V1 final image label, such as AI-generated or real image. |
| `result.probability` | V1 confidence probability. |
| `result.confidence` | V1 confidence level. |
| `result.visual_issues` | Image visual issues or suspicious evidence when available. |
| `result.itemid` | V1 history/report item ID. |

V1 records API-key call count in the Developer Console. It does not return `tokenUsage`.

### POST /forensics

Runs image-focused forensic analysis and returns explainable evidence such as ELA, noise consistency, frequency-domain anomalies, edge anomalies, and summary signals.

```bash
curl -fsS -X POST http://124.222.3.205/v2-api/forensics \
  -H "X-RealGuard-Key: <your-api-key>" \
  -F "file=@/path/to/image.png"
```

### POST /provenance

Checks C2PA, SynthID, visible watermark, and related content credential signals. Use this together with `/detect` when the final product needs both model-based and credential-based evidence.

```bash
curl -fsS -X POST http://124.222.3.205/v2-api/provenance \
  -H "X-RealGuard-Key: <your-api-key>" \
  -F "file=@/path/to/image.png"
```

### GET /report/{reportId}/download

Downloads the report returned by `/detect`.

Path parameters:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `reportId` | string | yes | Report identifier from the detection response, for example `RJ-RPT-20260602-0001`. |

Example:

```bash
curl -fsS http://124.222.3.205/v2-api/report/<reportId>/download \
  -H "X-RealGuard-Key: <your-api-key>" \
  -o realguard-report.html
```

## Errors

Agents should not hide API errors. Treat 4xx as request or permission problems. Treat 5xx as retryable service failures only after logging context.

| HTTP | Name | Handling |
| --- | --- | --- |
| `400` | Bad Request | Missing `file`, invalid `fileType`, or malformed multipart request. |
| `401` | Unauthorized | API Key is missing, invalid, or revoked. |
| `403` | Forbidden | The API Key cannot access the requested report or resource. |
| `413` | Payload Too Large | File exceeds service limits. Compress it or use an async/chunked flow. |
| `422` | Unprocessable Entity | File type cannot be recognized or is unsupported by the current chain. |
| `500` | Internal Server Error | Server-side analysis failed. Log context, retry if appropriate, or route to manual review. |

## Code Examples

### JavaScript Fetch

```js
const form = new FormData();
form.append("file", fileInput.files[0]);
form.append("fileType", "image");

const res = await fetch("http://124.222.3.205/v2-api/detect", {
  method: "POST",
  headers: { "X-RealGuard-Key": apiKey },
  body: form
});

const data = await res.json();
console.log(data.agentSummary || data);
```

### Python Requests

```python
import requests

url = "http://124.222.3.205/v2-api/detect"
headers = {"X-RealGuard-Key": api_key}

with open("/path/to/file.png", "rb") as f:
    response = requests.post(
        url,
        headers=headers,
        files={"file": f},
        data={"fileType": "image"},
    )

response.raise_for_status()
print(response.json().get("agentSummary") or response.json())
```

### RealGuard CLI

```bash
python3 scripts/realguard_cli.py detect /path/to/file \
  --base-url http://124.222.3.205 \
  --api-prefix /v2-api \
  --token <your-api-key> \
  --pretty
```

### V1 Image Detect

```bash
curl -fsS -X POST http://124.222.3.205/api/developer/v1/detect \
  -H "X-RealGuard-Key: <your-api-key>" \
  -F "file=@/path/to/image.png"
```

## Agent Output Rules

External agents should include the fields for the pipeline they called.

For V2:

- `verdict`
- `confidence`
- `evidence` or `explanation`
- `source`
- `modelVersion`
- `cacheVersion`
- `taskId`
- `reportId`
- `tokenUsage`

For V1:

- `result.final_label`
- `result.probability`
- `result.confidence`
- `result.visual_issues`
- `result.itemid`

Do not output only "real" or "fake". Always include the evidence and uncertainty.

## Interpretation Rules

- Treat API results as forensic evidence, not absolute proof.
- Always cite the version/source fields returned by the selected pipeline. For V2, cite `verdict`, `confidence`, `source`, `modelVersion`, `cacheVersion`, and `reportId`. For V1, cite `result.final_label`, probability/confidence, evidence, and `result.itemid`.
- If `source` is `mock`, `heuristic`, or another fallback chain, state that limitation.
- Preserve raw JSON and report IDs for auditability.
- Route low-confidence or high-impact results to human review.

## Enterprise Integration Checklist

- Versioning: store `modelVersion` and `cacheVersion` with every decision.
- Auditability: persist raw JSON, `taskId`, `reportId`, file hash, caller, and timestamp.
- Error handling: surface 4xx request problems and retry 5xx only with bounded retry logic.
- Security: keep tokens outside source code and use scoped tokens for automation.
- Human review: manually review low-confidence, high-risk, or externally disputed results.
- Reporting: archive downloaded reports when decisions are used outside the product.
