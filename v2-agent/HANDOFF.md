# V2 Agent 子服务说明

`v2-agent` 是慧鉴 AI 的统一前端和证据服务，不是独立产品版本。

| 目录 | 作用 |
| --- | --- |
| `frontend/` | 当前公网首页和 Agent 工作台 |
| `backend/` | FastAPI 证据服务，负责 C2PA、元数据、水印、取证分析和报告问答 |
| `docker-compose.yml` | 本地或隔离环境的容器化示例 |

生产环境中，登录、历史、开发者计费和图像主模型由
`realguard-server-main/RealGuard` 负责。外部开发者请求必须经过 Flask
计费网关，不能直接调用本服务绕过额度与账号隔离。

本地启动：

```bash
cd backend
cp .env.example .env
uv sync --frozen
uv run uvicorn app.main:app --host 127.0.0.1 --port 8848
```

```bash
cd frontend
npm ci
npm run dev
```

测试：

```bash
cd backend && uv run --with pytest pytest tests
cd ../frontend && npm run lint && npm run build
```

报告问答默认用 `qwen3-vl-flash` 解释检测报告；用户询问新闻、事件或配文是否属实时，
`report_web_search.py` 会先提取公开主张，再用 `qwen-plus` 联网检索并返回可点击来源。
联网证据只判断内容是否属实，不会改写图像鉴伪结论。相关开关和模型配置见
`backend/.env.example` 中的 `JIANZHEN_REPORT_QA_*`。

生产发布：

```bash
DEPLOY_SSH_KEY=/path/to/key ./scripts/deploy_v2.sh
```

完整架构、安全规则和运维步骤以仓库根目录
[README](../README.md) 与 [开发和运维指南](../docs/HANDOFF_GUIDE.md) 为准。
