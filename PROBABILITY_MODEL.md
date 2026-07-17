# 慧鉴 AI 风险概率模型

当前线上概率模型版本为 `huijian-evidence-lr-v1`。它把最终数值定义为“自动化伪造风险概率”，而不是司法鉴定置信度。

## 计算流程

1. 图像检测模型给出像素风险基线 `p_pixel`。
2. 可验证的来源证据转换为似然比 `LR`。
3. 同一来源、同一机制产生的重复证据按指数折扣，避免重复计分。
4. 独立来源证据在 odds 空间更新基线：

   `odds(p_final) = odds(p_pixel) * LR_effective ^ 0.75`

5. 没有元数据、没有水印、普通 Logo、摄影师/版权字段以及 Photoshop 软件字段都是中性事件，不会提高风险。

前置检测需要跳过像素模型时，使用保守基础率 `p_base = 0.10`：

`odds(p_precheck) = odds(0.10) * LR_effective`

## 证据强度

| 证据 | 策略似然比 | 说明 |
| --- | ---: | --- |
| 通过签名校验的 AI 生成 C2PA | 1000 | 内容凭证明确声明 AI 生成 |
| 明确的 AI 生成元数据 | 250 | 包含生成工具或参数等高特异性字段 |
| AI 合成编辑声明 | 150 | 表示经过 AI 编辑，不等同于完全生成 |
| 已知 AI 平台可见水印 | 60-240 | 根据水印检测置信度动态计算 |
| 来源凭证或元数据完整性冲突 | 9 | 单独不足以证明 AI 生成 |
| 签名异常的 AI 声明 | 1.5 | 因无法验证，只保留很弱权重 |

同一份失效 C2PA 中的 AI 声明和签名异常归入 `untrusted_provenance` 组，第二项只按 `LR ^ 0.65` 计入。多个同类水印也会折扣。来自已知 AI 平台的水印与独立的元数据完整性冲突可以相互印证；例如水印置信度为 `0.86` 时，前置证据风险约为 `99.19%`。

## 判定边界

- 已知 AI 平台水印 + 明确 AI 元数据：应高于 99%。
- 明显的已知 AI 平台水印 + 独立完整性冲突：应高于 99%。
- 普通 YOLO Logo + 完整性冲突：不得短路像素模型。
- 单独的签名损坏或元数据冲突：不得输出 99%，必须继续像素检测。
- 缺失元数据：保持中性，不再把真实图片推向“伪造”。
- `Artist`、`Copyright`、Photoshop 或“未检测到 AIGC 标记”不得被解释为生成器声明。

## 校准要求

当前似然比是版本化策略先验，API 会返回 `calibrationStatus=policy_prior_pending_dataset_calibration`，不能冒充已经完成统计校准的概率。正式校准需要：

1. 建立按生成器、平台、压缩方式、截图与真实相机分层的标注集。
2. 将训练、校准、测试样本按来源隔离，防止同源泄漏。
3. 在校准集上拟合温度缩放或等距回归，只调整概率映射，不重新训练检测器。
4. 在独立测试集报告 Brier Score、ECE、可靠性图、假阳性率和各子群指标。
5. 每次模型、水印库或元数据解析器升级后重新校准并提升版本号。

实现位置：

- `services/watermark-precheck/evidence_probability.py`
- `v2-agent/backend/app/evidence_probability.py`
- `realguard-server-main/RealGuard/imagedetection/views/probability_fusion.py`
