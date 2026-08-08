# Playground 样本来源

Playground 的素材与参考站点相互独立，避免把参考页面的题库和答案直接复制到慧鉴AI。

## AI 生成样本

`v2-agent/frontend/public/playground/samples/` 中的 `sample-02.webp`、`sample-05.webp`、`sample-09.webp`、`sample-13.webp` 和 `sample-16.webp` 均由 GPT Image 为慧鉴AI Playground 生成。每张图都刻意保留了可供观察的局部线索，仅用于科普小游戏，不作为真实模型评测数据。

## 实拍样本

其余 12 张样本取自 Unsplash 图片 CDN，并在项目中转换为 640 x 640 WebP。前端统一使用中性编号，避免在资源路径中泄露答案。源图片标识如下：

| 文件 | Unsplash 图片标识 |
| --- | --- |
| `sample-01.webp` | [`photo-1503736334956-4c8f8e92946d`](https://images.unsplash.com/photo-1503736334956-4c8f8e92946d) |
| `sample-03.webp` | [`photo-1533738363-b7f9aef128ce`](https://images.unsplash.com/photo-1533738363-b7f9aef128ce) |
| `sample-04.webp` | [`photo-1495474472287-4d71bcdd2085`](https://images.unsplash.com/photo-1495474472287-4d71bcdd2085) |
| `sample-06.webp` | [`photo-1507146426996-ef05306b995a`](https://images.unsplash.com/photo-1507146426996-ef05306b995a) |
| `sample-07.webp` | [`photo-1441974231531-c6227db76b6e`](https://images.unsplash.com/photo-1441974231531-c6227db76b6e) |
| `sample-08.webp` | [`photo-1474511320723-9a56873867b5`](https://images.unsplash.com/photo-1474511320723-9a56873867b5) |
| `sample-10.webp` | [`photo-1500530855697-b586d89ba3ee`](https://images.unsplash.com/photo-1500530855697-b586d89ba3ee) |
| `sample-11.webp` | [`photo-1472214103451-9374bd1c798e`](https://images.unsplash.com/photo-1472214103451-9374bd1c798e) |
| `sample-12.webp` | [`photo-1464822759023-fed622ff2c3b`](https://images.unsplash.com/photo-1464822759023-fed622ff2c3b) |
| `sample-14.webp` | [`photo-1497250681960-ef046c08a56e`](https://images.unsplash.com/photo-1497250681960-ef046c08a56e) |
| `sample-15.webp` | [`photo-1552053831-71594a27632d`](https://images.unsplash.com/photo-1552053831-71594a27632d) |
| `sample-17.webp` | [`photo-1470252649378-9c29740c9fa8`](https://images.unsplash.com/photo-1470252649378-9c29740c9fa8) |

后续新增或替换样本时，请继续保留来源标识，并复核 [Unsplash License](https://unsplash.com/license) 的当前条款。
