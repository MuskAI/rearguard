# RealGuard

RealGuard 是一个面向数字内容鉴伪、侵权检索和运营监控的 Web 系统。仓库包含两套彼此独立的鉴伪入口：

- **V1 RealGuard**：保留原系统功能，包括短信登录、图像/视频鉴伪、侵权检索、历史记录和用户信息。
- **V2 鉴伪 Agent**：独立新版 Agent，支持 `qwen3-vl-flash` 视觉语言模型检测、可解释性取证分析和 C2PA 内容凭证验证。
- **Analytics**：基于开源 Umami 的访问监控后台，用于 PV、访客数、DAU、设备、来源和 IP 地区统计大屏。

生产环境示例：

- 主站：`http://realguard.cn/`
- V2：`http://realguard.cn/v2/`
- V2 API：`http://realguard.cn/v2-api/`
- 监控后台：`http://analytics.realguard.cn/`

## 目录结构

```text
.
├── realguard-server-main/
│   ├── RealGuard/              # V1 Flask 后端
│   ├── frontend/               # V1 React/Vite 前端
│   └── deploy/                 # V1 基础 Nginx 配置
├── v2-agent/
│   ├── backend/                # V2 FastAPI 后端
│   ├── frontend/               # V2 React/Vite/Tailwind 前端
│   └── docker-compose.yml      # V2 独立容器化部署示例
└── deploy/
    ├── nginx/                  # 生产 Nginx 反向代理与安全片段
    └── analytics/              # Umami 监控后台部署模板
```

## 功能概览

V1 RealGuard：

- 短信验证码登录，登录态可持久化。
- 图像检测、视频检测、侵权检索。
- 历史记录查看，图片历史支持缩略图。
- 手机端适配，保留原页面风格。
- 首页新增 V2 Agent 独立入口。

V2 鉴伪 Agent：

- 图像、视频、音频、文档入口统一上传。
- 图像检测与可提取正文的文档检测（`txt` / `md` / `docx`）调用 DashScope OpenAI 兼容接口，默认模型为 `qwen3-vl-flash`。
- 视频、音频和复杂文档当前保留演示判定链路，前端会明确显示回退状态。
- 支持导出自包含 HTML 鉴定报告，便于留档与分享给复核方。
- 若已在界面中执行取证分析或内容凭证验证，导出报告会自动并入这些附加结果。
- ELA、噪声残差、频域、光照梯度等可解释性取证可视化。
- C2PA 内容凭证读取与验证。
- 浅色工作台风格，独立路径 `/v2/`。

监控后台：

- Umami 自托管网站分析。
- 统计 PV、访客、会话、页面路径、来源、设备、国家/地区。
- 支持 Boards 大屏和 World Map 组件。
- 前端通过 `/umami/script.js` 和 `/umami/api/` 同源代理采集。

## 环境变量

所有真实密钥只通过 `.env` 或系统环境变量提供，不要提交到 Git。

V1 后端示例：

```bash
cd realguard-server-main/RealGuard
cp .env.example .env
```

关键变量：

- `REALGUARD_DB_*`：主业务数据库。
- `REALGUARD_DETECTION_DB_*`：鉴伪历史数据库。
- `REALGUARD_DETECTION_BACKEND_URL`：V1 内网检测服务，例如 `http://127.0.0.1:15000`。
- `ALIYUN_*`：短信验证码服务。

V2 后端示例：

```bash
cd v2-agent/backend
cp .env.example .env
```

关键变量：

- `DASHSCOPE_API_KEY`
- `DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`
- `VLM_MODEL=qwen3-vl-flash`
- `JIANZHEN_ACCESS_TOKEN`：可选。配置后，V2 历史、报告和监控接口需携带访问令牌。

## 本地开发

V1 后端：

```bash
cd realguard-server-main/RealGuard
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py
```

V1 前端：

```bash
cd realguard-server-main/frontend
npm ci
npm run dev
```

V2 后端：

```bash
cd v2-agent/backend
python -m venv .venv
source .venv/bin/activate
pip install fastapi "uvicorn[standard]" python-multipart openai python-dotenv pillow numpy matplotlib
cp .env.example .env
python -m uvicorn app.main:app --host 127.0.0.1 --port 8848 --workers 1
```

V2 前端：

```bash
cd v2-agent/frontend
npm ci
npm run dev
```

## 验证

V1 回归与 smoke 测试：

```bash
uv venv realguard-server-main/RealGuard/.venv-test --python 3.13
uv pip install --python realguard-server-main/RealGuard/.venv-test/bin/python flask pymysql pillow requests werkzeug pytest
realguard-server-main/RealGuard/.venv-test/bin/pytest realguard-server-main/RealGuard/tests
```

V2 接口保护与报告导出测试：

```bash
v2-agent/backend/.venv/bin/pytest v2-agent/backend/tests
```

## 生产部署参考

推荐进程与端口：

| 服务 | 监听地址 | 说明 |
| --- | --- | --- |
| Nginx | `0.0.0.0:80` | 公网唯一入口 |
| V1 前端静态服务 | `127.0.0.1:8081` | 内部访问 |
| V1 Flask 后端 | `127.0.0.1:5000` | 内部访问 |
| V2 FastAPI 后端 | `127.0.0.1:8848` | 内部访问 |
| Umami | `127.0.0.1:3001` | 内部访问 |
| PostgreSQL/Umami | `127.0.0.1:5432` | 内部访问 |

Nginx 配置模板在 `deploy/nginx/`：

- `realguard.conf`：主站、V2 和 V1 路由。
- `analytics.conf`：Umami 后台子域名。
- `snippets/realguard-security-server.conf`：安全响应头、敏感文件拦截。
- `snippets/realguard-zones.conf`：限流和连接数区域，需要放在 Nginx `http` 作用域。
- `snippets/realguard-umami-tracking.conf`：同源统计脚本代理。

部署时可以按需复制：

```bash
sudo cp deploy/nginx/realguard.conf /etc/nginx/conf.d/realguard.conf
sudo cp deploy/nginx/analytics.conf /etc/nginx/conf.d/analytics.conf
sudo cp deploy/nginx/snippets/*.conf /etc/nginx/snippets/
sudo nginx -t
sudo systemctl reload nginx
```

Umami 部署模板：

```bash
cd deploy/analytics
cp .env.example .env
docker compose -f docker-compose.example.yml up -d
```

生产环境建议把 `analytics.realguard.cn` 配成 A 记录指向服务器公网 IP，并尽快补 HTTPS。

## 安全说明

- 仓库不包含真实 API Key、短信密钥、数据库密码、SSH 私钥。
- 后端服务建议只监听 `127.0.0.1`，公网只开放 Nginx 的 `80/443`。
- Nginx 模板包含基础限流、隐藏版本、敏感文件拦截和安全响应头。
- 上传接口限流较普通接口更宽松，避免影响正常检测。
- V2 使用本地 SQLite 持久化历史、缓存与轻量监控指标；生产建议把数据库文件放在稳定磁盘路径。
- 如需对外开放 V2 后台能力，建议配置 `JIANZHEN_ACCESS_TOKEN` 保护历史、报告和监控接口。

## GitHub

```bash
git clone https://github.com/MuskAI/rearguard.git
```
