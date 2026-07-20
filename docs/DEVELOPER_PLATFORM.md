# 慧鉴AI 开发者平台交接

## 一期范围

- 只开放图像鉴伪。
- `fast`：主鉴伪模型与可见水印检测。
- `swarm`：多源专家交叉复核。
- 每个登录账号一次性赠送 100 次成功检测。
- 快速与 Swarm 独立定价；一期仅支持管理员手工充值，不接在线支付。
- cURL、Python、Node.js/TypeScript、Java、Go 示例在登录后的“接入文档”页提供。
- 对外 Agent Skill 位于 `skills/huijian-image-forensics/`。

## 请求链路

```text
第三方服务
  -> Nginx 上传限流
  -> Flask /api/openapi/v1/image-detections
  -> API Key、有效期、IP 白名单、scope 校验
  -> 账号额度原子预占
  -> fast 或 swarm 检测
  -> 成功结果持久化
  -> 额度结算与用量流水
```

证据服务的开发者 Key 直连默认关闭，避免跳过统一额度与账本：

```env
JIANZHEN_ALLOW_DIRECT_DEVELOPER_KEYS=false
```

## API

所有请求都使用：

```http
Authorization: Bearer rg_sk_...
```

创建任务：

```http
POST /api/openapi/v1/image-detections
Content-Type: multipart/form-data

image=<binary>
mode=fast|swarm
```

必须提供请求头 `Idempotency-Key`，长度为 8 到 128 个可见 ASCII 字符。同一账号、模式和文件可安全重试；同一键用于不同内容返回 `409`。每个新的业务请求应生成一个 UUID，并在网络重试时复用它。

查询与报告：

```text
GET /api/openapi/v1/image-detections/{task_id}
GET /api/openapi/v1/image-detections/{task_id}/report
```

任务只对所属开发者账号可见。不同账号统一返回 `404`，不泄露任务是否存在。报告为 PDF。

## 状态与结算

任务状态：

```text
queued -> running -> success
                  -> failed
queued            -> rejected
```

结算状态：

```text
reserved -> settled   仅成功任务
reserved -> released  失败或未调度任务
```

预占过程使用 InnoDB 事务和 `SELECT ... FOR UPDATE`，并发请求不能超卖赠送额度或余额。成功结果先写入任务表，再执行结算；若结算发生短暂故障，状态查询会重试结算。

## 数据表

- `developer_api_keys`：Key 哈希、scope、有效期、IP 白名单、最后使用信息。
- `developer_accounts`：赠送总额、已使用/预占、余额、预占余额。
- `developer_pricing`：`fast` 与 `swarm` 单价和启用状态。
- `developer_detection_tasks`：账号隔离的异步任务与公开结果快照。
- `developer_billing_reservations`：每个任务的预占和结算状态。
- `developer_billing_ledger`：赠送消费、付费扣款和管理员调整流水。
- `developer_usage_events`：端点、模型与 Token 用量统计。

升级命令：

```bash
cd /opt/realguard-server/RealGuard
set -a
. /etc/realguard/realguard-backend.env
[ ! -f /etc/realguard/detector-db.env ] || . /etc/realguard/detector-db.env
set +a
/opt/realguard-server/.venv/bin/python -m flask --app run:app developer-db-upgrade
```

对应 SQL：`realguard-server-main/RealGuard/sql/migrations/20260717_developer_platform.sql`。

## 管理员操作

管理员接口要求后台登录会话、权限 `api_key.manage` 和 `X-CSRF-Token`。

```text
GET  /api/admin/developer/pricing
POST /api/admin/developer/pricing
POST /api/admin/developer/accounts/{user_id}/adjust
```

调价请求：

```json
{"mode":"fast","unitPriceFen":10,"enabled":true}
```

手工充值或调整赠送总额：

```json
{"balanceDeltaFen":5000,"freeTotalDelta":0,"note":"线下充值 50 元"}
```

所有调整写入 `developer_billing_ledger` 与管理员审计。余额不能低于已预占金额，赠送总额不能低于已使用和已预占次数。

## 常见响应

- `401`：Key 缺失、无效、过期或已撤销。
- `402`：赠送额度耗尽且付费未启用，或余额不足。
- `403`：scope 不足或来源 IP 不在白名单。
- `409`：幂等键冲突，或任务尚未完成时请求报告。
- `413`：图片超过 25 MB。
- `429`：Nginx 上传限流；客户端应退避，不要重复创建任务。

## 发布验证

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://www.rrreal.cn/api/developer/account
curl -sS -o /dev/null -w '%{http_code}\n' https://www.rrreal.cn/api/openapi/v1/image-detections
```

未登录/未携带 Key 时两项都应返回 `401`。之后使用测试账号创建临时 Key，分别完成一次 `fast` 与 `swarm`，确认任务成功、报告为 `application/pdf`、跨账号任务返回 `404`，最后撤销临时 Key。
