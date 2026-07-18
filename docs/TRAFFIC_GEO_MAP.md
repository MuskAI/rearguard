# 后台访问地域地图

## 架构

后台运营大屏从本机 Nginx access log 的尾部读取最近 24 小时页面访问，排除静态资源、API、后台、健康检查、失败请求和常见机器人。公网 IPv4 使用 ip2region XDB 在服务器本地解析，最终接口只返回省级聚合计数。

数据链路：

`Nginx access log -> 页面访问过滤 -> 公网 IPv4 去重 -> ip2region 离线查询 -> 省级聚合 -> 管理员大屏`

隐私约束：

- API 响应不包含原始 IP。
- 城市只有至少 2 个独立访客时才显示，并且仅作为省份详情的辅助信息。
- 地图默认展示省级粒度，不提供用户级下钻。
- access log 仍属于受控运维数据，应继续遵循服务器日志的访问权限和保留策略。

## 开源方案选择

- 渲染：Apache ECharts 6.1.0，Apache-2.0。
- IP 归属：ip2region 3.17.0 数据库与 py-ip2region 3.0.4，Apache-2.0 或 MIT。
- 地图数据：DataV GeoAtlas 省级 GeoJSON，通过 MIT 许可的 ChinaGeoJson 项目核验来源。

没有采用在线 IP 查询 API，避免向第三方发送访客 IP。没有采用最初评估的 `@svg-maps/china`，因为其省级 SVG 缺少台湾省，不满足本项目对中国地图完整性的要求。

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `REALGUARD_IP2REGION_XDB` | `/opt/realguard-data/ip2region_v4.xdb` | IPv4 XDB 路径 |
| `REALGUARD_ACCESS_LOG_PATHS` | `/var/log/nginx/access.log,/var/log/nginx/access.log.1` | 逗号分隔的日志路径 |
| `REALGUARD_ACCESS_LOG_TAIL_BYTES` | `4194304` | 每个日志最多读取的尾部字节数 |
| `REALGUARD_TRAFFIC_WINDOW_HOURS` | `24` | 聚合时间窗口 |

部署脚本固定下载 ip2region commit `cd40e3a1d532d645697999d646cf0e10481cef33` 的 IPv4 XDB，并校验 SHA-256：

`6307a9696f5711f84bcb8b25f07894de68a64a0ed4a1cc7e990562dd3084f210`

## 地图合规

当前地图仅在登录后的管理员大屏中使用，包含 34 个省级行政区和南海诸岛数据。若未来把地图公开展示、用于宣传材料或对外数据产品，应在发布前使用自然资源部标准地图服务的最新版底图，并按适用规定完成地图审核与审图号标注。开源许可证不能替代地图内容合规审查。

## 运维检查

```bash
test -r /opt/realguard-data/ip2region_v4.xdb
sudo -u ubuntu test -r /var/log/nginx/access.log
sudo systemctl restart realguard-backend.service
```

登录后台后打开 `/admin/screen`，应看到“访问地图 / 检测趋势”切换项。地图无数据时先检查 XDB、日志读取权限和 Nginx 日志格式。
