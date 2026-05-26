import { ProvenanceReport } from "../api";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col sm:flex-row sm:gap-3 py-1.5 border-b border-ink-700 last:border-0">
      <span className="text-ink-500 sm:w-24 shrink-0">{label}</span>
      <span className="text-ink-950 break-all">{value}</span>
    </div>
  );
}

export default function ProvenanceCard({ report }: { report: ProvenanceReport }) {
  const has = report.hasCredentials;
  const valid = report.validationState?.toLowerCase() === "valid";

  return (
    <div className="rounded-2xl border border-ink-600 bg-ink-800 overflow-hidden shadow-sm">
      <div
        className="px-4 sm:px-5 py-3 border-b border-ink-600 flex flex-wrap items-center gap-2"
        style={{ background: has ? "linear-gradient(90deg,rgba(63,182,168,0.14),transparent)" : "linear-gradient(90deg,rgba(77,66,58,0.4),transparent)" }}
      >
        <span className="text-lg">🔏</span>
        <span className="font-serif text-base sm:text-lg font-semibold text-rice">内容凭证验证</span>
        <span className="text-xs text-ink-500">C2PA Content Credentials</span>
      </div>

      <div className="p-4 sm:p-5 space-y-4">
        {/* 总状态 */}
        <div className="flex items-start gap-3">
          <span className="text-2xl">{has ? (valid ? "✅" : "⚠️") : "🪪"}</span>
          <div>
            <div className="font-semibold text-ink-950">
              {has
                ? valid
                  ? "检测到有效内容凭证"
                  : "检测到内容凭证（签名校验未通过/不完整）"
                : "未检测到内容凭证"}
            </div>
            <div className="text-xs text-ink-500">
              {has
                ? "该文件内嵌 C2PA 签名元数据，可追溯来源与编辑历史"
                : "文件中没有 C2PA 凭证（可能从未签名，或在传输/二次保存中被剥离）"}
            </div>
          </div>
        </div>

        {/* 详情 */}
        {has && (
          <div className="rounded-lg bg-ink-900 border border-ink-600 p-4 text-sm">
            <Row label="生成工具" value={report.generator || "—"} />
            <Row label="签发者" value={report.issuer || "—"} />
            <Row
              label="签名校验"
              value={
                <span style={{ color: valid ? "#3fb6a8" : "#d99a2b" }}>
                  {report.validationState}
                </span>
              }
            />
            <Row label="签名算法" value={report.signatureAlg || "—"} />
            {report.signedTime && <Row label="签名时间" value={report.signedTime} />}
            <Row
              label="来源声明"
              value={
                report.isAiGenerated === true ? (
                  <span className="text-brand-magenta">🤖 声明为 AI 生成内容</span>
                ) : report.isAiGenerated === false ? (
                  <span className="text-verdict-real">📷 声明为真实拍摄</span>
                ) : (
                  "未声明"
                )
              }
            />
            {report.actions.length > 0 && (
              <Row
                label="编辑历史"
                value={
                  <div className="space-y-0.5">
                    {report.actions.map((a, i) => (
                      <div key={i} className="text-xs text-ink-950">
                        • {a.action}
                        {a.softwareAgent ? ` （${a.softwareAgent}）` : ""}
                      </div>
                    ))}
                  </div>
                }
              />
            )}
          </div>
        )}

        {/* SynthID */}
        <div className="rounded-lg bg-ink-900 border border-ink-600 p-3 flex items-start gap-2">
          <span className="text-ink-500">💧</span>
          <div className="text-xs">
            <span className="text-ink-950 font-medium">SynthID 隐形水印：</span>
            <span className="text-ink-500">未支持</span>
            <p className="text-ink-500 mt-0.5">{report.synthid.note}</p>
          </div>
        </div>

        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-500">
          <span>文件：{report.fileMeta.name}</span>
          <span>耗时：{report.elapsedMs}ms</span>
          <span className="text-ink-500">凭证可追溯来源，但可被剥离；无凭证 ≠ 伪造，建议结合取证分析综合判断</span>
        </div>
      </div>
    </div>
  );
}
