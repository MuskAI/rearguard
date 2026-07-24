import { type ReactNode, useState } from "react";
import { Link2, ShieldOff, X } from "lucide-react";
import {
  DetectResult,
  ForensicReport,
  ProvenanceReport,
  ReportShareItem,
  VERDICT_META,
  TYPE_LABEL,
  createReportShareLink,
  downloadReport,
  listReportShares,
  revokeReportShare,
} from "../api";
import ConfidenceRing from "./ConfidenceRing";

interface Props {
  result: DetectResult;
  previewUrl?: string;
  forensicsReport?: ForensicReport;
  provenanceReport?: ProvenanceReport;
  onForensics?: () => void;
  forensicsBusy?: boolean;
  onProvenance?: () => void;
  provenanceBusy?: boolean;
}

function pct(value: number | null | undefined) {
  return `${Math.round((value ?? 0) * 100)}%`;
}

function confidenceLabel(value?: string) {
  if (value === "high") return "高可信";
  if (value === "medium") return "中可信";
  if (value === "low") return "弱线索";
  return "无";
}

function sourceLabel(source?: string, reviewRequired?: boolean) {
  if (reviewRequired) return "已给出二元结论，当前置信度较低";
  if (source === "vlm") return "已完成自动检测";
  if (source === "provenance") return "来源证据直接判定";
  if (source === "mock") return "线索不足，建议复核";
  if (source === "maps-only") return "已生成证据图，建议复核";
  if (source === "heuristic") return "规则线索";
  if (source === "unknown") return "待确认";
  return source ? "已记录" : "未返回";
}

function validationLabel(value?: string | null) {
  const normalized = value?.toLowerCase();
  if (normalized === "trusted") return "可信链已建立";
  if (normalized === "valid") return "签名结构有效，信任链未建立";
  if (normalized === "invalid") return "需复核";
  if (normalized) return "已检测";
  return "未检测";
}

function credentialReadLabel(report: ProvenanceReport) {
  if (report.hasCredentials) return report.credentialTrusted ? "可信链已建立" : validationLabel(report.validationState);
  if (report.error === "no_manifest") return "未发现凭证清单";
  if (report.error) return "读取未完成";
  return "未检测到";
}

function actionErrorMessage(error: unknown, fallback: string) {
  const text = error instanceof Error ? error.message.trim() : "";
  if (!text) return fallback;
  const normalized = text.toLowerCase();
  if (text.includes("请登录") || text.includes("请先登录") || text.includes("认证") || text.includes("权限") || normalized.includes("unauthorized") || normalized.includes("forbidden")) return "请登录后继续操作";
  return text;
}

function toneForScore(score: number) {
  if (score >= 0.6) return "#c43d2f";
  if (score >= 0.4) return "#c78324";
  return "#238f82";
}

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 border-b border-ink-700/80 py-2 last:border-0 sm:grid-cols-[104px_1fr]">
      <div className="text-[11px] text-ink-500">{label}</div>
      <div className="min-w-0 break-words text-xs text-ink-950">{children}</div>
    </div>
  );
}

function MetricCell({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="min-w-0 border-l border-ink-700 pl-3">
      <div className="text-[10px] uppercase text-ink-500">{label}</div>
      <div className="mt-1 truncate text-sm font-semibold text-ink-950" style={tone ? { color: tone } : undefined}>
        {value}
      </div>
    </div>
  );
}

function EvidencePill({
  label,
  value,
  tone = "#238f82",
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className="min-w-0 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2">
      <div className="flex items-center gap-2">
        <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: tone }} />
        <span className="truncate text-xs font-medium text-ink-950">{label}</span>
      </div>
      <div className="mt-1 truncate text-[11px] text-ink-500">{value}</div>
    </div>
  );
}

function Disclosure({ title, children, defaultOpen = false }: { title: string; children: ReactNode; defaultOpen?: boolean }) {
  return (
    <details className="group border-t border-ink-700 py-2" open={defaultOpen}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-md px-2 py-2 text-sm font-semibold text-ink-950 hover:bg-ink-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-cyan/50">
        <span>{title}</span>
        <span className="text-xs font-normal text-ink-500 transition group-open:rotate-180">↓</span>
      </summary>
      <div className="mt-3">{children}</div>
    </details>
  );
}

function c2paStatus(report?: ProvenanceReport | null) {
  if (!report) {
    return { label: "未读取", value: "尚未读取内容凭证", tone: "#7d6f5e" };
  }
  if (report.hasCredentials) {
    const trusted = report.credentialTrusted === true || report.validationState?.toLowerCase() === "trusted";
    const aiClaim = report.isAiGenerated === true ? "声明 AI 生成" : report.isAiGenerated === false ? "声明真实拍摄" : "未声明内容类型";
    return {
      label: trusted ? "内容凭证可信" : "凭证签名可读，信任链未建立",
      value: `${aiClaim} · ${report.generator || report.issuer || "已检测到内容凭证"}`,
      tone: trusted ? "#238f82" : "#c78324",
    };
  }
  return { label: "无凭证", value: report.error === "no_manifest" ? "未发现内容凭证清单" : "未发现内容凭证", tone: "#7d6f5e" };
}

function metadataStatus(report?: ProvenanceReport | null) {
  if (!report) {
    return { label: "文件信息未读取", value: "尚未提取文件信息", tone: "#7d6f5e" };
  }
  const ai = report.aiMetadata;
  if (ai?.isAiLikely || report.metadataAiGenerated) {
    return {
      label: "文件信息 AI 线索",
      value: `${ai?.score ?? 0}/100 · ${confidenceLabel(ai?.confidence)}`,
      tone: "#c43d2f",
    };
  }
  if (report.metadataSummary?.fieldCount) {
    return {
      label: "文件信息已读取",
      value: `${report.metadataSummary.fieldCount} 项 · 未命中高可信 AI 线索`,
      tone: "#255f85",
    };
  }
  return { label: "无文件信息", value: "未读取到可用文件信息", tone: "#7d6f5e" };
}

function ProvenanceEvidence({ report }: { report?: ProvenanceReport | null }) {
  const c2pa = c2paStatus(report);
  const metadata = metadataStatus(report);
  if (!report) {
    return (
      <div className="rounded-lg border border-ink-700 bg-ink-900 px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-ink-950">内容凭证与文件信息</div>
            <div className="mt-1 text-xs text-ink-500">{c2pa.value} · {metadata.value}</div>
          </div>
          <span className="rounded-full border border-ink-700 px-2.5 py-1 text-xs text-ink-500">{c2pa.label}</span>
        </div>
      </div>
    );
  }

  const ai = report.aiMetadata;
  const summary = report.metadataSummary;
  const signals = Array.isArray(ai?.signals) ? ai.signals.slice(0, 3) : [];
  const metadataPreview = Array.isArray(summary?.preview) ? summary.preview.slice(0, 12) : [];
  const actions = Array.isArray(report.actions) ? report.actions : [];
  const ingredients = Array.isArray(report.ingredients) ? report.ingredients : [];
  const provenanceFileName = report.fileMeta?.name || "未知文件";

  return (
    <div className="rounded-lg border border-ink-700 bg-ink-900 px-4 py-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: c2pa.tone }} />
            <span className="text-sm font-semibold text-ink-950">内容凭证与文件信息</span>
            <span className="rounded-full border border-ink-700 bg-ink-800 px-2 py-0.5 text-[11px] text-ink-500">
              {c2pa.label}
            </span>
            <span className="rounded-full border border-ink-700 bg-ink-800 px-2 py-0.5 text-[11px] text-ink-500">
              {metadata.label}
            </span>
          </div>
          <div className="mt-1 text-xs text-ink-500">{c2pa.value}</div>
          <div className="mt-0.5 text-xs text-ink-500">{metadata.value}</div>
        </div>
        <div className="grid grid-cols-3 gap-2 text-center sm:min-w-72">
          <div>
            <div className="text-sm font-semibold text-ink-950">{report.hasCredentials ? "有" : "无"}</div>
            <div className="text-[10px] text-ink-500">内容凭证</div>
          </div>
            <div>
              <div className="text-sm font-semibold text-ink-950">{ai?.score ?? 0}</div>
              <div className="text-[10px] text-ink-500">AI 线索分</div>
            </div>
            <div>
              <div className="text-sm font-semibold text-ink-950">{summary?.fieldCount ?? 0}</div>
              <div className="text-[10px] text-ink-500">文件信息项</div>
            </div>
        </div>
      </div>

      {signals.length > 0 && (
        <div className="mt-3 grid gap-2 sm:grid-cols-3">
          {signals.map((signal, index) => (
              <div key={`${signal.id}-${signal.path}-${index}`} className="min-w-0 border-l-2 border-cinnabar/50 pl-2">
                <div className="truncate text-xs font-medium text-ink-950">{signal.label}</div>
                <div className="mt-0.5 truncate text-[11px] text-ink-500">{signal.reason || `线索 ${index + 1}`}</div>
              </div>
            ))}
          </div>
        )}

      <Disclosure title="查看检测详情">
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-lg border border-ink-700 bg-ink-800 px-3 py-2">
            <DetailRow label="签名校验">
              {credentialReadLabel(report)}
            </DetailRow>
            <DetailRow label="记录工具">{report.generator || "未声明"}</DetailRow>
            <DetailRow label="签发者">{report.issuer || "未声明"}</DetailRow>
            <DetailRow label="签名算法">{report.signatureAlg || "未声明"}</DetailRow>
            <DetailRow label="签名时间">{report.signedTime || "未声明"}</DetailRow>
            <DetailRow label="内容声明">
              {report.isAiGenerated === true ? "声明为 AI 生成内容" : report.isAiGenerated === false ? "声明为真实拍摄" : "未声明"}
            </DetailRow>
          </div>
          <div className="rounded-lg border border-ink-700 bg-ink-800 px-3 py-2">
            <DetailRow label="AI 线索分">{ai ? `${ai.score}/100 · ${confidenceLabel(ai.confidence)}` : "无"}</DetailRow>
            <DetailRow label="命中线索">{ai?.signalCount ?? 0} 条</DetailRow>
            <DetailRow label="信息分组">{summary ? `${summary.sectionCount} 组，其中嵌入分组 ${summary.embeddedSectionCount} 组` : "无"}</DetailRow>
            <DetailRow label="信息项">{summary?.fieldCount ?? 0} 个</DetailRow>
            <DetailRow label="文件">{provenanceFileName}</DetailRow>
            <DetailRow label="耗时">{report.elapsedMs}ms</DetailRow>
          </div>
        </div>

        {(actions.length > 0 || ingredients.length > 0) && (
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            {actions.length > 0 && (
              <div>
                <div className="mb-2 text-xs font-semibold text-ink-950">编辑历史</div>
                <div className="space-y-2">
                  {actions.map((item, index) => (
                    <div key={`${item.action}-${index}`} className="rounded-lg border border-ink-700 bg-ink-800 px-3 py-2 text-xs text-ink-500">
                      <span className="text-ink-950">{item.action}</span>
                      {item.softwareAgent ? ` · ${item.softwareAgent}` : ""}
                      {item.digitalSourceType ? ` · ${item.digitalSourceType}` : ""}
                    </div>
                  ))}
                </div>
              </div>
            )}
            {ingredients.length > 0 && (
              <div>
                <div className="mb-2 text-xs font-semibold text-ink-950">素材记录</div>
                <div className="space-y-2">
                  {ingredients.map((item, index) => (
                    <div key={`${item.title}-${index}`} className="rounded-lg border border-ink-700 bg-ink-800 px-3 py-2 text-xs text-ink-500">
                      <span className="text-ink-950">{item.title || "未命名素材"}</span>
                      {item.relationship ? ` · ${item.relationship}` : ""}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {metadataPreview.length > 0 && (
          <div className="mt-4">
            <div className="mb-2 flex items-center justify-between gap-2">
              <div className="text-xs font-semibold text-ink-950">文件信息预览</div>
              <div className="text-[11px] text-ink-500">显示前 {metadataPreview.length} 项</div>
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              {metadataPreview.map((item, index) => (
                <div key={`${item.path}-${index}`} className="min-w-0 rounded-lg border border-ink-700 bg-ink-800 px-3 py-2">
                  <div className="truncate text-[11px] font-semibold text-ink-950">{item.path}</div>
                  <div className="mt-1 max-h-16 overflow-hidden break-words text-[11px] leading-relaxed text-ink-500">{item.value || "—"}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {summary && (
          <div className="mt-4">
            <div className="mb-2 text-xs font-semibold text-ink-950">完整文件信息</div>
            <pre className="max-h-72 overflow-auto rounded-lg border border-ink-700 bg-ink-950 px-3 py-3 text-[11px] leading-relaxed text-ink-900">
              {JSON.stringify(report.metadata || {}, null, 2)}
            </pre>
          </div>
        )}
      </Disclosure>
    </div>
  );
}

export default function ResultCard({
  result,
  previewUrl,
  forensicsReport,
  provenanceReport,
  onForensics,
  forensicsBusy,
  onProvenance,
  provenanceBusy,
}: Props) {
  const meta = VERDICT_META[result.verdict];
  const [showOverlay, setShowOverlay] = useState(true);
  const [reportBusy, setReportBusy] = useState(false);
  const [shareBusy, setShareBusy] = useState(false);
  const [shareMessage, setShareMessage] = useState("");
  const [sharePanelOpen, setSharePanelOpen] = useState(false);
  const [shares, setShares] = useState<ReportShareItem[]>([]);
  const fileMeta = result.fileMeta || { name: "未知文件", type: "document" as const, size: "未知" };
  const fileType = fileMeta.type || "document";
  const fileLabel = TYPE_LABEL[fileType] || "文件";
  const effectivePreview = previewUrl || fileMeta.preview || fileMeta.thumbnail || undefined;
  const isImage = fileType === "image" && effectivePreview;
  const provenance = provenanceReport || result.provenance || undefined;
  const synthid = result.synthid;
  const visibleWatermark = result.visibleWatermark;
  const visiblePlatformPending = visibleWatermark?.provider === "yolo11x_watermark";
  const provenancePrecheck = result.provenancePrecheck;
  const dimensions = Array.isArray(result.dimensions) ? result.dimensions : [];
  const regions = Array.isArray(result.regions) ? result.regions : [];
  const topDimensions = dimensions.slice(0, 3);
  const provenanceTone = c2paStatus(provenance).tone;
  const reviewOnly = result.decisionStatus === "review_only" || result.reviewRequired === true;

  async function handleDownloadReport() {
    if (reportBusy) return;
    setReportBusy(true);
    try {
      await downloadReport(result.reportId);
    } catch (error) {
      window.alert(actionErrorMessage(error, "下载报告失败"));
    } finally {
      setReportBusy(false);
    }
  }

  async function handleShareReport() {
    if (shareBusy) return;
    setShareBusy(true);
    setShareMessage("");
    try {
      const link = await createReportShareLink(result.reportId);
      setShares(await listReportShares(result.reportId));
      try {
        await navigator.clipboard.writeText(link.url);
        setShareMessage("分享链接已复制，7 天内有效");
      } catch {
        window.prompt("复制报告分享链接", link.url);
        setShareMessage("分享链接已生成，7 天内有效");
      }
    } catch (error) {
      window.alert(actionErrorMessage(error, "生成分享链接失败"));
    } finally {
      setShareBusy(false);
    }
  }

  async function handleSharePanel() {
    if (sharePanelOpen) {
      setSharePanelOpen(false);
      return;
    }
    if (shareBusy) return;
    setShareBusy(true);
    try {
      setShares(await listReportShares(result.reportId));
      setSharePanelOpen(true);
    } catch (error) {
      window.alert(actionErrorMessage(error, "加载分享记录失败"));
    } finally {
      setShareBusy(false);
    }
  }

  async function handleRevokeShare(shareId: string) {
    if (shareBusy) return;
    setShareBusy(true);
    try {
      await revokeReportShare(result.reportId, shareId);
      setShares((current) => current.map((item) => (
        item.shareId === shareId
          ? { ...item, active: false, revokedAt: new Date().toISOString() }
          : item
      )));
      setShareMessage("分享链接已撤销");
    } catch (error) {
      window.alert(actionErrorMessage(error, "撤销分享链接失败"));
    } finally {
      setShareBusy(false);
    }
  }

  return (
    <article className="overflow-hidden rounded-lg border border-ink-700 bg-ink-800 shadow-sm">
      <header className="border-b border-ink-700 bg-ink-900 px-4 py-4 sm:px-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: meta.color }} />
              <span className="text-[11px] text-ink-500">检测结果</span>
            </div>
            <h2 className="mt-2 text-2xl font-semibold text-ink-950 sm:text-3xl">{meta.label}</h2>
            <p className="mt-2 max-w-3xl text-sm leading-relaxed text-ink-500">{result.explanation}</p>
          </div>
          <div className="grid shrink-0 grid-cols-3 gap-3 sm:min-w-[360px]">
            <MetricCell label="置信说明" value={reviewOnly ? "低，建议复核" : pct(result.confidence)} tone={meta.color} />
            <MetricCell label="类型" value={fileLabel} />
            <MetricCell label="耗时" value={`${result.elapsedMs}ms`} />
          </div>
        </div>
      </header>

      <div className="grid gap-5 px-4 py-4 sm:px-5 lg:grid-cols-[minmax(220px,320px)_1fr]">
        <aside className="order-2 space-y-3 lg:order-1">
          <div className="rounded-lg border border-ink-700 bg-ink-900 p-3">
            {reviewOnly ? (
              <div className="rounded-lg border border-ink-700 bg-ink-800 px-3 py-4 text-center">
                <div className="text-sm font-semibold text-ink-950">已给出二元结论</div>
                <div className="mt-1 text-xs leading-relaxed text-ink-500">当前置信度较低，未校准模型分数仅作为内部诊断，建议结合原始来源复核。</div>
              </div>
            ) : (
              <ConfidenceRing value={result.confidence} color={meta.color} />
            )}
            <div className="mt-3 space-y-1 text-xs text-ink-500">
              <div className="truncate">
                <span className="text-ink-950">文件：</span>
                {fileMeta.name}
              </div>
              <div>
                <span className="text-ink-950">大小：</span>
                {fileMeta.size}
              </div>
              {fileMeta.resolution && (
                <div>
                  <span className="text-ink-950">分辨率：</span>
                  {fileMeta.resolution}
                </div>
              )}
              <div className="break-all">
                <span className="text-ink-950">报告：</span>
                {result.reportId}
              </div>
            </div>
          </div>

          {isImage ? (
            <div className="relative overflow-hidden rounded-lg border border-ink-700 bg-ink-900">
              <img src={effectivePreview} alt={fileMeta.name} className="block w-full" />
              {showOverlay &&
                regions.map((rg, i) => (
                  <div
                    key={i}
                    className="absolute border-2"
                    style={{
                      left: `${rg.x * 100}%`,
                      top: `${rg.y * 100}%`,
                      width: `${rg.w * 100}%`,
                      height: `${rg.h * 100}%`,
                      borderColor: meta.color,
                      boxShadow: `0 0 0 9999px ${meta.color}10 inset`,
                    }}
                  >
                    <span
                      className="absolute -top-5 left-0 whitespace-nowrap rounded px-1.5 py-0.5 text-[10px]"
                      style={{ background: meta.color, color: "#ffffff" }}
                    >
                      {rg.label} {pct(rg.score)}
                    </span>
                  </div>
                ))}
            </div>
          ) : (
            <div className="grid min-h-36 place-items-center rounded-lg border border-dashed border-ink-600 bg-ink-900 text-sm text-ink-500">
              {fileLabel}文件
            </div>
          )}
          {isImage && regions.length > 0 && (
            <button
              onClick={() => setShowOverlay((value) => !value)}
              className="w-full rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-xs text-ink-950 hover:border-brand-cyan/50"
            >
              {showOverlay ? "隐藏可疑区域" : "显示可疑区域"}
            </button>
          )}
        </aside>

        <section className="order-1 min-w-0 space-y-4 lg:order-2">
          <ProvenanceEvidence report={provenance} />

          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            {topDimensions.map((item) => (
              <EvidencePill
                key={item.key}
                label={item.label}
                value={`${item.result} · ${pct(item.score)}`}
                tone={toneForScore(item.score)}
              />
            ))}
            <EvidencePill label="内容凭证" value={c2paStatus(provenance).label} tone={provenanceTone} />
            <EvidencePill label="文件信息" value={metadataStatus(provenance).label} tone={metadataStatus(provenance).tone} />
            {visibleWatermark && (
              <EvidencePill
                label="可见水印"
                value={!visibleWatermark.supported ? "检测不可用" : visibleWatermark.detected ? `${visiblePlatformPending ? "平台待确认" : visibleWatermark.provider || "未知"} · ${pct(visibleWatermark.confidence)}` : "未检出"}
                tone={!visibleWatermark.supported ? "#9a6700" : visibleWatermark.detected ? visiblePlatformPending ? "#9a6700" : "#c43d2f" : "#238f82"}
              />
            )}
            {provenancePrecheck && (
              <EvidencePill
                label="判定路径"
                value={
                  result.source === "provenance"
                    ? "来源标记直判，未调用模型"
                    : provenancePrecheck.available
                      ? "前置核验后进入模型"
                      : "前置服务不可用，已回退模型"
                }
                tone={result.source === "provenance" ? "#c43d2f" : "#238f82"}
              />
            )}
            {synthid && (
              <EvidencePill
                label="隐式水印"
                value={synthid.supported ? (synthid.detected ? `检出 · ${pct(synthid.confidence)}` : synthid.possiblyDetected ? `疑似 · ${pct(synthid.confidence)}` : "未检出") : "未启用"}
                tone={synthid.detected ? "#c43d2f" : synthid.possiblyDetected ? "#9a6700" : "#238f82"}
              />
            )}
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              onClick={handleDownloadReport}
              disabled={reportBusy}
              className="rounded-lg border border-cinnabar/30 bg-cinnabar px-3 py-2 text-xs font-medium text-white shadow-sm hover:bg-cinnabar-dark disabled:opacity-50"
            >
              {reportBusy ? "导出中" : forensicsReport || provenance ? "下载完整鉴定报告" : "下载鉴定报告"}
            </button>
            <button
              onClick={handleShareReport}
              disabled={shareBusy}
              className="rounded-lg border border-ink-600 bg-ink-900 px-3 py-2 text-xs font-medium text-ink-950 hover:border-brand-cyan/50 disabled:opacity-50"
            >
              {shareBusy ? "生成中" : "复制分享链接"}
            </button>
            <button
              onClick={handleSharePanel}
              disabled={shareBusy}
              aria-label={sharePanelOpen ? "关闭分享管理" : "管理分享链接"}
              title={sharePanelOpen ? "关闭分享管理" : "管理分享链接"}
              className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-ink-600 bg-ink-900 text-ink-950 hover:border-brand-cyan/50 disabled:opacity-50"
            >
              {sharePanelOpen ? <X size={15} /> : <Link2 size={15} />}
            </button>
            {onForensics && (
              <button
                onClick={onForensics}
                disabled={forensicsBusy}
                className="rounded-lg border border-gold/40 bg-gold/10 px-3 py-2 text-xs font-medium text-gold-dark hover:bg-gold/15 disabled:opacity-50"
              >
                {forensicsBusy ? "分析中" : "深度取证分析"}
              </button>
            )}
            {onProvenance && (
              <button
                onClick={onProvenance}
                disabled={provenanceBusy}
                className="rounded-lg border border-jade/40 bg-jade/10 px-3 py-2 text-xs font-medium text-jade hover:bg-jade/15 disabled:opacity-50"
              >
                {provenanceBusy ? "检测中" : "核验内容凭证"}
              </button>
            )}
            {shareMessage && (
              <span className="inline-flex items-center rounded-lg border border-jade/30 bg-jade/10 px-3 py-2 text-xs text-jade">
                {shareMessage}
              </span>
            )}
          </div>

          {sharePanelOpen && (
            <section aria-label="分享链接管理" className="border-y border-ink-700 py-3">
              <div className="mb-2 flex items-center justify-between gap-3">
                <h3 className="text-xs font-semibold text-ink-950">分享链接</h3>
                <span className="text-[11px] text-ink-500">{shares.filter((item) => item.active).length} 个有效</span>
              </div>
              {shares.length === 0 ? (
                <p className="text-xs text-ink-500">尚未创建分享链接</p>
              ) : (
                <div className="divide-y divide-ink-700">
                  {shares.map((item) => (
                    <div key={item.shareId} className="flex items-center justify-between gap-3 py-2 first:pt-0 last:pb-0">
                      <div className="min-w-0">
                        <div className="truncate font-mono text-[11px] text-ink-950">{item.shareId}</div>
                        <div className="mt-0.5 text-[11px] text-ink-500">
                          {item.active ? `有效至 ${new Date(item.expiresAt).toLocaleString()}` : "已失效"}
                        </div>
                      </div>
                      {item.active && (
                        <button
                          onClick={() => handleRevokeShare(item.shareId)}
                          disabled={shareBusy}
                          aria-label="撤销分享链接"
                          title="撤销分享链接"
                          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-cinnabar/30 text-cinnabar hover:bg-cinnabar/10 disabled:opacity-50"
                        >
                          <ShieldOff size={14} />
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}

          <div className="rounded-lg border border-ink-700 bg-ink-900 px-4">
            <Disclosure title="判定维度">
              <div className="space-y-3">
                {dimensions.map((item) => (
                  <div key={item.key}>
                    <div className="mb-1 flex items-start justify-between gap-3 text-xs">
                      <span className="text-ink-950">{item.label}</span>
                      <span className="text-right text-ink-500">{item.result} · {pct(item.score)}</span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-ink-700">
                      <div className="h-full rounded-full" style={{ width: pct(item.score), background: toneForScore(item.score) }} />
                    </div>
                  </div>
                ))}
              </div>
            </Disclosure>

            {(visibleWatermark || synthid) && (
              <Disclosure title="水印线索">
                <div className="grid gap-4 lg:grid-cols-2">
                  {synthid && (
                    <div className="rounded-lg border border-ink-700 bg-ink-800 px-3 py-3 text-sm">
                      <div className="font-semibold text-ink-950">SynthID 多模型频谱核验</div>
                      <p className="mt-2 text-xs leading-relaxed text-ink-500">{synthid.note}</p>
                      {synthid.supported && (
                        <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] text-ink-500">
                          <span>相位匹配：{pct(synthid.phaseMatch)}</span>
                          <span>分辨率档案：{synthid.exactResolutionMatch ? "原始尺寸" : "近邻尺寸"}</span>
                        </div>
                      )}
                    </div>
                  )}
                  {visibleWatermark && (
                    <div className="rounded-lg border border-ink-700 bg-ink-800 px-3 py-3 text-sm">
                      <div className="font-semibold text-ink-950">可见水印</div>
                      <p className="mt-2 text-xs leading-relaxed text-ink-500">{visibleWatermark.note}</p>
                      {visibleWatermark.detected && (
                        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                          {visibleWatermark.hits.slice(0, 4).map((hit, index) => (
                            <div key={`${hit.method}-${index}`} className="flex gap-3">
                              {hit.crop ? (
                                <img src={hit.crop} alt="可见水印裁剪证据" className="h-16 w-16 shrink-0 rounded-md border border-ink-700 bg-ink-900 object-contain" loading="lazy" />
                              ) : (
                                <div className="h-16 w-16 shrink-0 rounded-md border border-ink-700 bg-ink-900" />
                              )}
                              <div className="min-w-0 text-[11px] leading-relaxed text-ink-500">
                                <div className="truncate font-medium text-ink-950">证据 {index + 1}</div>
                                <div>方法：{hit.method}</div>
                                <div>置信度：{pct(hit.confidence)}</div>
                                <div>位置：x {pct(hit.bbox.x)}, y {pct(hit.bbox.y)}</div>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </Disclosure>
            )}

            <Disclosure title="检测详情">
              <div className="grid gap-4 md:grid-cols-2">
                <div className="rounded-lg border border-ink-700 bg-ink-800 px-3 py-2">
                  <DetailRow label="文件">{fileMeta.name}</DetailRow>
                  <DetailRow label="大小">{fileMeta.size}</DetailRow>
                  <DetailRow label="文件指纹">{fileMeta.sha256 || "未返回"}</DetailRow>
                  <DetailRow label="报告号">{result.reportId}</DetailRow>
                </div>
                <div className="rounded-lg border border-ink-700 bg-ink-800 px-3 py-2">
                  <DetailRow label="检测状态">{sourceLabel(result.source, reviewOnly)}</DetailRow>
                  <DetailRow label="复核记录">{result.cacheHit ? "已参考历史检测记录" : "本次新检测"}</DetailRow>
                  <DetailRow label="报告生成">已完成</DetailRow>
                  <DetailRow label="检测用时">{result.elapsedMs}ms</DetailRow>
                </div>
              </div>
              <p className="mt-3 border-l-2 border-cinnabar/40 pl-3 text-xs leading-relaxed text-ink-500">
                {result.disclaimer}
              </p>
            </Disclosure>
          </div>
        </section>
      </div>
    </article>
  );
}
