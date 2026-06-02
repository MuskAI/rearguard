---
title: RealGuard Developer API
base_url: http://124.222.3.205/v2-api
skill_url: http://124.222.3.205/skills/realguard-forensics/SKILL.md
---

# RealGuard Developer API

This document describes the public RealGuard/Jianzhen V2 API for external agents, automation tools, and business integrations.

## Quick Start For Agents

Read the public skill first:

```text
http://124.222.3.205/skills/realguard-forensics/SKILL.md
```

One-sentence handoff:

```text
Use $realguard-forensics; read http://124.222.3.205/skills/realguard-forensics/SKILL.md; call POST http://124.222.3.205/v2-api/detect with multipart field file, then return verdict, confidence, evidence, modelVersion, cacheVersion, and reportId.
```

## Web Console

Use the in-site developer console to test API connectivity, upload files, inspect JSON responses, and verify latency:

```text
http://124.222.3.205/?page=developer
```

## Base URL

```text
http://124.222.3.205/v2-api
```

## Authentication

If access protection is enabled, send either header:

```http
X-Jianzhen-Token: <token>
Authorization: Bearer <token>
```

## Endpoints

### Health

```bash
curl -fsS http://124.222.3.205/v2-api/health
```

### Detect

```bash
curl -fsS -X POST http://124.222.3.205/v2-api/detect \
  -F "file=@/path/to/file" \
  -F "fileType=image"
```

`fileType` is optional. Supported values: `image`, `video`, `audio`, `document`.

## Code Examples

### JavaScript

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

### Python

```python
import requests

url = "http://124.222.3.205/v2-api/detect"
headers = {"X-Jianzhen-Token": token}  # optional
with open("/path/to/file.png", "rb") as f:
    response = requests.post(url, headers=headers, files={"file": f}, data={"fileType": "image"})
response.raise_for_status()
print(response.json().get("agentSummary") or response.json())
```

### Image Forensics

```bash
curl -fsS -X POST http://124.222.3.205/v2-api/forensics \
  -F "file=@/path/to/image"
```

### Provenance / C2PA

```bash
curl -fsS -X POST http://124.222.3.205/v2-api/provenance \
  -F "file=@/path/to/image"
```

### Report Download

```bash
curl -fsS http://124.222.3.205/v2-api/report/<reportId>/download -o report.html
```

## Important Response Fields

- `agentSummary`: Agent-oriented concise summary when available.
- `verdict`: Final forensic verdict.
- `confidence`: Confidence score from `0` to `1`.
- `reportId`: Report identifier for download and audit.
- `taskId`: Detection task identifier.
- `source`: Detection source, such as `vlm`, `mock`, or fallback chains.
- `modelVersion`: Model or rule version.
- `cacheVersion`: Analysis cache version.
- `cacheHit`: Whether the result came from cache.
- `explanation`: Human-readable evidence and reasons.
- `synthid`: SynthID evidence when applicable.
- `visibleWatermark`: Visible watermark evidence when applicable.

## Interpretation Rules

- Treat API results as forensic evidence, not absolute proof.
- Always cite `verdict`, `confidence`, `source`, `modelVersion`, `cacheVersion`, and `reportId`.
- If `source` is `mock`, `heuristic`, or another fallback, state that limitation.
- Preserve raw JSON and report IDs for auditability.

## Enterprise Integration Checklist

- Versioning: Store `modelVersion` and `cacheVersion` with every decision.
- Auditability: Persist raw JSON, `taskId`, `reportId`, file hash, caller, and timestamp.
- Error handling: Treat 4xx as request/configuration problems and 5xx as retryable service failures.
- Security: Keep tokens outside source code and use scoped tokens for automation.
- Human review: Route low-confidence or high-impact results to manual review.
- Reporting: Archive downloaded reports when decisions are used externally.
