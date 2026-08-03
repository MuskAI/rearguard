# HuiJianBenchmark v1

这是由 FraudBench 与 RRDataset_final 组成的逻辑合并测试集。清单引用原始图片，不复制图片本体。

## 数据概览

| 来源 | 图片数 | Real | Fake | 分类维度 |
| --- | ---: | ---: | ---: | --- |
| FraudBench | 7,928 | 2,000 | 5,928 | 领域、评论类别、生成器、Review 组 |
| RRDataset_final | 50,999 | 25,499 | 25,500 | 原始、再数字化、传输变换 |
| 合计 | 58,927 | 27,499 | 31,428 | 统一真假标签与层级子类 |

## 标签规则

- FraudBench：`Positive`、`Negative` 是评论类别，两者图片均为真实原图；`DeepFake/<generator>` 为 AI 编辑图。
- RRDataset：`real` 为真实图，`ai` 为 AI 图；`original`、`redigital`、`transfer` 是图像变换方式。
- 未使用目录名 `Positive/Negative` 作为通用真假关键词，避免跨数据集误标。

## 科学划分

清单以 `group_id` 为单位进行确定性 80/10/10 划分。关联图片不会跨越 train、validation、test：

- FraudBench：原图与 6 个生成器的编辑图保持在同一分组。
- RRDataset：同一内容的 original、redigital、transfer 版本保持在同一分组。
- 最终共 19,000 个内容组，跨 split 泄漏数为 0。

| Split | 图片数 | Real | Fake |
| --- | ---: | ---: | ---: |
| train | 47,124 | 22,005 | 25,119 |
| validation | 5,897 | 2,681 | 3,216 |
| test | 5,906 | 2,813 | 3,093 |

## 文件位置

- 清单：`/Volumes/HIKVISION/AIGC鉴伪系统部署/HuiJianBenchmark_v1/huijian_benchmark_v1.jsonl`
- 摘要：`/Volumes/HIKVISION/AIGC鉴伪系统部署/HuiJianBenchmark_v1/huijian_benchmark_v1.summary.json`
- 生成工具：`scripts/build_internal_dataset_manifest.py`

每行 JSON 包含原始路径、真假标签、完整分类路径、子类字典、`group_id`、split 和文件大小。移动原始目录后，需要重新生成清单。
