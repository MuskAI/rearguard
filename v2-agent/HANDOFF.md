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

报告问答默认用 `qwen3-vl-flash` 解释检测报告。用户核验新闻、事件或配文时，
`report_web_search.py` 按“主张提取 → 多通道召回 → URL 去重与分层排序 → 正文/官方元数据取证
→ 来源分级与交叉裁决”执行。生产默认并行使用 Qwen Native Search 与 Qwen Responses；
配置密钥后可增加 Google Fact Check 和 Brave Search。搜索标题、摘要及模型记忆都不能直接成为证据，
只有读取到的网页正文、官方平台元数据或结构化事实核查记录可以参与裁决。联网结论只核验图片表达的
公开事件，不会改写图像鉴伪结论。配置见 `backend/.env.example`。

生产发布：

```bash
DEPLOY_SSH_KEY=/path/to/key ./scripts/deploy_v2.sh
```

完整架构、安全规则和运维步骤以仓库根目录
[README](../README.md) 与 [开发和运维指南](../docs/HANDOFF_GUIDE.md) 为准。
