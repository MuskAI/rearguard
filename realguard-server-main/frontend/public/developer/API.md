---
title: RealGuard Developer API
base_url: http://124.222.3.205/v2-api
skill_url: http://124.222.3.205/skills/realguard-forensics/SKILL.md
console_url: http://124.222.3.205/?page=developer
---

# RealGuard Developer API

RealGuard Developer API is the public integration surface for external agents, automation tools, and business systems that need AI-generated content detection, image forensics, content credential checks, and report downloads.

## Getting Started

Base URL:

```text
http://124.222.3.205/v2-api
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
Use $realguard-forensics; read http://124.222.3.205/skills/realguard-forensics/SKILL.md; call POST http://124.222.3.205/v2-api/detect with multipart field file, or run python3 scripts/realguard_cli.py detect <file> --base-url http://124.222.3.205 --api-prefix /v2-api --pretty if the repo CLI is available; then return a concise verdict with confidence, evidence, model version, cache version, and report id.
```

The public Skill is required because external agents cannot access your local repository path. They need a stable public URL that documents the API, required fields, output constraints, report fields, and interpretation boundaries.

## Authentication

If access protection is enabled, send either header:

```http
X-Jianzhen-Token: <token>
Authorization: Bearer <token>
```

Keep tokens out of frontend source code and public repositories. Automation agents should use scoped tokens and log caller, timestamp, file hash, and report ID.

## API Reference

All endpoints below are relative to:

```text
http://124.222.3.205/v2-api
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

Core detection endpoint. Upload a file and receive a forensic verdict, confidence score, evidence summary, model version, cache version, task ID, and report ID.

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
  -H "X-Jianzhen-Token: <token>" \
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
  "cacheVersion": "v6-low-ela-weight"
}
```

### POST /forensics

Runs image-focused forensic analysis and returns explainable evidence such as ELA, noise consistency, frequency-domain anomalies, edge anomalies, and summary signals.

```bash
curl -fsS -X POST http://124.222.3.205/v2-api/forensics \
  -F "file=@/path/to/image.png"
```

### POST /provenance

Checks C2PA, SynthID, visible watermark, and related content credential signals. Use this together with `/detect` when the final product needs both model-based and credential-based evidence.

```bash
curl -fsS -X POST http://124.222.3.205/v2-api/provenance \
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
  -o realguard-report.html
```

## Errors

Agents should not hide API errors. Treat 4xx as request or permission problems. Treat 5xx as retryable service failures only after logging context.

| HTTP | Name | Handling |
| --- | --- | --- |
| `400` | Bad Request | Missing `file`, invalid `fileType`, or malformed multipart request. |
| `401` | Unauthorized | Access protection is enabled and the token is missing or invalid. |
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
  headers: { "X-Jianzhen-Token": token },
  body: form
});

const data = await res.json();
console.log(data.agentSummary || data);
```

### Python Requests

```python
import requests

url = "http://124.222.3.205/v2-api/detect"
headers = {"X-Jianzhen-Token": token}  # optional

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
  --pretty
```

## Agent Output Rules

External agents should include:

- `verdict`
- `confidence`
- `evidence` or `explanation`
- `source`
- `modelVersion`
- `cacheVersion`
- `taskId`
- `reportId`

Do not output only "real" or "fake". Always include the evidence and uncertainty.

## Interpretation Rules

- Treat API results as forensic evidence, not absolute proof.
- Always cite `verdict`, `confidence`, `source`, `modelVersion`, `cacheVersion`, and `reportId`.
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
