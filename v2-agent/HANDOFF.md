# 鉴真 · AI 鉴伪智能体 — 部署交接文档

> 给部署方（Codex / 运维）的完整说明。目标：把本项目作为一个 Web 服务跑起来。

## 1. 这是什么

「鉴真」是一个 AI 鉴伪 / 内容取证 Web 应用，两条能力线：

- **取证推断**：上传图像/视频/音频/文档 → 判断是否 AI 生成、深度伪造、篡改。
  图像/文本走真实视觉语言模型（阿里云 DashScope `qwen3-vl-flash`），并提供 7 项可解释性
  取证可视化（ELA / 噪声 / 频域 / 光照梯度 / 光照一致性 / 多次 JPEG 压缩曲线）。
  视频/音频及模型不可用时回退到确定性 Mock。
- **凭证验真**：读取并验证图片内嵌的 **C2PA 内容凭证**（对标 OpenAI Verify 的 C2PA 部分），
  报告生成器、签发者、签名校验、编辑历史、是否声明 AI 生成。SynthID 为 Google 专有水印，未实现（如实标注）。
- **报告导出**：可按报告号导出自包含 HTML 鉴定报告，保留结论、维度评分、局部区域和水印辅助证据；若前端已执行取证分析或 C2PA 验证，还会一并写入报告。

## 2. 架构 & 技术栈

```
浏览器 ──> 前端(nginx 静态托管, 80) ──/api──> 后端(FastAPI, 8848) ──> DashScope VLM API
```

- 后端：Python ≥3.10（镜像用 3.12）、FastAPI + uvicorn、uv 管理依赖。
  关键库：openai（兼容模式调 DashScope）、pillow、numpy、matplotlib、c2pa-python。
- 前端：React + TypeScript + Vite + TailwindCSS，构建为静态文件，nginx 托管并反代 `/api`。
- 存储：**无数据库**。检测历史存后端进程内存，重启即清空（见第 6 节注意事项）。

## 3. 目录结构

```
lingjian/
├── docker-compose.yml          # 一键部署（推荐）
├── HANDOFF.md                  # 本文件
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml / uv.lock
│   ├── .env.example            # 复制为 .env 并填 key
│   └── app/
│       ├── main.py             # FastAPI 入口与路由
│       ├── detector.py         # VLM 检测 + Mock 回退
│       ├── forensics.py        # ELA/噪声/频域等取证可视化
│       └── provenance.py       # C2PA 内容凭证验证
└── frontend/
    ├── Dockerfile
    ├── nginx.conf              # 静态托管 + /api 反代到 backend:8848
    ├── package.json / package-lock.json
    └── src/ ...
```

## 4. 环境变量（后端）

放在 `backend/.env`（compose 通过 `env_file` 注入）。

| 变量 | 必填 | 说明 | 默认 / 示例 |
|------|------|------|------|
| `DASHSCOPE_API_KEY` | 强烈建议 | 阿里云 DashScope API Key。**缺失或无效时，图像/可提取正文文档检测自动回退 Mock**（C2PA 验证不依赖它，照常工作）。 | `sk-xxxxxxxx` |
| `DASHSCOPE_BASE_URL` | 否 | OpenAI 兼容端点 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `VLM_MODEL` | 否 | 视觉语言模型名 | `qwen3-vl-flash` |
| `JIANZHEN_ACCESS_TOKEN` | 否 | 配置后，历史、报告与监控接口需要访问令牌。 | `change-me` |
| `SYNTHID_ENABLED` | 否 | 是否启用 Gemini SynthID 水印取证增强。只调用检测逻辑，不调用去水印/绕过逻辑。 | `false` |
| `SYNTHID_REPO_PATH` | 否 | `reverse-SynthID` 仓库部署路径。 | `/opt/reverse-SynthID` |
| `SYNTHID_CODEBOOK_PATH` | 否 | V4 codebook 路径。 | `/opt/reverse-SynthID/artifacts/spectral_codebook_v4.npz` |
| `SYNTHID_MODEL_PROFILE` | 否 | 使用的 SynthID 模型配置。 | `gemini-3.1-flash-image-preview` |

`backend/.env` 内容模板：

```env
DASHSCOPE_API_KEY=在此填入你的_DashScope_Key
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
VLM_MODEL=qwen3-vl-flash
JIANZHEN_ACCESS_TOKEN=
SYNTHID_ENABLED=false
SYNTHID_REPO_PATH=/opt/reverse-SynthID
SYNTHID_CODEBOOK_PATH=/opt/reverse-SynthID/artifacts/spectral_codebook_v4.npz
SYNTHID_MODEL_PROFILE=gemini-3.1-flash-image-preview
```

> 安全：`.env` 不要提交进仓库 / 不要打进分发包。仓库已 gitignore。

## 5. 部署

### 方式 A：Docker Compose（推荐）

```bash
cd lingjian
cp backend/.env.example backend/.env     # 编辑填入 DASHSCOPE_API_KEY
docker compose up -d --build
# 访问 http://<服务器IP>:8080
```

- 前端容器对外暴露 `8080:80`（改 compose 里的 ports 即可换端口，如 `80:80`）。
- 后端容器只在内网暴露 `8848`，由前端 nginx 反代 `/api` 访问，不直接对外。
- 查看日志：`docker compose logs -f backend` / `frontend`。
- 停止：`docker compose down`。

### 方式 B：裸机 / 分离部署

**后端**
```bash
cd backend
cp .env.example .env   # 填 key
uv sync --frozen --no-dev
uv run uvicorn app.main:app --host 0.0.0.0 --port 8848 --workers 1
```
**前端**
```bash
cd frontend
npm ci
npm run build          # 产物在 dist/
# 用 nginx/任意静态服务器托管 dist/，并把 /api 反代到后端 8848（参考 frontend/nginx.conf）
```

> 反代是生产必需：Vite 的 `/api` 代理只在 `npm run dev` 下生效，构建产物不含代理。

## 6. 生产注意事项（重要）

1. **单机 SQLite、谨慎多实例**：后端使用 `JIANZHEN_DATA_DIR/jianzhen-v2.sqlite3` 保存历史、缓存与轻量监控。当前仍建议 **单进程/单副本**（`--workers 1`，compose 不要 scale backend），否则虽然数据可持久化，但并发写入、清理和缓存一致性都不适合横向扩展。
2. **DASHSCOPE_API_KEY 决定能力**：无 key → 图像/可提取正文文档检测返回 Mock（响应里 `source: "mock"`）；有 key → `source: "vlm"`。前端不会报错，但结果含义不同。
3. **上传体积**：nginx 已设 `client_max_body_size 25m`，裸机反代请同样放宽；取证分析（`/api/forensics`）响应较大（7 张内联 base64 图，约 3MB），已设 120s 超时。
4. **CORS**：后端 `allow_origins=["*"]`，便于演示。生产建议收紧为你的域名。
5. **访问控制**：配置 `JIANZHEN_ACCESS_TOKEN` 后，`/api/history`、`/api/report/*`、`/api/metrics` 与删除历史接口都需要在请求头里带 `X-Jianzhen-Token` 或 `Authorization: Bearer <token>`。
6. **持久化与缓存**：同一文件按 `cacheVersion + fileType + sha256` 复用核心分析结果，避免重复调用模型导致结论漂移。
7. **可见水印检测**：`VISIBLE_WATERMARK_ENABLED=true` 时启用检测-only 模块。该模块只定位可见 AI 水印/角标，复用了 GeminiWatermarkTool/remove-ai-watermarks 的 NCC 检测思路和 MIT 资产，不包含 reverse alpha blending、inpainting 或任何去水印输出。命中结果会返回小尺寸 WebP 证据抠图、bbox 与阶段分数，供前端增强可信展示。
8. **能力边界**：`txt`/`md`/`docx` 会先做正文抽取再调用文本检测；`pdf`/`doc`、视频、音频仍为 Mock；C2PA 仅验证签名有效性，不内置 OpenAI/Adobe 官方信任根（无法断言"出自某官方"）；SynthID 与可见水印仅作为辅助证据，未检出不能证明图片真实。
9. **出网**：后端需能访问 `dashscope.aliyuncs.com`（VLM 调用）。内网/受限环境请放行或仅用 Mock + C2PA。

## 7. API 速览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/api/health` | 健康检查，返回模型名与是否启用 VLM |
| POST | `/api/detect` | 表单 `file`（可选 `fileType`）→ 检测结果 |
| POST | `/api/forensics` | 表单 `file`（仅图像）→ 7 项可解释取证报告 |
| POST | `/api/provenance` | 表单 `file`（图像）→ C2PA 内容凭证验证 |
| GET  | `/api/history` / `/api/history/{id}` | 历史列表 / 单条 |
| DELETE | `/api/history/{id}` | 删除历史 |
| GET  | `/api/report/{reportId}` | 按报告号取结果 |
| GET  | `/api/report/{reportId}/download` | 下载基础自包含 HTML 鉴定报告 |
| POST | `/api/report/{reportId}/export` | 下载带取证 / C2PA 附加结果的完整 HTML 鉴定报告 |
| GET  | `/api/metrics` | 监控大屏数据，前端入口为 `/#monitor` |

健康检查示例：`curl http://localhost:8848/api/health`

## 8. 给部署方的任务清单

- [ ] 准备一台可出网（访问 dashscope）的服务器，装好 Docker + Docker Compose。
- [ ] `cp backend/.env.example backend/.env`，填入 `DASHSCOPE_API_KEY`。
- [ ] `docker compose up -d --build`，确认两个容器 healthy。
- [ ] `curl http://localhost:8080/`（前端）与 `docker compose exec backend curl -s localhost:8848/api/health`（后端）验证。
- [ ] 浏览器打开 `http://<IP>:8080`，上传一张图走通 检测 / 取证 / 凭证 三个按钮。
- [ ] （生产）配置域名 + HTTPS（在前端 nginx 前再加一层反代或用 caddy/traefik），收紧后端 CORS，保持后端单副本。
```
