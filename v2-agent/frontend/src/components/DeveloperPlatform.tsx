import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  ArrowLeft,
  BookOpen,
  Check,
  ChevronRight,
  CircleDollarSign,
  Code2,
  Copy,
  ExternalLink,
  FileJson,
  Gauge,
  KeyRound,
  LayoutDashboard,
  LoaderCircle,
  LockKeyhole,
  LogIn,
  LogOut,
  Plus,
  RefreshCw,
  RotateCw,
  ShieldCheck,
  SquareTerminal,
  Trash2,
  UserRound,
  WalletCards,
  X,
} from "lucide-react";
import {
  AccountUser,
  DeveloperAccountResponse,
  DeveloperApiKey,
  DeveloperLedgerEntry,
  createDeveloperKey,
  fetchDeveloperAccount,
  fetchDeveloperKeys,
  fetchDeveloperLedger,
  revokeDeveloperKey,
  rotateDeveloperKey,
} from "../api";
import HuijianBrand from "./HuijianBrand";
import "./DeveloperPlatform.css";

type DeveloperTab = "overview" | "keys" | "docs" | "usage";
type CodeLanguage = "curl" | "python" | "typescript" | "java" | "go";

interface Props {
  authReady: boolean;
  user: AccountUser | null;
  onLogin: () => void;
  onHome: () => void;
  onWorkspace: () => void;
  onLogout: () => void;
}

const NAV_ITEMS: Array<{ key: DeveloperTab; label: string; icon: typeof LayoutDashboard }> = [
  { key: "overview", label: "概览", icon: LayoutDashboard },
  { key: "keys", label: "API Keys", icon: KeyRound },
  { key: "docs", label: "接入文档", icon: BookOpen },
  { key: "usage", label: "用量与账单", icon: Activity },
];

const LANGUAGE_LABELS: Record<CodeLanguage, string> = {
  curl: "cURL",
  python: "Python",
  typescript: "Node.js / TS",
  java: "Java",
  go: "Go",
};

function formatNumber(value: number | undefined) {
  return value === undefined ? "—" : new Intl.NumberFormat("zh-CN").format(Number(value));
}

function formatMoney(fen: number | undefined) {
  return fen === undefined ? "—" : `¥${(Number(fen) / 100).toFixed(2)}`;
}

function compactDate(value?: string) {
  if (!value) return "未使用";
  const parsed = new Date(value.replace(" ", "T"));
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

function statusLabel(status: string) {
  return {
    queued: "排队中",
    running: "检测中",
    success: "已完成",
    failed: "失败",
    rejected: "未受理",
  }[status] || status;
}

function keyStatusLabel(key: DeveloperApiKey) {
  if (key.status !== "active") return "已撤销";
  if (key.expiresAt && Date.parse(key.expiresAt.replace(" ", "T")) <= Date.now()) return "已过期";
  return "使用中";
}

function expiryFromChoice(choice: string) {
  if (choice === "never") return null;
  const days = Number(choice);
  return new Date(Date.now() + days * 24 * 60 * 60 * 1000).toISOString();
}

function integrationExamples(origin: string, mode: "fast" | "swarm"): Record<CodeLanguage, string> {
  const endpoint = `${origin}/api/openapi/v1/image-detections`;
  return {
    curl: [
      `ORIGIN="${origin}"`,
      'API_KEY="rg_sk_..."',
      'IDEMPOTENCY_KEY="$(uuidgen)"',
      "",
      `TASK=$(curl -sS -X POST "${endpoint}" \\`,
      '  -H "Authorization: Bearer $API_KEY" \\',
      '  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \\',
      `  -F "mode=${mode}" \\`,
      '  -F "image=@./sample.jpg")',
      "",
      'STATUS_URL="$ORIGIN$(echo "$TASK" | jq -r .links.self)"',
      'curl -sS "$STATUS_URL" -H "Authorization: Bearer $API_KEY" | jq',
    ].join("\n"),
    python: [
      "import os, time, uuid, requests",
      "",
      `ORIGIN = "${origin}"`,
      'API_KEY = os.environ["HUIJIAN_API_KEY"]',
      'headers = {"Authorization": f"Bearer {API_KEY}"}',
      'create_headers = {**headers, "Idempotency-Key": str(uuid.uuid4())}',
      "",
      'with open("sample.jpg", "rb") as image:',
      `    response = requests.post(f"{ORIGIN}/api/openapi/v1/image-detections", headers=create_headers, data={"mode": "${mode}"}, files={"image": image}, timeout=30)`,
      "response.raise_for_status()",
      "task = response.json()",
      "",
      "deadline = time.monotonic() + 300",
      'while task["status"] not in {"success", "failed", "rejected"}:',
      '    if time.monotonic() >= deadline: raise TimeoutError("检测任务等待超时")',
      "    time.sleep(1.5)",
      '    response = requests.get(ORIGIN + task["links"]["self"], headers=headers, timeout=15)',
      '    if response.status_code == 429:',
      '        time.sleep(int(response.headers.get("Retry-After", "2"))); continue',
      "    response.raise_for_status()",
      "    task = response.json()",
      'if task["status"] != "success": raise RuntimeError(task.get("error") or task["status"])',
      "print(task)",
    ].join("\n"),
    typescript: [
      'import { readFile } from "node:fs/promises";',
      "",
      `const origin = "${origin}";`,
      "const apiKey = process.env.HUIJIAN_API_KEY!;",
      "const body = new FormData();",
      `body.set("mode", "${mode}");`,
      'body.set("image", new Blob([await readFile("sample.jpg")]), "sample.jpg");',
      "",
      'const headers = { Authorization: `Bearer ${apiKey}` };',
      'const createHeaders = { ...headers, "Idempotency-Key": crypto.randomUUID() };',
      'let response = await fetch(`${origin}/api/openapi/v1/image-detections`, { method: "POST", headers: createHeaders, body });',
      'if (!response.ok) throw new Error(await response.text());',
      "let task = await response.json();",
      "const deadline = Date.now() + 300_000;",
      'while (!["success", "failed", "rejected"].includes(task.status)) {',
      '  if (Date.now() >= deadline) throw new Error("检测任务等待超时");',
      "  await new Promise(resolve => setTimeout(resolve, 1500));",
      "  response = await fetch(new URL(task.links.self, origin), { headers });",
      '  if (response.status === 429) { await new Promise(resolve => setTimeout(resolve, Number(response.headers.get("Retry-After") || 2) * 1000)); continue; }',
      '  if (!response.ok) throw new Error(await response.text());',
      "  task = await response.json();",
      "}",
      'if (task.status !== "success") throw new Error(task.error || task.status);',
      "console.log(task);",
    ].join("\n"),
    java: [
      "// Maven: com.squareup.okhttp3:okhttp and org.json:json",
      "OkHttpClient client = new OkHttpClient();",
      `String origin = "${origin}";`,
      'String apiKey = System.getenv("HUIJIAN_API_KEY");',
      "RequestBody body = new MultipartBody.Builder().setType(MultipartBody.FORM)",
      `    .addFormDataPart("mode", "${mode}")`,
      '    .addFormDataPart("image", "sample.jpg", RequestBody.create(new File("sample.jpg"), MediaType.get("image/jpeg")))',
      "    .build();",
      'Request request = new Request.Builder().url(origin + "/api/openapi/v1/image-detections")',
      '    .header("Authorization", "Bearer " + apiKey).header("Idempotency-Key", UUID.randomUUID().toString())',
      "    .post(body).build();",
      "JSONObject task = new JSONObject(client.newCall(request).execute().body().string());",
      "for (int poll = 0; poll < 200 && !task.getString(\"status\").matches(\"success|failed|rejected\"); poll++) {",
      "    Thread.sleep(1500);",
      '    request = new Request.Builder().url(origin + task.getJSONObject("links").getString("self"))',
      '        .header("Authorization", "Bearer " + apiKey).build();',
      "    task = new JSONObject(client.newCall(request).execute().body().string());",
      "}",
      "System.out.println(task.toString(2));",
    ].join("\n"),
    go: [
      "package main",
      "",
      'import ("bytes"; "encoding/json"; "fmt"; "io"; "mime/multipart"; "net/http"; "os"; "time")',
      "",
      "func main() {",
      `  origin := "${origin}"`,
      '  var body bytes.Buffer; writer := multipart.NewWriter(&body)',
      `  writer.WriteField("mode", "${mode}")`,
      '  part, _ := writer.CreateFormFile("image", "sample.jpg")',
      '  file, _ := os.Open("sample.jpg"); io.Copy(part, file); writer.Close()',
      '  req, _ := http.NewRequest("POST", origin + "/api/openapi/v1/image-detections", &body)',
      '  req.Header.Set("Authorization", "Bearer " + os.Getenv("HUIJIAN_API_KEY"))',
      '  req.Header.Set("Content-Type", writer.FormDataContentType())',
      '  req.Header.Set("Idempotency-Key", fmt.Sprintf("%d", time.Now().UnixNano()))',
      "  response, _ := http.DefaultClient.Do(req)",
      "  var task map[string]any; json.NewDecoder(response.Body).Decode(&task)",
      '  for poll := 0; poll < 200 && task["status"] != "success" && task["status"] != "failed" && task["status"] != "rejected"; poll++ {',
      "    time.Sleep(1500 * time.Millisecond)",
      '    url := origin + task["links"].(map[string]any)["self"].(string)',
      '    req, _ = http.NewRequest("GET", url, nil); req.Header.Set("Authorization", "Bearer " + os.Getenv("HUIJIAN_API_KEY"))',
      "    response, _ = http.DefaultClient.Do(req); json.NewDecoder(response.Body).Decode(&task)",
      "  }",
      '  output, _ := json.MarshalIndent(task, "", "  "); fmt.Println(string(output))',
      "}",
    ].join("\n"),
  };
}

export default function DeveloperPlatform({ authReady, user, onLogin, onHome, onWorkspace, onLogout }: Props) {
  const [tab, setTab] = useState<DeveloperTab>("overview");
  const [days, setDays] = useState<7 | 14 | 30 | 90>(30);
  const [account, setAccount] = useState<DeveloperAccountResponse | null>(null);
  const [keys, setKeys] = useState<DeveloperApiKey[]>([]);
  const [ledger, setLedger] = useState<DeveloperLedgerEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [keyBusy, setKeyBusy] = useState<number | "create" | null>(null);
  const [revealedKey, setRevealedKey] = useState<{ value: string; title: string } | null>(null);
  const [copied, setCopied] = useState("");
  const [language, setLanguage] = useState<CodeLanguage>("curl");
  const [docMode, setDocMode] = useState<"fast" | "swarm">("fast");
  const [newKeyName, setNewKeyName] = useState("生产环境");
  const [newKeyExpiry, setNewKeyExpiry] = useState("90");
  const [newKeyScopes, setNewKeyScopes] = useState({ fast: true, swarm: false, reports: false });
  const [newKeyIps, setNewKeyIps] = useState("");
  const loadGeneration = useRef(0);
  const accountIdentity = user?.account_uuid || (user ? String(user.Userid) : "");
  const accountIdentityRef = useRef(accountIdentity);
  accountIdentityRef.current = accountIdentity;

  const load = useCallback(async () => {
    if (!user) return;
    const generation = ++loadGeneration.current;
    setLoading(true);
    const [accountResult, keyResult, ledgerResult] = await Promise.allSettled([
      fetchDeveloperAccount(days),
      fetchDeveloperKeys(),
      fetchDeveloperLedger(80),
    ]);
    if (generation !== loadGeneration.current) return;
    setAccount(accountResult.status === "fulfilled" ? accountResult.value : null);
    setKeys(keyResult.status === "fulfilled" ? keyResult.value.keys || [] : []);
    setLedger(ledgerResult.status === "fulfilled" ? ledgerResult.value.entries || [] : []);
    const rejected = [accountResult, keyResult, ledgerResult].find((item) => item.status === "rejected");
    setError(rejected?.status === "rejected" ? (rejected.reason instanceof Error ? rejected.reason.message : "开发者数据读取失败") : "");
    setLoading(false);
  }, [days, user]);

  useEffect(() => {
    loadGeneration.current += 1;
    setAccount(null);
    setKeys([]);
    setLedger([]);
    setError("");
    setCreateOpen(false);
    setRevealedKey(null);
    setKeyBusy(null);
    setLoading(false);
  }, [accountIdentity]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(""), 1800);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const origin = window.location.origin;
  const endpoint = `${origin}/api/openapi/v1/image-detections`;
  const examples = useMemo(() => integrationExamples(origin, docMode), [docMode, origin]);

  async function copyText(value: string, token: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(token);
    } catch {
      window.prompt("复制内容", value);
    }
  }

  async function createKey() {
    if (!newKeyName.trim() || (!newKeyScopes.fast && !newKeyScopes.swarm)) return;
    setKeyBusy("create");
    setError("");
    const operationIdentity = accountIdentity;
    try {
      const response = await createDeveloperKey({
        name: newKeyName.trim(),
        scopes: [
          ...(newKeyScopes.fast ? ["image:fast"] : []),
          ...(newKeyScopes.swarm ? ["image:swarm"] : []),
          ...(newKeyScopes.reports ? ["reports"] : []),
        ],
        expiresAt: expiryFromChoice(newKeyExpiry),
        ipAllowlist: newKeyIps.split(/[\n,]+/).map((value) => value.trim()).filter(Boolean),
      });
      if (operationIdentity !== accountIdentityRef.current) return;
      setKeys((current) => [response.key, ...current]);
      setCreateOpen(false);
      setRevealedKey({ value: response.apiKey, title: "API Key 已创建" });
    } catch (requestError) {
      if (operationIdentity !== accountIdentityRef.current) return;
      setError(requestError instanceof Error ? requestError.message : "API Key 创建失败");
    } finally {
      if (operationIdentity === accountIdentityRef.current) setKeyBusy(null);
    }
  }

  async function revokeKey(key: DeveloperApiKey) {
    if (!window.confirm(`确认撤销 ${key.name}？使用该 Key 的请求会立即失败。`)) return;
    setKeyBusy(key.id);
    const operationIdentity = accountIdentity;
    try {
      await revokeDeveloperKey(key.id);
      if (operationIdentity !== accountIdentityRef.current) return;
      setKeys((current) => current.map((item) => item.id === key.id ? { ...item, status: "revoked", revokedAt: new Date().toISOString() } : item));
    } catch (requestError) {
      if (operationIdentity !== accountIdentityRef.current) return;
      setError(requestError instanceof Error ? requestError.message : "API Key 撤销失败");
    } finally {
      if (operationIdentity === accountIdentityRef.current) setKeyBusy(null);
    }
  }

  async function rotateKey(key: DeveloperApiKey) {
    if (!window.confirm(`轮换 ${key.name}？旧 Key 会立即撤销。`)) return;
    setKeyBusy(key.id);
    const operationIdentity = accountIdentity;
    try {
      const response = await rotateDeveloperKey(key.id);
      if (operationIdentity !== accountIdentityRef.current) return;
      setKeys((current) => [response.key, ...current.map((item) => item.id === key.id ? { ...item, status: "revoked" } : item)]);
      setRevealedKey({ value: response.apiKey, title: "API Key 已轮换" });
    } catch (requestError) {
      if (operationIdentity !== accountIdentityRef.current) return;
      setError(requestError instanceof Error ? requestError.message : "API Key 轮换失败");
    } finally {
      if (operationIdentity === accountIdentityRef.current) setKeyBusy(null);
    }
  }

  if (!authReady) {
    return <div className="developer-gate"><LoaderCircle className="spin" size={24} /><span>正在确认登录状态</span></div>;
  }

  if (!user) {
    return (
      <div className="developer-gate developer-login-gate">
        <button type="button" className="developer-back-link" onClick={onHome}><ArrowLeft size={17} /> 返回官网</button>
        <div className="developer-gate-panel">
          <span className="developer-gate-icon"><LockKeyhole size={28} /></span>
          <HuijianBrand />
          <h1>开发者平台需要登录</h1>
          <p>API Key、赠送额度、调用记录和账单都绑定到你的慧鉴AI账号。</p>
          <button type="button" className="developer-primary-action" onClick={onLogin}><LogIn size={17} /> 登录开发者平台</button>
        </div>
      </div>
    );
  }

  return (
    <div className="developer-shell">
      <aside className="developer-sidebar">
        <button type="button" className="developer-brand" onClick={onHome} aria-label="返回慧鉴AI官网">
          <HuijianBrand compact />
          <span>开发者平台</span>
        </button>
        <nav aria-label="开发者平台导航">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            return (
              <button key={item.key} type="button" className={tab === item.key ? "is-active" : ""} onClick={() => setTab(item.key)} aria-label={item.label} title={item.label}>
                <Icon size={17} /><span>{item.label}</span><ChevronRight size={14} />
              </button>
            );
          })}
        </nav>
        <div className="developer-side-note">
          <ShieldCheck size={17} />
          <div><strong>账号级额度</strong><span>轮换 Key 不会重置赠送次数</span></div>
        </div>
        <div className="developer-side-account">
          <span><UserRound size={17} /></span>
          <div><strong>{user.username || "慧鉴开发者"}</strong><small>{user.phone || `用户 ${user.Userid}`}</small></div>
          <button type="button" onClick={onLogout} title="退出登录" aria-label="退出登录"><LogOut size={16} /></button>
        </div>
      </aside>

      <main className="developer-main">
        <header className="developer-topbar">
          <div>
            <p>慧鉴AI / Developer</p>
            <h1 tabIndex={-1}>{NAV_ITEMS.find((item) => item.key === tab)?.label}</h1>
          </div>
          <div className="developer-topbar-actions">
            {error && <span className="developer-inline-error">{error}</span>}
            <button type="button" className="developer-icon-button" onClick={() => void load()} disabled={loading} title="刷新数据" aria-label="刷新数据"><RefreshCw className={loading ? "spin" : ""} size={17} /></button>
            <button type="button" className="developer-secondary-action" onClick={onHome}><ArrowLeft size={16} /> 官网</button>
            <button type="button" className="developer-primary-action compact" onClick={onWorkspace} aria-label="打开鉴伪工作台" title="打开鉴伪工作台"><ShieldCheck size={16} /><span>鉴伪工作台</span></button>
          </div>
        </header>

        <div className="developer-scroll">
          {tab === "overview" && <Overview account={account} available={Boolean(account && !error)} endpoint={endpoint} copied={copied} onCopy={copyText} onOpenKeys={() => setTab("keys")} onOpenDocs={() => setTab("docs")} />}
          {tab === "keys" && <KeysPanel keys={keys} busy={keyBusy} onCreate={() => setCreateOpen(true)} onRotate={rotateKey} onRevoke={revokeKey} />}
          {tab === "docs" && (
            <DocsPanel
              endpoint={endpoint}
              mode={docMode}
              language={language}
              code={examples[language]}
              copied={copied}
              onModeChange={setDocMode}
              onLanguageChange={setLanguage}
              onCopy={copyText}
            />
          )}
          {tab === "usage" && <UsagePanel account={account} ledger={ledger} days={days} onDaysChange={setDays} />}
        </div>
      </main>

      {createOpen && (
        <div className="developer-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target) setCreateOpen(false); }}>
          <section className="developer-modal" role="dialog" aria-modal="true" aria-labelledby="create-key-title">
            <header><div><h2 id="create-key-title">创建 API Key</h2><p>明文只展示一次，创建后请立即保存。</p></div><button type="button" onClick={() => setCreateOpen(false)} aria-label="关闭"><X size={19} /></button></header>
            <label><span>名称</span><input autoFocus value={newKeyName} onChange={(event) => setNewKeyName(event.target.value)} maxLength={120} placeholder="例如：生产环境" /></label>
            <fieldset>
              <legend>检测权限</legend>
              <label className="developer-check-row"><input type="checkbox" checked={newKeyScopes.fast} onChange={(event) => setNewKeyScopes((value) => ({ ...value, fast: event.target.checked }))} /><span><strong>快速检测</strong><small>主模型与水印检测</small></span></label>
              <label className="developer-check-row"><input type="checkbox" checked={newKeyScopes.swarm} onChange={(event) => setNewKeyScopes((value) => ({ ...value, swarm: event.target.checked }))} /><span><strong>Swarm 检测</strong><small>多源专家交叉复核</small></span></label>
              <label className="developer-check-row"><input type="checkbox" checked={newKeyScopes.reports} onChange={(event) => setNewKeyScopes((value) => ({ ...value, reports: event.target.checked }))} /><span><strong>报告下载</strong><small>读取该 Key 创建任务的 PDF 报告</small></span></label>
            </fieldset>
            <label><span>有效期</span><select value={newKeyExpiry} onChange={(event) => setNewKeyExpiry(event.target.value)}><option value="30">30 天</option><option value="90">90 天</option><option value="365">1 年</option><option value="never">永不过期</option></select></label>
            <label><span>IP 白名单 <small>可选，每行一个 IP 或 CIDR</small></span><textarea value={newKeyIps} onChange={(event) => setNewKeyIps(event.target.value)} rows={3} placeholder="203.0.113.10&#10;10.0.0.0/24" /></label>
            <footer><button type="button" className="developer-secondary-action" onClick={() => setCreateOpen(false)}>取消</button><button type="button" className="developer-primary-action" onClick={() => void createKey()} disabled={keyBusy === "create" || !newKeyName.trim() || (!newKeyScopes.fast && !newKeyScopes.swarm)}>{keyBusy === "create" ? <LoaderCircle className="spin" size={16} /> : <Plus size={16} />} 创建 Key</button></footer>
          </section>
        </div>
      )}

      {revealedKey && (
        <div className="developer-modal-backdrop">
          <section className="developer-modal developer-secret-modal" role="dialog" aria-modal="true" aria-labelledby="secret-title">
            <header><div><h2 id="secret-title">{revealedKey.title}</h2><p>关闭后将无法再次查看完整 Key。</p></div></header>
            <div className="developer-secret-value"><code>{revealedKey.value}</code><button type="button" onClick={() => void copyText(revealedKey.value, "secret")}>{copied === "secret" ? <Check size={17} /> : <Copy size={17} />}{copied === "secret" ? "已复制" : "复制"}</button></div>
            <footer><button autoFocus type="button" className="developer-primary-action" onClick={() => setRevealedKey(null)}>我已保存</button></footer>
          </section>
        </div>
      )}
    </div>
  );
}

function Overview({ account, available, endpoint, copied, onCopy, onOpenKeys, onOpenDocs }: {
  account: DeveloperAccountResponse | null;
  available: boolean;
  endpoint: string;
  copied: string;
  onCopy: (value: string, token: string) => void;
  onOpenKeys: () => void;
  onOpenDocs: () => void;
}) {
  const metrics = [
    { label: "赠送额度剩余", value: formatNumber(account?.account.freeRemaining), note: `共 ${formatNumber(account?.account.freeTotal)} 次`, icon: Gauge },
    { label: "快速检测", value: formatNumber(account?.modeSummary.fast.calls), note: "当前统计周期", icon: Activity },
    { label: "Swarm 检测", value: formatNumber(account?.modeSummary.swarm.calls), note: "当前统计周期", icon: ShieldCheck },
    { label: "Token 用量", value: formatNumber(account?.usage.summary.totalTokens), note: "模型调用累计", icon: Code2 },
    { label: "可用余额", value: formatMoney(account?.account.availableBalanceFen), note: "手工充值账户", icon: WalletCards },
  ];
  return (
    <div className="developer-page developer-overview">
      <section className="developer-section-heading"><div><p>API 状态 <span className={available ? "" : "is-unknown"}><i /> {available ? "账号数据已连接" : "状态尚未确认"}</span></p><h2>把慧鉴AI接入你的业务流程</h2><small>一期开放图像鉴伪，快速与 Swarm 模式使用同一套异步任务接口。</small></div><button type="button" className="developer-primary-action" onClick={onOpenKeys}><KeyRound size={16} /> 创建 API Key</button></section>
      <section className="developer-metric-strip" aria-label="开发者账户指标">
        {metrics.map((item) => { const Icon = item.icon; return <article key={item.label}><span><Icon size={18} /></span><div><small>{item.label}</small><strong>{item.value}</strong><p>{item.note}</p></div></article>; })}
      </section>
      <section className="developer-endpoint-band">
        <div><span><SquareTerminal size={19} /></span><div><small>POST</small><code>{endpoint}</code></div></div>
        <button type="button" onClick={() => void onCopy(endpoint, "endpoint")}>{copied === "endpoint" ? <Check size={16} /> : <Copy size={16} />}{copied === "endpoint" ? "已复制" : "复制端点"}</button>
      </section>
      <div className="developer-overview-grid">
        <section className="developer-process-section">
          <header><h3>完成首次调用</h3><span>约 5 分钟</span></header>
          <ol>
            <li><i>1</i><div><strong>创建并保存 API Key</strong><p>按环境拆分 Key，可设置权限、有效期与 IP 白名单。</p></div><button type="button" onClick={onOpenKeys}><ChevronRight size={17} /></button></li>
            <li><i>2</i><div><strong>提交图像任务</strong><p>上传 image，并选择 fast 或 swarm 模式。</p></div><button type="button" onClick={onOpenDocs}><ChevronRight size={17} /></button></li>
            <li><i>3</i><div><strong>轮询状态并下载报告</strong><p>成功后读取结构化证据，或下载 PDF 报告。</p></div><button type="button" onClick={onOpenDocs}><ChevronRight size={17} /></button></li>
          </ol>
        </section>
        <section className="developer-plan-section">
          <header><h3>当前计费</h3><CircleDollarSign size={19} /></header>
          <div className="developer-free-balance"><small>赠送额度</small><strong>{formatNumber(account?.account.freeRemaining)}<em> 次</em></strong><span>仅成功任务扣减</span></div>
          <div className="developer-price-list">
            {(account?.pricing || []).map((price) => <div key={price.mode}><span>{price.name}</span><strong>{price.enabled ? `${formatMoney(price.unitPriceFen)} / 次` : "待开通"}</strong></div>)}
          </div>
          <p>所有 API Key 共享账号额度；失败、超时和参数错误不扣减。</p>
        </section>
      </div>
      <RecentTasks tasks={account?.recentTasks || []} />
    </div>
  );
}

function RecentTasks({ tasks }: { tasks: DeveloperAccountResponse["recentTasks"] }) {
  return (
    <section className="developer-table-section">
      <header><div><h3>最近任务</h3><p>只显示当前账号通过开发者 API 创建的任务。</p></div></header>
      <div className="developer-table-wrap"><table><thead><tr><th>文件</th><th>模式</th><th>状态</th><th>结算</th><th>创建时间</th></tr></thead><tbody>
        {tasks.length ? tasks.map((task) => <tr key={task.id}><td><strong>{task.filename}</strong><small>{task.id}</small></td><td>{task.mode === "swarm" ? "Swarm" : "快速"}</td><td><span className={`developer-status ${task.status}`}>{statusLabel(task.status)}</span></td><td>{task.billing?.status === "settled" ? (task.billing.source === "free" ? "赠送额度" : formatMoney(task.billing.amountFen)) : task.billing?.status === "reserved" ? "已预占" : "未结算"}</td><td>{compactDate(task.createdAt)}</td></tr>) : <tr><td colSpan={5} className="developer-empty-cell">还没有 API 任务</td></tr>}
      </tbody></table></div>
    </section>
  );
}

function KeysPanel({ keys, busy, onCreate, onRotate, onRevoke }: { keys: DeveloperApiKey[]; busy: number | "create" | null; onCreate: () => void; onRotate: (key: DeveloperApiKey) => void; onRevoke: (key: DeveloperApiKey) => void }) {
  const activeCount = keys.filter((key) => key.status === "active").length;
  return (
    <div className="developer-page">
      <section className="developer-section-heading"><div><p>凭据管理</p><h2>API Keys</h2><small>按环境拆分密钥，降低泄露后的影响范围。完整 Key 仅在创建或轮换时展示一次。</small></div><button type="button" className="developer-primary-action" onClick={onCreate} disabled={activeCount >= 5}><Plus size={16} /> 创建 API Key</button></section>
      <section className="developer-security-rail"><ShieldCheck size={19} /><div><strong>服务端只保存 Key 哈希</strong><span>建议设置有效期与 IP 白名单，并定期轮换生产密钥。</span></div><small>{activeCount} / 5 个 active</small></section>
      <section className="developer-table-section developer-key-table"><header><div><h3>密钥列表</h3><p>撤销后立即失效，不影响账号额度和历史账单。</p></div></header><div className="developer-table-wrap"><table><thead><tr><th>名称</th><th>Key</th><th>权限</th><th>限制</th><th>最后使用</th><th aria-label="操作" /></tr></thead><tbody>
        {keys.length ? keys.map((key) => <tr key={`${key.id}-${key.status}`}><td><strong>{key.name}</strong><span className={`developer-key-state ${key.status}`}>{keyStatusLabel(key)}</span></td><td><code>{key.preview}</code></td><td><div className="developer-scope-list">{key.scopes.map((scope) => <span key={scope}>{scope === "image:fast" ? "快速" : scope === "image:swarm" ? "Swarm" : scope === "reports" ? "报告" : scope}</span>)}</div></td><td><small>{key.expiresAt ? `到期 ${compactDate(key.expiresAt)}` : "永不过期"}</small><small>{key.ipAllowlist?.length ? `${key.ipAllowlist.length} 条 IP 规则` : "不限 IP"}</small></td><td>{compactDate(key.lastUsedAt)}</td><td><div className="developer-row-actions">{key.status === "active" && <><button type="button" title="轮换 Key" aria-label={`轮换 ${key.name}`} disabled={busy === key.id} onClick={() => onRotate(key)}>{busy === key.id ? <LoaderCircle className="spin" size={16} /> : <RotateCw size={16} />}</button><button type="button" className="danger" title="撤销 Key" aria-label={`撤销 ${key.name}`} disabled={busy === key.id} onClick={() => onRevoke(key)}><Trash2 size={16} /></button></>}</div></td></tr>) : <tr><td colSpan={6} className="developer-empty-cell">尚未创建 API Key</td></tr>}
      </tbody></table></div></section>
    </div>
  );
}

function DocsPanel({ endpoint, mode, language, code, copied, onModeChange, onLanguageChange, onCopy }: { endpoint: string; mode: "fast" | "swarm"; language: CodeLanguage; code: string; copied: string; onModeChange: (mode: "fast" | "swarm") => void; onLanguageChange: (language: CodeLanguage) => void; onCopy: (value: string, token: string) => void }) {
  return (
    <div className="developer-page developer-docs-page">
      <section className="developer-section-heading"><div><p>API v1</p><h2>图像鉴伪接入</h2><small>统一异步任务接口，支持快速检测与 Swarm 多源复核。请求头使用 Bearer API Key。</small></div><a className="developer-secondary-action" href="/api/developer/openapi.json" target="_blank" rel="noreferrer"><FileJson size={16} /> OpenAPI JSON</a></section>
      <section className="developer-doc-callout"><LockKeyhole size={18} /><div><strong>请求认证</strong><code>Authorization: Bearer rg_sk_...</code></div><span>HTTPS only</span></section>
      <div className="developer-doc-layout">
        <aside className="developer-doc-index"><strong>图像鉴伪</strong><a href="#create-task" className="is-active">创建任务</a><a href="#poll-task">查询状态</a><a href="#download-report">下载报告</a><strong>Agent</strong><a href="#agent-skill">慧鉴AI Skill</a></aside>
        <div className="developer-doc-content">
          <section id="create-task"><p className="developer-method-line"><span>POST</span><code>/api/openapi/v1/image-detections</code></p><h3>创建图像鉴伪任务</h3><p>使用 multipart/form-data 上传图片。相同的 Idempotency-Key 与文件可安全重试，不会重复扣费。</p><div className="developer-mode-selector" aria-label="示例检测模式"><button type="button" className={mode === "fast" ? "is-active" : ""} onClick={() => onModeChange("fast")}><Gauge size={16} /><span><strong>快速检测</strong><small>主模型 + 水印</small></span></button><button type="button" className={mode === "swarm" ? "is-active" : ""} onClick={() => onModeChange("swarm")}><ShieldCheck size={16} /><span><strong>Swarm</strong><small>多源交叉复核</small></span></button></div></section>
          <section className="developer-code-section"><header><div className="developer-language-tabs">{(Object.keys(LANGUAGE_LABELS) as CodeLanguage[]).map((item) => <button type="button" key={item} className={language === item ? "is-active" : ""} onClick={() => onLanguageChange(item)}>{LANGUAGE_LABELS[item]}</button>)}</div><button type="button" onClick={() => void onCopy(code, "code")}>{copied === "code" ? <Check size={15} /> : <Copy size={15} />}{copied === "code" ? "已复制" : "复制"}</button></header><pre><code>{code}</code></pre></section>
          <section id="poll-task"><p className="developer-method-line"><span className="get">GET</span><code>/api/openapi/v1/image-detections/{'{task_id}'}</code></p><h3>查询任务状态</h3><p>建议从 1.5 秒间隔开始轮询，并逐步放慢；收到 429 时遵守 Retry-After。终态为 success、failed 或 rejected，只有 success 会完成额度结算。</p><div className="developer-response-grid"><div><small>status</small><code>queued · running · success · failed · rejected</code></div><div><small>billing.status</small><code>reserved · settled · released</code></div></div></section>
          <section id="download-report"><p className="developer-method-line"><span className="get">GET</span><code>/api/openapi/v1/image-detections/{'{task_id}'}/report</code></p><h3>下载 PDF 报告</h3><p>任务成功后可下载报告。报告与任务都按开发者账号隔离，轮换 Key 后仍可使用同账号的新 Key 访问。</p></section>
          <section id="agent-skill" className="developer-skill-section"><span><Code2 size={22} /></span><div><h3>慧鉴AI Agent Skill</h3><p>为 Codex 或兼容 Agent 提供图片提交、轮询、证据摘要和 PDF 下载流程。通过 HUIJIAN_API_KEY 配置密钥。</p><code>HUIJIAN_API_KEY=rg_sk_...</code></div><a href="https://github.com/MuskAI/rearguard/tree/main/skills/huijian-image-forensics" target="_blank" rel="noreferrer">查看 Skill <ExternalLink size={15} /></a></section>
          <section className="developer-endpoint-note"><SquareTerminal size={18} /><div><strong>完整端点</strong><code>{endpoint}</code></div></section>
        </div>
      </div>
    </div>
  );
}

function UsagePanel({ account, ledger, days, onDaysChange }: { account: DeveloperAccountResponse | null; ledger: DeveloperLedgerEntry[]; days: 7 | 14 | 30 | 90; onDaysChange: (days: 7 | 14 | 30 | 90) => void }) {
  const chart = account?.usage.byDay || [];
  const maxCalls = Math.max(1, ...chart.map((item) => Number(item.requests || 0)));
  return (
    <div className="developer-page">
      <section className="developer-section-heading"><div><p>账号级统计</p><h2>用量与账单</h2><small>检测次数、Token 用量和计费结算按账号汇总，不随 API Key 轮换变化。</small></div><select className="developer-days-select" value={days} onChange={(event) => onDaysChange(Number(event.target.value) as 7 | 14 | 30 | 90)}><option value={7}>近 7 天</option><option value={14}>近 14 天</option><option value={30}>近 30 天</option><option value={90}>近 90 天</option></select></section>
      <section className="developer-metric-strip usage-metrics"><article><span><Activity size={18} /></span><div><small>成功调用</small><strong>{formatNumber((account?.modeSummary.fast.calls || 0) + (account?.modeSummary.swarm.calls || 0))}</strong><p>仅统计已结算任务</p></div></article><article><span><Gauge size={18} /></span><div><small>快速检测</small><strong>{formatNumber(account?.modeSummary.fast.calls)}</strong><p>{formatMoney(account?.modeSummary.fast.spendFen)} 支出</p></div></article><article><span><ShieldCheck size={18} /></span><div><small>Swarm 检测</small><strong>{formatNumber(account?.modeSummary.swarm.calls)}</strong><p>{formatMoney(account?.modeSummary.swarm.spendFen)} 支出</p></div></article><article><span><Code2 size={18} /></span><div><small>Token 用量</small><strong>{formatNumber(account?.usage.summary.totalTokens)}</strong><p>输入与输出合计</p></div></article></section>
      <section className="developer-usage-chart"><header><div><h3>调用趋势</h3><p>每天成功记录到开发者用量系统的请求。</p></div></header><div className="developer-bars" aria-label="每日 API 调用趋势">{chart.map((item) => <div key={item.date} title={`${item.date}: ${item.requests} 次`}><span style={{ height: `${Math.max(item.requests ? 8 : 2, (Number(item.requests || 0) / maxCalls) * 100)}%` }} /><small>{chart.length <= 14 || item.date.endsWith("01") || item === chart[chart.length - 1] ? item.date.slice(5).replace("-", "/") : ""}</small></div>)}</div></section>
      <section className="developer-table-section"><header><div><h3>计费账本</h3><p>赠送额度消费、付费扣款与管理员调整均保留审计记录。</p></div></header><div className="developer-table-wrap"><table><thead><tr><th>时间</th><th>类型</th><th>模式 / 任务</th><th>额度变化</th><th>余额变化</th><th>说明</th></tr></thead><tbody>{ledger.length ? ledger.map((entry) => <tr key={entry.id}><td>{compactDate(entry.createdAt)}</td><td>{entry.type === "detection_free" ? "赠送额度" : entry.type === "detection_charge" ? "检测扣款" : "后台调整"}</td><td><strong>{entry.mode === "swarm" ? "Swarm" : entry.mode === "fast" ? "快速" : "-"}</strong><small>{entry.taskId || "-"}</small></td><td className={entry.freeCallsDelta < 0 ? "negative" : "positive"}>{entry.freeCallsDelta ? `${entry.freeCallsDelta > 0 ? "+" : ""}${entry.freeCallsDelta} 次` : "-"}</td><td className={entry.balanceDeltaFen < 0 ? "negative" : entry.balanceDeltaFen > 0 ? "positive" : ""}>{entry.balanceDeltaFen ? `${entry.balanceDeltaFen > 0 ? "+" : "-"}${formatMoney(Math.abs(entry.balanceDeltaFen))}` : "-"}</td><td>{entry.note}</td></tr>) : <tr><td colSpan={6} className="developer-empty-cell">账本暂无记录</td></tr>}</tbody></table></div></section>
    </div>
  );
}
