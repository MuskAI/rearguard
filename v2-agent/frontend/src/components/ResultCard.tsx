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
  const isImage = result.fileMeta.type === "image" && previewUrl;

  return (
    <div className="rounded-2xl border border-ink-600 bg-ink-800 overflow-hidden shadow-sm">
      {/* header */}
      <div
        className="flex items-center justify-between px-5 py-3 border-b border-ink-600"
        style={{ background: `linear-gradient(90deg, ${meta.color}22, transparent)` }}
      >
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: meta.color }} />
          <span className="font-serif text-lg font-semibold" style={{ color: meta.color }}>
            {meta.label}
          </span>
          <span className="text-xs text-ink-500">· {TYPE_LABEL[result.fileMeta.type]}检测</span>
        </div>
        <span className="text-xs text-ink-500">{result.modelVersion}</span>
      </div>

      <div className="p-5 grid grid-cols-1 md:grid-cols-[auto_1fr] gap-5">
        {/* left: ring + preview */}
        <div className="flex flex-col items-center gap-4">
          <ConfidenceRing value={result.confidence} color={meta.color} />
          {isImage && (
            <div className="relative w-44 rounded-lg overflow-hidden border border-ink-600">
              <img src={previewUrl} alt={result.fileMeta.name} className="w-full block" />
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
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-ink-950">{d.label}</span>
                  <span className="text-ink-500">
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

          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-500">
            <span>文件：{result.fileMeta.name}</span>
            <span>大小：{result.fileMeta.size}</span>
            <span>耗时：{result.elapsedMs}ms</span>
            <span>报告号：{result.reportId}</span>
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => window.print()}
              className="px-3 py-1.5 text-xs rounded-lg bg-cinnabar/10 text-cinnabar border border-cinnabar/30 hover:bg-cinnabar/15"
            >
              生成鉴定报告
            </button>
            {onForensics && (
              <button
                onClick={onForensics}
                disabled={forensicsBusy}
              className="px-3 py-1.5 text-xs rounded-lg bg-brand-magenta/10 text-brand-magenta border border-brand-magenta/30 hover:bg-brand-magenta/15 disabled:opacity-50"
              >
                {forensicsBusy ? "分析中…" : "🧬 可解释性取证分析"}
              </button>
            )}
            {onProvenance && (
              <button
                onClick={onProvenance}
                disabled={provenanceBusy}
              className="px-3 py-1.5 text-xs rounded-lg bg-jade/10 text-jade border border-jade/30 hover:bg-jade/15 disabled:opacity-50"
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
