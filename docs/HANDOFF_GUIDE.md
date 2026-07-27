# 慧鉴 AI 开发与运维指南

这份指南面向项目接手人。根目录 [README](../README.md) 用来理解系统，本文件用来完成日常开发、测试和发布。

## 1. 第一天先做什么

| 步骤 | 操作 | 完成标准 |
| --- | --- | --- |
| 1 | 获得 GitHub 仓库权限并克隆 `main` | `git status` 干净 |
| 2 | 获得公网服务器 SSH 权限 | 能登录 `ubuntu@124.221.92.85` |
| 3 | 向负责人单独领取环境变量和备份访问权 | 不通过聊天或 Git 传密钥 |
| 4 | 安装 Python 3.12+、Node.js 20+、npm、`uv` | 能运行下方测试 |
| 5 | 阅读本页的“改哪里”和“发布” | 知道每类修改的归属 |

生产数据、SSH 私钥、数据库密码、短信和 LLM Key **不在 GitHub**。源码和数据必须分开交接。

## 2. 技术栈

| 层 | 技术 | 主要职责 |
| --- | --- | --- |
| 统一前端 | React、TypeScript、Vite | 官网、Agent 工作台、历史和开发者平台 |
| 业务后端 | Flask、Gunicorn、MySQL | 登录、账号隔离、任务、历史、计费和后台管理 |
| 证据后端 | FastAPI、Uvicorn、SQLite | C2PA、元数据、水印和取证证据 |
| 模型服务 | ONNX Runtime、CUDA | 66 服务器上的主鉴伪模型和水印模型 |
| 异步任务 | MySQL 持久队列、Worker、长轮询 | 两路 GPU 调度、重试和视觉 LLM 补充 |
| 网关与运维 | Nginx、systemd、Bash | HTTPS、路由、部署、备份和健康检查 |

## 3. 本地开发

### 3.1 业务后端

```bash
cd realguard-server-main/RealGuard
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py
```

默认地址为 `http://127.0.0.1:5000`。完整检测需要 MySQL、模型服务和对应环境变量；没有生产配置时仍可开发页面并运行单元测试。

### 3.2 证据后端

```bash
cd v2-agent/backend
cp .env.example .env
uv sync --frozen
uv run uvicorn app.main:app --host 127.0.0.1 --port 8848
```

### 3.3 统一前端

```bash
cd v2-agent/frontend
npm ci
npm run dev
```

Vite 默认把账户与检测请求代理到 `5000`，把 `/v2-api` 代理到 `8848`。公网首页使用的就是这个前端。

## 4. 要改功能时去哪里

| 需求 | 主要目录 |
| --- | --- |
| 官网、工作台、结果页、历史页 | `v2-agent/frontend/src/` |
| 登录、用户隔离、历史和报告 | `realguard-server-main/RealGuard/imagedetection/views/` |
| 检测任务、Swarm、视觉 LLM | `detection.py`、`developer_platform.py` |
| 后台管理和内部测试平台 | `imagedetection/views/admin.py`、`internal_testing.py` |
| C2PA、元数据、取证图谱 | `v2-agent/backend/app/` |
| GPU 主模型服务 | `services/realguard-detection/` |
| 水印检测 | `services/watermark-precheck/`、`services/yolo-watermark/` |
| Nginx 和 systemd | `deploy/nginx/`、`deploy/systemd/` |
| 发布、备份和压测 | `scripts/` |

修改鉴伪结论前先阅读 [PROBABILITY_MODEL.md](../PROBABILITY_MODEL.md)。开发者 API 的细节见 [DEVELOPER_PLATFORM.md](DEVELOPER_PLATFORM.md)。

研究辅助脚本不参与生产发布：

| 脚本 | 用途 |
| --- | --- |
| `build_aigc_reading_report.mjs` | 根据结构化论文笔记生成阅读报告 |
| `download_selected_aigc_papers.py` | 根据清单下载论文 PDF |
| `extract_ppt_paper_texts.py` | 从论文 PDF 提取汇报用文本 |

它们默认从仓库下的 `reports/` 读取和输出，也可以通过 `AIGC_*` 环境变量改目录。`reports/` 和 `research/` 是本地生成资料，不进入生产代码仓库。

## 5. 提交前检查

### 后端测试

```bash
cd realguard-server-main/RealGuard
uv venv .venv-test --python 3.12
uv pip install --python .venv-test/bin/python -r requirements.txt pytest
.venv-test/bin/python -m pytest tests
```

```bash
cd v2-agent/backend
uv run --with pytest pytest tests
```

### 前端检查

```bash
cd v2-agent/frontend
npm ci
npm run lint
npm run build
```

旧前端只用于兼容和回滚，但改动相关代码时也要执行：

```bash
cd realguard-server-main/frontend
npm ci
npm run build
```

提交前确认：

```bash
git diff --check
git status --short
```

禁止提交 `.env`、私钥、生产数据库、上传文件、日志和测试报告。

## 6. 生产发布

发布脚本默认连接 `ubuntu@124.221.92.85`，要求工作区中的发布文件已经提交。

```bash
export DEPLOY_SSH_KEY=/path/to/your/private_key
```

| 修改范围 | 命令 |
| --- | --- |
| 统一前端或证据后端 | `./scripts/deploy_v2.sh` |
| 登录、历史、后台或检测编排 | `./scripts/deploy_v1.sh` |
| 66 GPU 模型或水印服务 | `./scripts/deploy_detection_service.sh` |
| 不确定哪些服务落后 | `./scripts/deploy_converge.sh` |

同时改动 V1 和 V2 时：

```bash
./scripts/deploy_v2.sh
./scripts/deploy_v1.sh
```

脚本会运行测试、构建、备份、原子切换和健康检查。不要手工覆盖 `/opt` 或 `/var/www` 下的线上文件。

发布后验证：

```bash
STRICT=1 ./scripts/check_deploy_status.sh
curl -I https://www.rrreal.cn/
```

## 7. 线上服务与日志

| 服务 | 端口 | 作用 |
| --- | ---: | --- |
| `realguard-backend` | 5000 | Flask 业务后端 |
| `realguard-detector-backend` | 15001 | 公网服务器上的检测代理 |
| `realguard-developer-worker` | 无 | 异步任务 Worker |
| `jianzhen-v2-backend` | 8848 | FastAPI 证据服务 |
| `nginx` | 80/443 | 公网入口 |

```bash
ssh -i "$DEPLOY_SSH_KEY" ubuntu@124.221.92.85
systemctl status realguard-backend realguard-detector-backend \
  realguard-developer-worker jianzhen-v2-backend nginx
journalctl -u realguard-backend -n 200 --no-pager
journalctl -u realguard-developer-worker -n 200 --no-pager
```

健康检查：

```bash
curl -fsS http://127.0.0.1:5000/api/ready
curl -fsS http://127.0.0.1:15001/ready
curl -fsS http://127.0.0.1:8848/api/ready
```

## 8. 数据、备份与安全

| 内容 | 位置 |
| --- | --- |
| 生产环境变量 | `/etc/realguard/*.env` |
| MySQL 业务数据 | `system`、`image_detection` |
| V2 SQLite | `/opt/jianzhen-v2/data/` |
| 用户上传文件 | `/opt/realguard-server/RealGuard/imagedetection/static/uploads/` |
| 备份 | `/var/backups/realguard/` |

```bash
systemctl status realguard-backup.timer
sudo systemctl start realguard-backup.service
sudo sh -c 'cd /var/backups/realguard/latest && sha256sum -c SHA256SUMS'
```

关键规则：

1. 用户历史只能按不可变 `account_uuid` 查询，不能用手机号或自增 ID 回退匹配。
2. 模型不可用时返回失败，禁止生成 Mock 或随机结论。
3. 视觉 LLM 是异步补充证据，不覆盖已经发布的主模型结论。
4. 最终标签只使用“真实图像”或“AI生成图像”；不确定性写入解释。
5. 修改 Nginx 前先运行 `sudo nginx -t`。

## 9. 常见问题

| 现象 | 先检查 |
| --- | --- |
| 页面 502/503 | `systemctl status` 和对应 `journalctl` |
| 检测长时间排队 | Worker 心跳、GPU readiness、队列数量 |
| 检测快但视觉说明未出现 | `visual_review` 子任务和 V2 服务日志 |
| 不同账号看到同一记录 | 立即停止发布，检查 `account_uuid` 过滤和缓存头 |
| 前端改动未生效 | 浏览器资源哈希、`/var/www/v2` 和 V2 部署提交 |
| 部署被拒绝 | 发布路径有未提交修改，先检查 `git status` |

## 10. 最终交接清单

- [ ] GitHub 管理权限已移交
- [ ] 公网服务器和 66 GPU 服务器权限已移交
- [ ] DNS、云平台、短信和 LLM 账号已单独移交
- [ ] 最近一次备份已校验
- [ ] 新接手人独立完成一次测试、部署和回滚演练
- [ ] 未在 Git、README 或聊天记录中泄露任何密钥
