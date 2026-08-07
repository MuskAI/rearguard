# 慧鉴 AI 统一前端

这里是 `rrreal.cn` 当前使用的 React 前端，包含官网首页、Agent 鉴伪工作台和开发者平台。业务账号、历史记录与开发者数据由 Flask API 提供，证据分析由 FastAPI 服务提供。

## 本地启动

```bash
npm ci
npm run dev
```

默认代理：

| 路径 | 默认目标 |
| --- | --- |
| `/api`、`/image_upload`、`/video_upload` | `http://127.0.0.1:5000` |
| `/v2-api` | `http://127.0.0.1:8848` |

可通过 `VITE_ACCOUNT_API_TARGET` 和 `VITE_API_TARGET` 修改目标地址。

## 提交前检查

```bash
npm run lint
npm run test:layout:install  # 仅首次需要
npm run test:layout
```

`test:layout` 会先生成生产构建，再用真实 Chromium 检查：

- 官网在 `390 / 1440 / 2560px` 下保持横向主标题且没有横向溢出；
- 手机导航支持 Escape 关闭并恢复焦点；
- 鉴伪入口居中，内容类型与模型菜单不重叠；
- 开发者平台移动布局和 API Key 弹窗键盘行为正常。

失败截图和 Trace 位于 `test-results/`，该目录不进入 Git。

## 关键文件

| 文件 | 作用 |
| --- | --- |
| `src/components/OfficialHome.tsx` | 官网首页 |
| `src/App.tsx` | Agent 工作台与任务编排 |
| `src/components/DeveloperPlatform.tsx` | 开发者平台 |
| `src/experience.css` | 官网和工作台视觉语言 |
| `tests/layout/app-layout.spec.ts` | 多视口布局门禁 |

生产发布统一使用仓库根目录的 `scripts/deploy_v2.sh`，不要手工覆盖服务器文件。
