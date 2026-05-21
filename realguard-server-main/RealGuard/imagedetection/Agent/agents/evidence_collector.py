from tools.AIGC_Detection.inference_onnx import predict
from tools.meta_data import analyze_image_metadata, classify_metadata_signals


class EvidenceCollector:
    """
    证据收集器（Evidence Gatherer）
    
    对应论文 AIFo 框架中的 Evidence Gatherer Agent。
    负责调用所有可用的取证工具，收集原始证据并进行结构化汇总。
    
    当前可用工具：
        1. 元数据分析工具（Metadata Extraction Tool）
        2. 预训练 AIGC 检测模型（Pre-Trained Classifier Tool）
    """

    def __init__(self, strict: bool = True):
        # strict=True 时，元数据与检测器任一失败都会抛错，避免静默降级
        self.strict = strict

    def collect(self, image_path: str) -> dict:
        """
        对给定图像路径执行全部取证工具，收集并汇总所有证据。

        Args:
            image_path: 待检测图像的文件路径

        Returns:
            dict: 包含以下键的原始证据字典：
                - "metadata_raw"       : 过滤后的原始元数据字段（完整）
                - "metadata_signals"   : 元数据信号分类结果（AI信号/真实信号列表）
                - "detector_probability": 预训练模型预测的 AI 生成概率（float, 0~1）
        """
        evidence = {}

        metadata_raw = {}
        metadata_signals = {
            "ai_signals": [],
            "real_signals": [],
            "has_ai_signal": False,
            "has_real_signal": False,
            "all_metadata": {},
        }

        print("\n[工具0] 正在提取元数据 (ExifTool)...")
        try:
            metadata_raw = analyze_image_metadata(image_path, verbose=False) or {}
            metadata_signals = classify_metadata_signals(metadata_raw, image_path=image_path)
            print(
                f"  元数据字段: {len(metadata_raw)}，"
                f"AI信号={metadata_signals.get('has_ai_signal')}，"
                f"真实信号={metadata_signals.get('has_real_signal')}"
            )
        except EnvironmentError as e:
            if self.strict:
                raise RuntimeError(f"元数据提取失败（ExifTool 不可用）: {e}")
            print(f"  [提示] 跳过元数据: {e}")
        except Exception as e:
            if self.strict:
                raise RuntimeError(f"元数据提取失败: {e}")
            print(f"  [警告] 元数据提取失败，将忽略该维度: {e}")

        evidence["metadata_raw"] = metadata_raw
        evidence["metadata_signals"] = metadata_signals

        # ===== 工具 1：预训练 AIGC 检测模型 =====
        print("\n[工具1] 正在运行预训练 AIGC 检测模型 (Pre-Trained Classifier)...")
        try:
            prob_fake = predict(image_path)
            print(f"  检测结果 - AI生成概率: {prob_fake:.4f} ({prob_fake * 100:.2f}%)")
        except Exception as e:
            if self.strict:
                raise RuntimeError(f"检测模型运行失败: {e}")
            print(f"  [警告] 检测模型运行失败: {e}")
            prob_fake = 0.5  # 无法判断时返回中性值

        evidence["detector_probability"] = prob_fake

        return evidence
