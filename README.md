# 慧鉴 AI 项目交接说明

慧鉴 AI 是一个面向数字内容鉴伪、证据归档和运营监控的 Web 系统。这个仓库包含主站 V1、深度分析 V2、部署脚本、Nginx 模板和测试代码；`RealGuard`、`jianzhen-v2` 等旧名称仅保留在内部服务名和兼容路径中。

本 README 面向项目接手人。请先读完“交接重点”和“哪些东西不在 Git 里”，再开始部署或改代码。

## 交接重点

- GitHub 仓库：`git@github.com:MuskAI/rearguard.git`
- 主分支：`main`
- 生产服务器：`124.221.92.85`
- 生产用户：`ubuntu`
- 当前公网入口：
  - 主站：`https://www.rrreal.cn/`
  - V2 深度分析：`https://www.rrreal.cn/v2/`
  - V2 API：`https://www.rrreal.cn/v2-api/`
  - 应急 IP 入口：`http://124.221.92.85/`
  - Umami 监控：`http://analytics.realguard.cn/`
- 生产服务：
  - `realguard-backend.service`：V1 Flask 后端
  - `realguard-detector-backend.service`：V1 检测后端
  - `jianzhen-v2-backend.service`：V2 FastAPI 后端
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

### V1 主站

V1 是原主站，包含：

- Flask 后端：`realguard-server-main/RealGuard`
- React/Vite 前端：`realguard-server-main/frontend`
- MySQL 主业务库：`system`
- MySQL 检测历史库：`image_detection`
- 图像/视频检测历史、报告、缩略图、短信登录、管理员控制台和运营大屏

生产服务关系：

```text
Nginx :80 -> HTTPS canonical redirect
Nginx :443 (rrreal.cn, www.rrreal.cn)
  ├── /                         -> /var/www/realguard-frontend
  ├── /api/*, /admin/*, /image_upload/*, /video_upload/* -> 127.0.0.1:5000
  ├── /detection-static/*       -> /opt/realguard-server/RealGuard/imagedetection/static
  └── /v2/*, /v2-api/*          -> V2 静态资源和 API

realguard-backend.service       -> Flask app, 127.0.0.1:5000
realguard-detector-backend.service -> detector_backend.py, 127.0.0.1:15001
mysql.service                   -> system / image_detection
```

### V2 深度分析

V2 是深度分析工作台，包含：

- FastAPI 后端：`v2-agent/backend`
- React/Vite/Tailwind 前端：`v2-agent/frontend`
- SQLite 持久化：默认 `/opt/jianzhen-v2/data/jianzhen-v2.sqlite3`
- DashScope OpenAI 兼容接口，默认模型 `qwen3-vl-flash`
- C2PA、可见水印、SynthID、元数据 AI 线索、统一取证摘要

生产服务关系：

```text
/v2/       -> /var/www/v2
/v2-api/*  -> 127.0.0.1:8848

jianzhen-v2-backend.service -> FastAPI, 127.0.0.1:8848
```

### Analytics

`deploy/analytics/` 里是 Umami 监控后台模板。监控不在主部署脚本里自动发布，需要单独维护。

## 仓库结构

```text
.
├── realguard-server-main/
│   ├── RealGuard/              # V1 Flask 后端、模板、SQL、测试
│   ├── frontend/               # V1 React/Vite 前端
│   └── deploy/                 # V1 Nginx 配置
├── v2-agent/
│   ├── backend/                # V2 FastAPI 后端
│   ├── frontend/               # V2 React/Vite/Tailwind 前端
│   └── docker-compose.yml      # V2 容器化示例
├── deploy/
│   ├── nginx/                  # 生产 Nginx 配置模板
│   ├── letsencrypt/            # Certbot 自动续期部署钩子
│   └── analytics/              # Umami 部署模板
├── scripts/
│   ├── deploy_v1.sh            # V1 发布
│   ├── deploy_v2.sh            # V2 发布
│   ├── deploy_converge.sh      # 按需发布 V1/V2
│   └── check_deploy_status.sh  # 线上状态检查
└── skills/
    └── realguard-forensics/    # 内部 agent skill 资料
```

## 这次迁移后的重要变化

- 已迁移到新服务器 `124.221.92.85`。
- 正式域名为 `https://www.rrreal.cn/`，HTTP 和根域名统一跳转到该地址。
- Let's Encrypt 证书覆盖 `rrreal.cn` 与 `www.rrreal.cn`，由 `certbot.timer` 自动续期。
- 首页 UI 已重做为“数字内容鉴伪工作台”。
- 对外产品名统一为“慧鉴 AI”，旧服务名仅作为部署兼容标识保留。
- 生产检测不再生成随机或模拟结论：模型不可用时返回明确错误，不写历史、不生成报告。
- 首页不再展示“管理入口”按钮，普通用户入口只保留任务、历史和报告。
- 侵权检索相关页面、接口和公开文档已移除或由 Nginx 拦截。
- 公开 `developer/API.md` 和 public skill 文件已从前端静态目录删除。
- V1 历史记录匹配逻辑已修复：现在按 `Userid`、`phone`、`openid` 三重匹配，避免旧微信 openid 记录在手机号登录后不可见。
- 仍有一批旧图像记录只有 openid、没有 `Userid`/手机号，不能安全自动归属。需要确认映射关系后再手工绑定。

## 品牌与界面规范

- 品牌标志：扫描框包围玉色镜片，并以朱砂色确认方印收尾，组件位于 `realguard-server-main/frontend/src/components/BrandMark.tsx`。
- 品牌形象：“小鉴”，一个手持放大镜的取证印章助手。它只用于空状态、登录引导和友好提示，不遮挡证据图片或检测结论。
- 主色：墨蓝 `#16324A`、湖蓝 `#1F5F7A`、玉色 `#1B8F7A`；风险色使用朱砂 `#D9573F`，提醒色使用暖黄 `#F2C14E`，页面底色为纸白 `#F7F7F2`。
- 视觉原则：工作台优先、证据优先、少装饰；圆角不超过 `8px`，交互目标至少 `44px`，不要使用大面积渐变、悬浮装饰球或卡片套卡片。
- 结论语言：只有真实模型调用成功且返回明确判定时才展示概率。证据不足时使用“需人工复核”，元数据缺失不能单独作为伪造证据。
- 形象资源：V1 与 V2 分别位于 `realguard-server-main/frontend/public/brand/` 和 `v2-agent/frontend/public/brand/`。

## 哪些东西在 GitHub 里

GitHub 仓库包含：

- V1/V2 后端源码
- V1/V2 前端源码
- SQL schema 和迁移辅助脚本
- Nginx 配置模板
- 发布和状态检查脚本
- 前后端依赖锁文件
- 测试代码

关键依赖文件：

- `realguard-server-main/RealGuard/requirements.txt`
- `realguard-server-main/frontend/package-lock.json`
- `v2-agent/backend/pyproject.toml`
- `v2-agent/backend/uv.lock`
- `v2-agent/frontend/package-lock.json`

## 哪些东西不在 GitHub 里

这些东西不要提交到 GitHub，需要从服务器或备份里单独交接：

- SSH 私钥
- 数据库密码
- 阿里云短信密钥
- DashScope / LLM API Key
- 管理员 token
- MySQL 生产数据
- V2 SQLite 数据
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

### V1 后端

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

### V1 前端

```bash
cd realguard-server-main/frontend
npm ci
npm run dev
```

构建：

```bash
npm run build
```

### V2 后端

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

### V2 前端

```bash
cd v2-agent/frontend
npm ci
npm run dev
```

构建：

```bash
npm run build
```

## 测试和验证

### V1 全量测试

```bash
uv venv realguard-server-main/RealGuard/.venv-test --python 3.13
uv pip install --python realguard-server-main/RealGuard/.venv-test/bin/python \
  flask pymysql pillow requests werkzeug pytest
realguard-server-main/RealGuard/.venv-test/bin/pytest realguard-server-main/RealGuard/tests
```

当前基准：`102 passed`。

### V2 测试

```bash
v2-agent/backend/.venv/bin/pytest v2-agent/backend/tests
```

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

### 发布 V1

```bash
DEPLOY_SSH_KEY=/path/to/private_key ./scripts/deploy_v1.sh
```

脚本会做：

- V1 后端编译
- V1 测试
- V1 前端构建
- 后端打包上传
- 前端 `dist/` 同步到 `/var/www/realguard-frontend/`
- 重启 `realguard-detector-backend.service`
- 重启 `realguard-backend.service`
- 写入 `/opt/realguard-server/DEPLOYED_COMMIT`
- 健康检查

### 发布 V2

```bash
DEPLOY_SSH_KEY=/path/to/private_key ./scripts/deploy_v2.sh
```

脚本会做：

- V2 后端编译和测试
- V2 前端构建
- 后端打包上传
- 前端 `dist/` 同步到 `/var/www/v2/`
- 重启 `jianzhen-v2-backend.service`
- 写入 `/opt/jianzhen-v2/DEPLOYED_COMMIT`
- 健康检查

### 收敛部署

如果不确定 V1/V2 哪个落后，可以用：

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

### V1 上传文件备份

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

### V2 SQLite 数据备份

V2 使用 SQLite，备份时建议先停服务或使用 SQLite 在线备份方式。

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

V1 历史记录主要在：

- `image_detection.data`
- `image_detection.video_data`
- `image_detection.exif`

用户信息主要在：

- `system.user`

当前历史接口用以下字段归属记录：

- `Userid`
- `phone`
- `openid`

原因：旧系统里部分记录来自微信 openid，部分记录来自手机号登录，迁移后如果只按手机号查会漏历史。

注意：仍有旧记录只有 openid，没有 `Userid` 或手机号。不能随便把这类记录展示给任意登录账号，否则会泄露他人历史。需要人工确认 openid 和手机号映射后再更新数据库。

查询未绑定图像记录：

```sql
SELECT COUNT(*)
FROM image_detection.data
WHERE Userid IS NULL AND (phone IS NULL OR phone = '');
```

## 安全说明

- 不要提交 `.env`、私钥、数据库 dump、上传文件、SQLite 数据库。
- 后端服务建议只监听 `127.0.0.1`，公网只开放 Nginx 的 `80/443`。
- Nginx 模板包含敏感文件拦截、基础限流和安全响应头。
- 生产建议尽快补 HTTPS，并把 cookie 设置和代理头一起复核。
- V2 历史、报告和监控接口如需开放，应配置 `JIANZHEN_ACCESS_TOKEN`。
- 管理员账号和大屏 token 只在服务器环境变量或数据库中维护。

## 已知问题和待办

- V1 detector health 可能显示 `degraded`，原因是 `model_deploy.onnx.data` 外部权重文件缺失。服务仍可启动，但如需完整 V1 ONNX 推理，需要补齐该模型文件或确认线上走回退链路。
- 旧图像历史里有部分 openid-only 记录，不能自动安全归属，需要人工确认映射。
- Umami 监控后台不在 `deploy_v1.sh` / `deploy_v2.sh` 自动发布范围内。
- 当前公网示例使用 HTTP。正式对外服务建议配置 HTTPS。
- 生产数据备份流程需要定期自动化，目前 README 只给手动命令。

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
- 最近一次 V1 uploads 备份
- 最近一次 V2 data 备份
- DashScope / 阿里云短信 / 其他云服务权限
- 管理员账号或重置方式
- 域名和 DNS 管理权限
- 如需完整 V1 ONNX 推理，确认模型权重文件来源

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
systemctl is-active jianzhen-v2-backend.service
sudo nginx -t
```

最后用测试账号登录主站，确认：

- 首页可打开
- 图像鉴伪入口可进入
- 视频鉴伪入口可进入
- 历史记录能显示属于该账号的数据
- V2 深度分析页面可打开
- 管理员后台需要登录后访问
