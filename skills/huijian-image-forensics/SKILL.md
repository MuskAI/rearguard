---
name: huijian-image-forensics
description: Submit local images to the authenticated Huijian AI image-forensics API, run fast or Swarm analysis, poll asynchronous jobs, summarize reproducible evidence, and download PDF reports. Use when an agent is asked to determine whether a JPEG, PNG, WebP, BMP, or GIF is likely real or AI-generated, inspect watermark and provenance evidence, compare fast versus multi-source review, or produce a Huijian AI forensic report.
---

# Huijian AI Image Forensics

Use the bundled client rather than rewriting multipart upload and polling logic. Treat the service result as an aid to review, not as proof of authorship or intent.

## Quick Start

Trigger this Skill when the user asks in ordinary language, for example:

- `请鉴别 /absolute/path/photo.jpg 是真图还是 AI 生成图。`
- `这张图为什么被判断为假？请用通俗的话解释证据。`
- `用 Swarm 模式复核这张图片，并下载 PDF 报告。`

When the user supplies an image path, first confirm that the file exists and is a supported image. Use `fast` unless the user asks for Swarm or the escalation rules below apply. Run the bundled client, then answer with the conclusion first and the strongest evidence second.

## Configure

Require an API key in the environment:

```bash
export HUIJIAN_API_KEY="rg_sk_..."
export HUIJIAN_API_BASE_URL="https://www.rrreal.cn"  # optional
```

Never print, log, commit, or include the full API key in an answer. Ask the user to configure `HUIJIAN_API_KEY` when it is missing.

## Choose A Mode

- Use `fast` by default for routine screening. It runs the primary detector and watermark checks.
- Use `swarm` when the user explicitly requests deeper review, the fast result is disputed or near the decision boundary, or independent evidence sources are important.
- Do not silently rerun Swarm after a fast result. Explain the added latency and quota cost before escalating unless the user already requested deep review.

## Run A Detection

From the skill directory, run:

```bash
python3 scripts/huijian_forensics.py detect /absolute/path/image.jpg --mode fast
```

For Swarm review and a PDF report:

```bash
python3 scripts/huijian_forensics.py detect /absolute/path/image.jpg \
  --mode swarm --report /absolute/path/huijian-report.pdf
```

Use `--no-wait` only when the caller wants the task ID immediately. Use `--idempotency-key` when retrying the same logical request after a network interruption.

Before the first real request, verify the client is available:

```bash
python3 scripts/huijian_forensics.py --help
```

## Interpret The Result

1. Start with `Real` or `Fake`, then state the fake probability and confidence in plain language.
2. Summarize concrete evidence separately: visible watermark hits and boxes, metadata or provenance signals, visual findings, and Swarm agreement or disagreement.
3. Preserve uncertainty. A low-confidence or boundary score requires manual review; absent metadata is not evidence of manipulation.
4. When a visible watermark is detected, distinguish a generic visible watermark from a provider-specific AI watermark. Do not infer the provider unless the API evidence identifies it.
5. Mention that only successful tasks consume quota. Do not expose internal IDs unless they help the user retrieve the report.

Avoid unexplained model jargon such as “linear probe”, “logits”, or “feature embedding”. If a technical term is necessary, immediately translate it into what it means for this image. Never invent visual defects that are absent from the returned evidence.

Read [references/api-contract.md](references/api-contract.md) when debugging authentication, status codes, response fields, or direct API integration.

## Failure Handling

- `401`: the key is missing, invalid, expired, or revoked. Check the environment and key status.
- `402`: the shared account quota or balance is unavailable. Do not retry repeatedly.
- `403`: the key lacks the selected mode scope or the caller IP is outside its allowlist.
- `409`: an idempotency key was reused for different content, or a report was requested before completion.
- `429`: honor `Retry-After` and retry status polling with backoff; do not resubmit the image.
- `5xx`: report that the service failed. The unsuccessful task should release its reserved quota.
