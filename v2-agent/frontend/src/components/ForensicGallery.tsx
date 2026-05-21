import { useState } from "react";
import { ForensicReport, ForensicItem, STATUS_META, VERDICT_META } from "../api";

export default function ForensicGallery({ report }: { report: ForensicReport }) {
  const meta = VERDICT_META[report.verdict];
  const [zoom, setZoom] = useState<ForensicItem | null>(null);

  return (
    <div className="rounded-2xl border border-ink-600 bg-ink-800 overflow-hidden shadow-sm">
      <div
        className="px-5 py-3 border-b border-ink-600 flex items-center justify-between"
        style={{ background: `linear-gradient(90deg, ${meta.color}22, transparent)` }}
      >
        <div className="flex items-center gap-2">
          <span className="text-lg">🧬</span>
          <span className="font-serif text-lg font-semibold text-rice">可解释性取证分析</span>
          <span className="text-xs text-ink-500">（共 {report.items.length} 项）</span>
        </div>
        <span className="text-xs font-medium" style={{ color: meta.color }}>
          {meta.label} · {Math.round(report.confidence * 100)}%
        </span>
      </div>

      <div className="p-5">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {report.items.map((it) => {
            const s = STATUS_META[it.status];
            return (
              <div key={it.key} className="rounded-xl border border-ink-600 bg-ink-900 overflow-hidden flex flex-col">
                <button
                  onClick={() => setZoom(it)}
                  className="block aspect-[4/3] bg-ink-700 overflow-hidden"
                  title="点击放大"
                >
                  <img src={it.image} alt={it.title} className="w-full h-full object-contain" />
                </button>
                <div className="p-3 flex-1 flex flex-col gap-1.5">
                  <div className="flex items-center gap-1.5">
                    <span>{s.dot}</span>
                    <span className="text-sm font-medium text-ink-950">{it.title}</span>
                  </div>
                  <p className="text-xs leading-relaxed" style={{ color: s.color }}>
                    {it.finding}
                  </p>
                  <p className="text-[11px] text-ink-500 leading-relaxed mt-auto pt-1 border-t border-ink-700">
                    💡 {it.explanation}
                  </p>
                </div>
              </div>
            );
          })}
        </div>

        <div className="mt-5 rounded-xl bg-ink-900 border border-ink-600 p-4">
          <div className="text-sm font-semibold text-brand-cyan mb-1">📊 综合判定</div>
          <p className="text-sm text-ink-950 leading-relaxed whitespace-pre-wrap">{report.summary}</p>
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-500">
            <span>来源：{report.source === "vlm" ? `模型判读 (${report.modelVersion})` : "仅可视化证据"}</span>
            <span>耗时：{report.elapsedMs}ms</span>
            <span>文件：{report.fileMeta.name}</span>
          </div>
        </div>
      </div>

      {zoom && (
        <div
          className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-8"
          onClick={() => setZoom(null)}
        >
          <div className="max-w-4xl max-h-full flex flex-col items-center gap-3" onClick={(e) => e.stopPropagation()}>
            <img src={zoom.image} alt={zoom.title} className="max-h-[80vh] object-contain rounded-lg" />
            <div className="text-center">
              <div className="text-slate-100 font-medium">
                {STATUS_META[zoom.status].dot} {zoom.title}
              </div>
              <p className="text-sm text-slate-400 mt-1 max-w-2xl">{zoom.finding}</p>
            </div>
            <button onClick={() => setZoom(null)} className="text-xs text-slate-400 hover:text-white">
              点击任意处关闭
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
