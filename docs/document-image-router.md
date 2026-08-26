# 文档图片送检 Router

## 目标

Router 回答的是“这张从 PDF / Word 提取出的对象，是否值得进入图像鉴伪模型”，不是判断图片真假。

| 决策 | 含义 | 正式流程 |
| --- | --- | --- |
| 建议送检 | 完整照片、写实图、插画或其他独立视觉作品 | 调用鉴伪模型 |
| 无需检测 | Logo、图标、图表、流程图、UI 截图、表格、纯文字、二维码、装饰与蒙版 | 跳过 |
| 边界项 | 语义或结构证据不足 | 为避免漏检，仍调用鉴伪模型 |

核心质量指标是“应送检图片召回率”。节省调用量排在其后，不能通过激进跳过换取。

## Baseline 设计

1. **结构规则**：先处理 PDF 蒙版、重复对象、透明层、微小对象、纯色块、装饰条和明确的复合论文插图组件。
2. **视觉语义**：对剩余对象批量运行 TinyCLIP INT8 ONNX，区分完整视觉作品与 Logo、图标、图表、流程图、界面截图等版式对象。
3. **保守融合**：只有非视觉作品的综合概率高、类别集中且与“值得鉴伪”分数拉开间隔时才跳过；其余一律送检。

TinyCLIP 采用 `wkcn/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M`。模型卡与官方实现标注 MIT；INT8 ONNX 文件约 23 MB。模型缺失或运行失败时自动退回结构规则，不中断文档检测。

## 安装模型

```bash
cd v2-agent/backend
python scripts/download_document_router_model.py
```

也可以部署到独立目录：

```bash
python scripts/download_document_router_model.py --output /opt/jianzhen-v2/models/document-router/tinyclip-int8
export JIANZHEN_DOCUMENT_ROUTER_MODEL_DIR=/opt/jianzhen-v2/models/document-router/tinyclip-int8
```

## 如何评测

打开 `/?router=1`，上传 PDF 或 DOCX。逐项标注“应该送检 / 应该跳过 / 不确定”，页面会即时计算：

- 应送检召回率：最重要，漏掉的照片会直接列为错误；
- 标注准确率：Router 与人工标注一致的比例；
- 送检精确率：实际送检对象中真正值得鉴伪的比例；
- 模型调用节省率：在不牺牲召回率的前提下衡量效率。

标注保存在当前浏览器本地，可导出 JSON 形成回归集。模型或阈值调整后，应使用同一回归集重新评测。

## 后续训练

第一期不需要从零训练模型。积累人工标注后，优先冻结 TinyCLIP，仅在 512 维图像向量上训练逻辑回归或小型 MLP，并按文档来源拆分训练集和测试集。只有零样本语义分类无法满足召回率时，才考虑微调视觉编码器。

