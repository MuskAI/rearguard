import { useState } from "react";
import { DetectResult, VERDICT_META, TYPE_LABEL } from "../api";
import ConfidenceRing from "./ConfidenceRing";

interface Props {
  result: DetectResult;
  previewUrl?: string;
  onForensics?: () => void;
  forensicsBusy?: boolean;
  onProvenance?: () => void;
  provenanceBusy?: boolean;
}

export default function ResultCard({
  result,
  previewUrl,
  onForensics,
  forensicsBusy,
  onProvenance,
  provenanceBusy,
}: Props) {
  const meta = VERDICT_META[result.verdict];
  const [showOverlay, setShowOverlay] = useState(true);
  const effectivePreview = previewUrl || result.fileMeta.thumbnail || undefined;
  const isImage = result.fileMeta.type === "image" && effectivePreview;
  const synthid = result.synthid;
  const visibleWatermark = result.visibleWatermark;
  const synthidTone =
    synthid?.evidenceLevel === "strong" ? "#d8412f" :
    synthid?.evidenceLevel === "medium" || synthid?.evidenceLevel === "weak" ? "#d99a2b" :
    "#3fb6a8";
  const visibleTone =
    visibleWatermark?.evidenceLevel === "strong" ? "#d8412f" :
    visibleWatermark?.evidenceLevel === "medium" || visibleWatermark?.evidenceLevel === "weak" ? "#d99a2b" :
    "#3fb6a8";

  return (
    <div className="rounded-2xl border border-ink-600 bg-ink-800 overflow-hidden shadow-sm">
      {/* header */}
      <div
        className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 px-4 sm:px-5 py-3 border-b border-ink-600"
        style={{ background: `linear-gradient(90deg, ${meta.color}22, transparent)` }}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: meta.color }} />
          <span className="font-serif text-lg font-semibold" style={{ color: meta.color }}>
            {meta.label}
          </span>
          <span className="text-xs text-ink-500">· {TYPE_LABEL[result.fileMeta.type]}检测</span>
          {result.cacheHit && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-jade/10 text-jade border border-jade/30">
              缓存复用
            </span>
          )}
        </div>
        <span className="text-xs text-ink-500 break-all">{result.modelVersion}</span>
      </div>

      <div className="p-4 sm:p-5 grid grid-cols-1 md:grid-cols-[auto_1fr] gap-4 sm:gap-5">
        {/* left: ring + preview */}
        <div className="flex flex-col items-center gap-4">
          <ConfidenceRing value={result.confidence} color={meta.color} />
          {isImage && (
            <div className="relative w-full max-w-64 md:w-44 rounded-lg overflow-hidden border border-ink-600">
              <img src={effectivePreview} alt={result.fileMeta.name} className="w-full block" />
              {showOverlay &&
                result.regions.map((rg, i) => (
                  <div
                    key={i}
                    className="absolute border-2 rounded-sm"
                    style={{
                      left: `${rg.x * 100}%`,
                      top: `${rg.y * 100}%`,
                      width: `${rg.w * 100}%`,
                      height: `${rg.h * 100}%`,
                      borderColor: meta.color,
                      boxShadow: `0 0 0 9999px ${meta.color}11 inset`,
                    }}
                  >
                    <span
                      className="absolute -top-4 left-0 text-[9px] px-1 rounded whitespace-nowrap"
                      style={{ background: meta.color, color: "#ffffff" }}
                    >
                      {rg.label} {Math.round(rg.score * 100)}%
                    </span>
                  </div>
                ))}
            </div>
          )}
          {isImage && result.regions.length > 0 && (
            <button
              onClick={() => setShowOverlay((v) => !v)}
              className="text-xs text-brand-cyan hover:underline"
            >
              {showOverlay ? "隐藏可疑区域" : "显示可疑区域"}
            </button>
          )}
        </div>

        {/* right: dimensions + explanation */}
        <div className="space-y-4">
          <div className="space-y-2.5">
            {result.dimensions.map((d) => (
              <div key={d.key}>
                <div className="flex items-start justify-between gap-3 text-xs mb-1">
                  <span className="text-ink-950 shrink-0">{d.label}</span>
                  <span className="text-ink-500 text-right break-words">
                    {d.result} · {Math.round(d.score * 100)}
                  </span>
                </div>
                <div className="h-1.5 rounded-full bg-ink-600 overflow-hidden">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${d.score * 100}%`,
                      background:
                        d.score >= 0.6 ? "#d8412f" : d.score >= 0.4 ? "#d99a2b" : "#3fb6a8",
                    }}
                  />
                </div>
              </div>
            ))}
          </div>

          <div className="rounded-lg bg-ink-900 border border-ink-600 p-3 text-sm text-ink-950 leading-relaxed">
            <span className="text-brand-cyan font-medium">判定依据：</span>
            {result.explanation}
          </div>

          {synthid && (
            <div
              className="rounded-lg border p-3 text-sm leading-relaxed"
              style={{ borderColor: `${synthidTone}55`, background: `${synthidTone}0f` }}
            >
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1 sm:gap-2 mb-2">
                <span className="font-medium" style={{ color: synthidTone }}>
                  SynthID 水印取证
                </span>
                <span className="text-xs text-ink-500">
                  {synthid.supported
                    ? synthid.detected
                      ? `置信度 ${Math.round(synthid.confidence * 100)}%`
                      : "未检出"
                    : "未启用"}
                </span>
              </div>
              <p className="text-ink-950">{synthid.note}</p>
              {synthid.supported && (
                <div className="mt-2 grid grid-cols-1 sm:grid-cols-3 gap-2 text-[11px] text-ink-500 break-words">
                  <span>相位匹配：{Math.round(synthid.phaseMatch * 100)}%</span>
                  <span>模型配置：{synthid.modelProfile}</span>
                  <span>Profile：{synthid.profile || "自动匹配"}</span>
                </div>
              )}
            </div>
          )}

          {visibleWatermark && (
            <div
              className="rounded-lg border p-3 text-sm leading-relaxed"
              style={{ borderColor: `${visibleTone}55`, background: `${visibleTone}0f` }}
            >
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1 sm:gap-2 mb-2">
                <span className="font-medium" style={{ color: visibleTone }}>
                  可见 AI 水印检测
                </span>
                <span className="text-xs text-ink-500">
                  {visibleWatermark.supported
                    ? visibleWatermark.detected
                      ? `置信度 ${Math.round(visibleWatermark.confidence * 100)}%`
                      : "未检出"
                    : "不支持"}
                </span>
              </div>
              <p className="text-ink-950">{visibleWatermark.note}</p>
              {visibleWatermark.detected && (
                <>
                  <div className="mt-2 grid grid-cols-1 sm:grid-cols-3 gap-2 text-[11px] text-ink-500 break-words">
                    <span>来源：{visibleWatermark.provider || "未知角标"}</span>
                    <span>
                      命中：{visibleWatermark.temporal.positiveFrames}/{visibleWatermark.temporal.sampledFrames || 1}
                    </span>
                    <span>跳动：{visibleWatermark.temporal.moving ? "是" : "否"}</span>
                  </div>
                  <div className="mt-3 border-t border-ink-600/70 pt-3">
                    <div className="text-xs font-medium text-ink-950 mb-2">中间证据</div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {visibleWatermark.hits.slice(0, 4).map((hit, idx) => (
                        <div key={`${hit.method}-${idx}`} className="flex gap-3 min-w-0">
                          {hit.crop ? (
                            <img
                              src={hit.crop}
                              alt="可见水印裁剪证据"
                              className="h-20 w-20 shrink-0 rounded-md object-contain bg-ink-900 border border-ink-600"
                              loading="lazy"
                            />
                          ) : (
                            <div className="h-20 w-20 shrink-0 rounded-md bg-ink-900 border border-ink-600" />
                          )}
                          <div className="min-w-0 text-[11px] text-ink-500 leading-relaxed">
                            <div className="text-ink-950 font-medium">抠图 {idx + 1}</div>
                            <div>方法：{hit.method}</div>
                            <div>置信度：{Math.round(hit.confidence * 100)}%</div>
                            {hit.frame !== null && <div>帧：{hit.frame}</div>}
                            <div>
                              位置：x {Math.round(hit.bbox.x * 100)}%, y {Math.round(hit.bbox.y * 100)}%
                            </div>
                            {Object.keys(hit.scores).length > 0 && (
                              <div className="break-words">
                                分数：
                                {Object.entries(hit.scores)
                                  .map(([key, value]) => `${key} ${Math.round(value * 100)}%`)
                                  .join(" / ")}
                              </div>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </>
              )}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-500">
            <span>文件：{result.fileMeta.name}</span>
            <span>大小：{result.fileMeta.size}</span>
            <span>耗时：{result.elapsedMs}ms</span>
            <span>报告号：{result.reportId}</span>
          </div>

          <div className="grid grid-cols-1 sm:flex sm:flex-wrap gap-2">
            <button
              onClick={() => window.print()}
              className="px-3 py-2 sm:py-1.5 text-xs rounded-lg bg-cinnabar/10 text-cinnabar border border-cinnabar/30 hover:bg-cinnabar/15"
            >
              生成鉴定报告
            </button>
            {onForensics && (
              <button
                onClick={onForensics}
                disabled={forensicsBusy}
              className="px-3 py-2 sm:py-1.5 text-xs rounded-lg bg-brand-magenta/10 text-brand-magenta border border-brand-magenta/30 hover:bg-brand-magenta/15 disabled:opacity-50"
              >
                {forensicsBusy ? "分析中…" : "🧬 可解释性取证分析"}
              </button>
            )}
            {onProvenance && (
              <button
                onClick={onProvenance}
                disabled={provenanceBusy}
              className="px-3 py-2 sm:py-1.5 text-xs rounded-lg bg-jade/10 text-jade border border-jade/30 hover:bg-jade/15 disabled:opacity-50"
              >
                {provenanceBusy ? "验证中…" : "🔏 内容凭证验证"}
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="px-5 py-2 text-[11px] text-ink-500 border-t border-ink-600">
        ⚠ {result.disclaimer}
      </div>
    </div>
  );
}
