import { useEffect, useMemo, useState } from "react";
import { Metrics, TYPE_LABEL, VERDICT_META, fetchMetrics } from "../api";

function pct(value: number) {
  return `${Math.round(value * 100)}%`;
}

function compactDate(value: string) {
  return value.slice(5).replace("-", "/");
}

export default function AdminDashboard({
  onBack,
  onConfigureAccess,
  accessProtectionEnabled,
}: {
  onBack: () => void;
  onConfigureAccess: () => void;
  accessProtectionEnabled: boolean;
}) {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [error, setError] = useState<string>("");
  const [days, setDays] = useState<7 | 14 | 30>(() => getInitialMonitorDays());
  const [copied, setCopied] = useState(false);

  const load = () =>
    fetchMetrics(days)
      .then((data) => {
        setMetrics(data);
        setError("");
      })
      .catch((e) => setError(e instanceof Error ? e.message : "加载失败"));

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 30000);
    return () => window.clearInterval(timer);
  }, [days]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    if (days === 14) params.delete("monitorDays");
    else params.set("monitorDays", String(days));
    const next = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${next ? `?${next}` : ""}${window.location.hash}`);
  }, [days]);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1800);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const maxDay = useMemo(
    () => Math.max(1, ...(metrics?.byDay.map((d) => d.detections) ?? [1])),
    [metrics],
  );
  const sourceColors = {
    vlm: "#3fb6a8",
    mock: "#d8412f",
    "maps-only": "#d99a2b",
    unknown: "#7c8aa5",
  } as const;
  const evidenceColors = {
    visibleWatermarkHits: "#d8412f",
    synthidHits: "#3b82f6",
    forensicsCompleted: "#d99a2b",
    provenanceCompleted: "#3fb6a8",
  } as const;

  if (!metrics) {
    return (
      <main className="flex-1 min-w-0 bg-grid p-4 sm:p-6 overflow-y-auto">
        <div className="text-sm text-ink-500">{error || "正在加载监控数据…"}</div>
      </main>
    );
  }

  const cards = [
    { label: "今日检测", value: metrics.summary.todayDetections },
    { label: "总检测量", value: metrics.summary.totalDetections },
    { label: "今日访客IP", value: metrics.summary.uniqueClientsToday },
    { label: "平均耗时", value: `${metrics.summary.avgLatencyMs}ms` },
    { label: "缓存命中率", value: pct(metrics.summary.cacheHitRate) },
    { label: "缓存样本", value: metrics.summary.cacheEntries },
  ];
  const recentBase = Math.max(1, metrics.summary.recentDetections || metrics.summary.totalDetections || 1);
  const vlmRate = (metrics.bySource.vlm ?? 0) / recentBase;
  const watermarkRate = metrics.evidence.visibleWatermarkHits / recentBase;
  const forensicsRate = metrics.evidence.forensicsCompleted / recentBase;
  const provenanceRate = metrics.evidence.provenanceCompleted / recentBase;

  async function copyCurrentView() {
    const url = window.location.href;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      window.prompt("复制当前监控视图链接", url);
    }
  }

  return (
    <main className="flex-1 min-w-0 bg-grid overflow-y-auto">
      <header className="sticky top-0 z-10 px-4 sm:px-6 py-3 border-b border-ink-700 bg-ink-800/95 flex items-center justify-between gap-3">
        <div>
          <h1 className="font-serif text-lg sm:text-xl font-semibold text-rice tracking-wide">监控大屏</h1>
          <p className="text-[11px] sm:text-xs text-ink-500">检测流量、缓存效率与接口健康状态</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="hidden md:inline text-[11px] px-2.5 py-1 rounded-full border border-ink-600 text-ink-500">
            当前窗口 {days} 天
          </span>
          <label className="sm:hidden">
            <span className="sr-only">选择监控时间窗口</span>
            <select
              value={days}
              onChange={(event) => setDays(Number(event.target.value) as 7 | 14 | 30)}
              className="h-9 rounded-lg border border-ink-600 bg-ink-900 px-2 text-xs text-ink-950"
            >
              <option value={7}>7天</option>
              <option value={14}>14天</option>
              <option value={30}>30天</option>
            </select>
          </label>
          <div className="hidden sm:flex items-center gap-1 rounded-lg border border-ink-600 bg-ink-900 p-1">
            {[7, 14, 30].map((value) => (
              <button
                key={value}
                onClick={() => setDays(value as 7 | 14 | 30)}
                className={`h-7 px-2 rounded-md text-[11px] ${
                  days === value ? "bg-cinnabar text-white" : "text-ink-500"
                }`}
              >
                {value}天
              </button>
            ))}
          </div>
          {accessProtectionEnabled && (
            <button
              onClick={onConfigureAccess}
              className="h-9 px-3 rounded-lg border border-ink-600 bg-ink-900 text-xs text-ink-950 hover:border-brand-cyan/50"
            >
              访问令牌
            </button>
          )}
          <button
            onClick={copyCurrentView}
            className={`h-9 px-3 rounded-lg border text-xs ${
              copied
                ? "border-jade/40 bg-jade/10 text-jade"
                : "border-ink-600 bg-ink-900 text-ink-950 hover:border-brand-cyan/50"
            }`}
          >
            {copied ? "已复制" : "复制视图"}
          </button>
          <button
            onClick={load}
            className="h-9 px-3 rounded-lg border border-ink-600 bg-ink-900 text-xs text-ink-950 hover:border-jade/50"
          >
            刷新
          </button>
          <button
            onClick={onBack}
            className="h-9 px-3 rounded-lg bg-cinnabar text-xs text-white"
          >
            返回检测
          </button>
        </div>
      </header>

      <div className="p-4 sm:p-6 space-y-4 sm:space-y-5">
        {error && <div className="rounded-lg border border-cinnabar/30 bg-cinnabar/10 p-3 text-sm text-cinnabar">{error}</div>}

        <section className="grid grid-cols-2 lg:grid-cols-6 gap-3">
          {cards.map((card) => (
            <div key={card.label} className="rounded-xl border border-ink-600 bg-ink-800 p-3 sm:p-4">
              <div className="text-[11px] text-ink-500">{card.label}</div>
              <div className="mt-1 text-xl sm:text-2xl font-semibold text-rice">{card.value}</div>
            </div>
          ))}
        </section>

        <section className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {[
            { label: "VLM覆盖率", value: pct(vlmRate) },
            { label: "水印命中率", value: pct(watermarkRate) },
            { label: "取证完成率", value: pct(forensicsRate) },
            { label: "凭证完成率", value: pct(provenanceRate) },
          ].map((card) => (
            <div key={card.label} className="rounded-xl border border-ink-600 bg-ink-900 p-3 sm:p-4">
              <div className="text-[11px] text-ink-500">{card.label}</div>
              <div className="mt-1 text-lg sm:text-xl font-semibold text-rice">{card.value}</div>
            </div>
          ))}
        </section>

        <section className="grid grid-cols-1 xl:grid-cols-[1.5fr_1fr] gap-4">
          <div className="rounded-xl border border-ink-600 bg-ink-800 p-4">
            <div className="flex items-center justify-between gap-3 mb-4">
              <h2 className="font-serif text-base font-semibold text-rice">近 {days} 日检测趋势</h2>
              <span className="text-xs text-ink-500">最近 {metrics.summary.recentDetections} 次</span>
            </div>
            <div className="h-56 flex items-end gap-1.5 sm:gap-2">
              {metrics.byDay.map((day) => (
                <div key={day.date} className="flex-1 min-w-0 flex flex-col items-center justify-end gap-2">
                  <div className="w-full rounded-t bg-gradient-to-t from-jade to-cinnabar/80 min-h-1" style={{ height: `${(day.detections / maxDay) * 100}%` }} />
                  <div className="text-[10px] text-ink-500 whitespace-nowrap">{compactDate(day.date)}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-xl border border-ink-600 bg-ink-800 p-4">
            <h2 className="font-serif text-base font-semibold text-rice mb-4">判定分布</h2>
            <div className="space-y-3">
              {(["real", "suspected_fake", "highly_suspected_fake"] as const).map((key) => {
                const meta = VERDICT_META[key];
                const count = metrics.byVerdict[key] ?? 0;
                const total = Math.max(1, Object.values(metrics.byVerdict).reduce((a, b) => a + (b ?? 0), 0));
                return (
                  <div key={key}>
                    <div className="flex justify-between text-xs mb-1">
                      <span style={{ color: meta.color }}>{meta.label}</span>
                      <span className="text-ink-500">{count}</span>
                    </div>
                    <div className="h-2 rounded-full bg-ink-600 overflow-hidden">
                      <div className="h-full rounded-full" style={{ width: `${(count / total) * 100}%`, background: meta.color }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </section>

        <section className="rounded-xl border border-ink-600 bg-ink-800 p-4">
          <div className="flex items-center justify-between gap-3 mb-4">
            <h2 className="font-serif text-base font-semibold text-rice">按来源日趋势</h2>
            <div className="flex flex-wrap gap-3 text-[11px] text-ink-500">
              <span><span style={{ color: sourceColors.vlm }}>■</span> VLM</span>
              <span><span style={{ color: sourceColors.mock }}>■</span> Mock</span>
              <span><span style={{ color: sourceColors["maps-only"] }}>■</span> 仅证据图</span>
            </div>
          </div>
          <div className="h-56 flex items-end gap-1.5 sm:gap-2">
            {metrics.byDay.map((day) => (
              <div key={`${day.date}-source`} className="flex-1 min-w-0 flex flex-col items-center justify-end gap-2">
                <div className="w-full h-full flex flex-col justify-end rounded-t overflow-hidden bg-ink-900/40">
                  {(["vlm", "mock", "maps-only"] as const).map((key) => {
                    const total = Math.max(1, day.detections);
                    const height = `${(day.sources[key] / total) * ((day.detections / maxDay) * 100)}%`;
                    return (
                      <div
                        key={key}
                        style={{ height, background: sourceColors[key] }}
                        title={`${compactDate(day.date)} ${key}: ${day.sources[key]}`}
                      />
                    );
                  })}
                </div>
                <div className="text-[10px] text-ink-500 whitespace-nowrap">{compactDate(day.date)}</div>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-xl border border-ink-600 bg-ink-800 p-4">
          <div className="flex items-center justify-between gap-3 mb-4">
            <h2 className="font-serif text-base font-semibold text-rice">按证据日趋势</h2>
            <div className="flex flex-wrap gap-3 text-[11px] text-ink-500">
              <span><span style={{ color: evidenceColors.visibleWatermarkHits }}>■</span> 可见水印</span>
              <span><span style={{ color: evidenceColors.synthidHits }}>■</span> SynthID</span>
              <span><span style={{ color: evidenceColors.forensicsCompleted }}>■</span> 取证</span>
              <span><span style={{ color: evidenceColors.provenanceCompleted }}>■</span> 凭证</span>
            </div>
          </div>
          <div className="space-y-3">
            {[
              { key: "visibleWatermarkHits", label: "可见水印" },
              { key: "synthidHits", label: "SynthID" },
              { key: "forensicsCompleted", label: "取证完成" },
              { key: "provenanceCompleted", label: "凭证完成" },
            ].map((item) => {
              const maxValue = Math.max(1, ...metrics.byDay.map((day) => day.evidence[item.key as keyof typeof day.evidence]));
              return (
                <div key={item.key}>
                  <div className="flex justify-between text-xs mb-1.5">
                    <span className="text-ink-950">{item.label}</span>
                    <span className="text-ink-500">
                      {metrics.evidence[item.key as keyof typeof metrics.evidence]}
                    </span>
                  </div>
                  <div className="h-16 flex items-end gap-1">
                    {metrics.byDay.map((day) => {
                      const value = day.evidence[item.key as keyof typeof day.evidence];
                      return (
                        <div key={`${day.date}-${item.key}`} className="flex-1 min-w-0 flex flex-col items-center justify-end gap-1">
                          <div
                            className="w-full rounded-t"
                            style={{
                              height: `${(value / maxValue) * 100}%`,
                              minHeight: value ? 3 : 0,
                              background: evidenceColors[item.key as keyof typeof evidenceColors],
                            }}
                            title={`${compactDate(day.date)} ${item.label}: ${value}`}
                          />
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="rounded-xl border border-ink-600 bg-ink-800 p-4">
            <h2 className="font-serif text-base font-semibold text-rice mb-4">内容类型</h2>
            <div className="grid grid-cols-2 gap-2">
              {(["image", "video", "audio", "document"] as const).map((type) => (
                <div key={type} className="rounded-lg bg-ink-900 border border-ink-600 p-3">
                  <div className="text-xs text-ink-500">{TYPE_LABEL[type]}</div>
                  <div className="mt-1 text-lg font-semibold text-rice">{metrics.byType[type] ?? 0}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-xl border border-ink-600 bg-ink-800 p-4">
            <h2 className="font-serif text-base font-semibold text-rice mb-4">接口异常</h2>
            {metrics.recentErrors.length === 0 ? (
              <div className="rounded-lg bg-jade/10 border border-jade/25 p-3 text-sm text-jade">最近未记录到 4xx/5xx 异常。</div>
            ) : (
              <div className="space-y-2">
                {metrics.recentErrors.map((item) => (
                  <div key={`${item.createdAt}-${item.path}`} className="flex items-center justify-between gap-2 rounded-lg bg-ink-900 border border-ink-600 p-2 text-xs">
                    <span className="text-ink-950 truncate">{item.path}</span>
                    <span className="text-cinnabar">{item.status}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>

        <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="rounded-xl border border-ink-600 bg-ink-800 p-4">
            <h2 className="font-serif text-base font-semibold text-rice mb-4">分析来源</h2>
            <div className="grid grid-cols-2 gap-2">
              {[
                { key: "vlm", label: "真实模型" },
                { key: "mock", label: "Mock 回退" },
                { key: "maps-only", label: "仅证据图" },
                { key: "unknown", label: "未知来源" },
              ].map((item) => (
                <div key={item.key} className="rounded-lg bg-ink-900 border border-ink-600 p-3">
                  <div className="text-xs text-ink-500">{item.label}</div>
                  <div className="mt-1 text-lg font-semibold text-rice">
                    {metrics.bySource[item.key as keyof typeof metrics.bySource] ?? 0}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-xl border border-ink-600 bg-ink-800 p-4">
            <h2 className="font-serif text-base font-semibold text-rice mb-4">证据与附加分析</h2>
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-lg bg-ink-900 border border-ink-600 p-3">
                <div className="text-xs text-ink-500">可见水印命中</div>
                <div className="mt-1 text-lg font-semibold text-rice">{metrics.evidence.visibleWatermarkHits}</div>
              </div>
              <div className="rounded-lg bg-ink-900 border border-ink-600 p-3">
                <div className="text-xs text-ink-500">SynthID 命中</div>
                <div className="mt-1 text-lg font-semibold text-rice">{metrics.evidence.synthidHits}</div>
              </div>
              <div className="rounded-lg bg-ink-900 border border-ink-600 p-3">
                <div className="text-xs text-ink-500">已做取证分析</div>
                <div className="mt-1 text-lg font-semibold text-rice">{metrics.evidence.forensicsCompleted}</div>
              </div>
              <div className="rounded-lg bg-ink-900 border border-ink-600 p-3">
                <div className="text-xs text-ink-500">已验内容凭证</div>
                <div className="mt-1 text-lg font-semibold text-rice">{metrics.evidence.provenanceCompleted}</div>
              </div>
            </div>
          </div>
        </section>

        <section className="rounded-xl border border-ink-600 bg-ink-800 p-4">
          <h2 className="font-serif text-base font-semibold text-rice mb-4">来源 × 判定矩阵</h2>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[420px] text-sm">
              <thead>
                <tr className="border-b border-ink-600 text-ink-500">
                  <th className="text-left py-2 pr-3 font-medium">来源</th>
                  <th className="text-right py-2 px-3 font-medium">真实</th>
                  <th className="text-right py-2 px-3 font-medium">疑似</th>
                  <th className="text-right py-2 pl-3 font-medium">高度疑似</th>
                </tr>
              </thead>
              <tbody>
                {[
                  { key: "vlm", label: "真实模型" },
                  { key: "mock", label: "Mock 回退" },
                  { key: "maps-only", label: "仅证据图" },
                  { key: "unknown", label: "未知来源" },
                ].map((item) => {
                  const row = metrics.sourceVerdict[item.key as keyof typeof metrics.sourceVerdict] ?? {};
                  return (
                    <tr key={item.key} className="border-b border-ink-700/60 last:border-0">
                      <td className="py-2 pr-3 text-ink-950">{item.label}</td>
                      <td className="py-2 px-3 text-right text-rice">{row.real ?? 0}</td>
                      <td className="py-2 px-3 text-right text-rice">{row.suspected_fake ?? 0}</td>
                      <td className="py-2 pl-3 text-right text-rice">{row.highly_suspected_fake ?? 0}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

        <section className="rounded-xl border border-ink-600 bg-ink-800 p-4">
          <h2 className="font-serif text-base font-semibold text-rice mb-4">来源 × 证据矩阵</h2>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-sm">
              <thead>
                <tr className="border-b border-ink-600 text-ink-500">
                  <th className="text-left py-2 pr-3 font-medium">来源</th>
                  <th className="text-right py-2 px-3 font-medium">可见水印</th>
                  <th className="text-right py-2 px-3 font-medium">SynthID</th>
                  <th className="text-right py-2 px-3 font-medium">已做取证</th>
                  <th className="text-right py-2 pl-3 font-medium">已验凭证</th>
                </tr>
              </thead>
              <tbody>
                {[
                  { key: "vlm", label: "真实模型" },
                  { key: "mock", label: "Mock 回退" },
                  { key: "maps-only", label: "仅证据图" },
                  { key: "unknown", label: "未知来源" },
                ].map((item) => {
                  const row = metrics.sourceEvidence[item.key as keyof typeof metrics.sourceEvidence] ?? {};
                  return (
                    <tr key={`${item.key}-evidence`} className="border-b border-ink-700/60 last:border-0">
                      <td className="py-2 pr-3 text-ink-950">{item.label}</td>
                      <td className="py-2 px-3 text-right text-rice">{row.visibleWatermarkHits ?? 0}</td>
                      <td className="py-2 px-3 text-right text-rice">{row.synthidHits ?? 0}</td>
                      <td className="py-2 px-3 text-right text-rice">{row.forensicsCompleted ?? 0}</td>
                      <td className="py-2 pl-3 text-right text-rice">{row.provenanceCompleted ?? 0}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </main>
  );
}

function getInitialMonitorDays(): 7 | 14 | 30 {
  if (typeof window === "undefined") return 14;
  const raw = Number(new URLSearchParams(window.location.search).get("monitorDays") || "14");
  return raw === 7 || raw === 30 ? raw : 14;
}
