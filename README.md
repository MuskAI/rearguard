# 慧鉴AI 项目交接说明

慧鉴AI 是一个面向数字内容鉴伪、证据核验和报告归档的 Agent 系统。用户只访问一个工作台并上传一次文件，系统会按内容类型调度图像、视频、文档、取证和内容凭证能力。`RealGuard`、`jianzhen-v2`、V1、V2 只用于说明内部代码与服务边界，不再作为用户可见的产品版本。

本 README 面向项目接手人。请先读完“交接重点”和“哪些东西不在 Git 里”，再开始部署或改代码。

## 交接重点

- GitHub 仓库：`git@github.com:MuskAI/rearguard.git`
- 主分支：`main`
- 生产服务器：`124.221.92.85`
- 生产用户：`ubuntu`
- 当前公网入口：
  - 统一 Agent 工作台：`https://www.rrreal.cn/`
  - `/v2`、`/v2/`：兼容旧链接，重定向到 `/`
  - 内部证据 API 反向代理：`https://www.rrreal.cn/v2-api/`
  - 应急 IP 入口：`http://124.221.92.85/`
  - Umami 监控：`http://analytics.realguard.cn/`
- 生产服务：
  - `realguard-backend.service`：账户、历史、图像/视频 Agent 编排与管理后台
  - `realguard-detector-backend.service`：图像和视频检测后端
  - `jianzhen-v2-backend.service`：文档、取证、内容凭证与证据服务
  - `nginx.service`：公网入口和静态资源
- 生产代码路径：
  - `/opt/realguard-server/RealGuard`
  - `/opt/jianzhen-v2`
  - `/var/www/realguard-frontend`
  - `/var/www/v2`
- 生产配置路径：
  - `/etc/realguard/realguard-backend.env`
  - `/etc/realguard/detector-db.env`
  - `/etc/realguard/jianzhen-v2.env`
  - `/etc/realguard/agent.env`
  - `/etc/realguard/sms.env`
  - `/etc/nginx/conf.d/myapp.conf`
  - `/etc/letsencrypt/live/www.rrreal.cn/`

重要：真实密钥、数据库密码、生产数据、上传文件、SQLite 数据库、运行态 JSON 不在 GitHub 仓库里。接手人需要单独拿到服务器权限和备份文件。

## 系统架构

### 统一公开工作台

公开页面由 `v2-agent/frontend` 提供，用户界面中没有 V1/V2 选择。一个上传入口自动路由：

- 图像：多源图像 Agent，失败时只切换到已部署的真实模型，不生成模拟结论
- 视频：视频抽帧与时序检测
- 文档：正文提取与生成式内容检测
- 深度取证：像素取证图谱、C2PA/元数据内容凭证
- 历史：合并展示三个内部数据源，但每条记录仍由对应后端负责鉴权

### 账户与检测服务

`realguard-server-main/RealGuard` 是 Flask 服务，包含：

- Flask 后端：`realguard-server-main/RealGuard`
- 旧 React/Vite 前端：`realguard-server-main/frontend`，只保留兼容和回滚，不再作为公开首页
- MySQL 主业务库：`system`
- MySQL 检测历史库：`image_detection`
- 图像/视频检测历史、报告、缩略图、短信登录、管理员控制台和运营大屏

生产服务关系：

```text
Nginx :80 -> HTTPS canonical redirect
Nginx :443 (rrreal.cn, www.rrreal.cn)
  ├── /                         -> /var/www/v2（统一 Agent 静态资源）
  ├── /api/*, /admin/*, /image_upload/*, /video_upload/* -> 127.0.0.1:5000
  ├── /v2-api/*                 -> 127.0.0.1:8848/api/*
  ├── /v2, /v2/*                -> 301 /
  └── /detection-static/*, /static/uploads/* -> 404（禁止绕过归属校验）

realguard-backend.service       -> Flask app, 127.0.0.1:5000
realguard-detector-backend.service -> detector_backend.py, 127.0.0.1:15001
mysql.service                   -> system / image_detection
```

### 证据服务

`v2-agent/backend` 是 FastAPI 证据服务，包含：

- FastAPI 后端：`v2-agent/backend`
- 统一 React/Vite/Tailwind Agent 前端：`v2-agent/frontend`
- SQLite 持久化：默认 `/opt/jianzhen-v2/data/jianzhen-v2.sqlite3`
- DashScope OpenAI 兼容接口，默认模型 `qwen3-vl-flash`
- C2PA、可见水印、SynthID、元数据 AI 线索、统一取证摘要

生产服务关系：

```text
/          -> /var/www/v2
/v2-api/*  -> 127.0.0.1:8848

jianzhen-v2-backend.service -> FastAPI, 127.0.0.1:8848
```

### Analytics

`deploy/analytics/` 里是 Umami 监控后台模板。监控不在主部署脚本里自动发布，需要单独维护。

## 仓库结构

```text
.
├── realguard-server-main/
│   ├── RealGuard/              # 账户/检测 Flask 后端、SQL、测试
│   ├── frontend/               # 旧前端，仅兼容与回滚
│   └── deploy/                 # 旧前端内部 Nginx 配置
├── v2-agent/
│   ├── backend/                # 证据 FastAPI 后端
│   ├── frontend/               # 统一 Agent 前端（公网首页）
│   └── docker-compose.yml      # 容器化示例
├── deploy/
│   ├── nginx/                  # 生产 Nginx 配置模板
│   ├── letsencrypt/            # Certbot 自动续期部署钩子
│   └── analytics/              # Umami 部署模板
├── scripts/
│   ├── deploy_v1.sh            # 账户与检测服务发布（沿用旧文件名）
│   ├── deploy_v2.sh            # 统一 Agent 与证据服务发布（沿用旧文件名）
│   ├── deploy_detection_service.sh # 66 GPU、双水印服务与公网隧道配置原子发布
│   ├── deploy_converge.sh      # 按需发布 V1、V2 与 GPU 三个服务组
│   └── check_deploy_status.sh  # 线上状态检查
└── skills/
    ├── realguard-forensics/    # 内部取证 skill 资料
    └── huijian-image-forensics/ # 对外图像鉴伪 Agent Skill
```

## 这次迁移后的重要变化

- 已迁移到新服务器 `124.221.92.85`。
- 正式域名为 `https://www.rrreal.cn/`，HTTP 和根域名统一跳转到该地址。
- Let's Encrypt 证书覆盖 `rrreal.cn` 与 `www.rrreal.cn`，由 `certbot.timer` 自动续期。
- 首页 UI 已重做为“慧鉴AI 内容鉴伪智能体”，图像、视频、文档、取证和报告都在同一任务流中完成。
- 对外产品名统一为“慧鉴AI”，旧服务名仅作为部署兼容标识保留。
- 生产检测不再生成随机或模拟结论：模型不可用时返回明确错误；主模型未通过独立校准门禁时返回“需人工复核”，不把原始 softmax 当成真假概率。
- 首页不再展示“管理入口”按钮，普通用户入口只保留任务、历史和报告。
- 侵权检索相关页面、接口和公开文档已移除或由 Nginx 拦截。
- 公开 `developer/API.md` 和 public skill 文件已从前端静态目录删除。
- 历史列表、详情、媒体、报告、工件更新和删除均执行服务端归属校验；前端切换账号时会中止旧请求并立即清空状态。
- 正式账户归属统一使用不可变 `account_uuid`；正常请求不再按手机号、openid 或不同数据库的自增 ID 回退匹配。
- 私有历史、媒体和报告响应带 `Cache-Control: private, no-store`，防止浏览器或代理跨账号复用。
- 仍有一批旧图像记录缺少 `account_uuid`，不能安全自动归属。需要确认映射关系后再手工绑定或按留存策略归档。

## 品牌与界面规范

- 品牌标志：`ScanEye` 线性图标加朱砂色确认印记，组件位于 `v2-agent/frontend/src/components/HuijianBrand.tsx`。
- 品牌形象：“小鉴”，一个结合取证镜头和印章语言的可爱助手。它用于欢迎、进度和空状态，不遮挡证据图片或检测结论。
- 主色：墨青 `#173140`、青玉 `#147D7C`、证据蓝 `#2B6FA8`；风险色使用朱砂 `#DC654F`，提醒色使用暖黄 `#B7791F`，页面底色为冷白青 `#EEF4F5`。
- 视觉原则：工作台优先、证据优先、少装饰；圆角不超过 `8px`，交互目标至少 `44px`，不要使用大面积渐变、悬浮装饰球或卡片套卡片。
- 结论语言：只有通过独立签名校准门禁的模型，或严格验证的来源凭证/平台水印，才能展示自动真假结论与概率。普通模型调用成功但未获决策授权时仍显示“需人工复核”；元数据缺失不能单独作为伪造证据。
- 统一前端形象资源：`v2-agent/frontend/public/brand/huijian-mascot.webp`。

## 哪些东西在 GitHub 里

GitHub 仓库包含：

- 两个内部后端服务的完整源码
- 统一 Agent 前端和旧前端兼容源码
- SQL schema 和迁移辅助脚本
- Nginx 配置模板
- 发布和状态检查脚本
- 前后端依赖锁文件
- 测试代码

关键依赖文件：

- `realguard-server-main/RealGuard/requirements.txt`
- `realguard-server-main/RealGuard/requirements.lock`（生产 Linux 精确版本快照）
- `realguard-server-main/frontend/package-lock.json`
- `v2-agent/backend/pyproject.toml`
- `v2-agent/backend/uv.lock`
- `v2-agent/frontend/package-lock.json`
- `services/realguard-detection/runtime.lock`
- `services/watermark-precheck/runtime.lock`
- `services/yolo-watermark/runtime.lock`

主模型自动结论还要求独立评测环境签发
`cn.huijian.model-calibration-v2` Ed25519 校准清单。生产服务器只保存公钥，
私钥、校准数据集和原始评测材料不进入仓库或模型服务器。
同一公钥必须以只读、非组/全局可写文件同时安装到 66 模型服务器和公网 Web
服务器的 `REALGUARD_V2_CALIBRATION_PUBLIC_KEY_FILE`；任一端缺失或验签失败时，
结果会安全降级为 `review_only`。
GPU 与 Web 的 `/etc/realguard/model-inference.env` 还必须配置同一个独立的
`REALGUARD_MODEL_RESPONSE_HMAC_KEY`（64 位小写十六进制）及活动 key ID。
部署器会比较两端密钥指纹和 key ID；缺失或不一致时拒绝发布，密钥本身不会
随 HTTP 请求发送。Web 可通过 `REALGUARD_MODEL_RESPONSE_HMAC_KEYS_JSON`
保留历史验证密钥，实现不中断的签名密钥轮换。

## 哪些东西不在 GitHub 里

这些东西不要提交到 GitHub，需要从服务器或备份里单独交接：

- SSH 私钥
- 数据库密码
- 阿里云短信密钥
- DashScope / LLM API Key
- 管理员 token
- MySQL 生产数据
- 证据服务 SQLite 数据
- 用户上传文件
- `admin_state.json`
- Playwright 截图、报告缓存、论文/研究输出

本地常见不应提交文件：

```text
.playwright-cli/
output/
reports/
research/
realguard-server-main/RealGuard/admin_state.json
```

## 本地开发

### 前置依赖

建议版本：

- Python 3.13
- Node.js 20+
- npm 10+
- MySQL 8 或兼容版本
- `uv`，用于快速创建测试虚拟环境

### 账户与检测后端

```bash
cd realguard-server-main/RealGuard
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py
```

默认监听：

```text
http://127.0.0.1:5000
```

本地 `.env` 至少需要配置：

- `SECRET_KEY`
- `REALGUARD_DB_*`
- `REALGUARD_DETECTION_DB_*`
- `REALGUARD_DETECTION_BACKEND_URL`
- `ALIYUN_*`，如果要测试短信
- `DASHSCOPE_API_KEY`，如果要测试 LLM 相关链路

### 旧前端（通常不需要启动）

```bash
cd realguard-server-main/frontend
npm ci
npm run dev
```

构建：

```bash
npm run build
```

### 证据后端

```bash
cd v2-agent/backend
uv sync
cp .env.example .env
uv run uvicorn app.main:app --host 127.0.0.1 --port 8848 --workers 1
```

如果机器没有 `uv`，可以使用普通虚拟环境：

```bash
python -m venv .venv
source .venv/bin/activate
pip install fastapi "uvicorn[standard]" python-multipart openai python-dotenv pillow numpy matplotlib c2pa-python opencv-python-headless scipy PyWavelets scikit-learn
python -m uvicorn app.main:app --host 127.0.0.1 --port 8848 --workers 1
```

关键环境变量：

- `DASHSCOPE_API_KEY`
- `DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`
- `VLM_MODEL=qwen3-vl-flash`
- `JIANZHEN_DATA_DIR`
- `JIANZHEN_ACCESS_TOKEN`
- `REALGUARD_DEVELOPER_AUTH_SECRET`
- `JIANZHEN_DEVELOPER_AUTH_URL`

### 统一 Agent 前端

```bash
cd v2-agent/frontend
npm ci
npm run dev
```

Vite 默认把 `/api`、`/sms`、`/image_upload`、`/video_upload` 代理到 `127.0.0.1:5000`，把 `/v2-api` 代理到 `127.0.0.1:8848`。需要改端口时设置：

```bash
VITE_ACCOUNT_API_TARGET=http://127.0.0.1:5000 \
VITE_API_TARGET=http://127.0.0.1:8848 npm run dev
```

构建：

```bash
npm run build
```

## 开发者平台

登录后的用户可从官网或工作台进入 `https://www.rrreal.cn/?developer=1`。一期只开放图像鉴伪，快速检测与 Swarm 多源复核使用同一套异步 API：

```text
POST /api/openapi/v1/image-detections
GET  /api/openapi/v1/image-detections/{task_id}
GET  /api/openapi/v1/image-detections/{task_id}/report
```

创建任务必须携带 `Idempotency-Key`（建议 UUID）。同一次业务请求的网络重试应复用该值，避免重复建任务或扣费。

平台提供 API Key 创建、轮换和撤销，支持模式权限、有效期与 IP/CIDR 白名单。完整 Key 只在创建或轮换时显示一次，服务端只保存 SHA-256 派生哈希。所有 Key 共享账号级额度，轮换或重建 Key 不会重置赠送次数。

每个开发者账号首次初始化赠送 100 次成功检测额度。提交时先原子预占，只有任务成功落库后才结算；参数错误、模型失败、调度失败和超时不会消费额度。赠送额度耗尽后，快速与 Swarm 分别按 `developer_pricing` 配置计价；一期不开在线支付，由管理员手工调整余额并写入审计账本。

数据库升级：

```bash
cd realguard-server-main/RealGuard
python -m flask --app run:app developer-db-upgrade
```

显式迁移文件位于 `realguard-server-main/RealGuard/sql/migrations/20260717_developer_platform.sql`。正式发布脚本会自动执行开发者表升级。

关键环境变量：

- `REALGUARD_DEVELOPER_FREE_CALLS=100`
- `REALGUARD_DEVELOPER_MAX_IMAGE_BYTES=26214400`
- `REALGUARD_DEVELOPER_FAST_PRICE_FEN`、`REALGUARD_DEVELOPER_SWARM_PRICE_FEN`
- `REALGUARD_DEVELOPER_FAST_BILLING_ENABLED`、`REALGUARD_DEVELOPER_SWARM_BILLING_ENABLED`
- `REALGUARD_TRUSTED_PROXY_CIDRS=127.0.0.0/8,::1/128`
- `JIANZHEN_ALLOW_DIRECT_DEVELOPER_KEYS=false`，禁止绕过统一计费网关直接调用证据服务

完整运维与接口说明见 `docs/DEVELOPER_PLATFORM.md`。Agent Skill 位于 `skills/huijian-image-forensics/`，使用 `HUIJIAN_API_KEY`，可提交本地图像、轮询任务、输出结构化证据并下载 PDF 报告。

## 测试和验证

### 账户与检测服务全量测试

```bash
uv venv realguard-server-main/RealGuard/.venv-test --python 3.13
uv pip install --python realguard-server-main/RealGuard/.venv-test/bin/python \
  flask pymysql pillow requests werkzeug pytest
realguard-server-main/RealGuard/.venv-test/bin/pytest realguard-server-main/RealGuard/tests
```

当前基准以 CI 或本地全量 `pytest` 输出为准。

### 证据服务测试

```bash
v2-agent/backend/.venv/bin/pytest v2-agent/backend/tests
```

当前基准以 CI 或本地全量 `pytest` 输出为准。

### 前端构建

```bash
cd realguard-server-main/frontend && npm run build
cd ../../v2-agent/frontend && npm run build
```

### 线上状态检查

```bash
DEPLOY_SSH_KEY=/path/to/private_key ./scripts/check_deploy_status.sh
```

严格模式：

```bash
STRICT=1 DEPLOY_SSH_KEY=/path/to/private_key ./scripts/check_deploy_status.sh
```

## 生产部署

发布脚本默认目标为：

```text
ubuntu@124.221.92.85
```

接手人应使用自己的 SSH key，不要把私钥提交进仓库。

### 发布账户与检测服务

```bash
DEPLOY_SSH_KEY=/path/to/private_key ./scripts/deploy_v1.sh
```

脚本会做：

- 拒绝发布路径中的未提交改动，保证版本标记对应实际制品
- Flask 后端编译与测试
- 旧前端兼容构建
- 后端打包上传
- 前端 `dist/` 同步到 `/var/www/realguard-frontend/`
- 安装生产 Nginx 配置并执行 `nginx -t`
- 重启 `realguard-detector-backend.service`
- 安装并重启 `realguard-developer-worker.service`，从持久 spool 恢复网页与开发者检测任务，并为两个通道各保留一个执行槽位
- 重启 `realguard-backend.service`
- 在迁移前创建并校验完整备份
- 执行 `identity-db-upgrade` 和 `developer-db-upgrade`，升级不可变账户归属、API Key、用量和计费表
- 网页快速检测与 Swarm 先写入私有 spool 和数据库队列；重启后复用已落库业务结果或安全收口，不重复生成历史
- 后端和前端均保留上一版本，全部健康检查通过后才清理回滚副本
- 写入 `/opt/realguard-server/DEPLOYED_COMMIT`
- 健康检查

### 发布统一 Agent 与证据服务

```bash
DEPLOY_SSH_KEY=/path/to/private_key ./scripts/deploy_v2.sh
```

脚本会做：

- 拒绝发布路径中的未提交改动，保证版本标记对应实际制品
- 证据后端编译和测试
- 统一 Agent 前端构建
- 后端与锁定依赖打包上传
- 前端 `dist/` 同步到 `/var/www/v2/`
- 安装受限权限的 `jianzhen-v2-backend.service`
- 通过版本目录原子切换后端和前端，健康检查失败时自动恢复上一版本
- 写入 `/opt/jianzhen-v2/DEPLOYED_COMMIT`
- 健康检查

### 发布 66 GPU、可见水印与 YOLO 服务

```bash
DEPLOY_SSH_KEY=/path/to/public_server_key ./scripts/deploy_detection_service.sh
```

脚本会先上传完整 release，再暂停并排空公网 worker，统一切换主模型、水印、YOLO、依赖锁、隧道 unit 与 systemd 配置，并验证开机自启。发布必须通过 CUDA、签名校准状态契约、完整推理审计、YOLO revision/SHA、真实水印预检、真实主模型预测和双端 commit 可见性；任一失败会恢复上一版本并按原始服务状态恢复公网 worker。两端 15 分钟 watchdog 保留到到期并依据实际服务 commit 自动 no-op，避免发布进程中断或旧事务误回滚新版本。66 使用密码登录时，命令会在 SSH 和 `sudo` 阶段交互询问密码。

### 完整发布顺序

统一前端和证据服务先发布，再发布账户服务与 Nginx 配置：

```bash
DEPLOY_SSH_KEY=/path/to/private_key ./scripts/deploy_v2.sh
DEPLOY_SSH_KEY=/path/to/private_key ./scripts/deploy_v1.sh
```

### 收敛部署

如果不确定哪个内部服务组落后，可以用：

```bash
DEPLOY_SSH_KEY=/path/to/private_key ./scripts/deploy_converge.sh
```

### Dry Run

```bash
DRY_RUN=1 DEPLOY_SSH_KEY=/path/to/private_key ./scripts/deploy_v1.sh
```

## 生产服务器常用命令

登录：

```bash
ssh -i /path/to/private_key ubuntu@124.221.92.85
```

服务状态：

```bash
systemctl status realguard-backend.service
systemctl status realguard-detector-backend.service
systemctl status jianzhen-v2-backend.service
systemctl status nginx.service
```

查看日志：

```bash
journalctl -u realguard-backend.service -n 200 --no-pager
journalctl -u realguard-detector-backend.service -n 200 --no-pager
journalctl -u jianzhen-v2-backend.service -n 200 --no-pager
```

健康检查：

```bash
curl -fsS http://127.0.0.1:5000/api/history/image-detections
curl -fsS http://127.0.0.1:15001/health
curl -fsS http://127.0.0.1:8848/api/health
curl -fsS http://127.0.0.1/v2-api/health
curl -fsS -o /dev/null http://127.0.0.1/
```

Nginx：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

### 域名和 HTTPS

DNSPod 中应只保留以下主站解析：

```text
@    A    124.221.92.85
www  A    124.221.92.85
```

不要让根域名同时指向旧服务器 `124.222.3.205`。DNS 变更传播期间，旧服务器可临时安装
`deploy/nginx/rrreal-legacy-redirect.conf`，把根域请求和 ACME 校验转发到新服务器。

证书与自动续期状态：

```bash
sudo certbot certificates
systemctl status certbot.timer
sudo certbot renew --dry-run
```

Certbot 续期后通过以下钩子检查并热重载 Nginx：

```text
/etc/letsencrypt/renewal-hooks/deploy/realguard-reload-nginx
```

线上检查：

```bash
curl -I http://www.rrreal.cn/
curl -I https://www.rrreal.cn/
curl -I https://rrreal.cn/
```

## 数据备份和恢复

生产数据不在 GitHub。正式交接时至少要给接手人一份最近备份。

生产部署会安装 `realguard-backup.timer`，每天在北京时间 03:15 后的随机
30 分钟窗口内执行。它在线备份 `system`、`image_detection`、V2 SQLite、
累计访问 SQLite、上传原件和首次签名证据快照，并为每份备份生成
`SHA256SUMS`。默认目录是
`/var/backups/realguard`，默认保留 14 天。

```bash
systemctl status realguard-backup.timer
sudo systemctl start realguard-backup.service
sudo journalctl -u realguard-backup.service -n 100 --no-pager
sudo sh -c 'cd /var/backups/realguard/latest && sha256sum -c SHA256SUMS'
sudo /usr/local/sbin/realguard-restore-verify /var/backups/realguard/latest
```

本机备份不能应对整机或云盘故障。生产必须在 `/etc/realguard/backup.env`
配置异地 `rclone` 目标，并将该文件权限设为 `0600`：

```bash
REALGUARD_BACKUP_RETENTION_DAYS=14
REALGUARD_BACKUP_RCLONE_REMOTE=remote-name:realguard-backups
REALGUARD_BACKUP_REQUIRE_OFFSITE=1
REALGUARD_BACKUP_REQUIRE_ALL_SOURCES=1
REALGUARD_BACKUP_STATUS_FILE=/opt/realguard-data/backup-status.json
REALGUARD_BACKUP_MAX_AGE_SECONDS=129600
```

备份脚本会在异地上传后执行 `rclone check --one-way`，校验失败则整次备份任务失败。
商用环境必须启用两个 `REQUIRE_*` 开关；此时异地目标缺失、任一数据库/上传目录/证据目录
缺失或远端校验失败都会阻止 `latest` 指针更新。后台读取不含密钥的备份状态凭证，超过
36 小时未成功或异地副本未验证会触发关键告警。
每季度应先把异地快照下载到服务器上的隔离目录，再执行
`realguard-restore-verify /path/to/snapshot`。该命令会把两份 MySQL 备份恢复到随机临时库，
运行扩展表检查和 SQLite 完整性检查，安全解包上传原件与证据清单，写入
`/var/backups/realguard/restore-drills/` 后自动删除临时库。目标为 RPO 不超过 24 小时、
RTO 不超过 2 小时；还应使用抽样账号下载并验签 PDF 报告。

### MySQL 备份

在服务器上执行：

```bash
backup=/tmp/realguard-mysql-$(date +%F-%H%M%S).sql
sudo mysqldump --single-transaction --databases system image_detection > "$backup"
gzip "$backup"
ls -lh "$backup.gz"
```

从服务器拉回本地：

```bash
scp -i /path/to/private_key ubuntu@124.221.92.85:/tmp/realguard-mysql-*.sql.gz .
```

恢复前必须确认目标库可以被覆盖。恢复示例：

```bash
gunzip -c realguard-mysql-YYYY-MM-DD-HHMMSS.sql.gz | sudo mysql
```

### 检测服务上传文件备份

```bash
sudo tar -czf /tmp/realguard-v1-uploads-$(date +%F-%H%M%S).tgz \
  -C /opt/realguard-server/RealGuard/imagedetection/static uploads
```

恢复：

```bash
sudo tar -xzf realguard-v1-uploads-YYYY-MM-DD-HHMMSS.tgz \
  -C /opt/realguard-server/RealGuard/imagedetection/static
sudo chown -R ubuntu:ubuntu /opt/realguard-server/RealGuard/imagedetection/static/uploads
```

### 证据服务 SQLite 数据备份

证据服务使用 SQLite，备份时建议先停服务或使用 SQLite 在线备份方式。

简单停机备份：

```bash
sudo systemctl stop jianzhen-v2-backend.service
sudo tar -czf /tmp/jianzhen-v2-data-$(date +%F-%H%M%S).tgz -C /opt/jianzhen-v2 data
sudo systemctl start jianzhen-v2-backend.service
```

恢复：

```bash
sudo systemctl stop jianzhen-v2-backend.service
sudo tar -xzf jianzhen-v2-data-YYYY-MM-DD-HHMMSS.tgz -C /opt/jianzhen-v2
sudo chown -R ubuntu:ubuntu /opt/jianzhen-v2/data
sudo systemctl start jianzhen-v2-backend.service
```

## 历史记录说明

MySQL 检测历史主要在：

- `image_detection.data`
- `image_detection.video_data`
- `image_detection.exif`

用户信息主要在：

- `system.user`

正式账户使用 `system.user.account_uuid` 作为不可变账号标识，图像和视频历史分别
写入 `image_detection.data.owner_account_uuid` 与
`image_detection.video_data.owner_account_uuid`。登录态、网页历史、媒体、报告和
开发者 API 都必须只按该 UUID 精确过滤；两个数据库各自的自增 `Userid` 不能跨库
比较，手机号和 openid 也不能在正常请求里作为回退条件。

`identity-db-upgrade` 只创建 UUID 字段和索引，不按手机号或 openid 自动认领旧历史。
新模型返回的记录通过条件更新绑定；更新为 0 行时必须再次查询确认 UUID 完全一致，
避免读取与绑定之间的并发串号。

证据服务的 SQLite 历史同时保存审计用 `developer_user_id` 和强制租户字段
`developer_account_uuid`。列表、详情、工件、报告、分享和删除都必须先比较当前
会话的不可变 `account_uuid`；缺少该字段的旧访客记录不会被任意登录用户自动认领，
只有管理员可核实并修复归属。

注意：迁移后仍没有 `owner_account_uuid` 的旧记录默认对普通账号不可见。不能按相似
手机号或 openid 自动展示，必须由管理员核实归属后再修复。

查询未绑定图像记录：

```sql
SELECT COUNT(*)
FROM image_detection.data
WHERE owner_account_uuid IS NULL OR owner_account_uuid = '';
```

## 安全说明

- 不要提交 `.env`、私钥、数据库 dump、上传文件、SQLite 数据库。
- 后端服务建议只监听 `127.0.0.1`，公网只开放 Nginx 的 `80/443`。
- Nginx 模板包含敏感文件拦截、基础限流和安全响应头。
- 生产已启用 HTTPS；`REALGUARD_SESSION_COOKIE_SECURE` 必须设为 `1`，`SECRET_KEY` 必须是持久、随机且不入库的值。
- 历史和报告用户接口使用登录会话并执行对象归属校验；`JIANZHEN_ACCESS_TOKEN` 只用于管理员级诊断和维护，不可发给普通用户。
- `/detection-static/` 与 `/static/uploads/` 必须保持公网 `404`，原图和视频只能通过带归属校验的 `/api/media/{kind}/{itemid}` 获取。
- 任意远程视频 URL 默认关闭；只有明确设置 `REALGUARD_ALLOW_REMOTE_VIDEO_URLS=1` 才开启，生产不建议开启。
- 私有接口响应必须保留 `Cache-Control: private, no-store`，账户切换时前端必须取消旧请求并清空旧状态。
- 管理员账号和大屏 token 只在服务器环境变量或数据库中维护。
- 管理状态固定写入 `/opt/realguard-data/admin_state.json`；不要放回受 `ProtectHome` 隔离的用户主目录。
- 对外开发者请求必须经过 `/api/openapi/v1/` 计费网关；`/api/developer/v1/detect` 与证据服务直连 Key 均保持停用。
- 开发者异步检测只由 `realguard-developer-worker.service` 执行；上传文件先原子写入权限为 `0600` 的私有 spool，再预占额度并入队。数据库租约、心跳、幂等键和最大重试次数共同约束重启恢复。
- 图像 PDF 报告首次生成时固化服务端证据清单，记录原件 SHA-256、模型/策略版本和结论并独立签名；后续下载复用首次快照，原件或快照变化时失败关闭。

## 已知问题和待办

- 生产库当前有 699 条旧图像和 58 条旧视频记录尚未绑定 `account_uuid`。它们默认对用户不可见，不能按手机号或 openid 自动猜测归属。认领必须经过 `operator` 申请、`reviewer` 审批，校验原媒体 SHA-256、治理证据文件 SHA-256、目标账号锁和 HMAC 完整性；错误申请应先驳回释放，再重新提交。
- 当前 66 主模型在少量已知实拍样本上存在系统性高分误判，自动真假结论已由 `model_decision_policy.py` 关闭。必须完成独立校准集的标签/预处理/FP32-INT8 对齐、FPR/FNR 和阈值验收后，才能配置校准记录并开放自动判定。
- 网页端快速检测、Swarm 与开发者 API 已由独立持久 worker 执行。当前单机总执行并发固定为 2；扩展到多 worker 或多 GPU 前必须重新验证通道公平、租约恢复和容量报告。
- V1 图像 PDF 已有首次固化签名清单；视频报告和 V2 深度取证报告还没有统一到同一套证据签名、密钥轮换和验签策略，不能宣称达到司法取证级。
- Umami 监控后台不在 `deploy_v1.sh` / `deploy_v2.sh` 自动发布范围内。
- 本地自动备份、每周隔离恢复演练、独立告警 worker、dead-man watchdog 和安全审计链校验 timer 已纳入部署。异地 `rclone`、KMS/HSM、WORM/对象锁检查点及双机容灾仍需由运维配置并留档；未配置时后台必须保持红色告警，不得宣称完成商用灾备。

遗留历史认领使用精确记录接口，不提供批量猜测或按手机号自动回填：

```text
GET  /api/admin/legacy-history/{data|video_data}/{itemid}
GET  /api/admin/legacy-history/target-account/{user_id}
POST /api/admin/legacy-history/claims
POST /api/admin/legacy-history/claims/{claim_id}/approve
POST /api/admin/legacy-history/claims/{claim_id}/reject
```

证据文件须由运维先放入 `/opt/realguard-data/legacy-governance-evidence/`，接口只接受该目录内的相对路径和匹配的 SHA-256。`super_admin` 与普通 `admin` 均不能申请或审批，避免管理权限与数据认领权限合并。

## Git 工作流

首次克隆：

```bash
GIT_SSH_COMMAND='ssh -i /path/to/private_key -o IdentitiesOnly=yes' \
  git clone git@github.com:MuskAI/rearguard.git
```

日常开发：

```bash
git checkout main
git pull --ff-only
# 修改代码
git status
git add <files>
git commit -m "type(scope): message"
GIT_SSH_COMMAND='ssh -i /path/to/private_key -o IdentitiesOnly=yes' git push origin main
```

提交前建议：

```bash
git diff --check
realguard-server-main/RealGuard/.venv-test/bin/pytest realguard-server-main/RealGuard/tests
v2-agent/backend/.venv/bin/pytest v2-agent/backend/tests
cd realguard-server-main/frontend && npm run build
cd ../../v2-agent/frontend && npm run build
```

如果只是文档变更，不需要部署服务器。

## 回滚思路

如果线上出问题：

1. 先看服务日志和 Nginx 状态。
2. 确认最近部署提交：

```bash
ssh -i /path/to/private_key ubuntu@124.221.92.85 'cat /opt/realguard-server/DEPLOYED_COMMIT; cat /opt/jianzhen-v2/DEPLOYED_COMMIT'
```

3. 本地切到上一个稳定提交。
4. 重新运行对应部署脚本。

示例：

```bash
git checkout <stable-commit>
DEPLOY_SSH_KEY=/path/to/private_key ./scripts/deploy_v1.sh
DEPLOY_SSH_KEY=/path/to/private_key ./scripts/deploy_v2.sh
git checkout main
```

如果涉及数据库变更，先备份再回滚。

## 给接手人的交接清单

接手前请确认已经拿到：

- GitHub 仓库权限
- 服务器 SSH 权限
- `/etc/realguard/*.env` 的真实配置
- MySQL root 或运维账号权限
- 最近一次 MySQL 备份
- 最近一次检测服务 uploads 备份
- 最近一次签名证据快照备份，并完成抽样验签
- 最近一次证据服务 data 备份
- DashScope / 阿里云短信 / 其他云服务权限
- 管理员账号或重置方式
- 域名和 DNS 管理权限
- 如需完整 ONNX 推理，确认模型权重文件来源

接手后建议先做：

```bash
git clone git@github.com:MuskAI/rearguard.git
cd rearguard
DEPLOY_SSH_KEY=/path/to/private_key ./scripts/check_deploy_status.sh
```

然后登录服务器确认：

```bash
systemctl is-active realguard-backend.service
systemctl is-active realguard-detector-backend.service
systemctl is-active realguard-alert-worker.service
systemctl is-active realguard-alert-watchdog.timer
systemctl is-active realguard-restore-drill.timer
systemctl is-active realguard-security-audit-verify.timer
systemctl is-active jianzhen-v2-backend.service
sudo nginx -t
```

最后用测试账号登录主站，确认：

- 首页可打开
- 图像、视频、文档从同一个上传入口进入，且都返回真实服务结果或明确失败
- 取证图谱、内容凭证和报告操作位于同一结果页
- 使用账号 A、账号 B 各创建一条任务；两边列表、详情、媒体、报告和删除接口都看不到对方数据
- 退出账号后，页面立即清空上一账号的历史和结果
- `/v2` 跳回统一首页，页面中不出现 V1/V2 版本选择
- 管理员后台需要登录后访问
