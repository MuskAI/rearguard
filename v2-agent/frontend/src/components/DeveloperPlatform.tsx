import { KeyboardEvent as ReactKeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BookOpen,
  Check,
  ChevronRight,
  CircleDollarSign,
  Clock3,
  Code2,
  Copy,
  Download,
  ExternalLink,
  FileJson,
  Gauge,
  Image as ImageIcon,
  KeyRound,
  LayoutDashboard,
  LoaderCircle,
  LockKeyhole,
  LogIn,
  LogOut,
  Plus,
  Play,
  RefreshCw,
  RotateCw,
  ShieldCheck,
  SquareTerminal,
  Trash2,
  UserRound,
  UploadCloud,
  WalletCards,
  X,
} from "lucide-react";
import {
  AccountUser,
  ApiRequestError,
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

type DeveloperTab = "overview" | "keys" | "tester" | "docs" | "usage";
type CodeLanguage = "curl" | "python" | "typescript" | "java" | "go";
type CreateKeyPayload = Parameters<typeof createDeveloperKey>[0];
type DeveloperResourceErrors = { account: string; keys: string; ledger: string };

interface PendingCreateKeyOperation {
  fingerprint: string;
  idempotencyKey: string;
  payload: CreateKeyPayload;
}

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
  { key: "keys", label: "API 密钥", icon: KeyRound },
  { key: "tester", label: "在线调试", icon: SquareTerminal },
  { key: "docs", label: "接入文档", icon: BookOpen },
  { key: "usage", label: "用量与账单", icon: Activity },
];

function initialDeveloperTab(): DeveloperTab {
  const requested = new URLSearchParams(window.location.search).get("developerTab");
  return NAV_ITEMS.some((item) => item.key === requested) ? requested as DeveloperTab : "overview";
}

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

function billingStatusLabel(status: string) {
  return {
    reserved: "已预占",
    settled: "已结算",
    released: "已释放",
  }[status] || status || "待结算";
}

function testerVerdictLabel(verdict: string) {
  const normalized = verdict.trim().toLowerCase();
  if (["real", "真实", "真实图像", "real image"].includes(normalized)) return "真实图像";
  if (["fake", "ai", "ai-generated", "ai_generated", "ai生成图像", "ai 生成图像"].includes(normalized)) return "AI生成图像";
  return verdict || "-";
}

function keyStatusLabel(key: DeveloperApiKey) {
  if (key.status !== "active") return "已撤销";
  if (key.expiresAt && Date.parse(key.expiresAt.replace(" ", "T")) <= Date.now()) return "已过期";
  return "使用中";
}

function expiryFromChoice(choice: string) {
  const days = Number(choice);
  return new Date(Date.now() + days * 24 * 60 * 60 * 1000).toISOString();
}

function hasUnknownOperationOutcome(error: unknown) {
  if (!(error instanceof ApiRequestError)) return true;
  return error.status === 408
    || error.status === 425
    || error.status === 429
    || error.status >= 500;
}

function operationErrorMessage(error: unknown, fallback: string, retryWithSameKey: boolean) {
  const base = error instanceof Error ? error.message : fallback;
  const requestId = error instanceof ApiRequestError && error.requestId
    ? `（请求 ID：${error.requestId}）`
    : "";
  return retryWithSameKey
    ? `${base}${requestId}。本次结果尚未确认，再次提交将使用同一幂等键安全重试。`
    : `${base}${requestId}`;
}

function moveTabFocus(event: ReactKeyboardEvent<HTMLElement>) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
  const current = tabs.indexOf(document.activeElement as HTMLButtonElement);
  if (current < 0) return;
  event.preventDefault();
  const next = event.key === "Home"
    ? 0
    : event.key === "End"
      ? tabs.length - 1
      : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
  tabs[next]?.focus();
  tabs[next]?.click();
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
      'BODY_FILE="$(mktemp)"; HEADER_FILE="$(mktemp)"',
      'trap \'rm -f "$BODY_FILE" "$HEADER_FILE"\' EXIT',
      'DEADLINE=$((SECONDS + 300))',
      'COMPLETED=0',
      'while (( SECONDS < DEADLINE )); do',
      '  HTTP_CODE=$(curl -sS -o "$BODY_FILE" -D "$HEADER_FILE" -w "%{http_code}" "$STATUS_URL" -H "Authorization: Bearer $API_KEY")',
      '  if [ "$HTTP_CODE" = "429" ]; then',
      '    RETRY_AFTER=$(awk \'BEGIN{IGNORECASE=1} /^Retry-After:/ {gsub("\\r","",$2); print $2}\' "$HEADER_FILE")',
      '    sleep "${RETRY_AFTER:-2}"; continue',
      '  fi',
      '  if [ "$HTTP_CODE" -lt 200 ] || [ "$HTTP_CODE" -ge 300 ]; then cat "$BODY_FILE" >&2; exit 1; fi',
      '  STATUS=$(jq -r .status "$BODY_FILE")',
      '  case "$STATUS" in',
      '    success) jq . "$BODY_FILE"; COMPLETED=1; break ;;',
      '    failed|rejected) jq . "$BODY_FILE" >&2; exit 1 ;;',
      '    queued|running) sleep 2 ;;',
      '    *) cat "$BODY_FILE" >&2; exit 1 ;;',
      '  esac',
      'done',
      'if [ "$COMPLETED" -ne 1 ]; then echo "检测任务等待超时" >&2; exit 1; fi',
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
  const [tab, setTab] = useState<DeveloperTab>(initialDeveloperTab);
  const [days, setDays] = useState<7 | 14 | 30 | 90>(30);
  const [account, setAccount] = useState<DeveloperAccountResponse | null>(null);
  const [keys, setKeys] = useState<DeveloperApiKey[]>([]);
  const [ledger, setLedger] = useState<DeveloperLedgerEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [resourceErrors, setResourceErrors] = useState<DeveloperResourceErrors>({ account: "", keys: "", ledger: "" });
  const [createOpen, setCreateOpen] = useState(false);
  const [keyBusy, setKeyBusy] = useState<number | "create" | null>(null);
  const [revealedKey, setRevealedKey] = useState<{ value: string; title: string } | null>(null);
  const [copied, setCopied] = useState("");
  const [language, setLanguage] = useState<CodeLanguage>("curl");
  const [docMode, setDocMode] = useState<"fast" | "swarm">("fast");
  const [testerKeySeed, setTesterKeySeed] = useState("");
  const [newKeyName, setNewKeyName] = useState("生产环境");
  const [newKeyExpiry, setNewKeyExpiry] = useState("90");
  const [newKeyScopes, setNewKeyScopes] = useState({ fast: true, swarm: false, reports: true });
  const [newKeyIps, setNewKeyIps] = useState("");
  const loadGeneration = useRef(0);
  const createDialogRef = useRef<HTMLElement>(null);
  const secretDialogRef = useRef<HTMLElement>(null);
  const modalOpenerRef = useRef<HTMLElement | null>(null);
  const createOperationRef = useRef<PendingCreateKeyOperation | null>(null);
  const rotateOperationKeysRef = useRef(new Map<number, string>());
  const keyMutationInFlightRef = useRef(false);
  const accountIdentity = user?.account_uuid || (user ? String(user.Userid) : "");
  const accountIdentityRef = useRef(accountIdentity);
  accountIdentityRef.current = accountIdentity;

  const selectDeveloperTab = useCallback((nextTab: DeveloperTab) => {
    setTab(nextTab);
    const url = new URL(window.location.href);
    const currentTab = initialDeveloperTab();
    url.searchParams.delete("workspace");
    url.searchParams.set("developer", "1");
    url.searchParams.set("developerTab", nextTab);
    url.hash = "";
    if (currentTab !== nextTab || window.location.search !== url.search) {
      window.history.pushState({ view: "developer", developerTab: nextTab }, "", url);
    }
  }, []);

  const restoreModalOpener = useCallback(() => {
    const opener = modalOpenerRef.current;
    window.setTimeout(() => {
      if (opener?.isConnected) opener.focus();
    }, 0);
  }, []);

  const closeCreateDialog = useCallback(() => {
    setCreateOpen(false);
    restoreModalOpener();
  }, [restoreModalOpener]);

  const closeSecretDialog = useCallback(() => {
    setRevealedKey(null);
    restoreModalOpener();
  }, [restoreModalOpener]);

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
    if (accountResult.status === "fulfilled") setAccount(accountResult.value);
    if (keyResult.status === "fulfilled") setKeys(keyResult.value.keys || []);
    if (ledgerResult.status === "fulfilled") setLedger(ledgerResult.value.entries || []);
    setResourceErrors({
      account: accountResult.status === "rejected" ? "账户额度与调用统计读取失败" : "",
      keys: keyResult.status === "rejected" ? "API Key 列表读取失败" : "",
      ledger: ledgerResult.status === "rejected" ? "计费账本读取失败" : "",
    });
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
    setResourceErrors({ account: "", keys: "", ledger: "" });
    setCreateOpen(false);
    setRevealedKey(null);
    setTesterKeySeed("");
    setKeyBusy(null);
    setLoading(false);
    createOperationRef.current = null;
    rotateOperationKeysRef.current.clear();
    keyMutationInFlightRef.current = false;
  }, [accountIdentity]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const syncTabFromUrl = () => setTab(initialDeveloperTab());
    window.addEventListener("popstate", syncTabFromUrl);
    return () => window.removeEventListener("popstate", syncTabFromUrl);
  }, []);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(""), 1800);
    return () => window.clearTimeout(timer);
  }, [copied]);

  useEffect(() => {
    const dialog = revealedKey ? secretDialogRef.current : createOpen ? createDialogRef.current : null;
    if (!dialog) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusTimer = window.requestAnimationFrame(() => {
      dialog.querySelector<HTMLElement>("[autofocus], button, input, select, textarea, a[href]")?.focus();
    });
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !revealedKey) {
        event.preventDefault();
        closeCreateDialog();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])'));
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      window.cancelAnimationFrame(focusTimer);
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [closeCreateDialog, createOpen, revealedKey]);

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
    if (!newKeyName.trim() || (!newKeyScopes.fast && !newKeyScopes.swarm) || keyMutationInFlightRef.current) return;
    const scopes = [
      ...(newKeyScopes.fast ? ["image:fast"] : []),
      ...(newKeyScopes.swarm ? ["image:swarm"] : []),
      ...(newKeyScopes.reports ? ["reports"] : []),
    ];
    const ipAllowlist = newKeyIps.split(/[\n,]+/).map((value) => value.trim()).filter(Boolean);
    const fingerprint = JSON.stringify({
      name: newKeyName.trim(),
      scopes,
      expiryChoice: newKeyExpiry,
      ipAllowlist,
    });
    let operation = createOperationRef.current;
    if (!operation || operation.fingerprint !== fingerprint) {
      operation = {
        fingerprint,
        idempotencyKey: globalThis.crypto.randomUUID(),
        payload: {
          name: newKeyName.trim(),
          scopes,
          expiresAt: expiryFromChoice(newKeyExpiry),
          ipAllowlist,
        },
      };
      createOperationRef.current = operation;
    }
    keyMutationInFlightRef.current = true;
    setKeyBusy("create");
    setError("");
    const operationIdentity = accountIdentity;
    try {
      const response = await createDeveloperKey(operation.payload, operation.idempotencyKey);
      if (operationIdentity !== accountIdentityRef.current) return;
      createOperationRef.current = null;
      setKeys((current) => [response.key, ...current]);
      setCreateOpen(false);
      setRevealedKey({ value: response.apiKey, title: "API Key 已创建" });
      setTesterKeySeed(response.apiKey);
    } catch (requestError) {
      if (operationIdentity !== accountIdentityRef.current) return;
      const retryWithSameKey = hasUnknownOperationOutcome(requestError);
      if (!retryWithSameKey) createOperationRef.current = null;
      setError(operationErrorMessage(requestError, "API Key 创建失败", retryWithSameKey));
    } finally {
      keyMutationInFlightRef.current = false;
      if (operationIdentity === accountIdentityRef.current) setKeyBusy(null);
    }
  }

  async function revokeKey(key: DeveloperApiKey) {
    if (keyMutationInFlightRef.current) return;
    if (!window.confirm(`确认撤销 ${key.name}？使用该 Key 的请求会立即失败。`)) return;
    keyMutationInFlightRef.current = true;
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
      keyMutationInFlightRef.current = false;
      if (operationIdentity === accountIdentityRef.current) setKeyBusy(null);
    }
  }

  async function rotateKey(key: DeveloperApiKey, opener?: HTMLElement) {
    if (keyMutationInFlightRef.current) return;
    if (!window.confirm(`轮换 ${key.name}？旧 Key 会立即撤销。`)) return;
    modalOpenerRef.current = opener || (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    const idempotencyKey = rotateOperationKeysRef.current.get(key.id) || globalThis.crypto.randomUUID();
    rotateOperationKeysRef.current.set(key.id, idempotencyKey);
    keyMutationInFlightRef.current = true;
    setKeyBusy(key.id);
    setError("");
    const operationIdentity = accountIdentity;
    try {
      const response = await rotateDeveloperKey(key.id, idempotencyKey);
      if (operationIdentity !== accountIdentityRef.current) return;
      rotateOperationKeysRef.current.delete(key.id);
      setKeys((current) => [response.key, ...current.map((item) => item.id === key.id ? { ...item, status: "revoked" } : item)]);
      setRevealedKey({ value: response.apiKey, title: "API Key 已轮换" });
      setTesterKeySeed(response.apiKey);
    } catch (requestError) {
      if (operationIdentity !== accountIdentityRef.current) return;
      const retryWithSameKey = hasUnknownOperationOutcome(requestError);
      if (!retryWithSameKey) rotateOperationKeysRef.current.delete(key.id);
      setError(operationErrorMessage(requestError, "API Key 轮换失败", retryWithSameKey));
    } finally {
      keyMutationInFlightRef.current = false;
      if (operationIdentity === accountIdentityRef.current) setKeyBusy(null);
    }
  }

  function openCreateDialog(opener?: HTMLElement) {
    modalOpenerRef.current = opener || (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    setError("");
    setCreateOpen(true);
  }

  if (!authReady) {
    return <div className="developer-gate"><LoaderCircle className="spin" size={24} /><span>正在确认登录状态</span></div>;
  }

  if (!user) {
    return (
      <div className="developer-gate developer-login-gate">
        <div className="developer-gate-panel">
          <span className="developer-gate-icon"><LockKeyhole size={28} /></span>
          <HuijianBrand onClick={onHome} />
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
              <button key={item.key} type="button" className={tab === item.key ? "is-active" : ""} onClick={() => selectDeveloperTab(item.key)} aria-current={tab === item.key ? "page" : undefined} aria-label={item.label} title={item.label}>
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
            <button type="button" className="developer-primary-action compact" onClick={onWorkspace} aria-label="打开鉴伪工作台" title="打开鉴伪工作台"><ShieldCheck size={16} /><span>鉴伪工作台</span></button>
          </div>
        </header>

        <div className="developer-scroll" aria-busy={loading}>
          {loading && account === null && keys.length === 0 && ledger.length === 0 ? <DeveloperLoadingState /> : <>
          {error && <div className="developer-page-error" role="alert">{error}</div>}
          {tab === "overview" && <Overview account={account} accountError={resourceErrors.account} keysReady={!resourceErrors.keys} endpoint={endpoint} copied={copied} onCopy={copyText} onOpenKeys={() => selectDeveloperTab("keys")} onOpenDocs={() => selectDeveloperTab("docs")} />}
          {tab === "keys" && <KeysPanel keys={keys} loadError={resourceErrors.keys} busy={keyBusy} loading={loading} onCreate={openCreateDialog} onRotate={rotateKey} onRevoke={revokeKey} />}
          {tab === "tester" && <ApiTesterPanel key={accountIdentity} endpoint={endpoint} apiKeySeed={testerKeySeed} />}
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
          {tab === "usage" && <UsagePanel account={account} ledger={ledger} accountError={resourceErrors.account} ledgerError={resourceErrors.ledger} days={days} onDaysChange={setDays} />}
          </>}
        </div>
      </main>

      {createOpen && (
        <div className="developer-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target) closeCreateDialog(); }}>
          <section ref={createDialogRef} className="developer-modal" role="dialog" aria-modal="true" aria-labelledby="create-key-title">
            <header><div><h2 id="create-key-title">创建 API Key</h2><p>明文只展示一次，创建后请立即保存。</p></div><button type="button" onClick={closeCreateDialog} aria-label="关闭"><X size={19} /></button></header>
            <label><span>名称</span><input autoFocus value={newKeyName} onChange={(event) => setNewKeyName(event.target.value)} maxLength={120} placeholder="例如：生产环境" /></label>
            <fieldset>
              <legend>检测权限</legend>
              <label className="developer-check-row"><input type="checkbox" checked={newKeyScopes.fast} onChange={(event) => setNewKeyScopes((value) => ({ ...value, fast: event.target.checked }))} /><span><strong>快速检测</strong><small>真实性分析与水印证据</small></span></label>
              <label className="developer-check-row"><input type="checkbox" checked={newKeyScopes.swarm} onChange={(event) => setNewKeyScopes((value) => ({ ...value, swarm: event.target.checked }))} /><span><strong>Swarm 多源复核</strong><small>多条独立证据交叉核验</small></span></label>
              <label className="developer-check-row"><input type="checkbox" checked={newKeyScopes.reports} onChange={(event) => setNewKeyScopes((value) => ({ ...value, reports: event.target.checked }))} /><span><strong>报告下载</strong><small>读取该 Key 创建任务的 PDF 报告</small></span></label>
            </fieldset>
            <label><span>有效期</span><select value={newKeyExpiry} onChange={(event) => setNewKeyExpiry(event.target.value)}><option value="30">30 天</option><option value="90">90 天</option><option value="365">1 年</option></select></label>
            <label><span>IP 白名单 <small>可选，每行一个 IP 或 CIDR</small></span><textarea value={newKeyIps} onChange={(event) => setNewKeyIps(event.target.value)} rows={3} placeholder="203.0.113.10&#10;10.0.0.0/24" /></label>
            {error && <p className="developer-modal-error" role="alert">{error}</p>}
            <footer><button type="button" className="developer-secondary-action" onClick={closeCreateDialog}>取消</button><button type="button" className="developer-primary-action" onClick={() => void createKey()} disabled={keyBusy === "create" || !newKeyName.trim() || (!newKeyScopes.fast && !newKeyScopes.swarm)}>{keyBusy === "create" ? <LoaderCircle className="spin" size={16} /> : <Plus size={16} />} 创建 Key</button></footer>
          </section>
        </div>
      )}

      {revealedKey && (
        <div className="developer-modal-backdrop">
          <section ref={secretDialogRef} className="developer-modal developer-secret-modal" role="dialog" aria-modal="true" aria-labelledby="secret-title">
            <header><div><h2 id="secret-title">{revealedKey.title}</h2><p>关闭后将无法再次查看完整 Key。</p></div></header>
            <div className="developer-secret-value"><code>{revealedKey.value}</code><button type="button" onClick={() => void copyText(revealedKey.value, "secret")}>{copied === "secret" ? <Check size={17} /> : <Copy size={17} />}{copied === "secret" ? "已复制" : "复制"}</button></div>
            <footer><button autoFocus type="button" className="developer-primary-action" onClick={closeSecretDialog}>我已保存</button></footer>
          </section>
        </div>
      )}
    </div>
  );
}

type TesterTask = Record<string, unknown>;

function testerObject(value: unknown): TesterTask {
  return value && typeof value === "object" && !Array.isArray(value) ? value as TesterTask : {};
}

function testerString(value: unknown, fallback = "") {
  return value === null || value === undefined ? fallback : String(value);
}

function testerLink(task: TesterTask, key: "self" | "report") {
  const links = testerObject(task.links);
  const value = testerString(links[key]);
  return value ? new URL(value, window.location.origin).toString() : "";
}

function testerTaskId(task: TesterTask) {
  return testerString(task.id || task.taskId || task.jobId, "-");
}

function waitForTester(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = window.setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    }, { once: true });
  });
}

async function readTesterResponse(response: Response) {
  const text = await response.text();
  let payload: unknown = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`接口返回了非 JSON 响应（HTTP ${response.status}）`);
  }
  const body = testerObject(payload);
  if (!response.ok) {
    const error = testerObject(body.error);
    const message = testerString(body.message || body.detail || error.message, `请求失败（HTTP ${response.status}）`);
    throw new Error(message);
  }
  return body;
}

function ApiTesterPanel({ endpoint, apiKeySeed }: { endpoint: string; apiKeySeed: string }) {
  const [apiKey, setApiKey] = useState(apiKeySeed);
  const [mode, setMode] = useState<"fast" | "swarm">("fast");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [running, setRunning] = useState(false);
  const [phase, setPhase] = useState<"idle" | "submitting" | "polling" | "complete">("idle");
  const [task, setTask] = useState<TesterTask | null>(null);
  const [error, setError] = useState("");
  const [elapsedMs, setElapsedMs] = useState(0);
  const [pollCount, setPollCount] = useState(0);
  const [requestId, setRequestId] = useState("");
  const [reportBusy, setReportBusy] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);
  const requestOperationRef = useRef<{ fingerprint: string; idempotencyKey: string } | null>(null);
  const fileDigestRef = useRef(new WeakMap<File, Promise<string>>());

  useEffect(() => {
    setApiKey(apiKeySeed);
    requestOperationRef.current = null;
  }, [apiKeySeed]);

  useEffect(() => () => controllerRef.current?.abort(), []);

  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview);
  }, [preview]);

  function chooseTesterFile(next: File | null) {
    if (preview) URL.revokeObjectURL(preview);
    setFile(next);
    setPreview(next ? URL.createObjectURL(next) : "");
    setTask(null);
    setError("");
    setPhase("idle");
    requestOperationRef.current = null;
  }

  async function fileDigest(value: File) {
    const cached = fileDigestRef.current.get(value);
    if (cached) return cached;
    const pending = (async () => {
      if (!globalThis.crypto?.subtle) return `${value.name}:${value.size}:${value.lastModified}`;
      const digest = await globalThis.crypto.subtle.digest("SHA-256", await value.arrayBuffer());
      return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    })();
    fileDigestRef.current.set(value, pending);
    return pending;
  }

  function resetTesterRequest() {
    requestOperationRef.current = null;
    setTask(null);
    setError("");
    setPhase("idle");
  }

  async function runTest() {
    if (running || !file || !apiKey.trim()) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    const startedAt = performance.now();
    setRunning(true);
    setPhase("submitting");
    setTask(null);
    setError("");
    setElapsedMs(0);
    setPollCount(0);
    setRequestId("");
    try {
      const fingerprint = `${mode}:${apiKey.trim()}:${await fileDigest(file)}`;
      let operation = requestOperationRef.current;
      if (!operation || operation.fingerprint !== fingerprint) {
        operation = { fingerprint, idempotencyKey: globalThis.crypto.randomUUID() };
        requestOperationRef.current = operation;
      }
      const body = new FormData();
      body.append("mode", mode);
      body.append("image", file);
      const response = await fetch(endpoint, {
        method: "POST",
        body,
        signal: controller.signal,
        headers: {
          Authorization: `Bearer ${apiKey.trim()}`,
          "Idempotency-Key": operation.idempotencyKey,
        },
      });
      setRequestId(response.headers.get("X-Request-ID") || response.headers.get("X-Request-Id") || "");
      const created = await readTesterResponse(response);
      let current = Object.keys(testerObject(created.task)).length ? testerObject(created.task) : created;
      setTask(current);
      const pollUrl = testerLink(current, "self") || `${endpoint}/${encodeURIComponent(testerTaskId(current))}`;
      const deadline = performance.now() + 300_000;
      let polls = 0;
      while (!["success", "failed", "rejected"].includes(testerString(current.status)) && performance.now() < deadline) {
        setPhase("polling");
        await waitForTester(1_400, controller.signal);
        const pollResponse = await fetch(pollUrl, {
          signal: controller.signal,
          headers: { Authorization: `Bearer ${apiKey.trim()}` },
        });
        if (pollResponse.status === 429) {
          const retrySeconds = Math.max(1, Number(pollResponse.headers.get("Retry-After") || 2));
          await waitForTester(retrySeconds * 1000, controller.signal);
          continue;
        }
        current = await readTesterResponse(pollResponse);
        polls += 1;
        setPollCount(polls);
        setTask(current);
        setElapsedMs(Math.round(performance.now() - startedAt));
      }
      if (!["success", "failed", "rejected"].includes(testerString(current.status))) {
        throw new Error("任务在 5 分钟内未进入终态，请稍后使用任务编号继续查询");
      }
      setTask(current);
      setPhase("complete");
      if (testerString(current.status) !== "success") {
        const taskError = testerObject(current.error);
        setError(testerString(taskError.message || current.message, `任务状态：${testerString(current.status)}`));
      }
    } catch (requestError) {
      if (requestError instanceof DOMException && requestError.name === "AbortError") {
        setError("已停止等待；服务器任务可能仍在运行。任务编号可在响应区查看。");
      } else {
        setError(requestError instanceof Error ? requestError.message : "API 调用失败");
      }
    } finally {
      setElapsedMs(Math.round(performance.now() - startedAt));
      setRunning(false);
      controllerRef.current = null;
    }
  }

  async function downloadTesterReport() {
    if (!task || reportBusy) return;
    const reportUrl = testerLink(task, "report");
    if (!reportUrl) {
      setError("当前响应没有返回报告下载地址");
      return;
    }
    setReportBusy(true);
    setError("");
    try {
      const response = await fetch(reportUrl, { headers: { Authorization: `Bearer ${apiKey.trim()}` } });
      if (!response.ok) {
        const contentType = response.headers.get("content-type") || "";
        if (contentType.includes("json")) await readTesterResponse(response);
        throw new Error(`报告下载失败（HTTP ${response.status}）`);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `huijian-${testerTaskId(task)}.pdf`;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (downloadError) {
      setError(downloadError instanceof Error ? downloadError.message : "报告下载失败");
    } finally {
      setReportBusy(false);
    }
  }

  const statusCode = testerString(task?.status, phase === "idle" ? "idle" : phase === "submitting" ? "submitting" : "running");
  const billing = testerObject(task?.billing);
  const resultEnvelope = testerObject(task?.result);
  const result = Object.keys(testerObject(resultEnvelope.result)).length ? testerObject(resultEnvelope.result) : resultEnvelope;
  const verdict = testerVerdictLabel(testerString(result.final_label || result.verdict || task?.verdict, "-"));

  return (
    <section className="developer-tester-page" aria-labelledby="api-tester-title">
      <header className="developer-page-heading">
        <div><p>API CONSOLE</p><h2 id="api-tester-title">在线调用调试</h2><span>使用真实 API Key 与公开接口验证请求、耗时和计费结果。</span></div>
        <span className="developer-tester-security"><LockKeyhole size={15} /> Key 仅保存在当前页面内存</span>
      </header>
      <div className="developer-tester-grid">
        <section className="tester-request-panel">
          <div className="tester-endpoint"><span>POST</span><code>{endpoint.replace(window.location.origin, "")}</code></div>
          <label className="tester-field"><span>API Key</span><input type="password" autoComplete="off" spellCheck={false} value={apiKey} onChange={(event) => { setApiKey(event.target.value); resetTesterRequest(); }} placeholder="rg_sk_..." /></label>
          <fieldset className="tester-mode-field">
            <legend>检测模式</legend>
            <button type="button" aria-pressed={mode === "fast"} className={mode === "fast" ? "is-active" : ""} onClick={() => { setMode("fast"); resetTesterRequest(); }}><Gauge size={17} /><span><strong>快速检测</strong><small>日常筛查</small></span></button>
            <button type="button" aria-pressed={mode === "swarm"} className={mode === "swarm" ? "is-active" : ""} onClick={() => { setMode("swarm"); resetTesterRequest(); }}><ShieldCheck size={17} /><span><strong>Swarm 模式</strong><small>多源复核</small></span></button>
          </fieldset>
          <label
            className={`tester-file-drop ${file ? "has-file" : ""}`}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              chooseTesterFile(event.dataTransfer.files?.[0] || null);
            }}
          >
            <input type="file" accept="image/jpeg,image/png,image/webp,image/bmp,image/heic,image/heif,.heic,.heif" onChange={(event) => chooseTesterFile(event.target.files?.[0] || null)} />
            {preview ? <img src={preview} alt="待调试图片预览" /> : <span><UploadCloud size={25} /></span>}
            <div><strong>{file ? file.name : "选择或拖入测试图片"}</strong><small>{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB · 点击可替换` : "使用公开接口支持的图片格式"}</small></div>
          </label>
          <div className="tester-actions">
            <button type="button" className="developer-primary-action" disabled={running || !file || !apiKey.trim()} onClick={() => void runTest()}>{running ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}{running ? "正在调用" : phase === "complete" ? "重新查询同一请求" : "发送真实请求"}</button>
            {running && <button type="button" className="developer-secondary-action" onClick={() => controllerRef.current?.abort()}>停止等待</button>}
          </div>
          {file && <p className="tester-idempotency-note"><ShieldCheck size={14} /> 网络中断后重试会复用同一请求，不会重复创建任务或扣量。</p>}
        </section>

        <section className="tester-response-panel" aria-live="polite">
          <header><div><SquareTerminal size={18} /><span><strong>响应监视器</strong><small>{phase === "idle" ? "等待发送请求" : phase === "submitting" ? "正在创建任务" : phase === "polling" ? `正在轮询 · ${pollCount} 次` : "任务已进入终态"}</small></span></div><b className={`tester-status ${statusCode}`}>{statusCode === "idle" ? "未运行" : statusCode === "submitting" ? "正在提交" : statusLabel(statusCode)}</b></header>
          {!task && !error && phase === "idle" && <div className="tester-empty"><ImageIcon size={28} /><strong>还没有调用结果</strong><p>左侧填写 Key、选择图片并发送请求。</p></div>}
          {(running || task) && (
            <div className="tester-metrics">
              <div><Clock3 size={15} /><span><small>总耗时</small><strong>{elapsedMs ? `${(elapsedMs / 1000).toFixed(2)} s` : "计算中"}</strong></span></div>
              <div><Activity size={15} /><span><small>任务状态</small><strong>{statusCode === "submitting" ? "正在提交" : statusLabel(statusCode)}</strong></span></div>
              <div><CircleDollarSign size={15} /><span><small>计费状态</small><strong>{billingStatusLabel(testerString(billing.status))}</strong></span></div>
            </div>
          )}
          {task && (
            <>
              <dl className="tester-result-facts">
                <div><dt>任务编号</dt><dd>{testerTaskId(task)}</dd></div>
                <div><dt>检测模式</dt><dd>{mode === "swarm" ? "Swarm" : "快速"}</dd></div>
                <div><dt>检测结论</dt><dd>{verdict}</dd></div>
                <div><dt>请求编号</dt><dd>{requestId || "未返回"}</dd></div>
              </dl>
              <div className="tester-response-actions">
                {testerString(task.status) === "success" && testerLink(task, "report") && <button type="button" className="developer-secondary-action" disabled={reportBusy} onClick={() => void downloadTesterReport()}>{reportBusy ? <LoaderCircle className="spin" size={15} /> : <Download size={15} />} 下载 PDF</button>}
                <button type="button" className="developer-secondary-action" onClick={() => void copyTextForTester(task)}><Copy size={15} /> 复制 JSON</button>
              </div>
              <details className="tester-json"><summary>查看完整结构化响应 <ChevronRight size={14} /></summary><pre>{JSON.stringify(task, null, 2)}</pre></details>
            </>
          )}
          {error && <div className="tester-error" role="alert"><AlertTriangle size={17} /><span><strong>调用提示</strong><p>{error}</p></span></div>}
        </section>
      </div>
    </section>
  );
}

async function copyTextForTester(task: TesterTask) {
  const text = JSON.stringify(task, null, 2);
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    window.prompt("复制 JSON 响应", text);
  }
}

function DeveloperLoadingState() {
  return (
    <section className="developer-loading-state" role="status" aria-live="polite">
      <span><LoaderCircle className="spin" size={22} /></span>
      <div><strong>正在读取开发者账户</strong><p>同步额度、API Key 与调用记录</p></div>
    </section>
  );
}

function Overview({ account, accountError, keysReady, endpoint, copied, onCopy, onOpenKeys, onOpenDocs }: {
  account: DeveloperAccountResponse | null;
  accountError: string;
  keysReady: boolean;
  endpoint: string;
  copied: string;
  onCopy: (value: string, token: string) => void;
  onOpenKeys: () => void;
  onOpenDocs: () => void;
}) {
  const metrics = [
    { label: "赠送额度剩余", value: formatNumber(account?.account.freeRemaining), note: `共 ${formatNumber(account?.account.freeTotal)} 次`, icon: Gauge },
    { label: "快速检测", value: formatNumber(account?.modeSummary.fast.calls), note: "当前统计周期", icon: Activity },
    { label: "Swarm 多源复核", value: formatNumber(account?.modeSummary.swarm.calls), note: "当前统计周期", icon: ShieldCheck },
    { label: "Token 用量", value: formatNumber(account?.usage.summary.totalTokens), note: "模型调用累计", icon: Code2 },
    { label: "可用余额", value: formatMoney(account?.account.availableBalanceFen), note: "手工充值账户", icon: WalletCards },
  ];
  return (
    <div className="developer-page developer-overview">
      <section className="developer-section-heading"><div><p>开发者平台 <span><i /> 账号级权限与用量</span></p><h2>把慧鉴AI接入你的业务流程</h2><small>一期开放图像鉴伪；快速检测与 Swarm 复核共用异步接口。</small></div><button type="button" className="developer-primary-action" onClick={onOpenKeys} disabled={!keysReady} title={keysReady ? undefined : "API Key 状态尚未读取成功"}><KeyRound size={16} /> 创建 API Key</button></section>
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
      <RecentTasks tasks={account?.recentTasks || []} error={accountError} />
    </div>
  );
}

function RecentTasks({ tasks, error }: { tasks: DeveloperAccountResponse["recentTasks"]; error?: string }) {
  return (
    <section className="developer-table-section">
      <header><div><h3>最近任务</h3><p>只显示当前账号通过开发者 API 创建的任务。</p></div></header>
      <div className="developer-table-wrap"><table><thead><tr><th>文件</th><th>模式</th><th>状态</th><th>结算</th><th>创建时间</th></tr></thead><tbody>
        {error ? <tr><td colSpan={5} className="developer-empty-cell developer-load-error">{error}，请使用页面右上角刷新</td></tr> : tasks.length ? tasks.map((task) => <tr key={task.id}><td><strong>{task.filename}</strong><small>{task.id}</small></td><td>{task.mode === "swarm" ? "Swarm" : "快速"}</td><td><span className={`developer-status ${task.status}`}>{statusLabel(task.status)}</span></td><td>{task.billing?.status === "settled" ? (task.billing.source === "free" ? "赠送额度" : formatMoney(task.billing.amountFen)) : task.billing?.status === "reserved" ? "已预占" : "未结算"}</td><td>{compactDate(task.createdAt)}</td></tr>) : <tr><td colSpan={5} className="developer-empty-cell">还没有 API 任务</td></tr>}
      </tbody></table></div>
    </section>
  );
}

function KeysPanel({ keys, loadError, busy, loading, onCreate, onRotate, onRevoke }: { keys: DeveloperApiKey[]; loadError: string; busy: number | "create" | null; loading: boolean; onCreate: (opener: HTMLElement) => void; onRotate: (key: DeveloperApiKey, opener: HTMLElement) => void; onRevoke: (key: DeveloperApiKey) => void }) {
  const activeCount = keys.filter((key) => key.status === "active").length;
  return (
    <div className="developer-page">
      <section className="developer-section-heading"><div><p>凭据管理</p><h2>API 密钥</h2><small>按环境拆分 API Key，降低泄露后的影响范围。完整密钥仅在创建或轮换时展示一次。</small></div><button type="button" className="developer-primary-action" onClick={(event) => onCreate(event.currentTarget)} disabled={loading || Boolean(loadError) || activeCount >= 5 || busy !== null}><Plus size={16} /> 创建 API Key</button></section>
      <section className="developer-security-rail"><ShieldCheck size={19} /><div><strong>服务端只保存 Key 哈希</strong><span>建议设置有效期与 IP 白名单，并定期轮换生产密钥。</span></div><small>{loadError ? "状态未知" : `${activeCount} / 5 个 active`}</small></section>
      <section className="developer-table-section developer-key-table"><header><div><h3>密钥列表</h3><p>撤销后立即失效，不影响账号额度和历史账单。</p></div></header><div className="developer-table-wrap"><table><thead><tr><th>名称</th><th>Key</th><th>权限</th><th>限制</th><th>最后使用</th><th aria-label="操作" /></tr></thead><tbody>
        {loadError ? <tr><td colSpan={6} className="developer-empty-cell developer-load-error">{loadError}，为避免重复创建，当前已暂停凭据操作</td></tr> : keys.length ? keys.map((key) => <tr key={`${key.id}-${key.status}`}><td><strong>{key.name}</strong><span className={`developer-key-state ${key.status}`}>{keyStatusLabel(key)}</span></td><td><code>{key.preview}</code></td><td><div className="developer-scope-list">{key.scopes.map((scope) => <span key={scope}>{scope === "image:fast" ? "快速" : scope === "image:swarm" ? "Swarm" : scope === "reports" ? "报告" : scope}</span>)}</div></td><td><small>{key.expiresAt ? `到期 ${compactDate(key.expiresAt)}` : "未设置到期时间"}</small><small>{key.ipAllowlist?.length ? `${key.ipAllowlist.length} 条 IP 规则` : "不限 IP"}</small></td><td>{compactDate(key.lastUsedAt)}</td><td><div className="developer-row-actions">{key.status === "active" && <><button type="button" title="轮换 Key" aria-label={`轮换 ${key.name}`} disabled={busy !== null} onClick={(event) => onRotate(key, event.currentTarget)}>{busy === key.id ? <LoaderCircle className="spin" size={16} /> : <RotateCw size={16} />}</button><button type="button" className="danger" title="撤销 Key" aria-label={`撤销 ${key.name}`} disabled={busy !== null} onClick={() => onRevoke(key)}><Trash2 size={16} /></button></>}</div></td></tr>) : <tr><td colSpan={6} className="developer-empty-cell">尚未创建 API Key</td></tr>}
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
          <section id="create-task"><p className="developer-method-line"><span>POST</span><code>/api/openapi/v1/image-detections</code></p><h3>创建图像鉴伪任务</h3><p>使用 multipart/form-data 上传图片。相同的 Idempotency-Key 与文件可安全重试，不会重复扣费。</p><div className="developer-mode-selector" role="group" aria-label="示例检测模式"><button type="button" aria-pressed={mode === "fast"} className={mode === "fast" ? "is-active" : ""} onClick={() => onModeChange("fast")}><Gauge size={16} /><span><strong>快速检测</strong><small>真实性分析 + 水印证据</small></span></button><button type="button" aria-pressed={mode === "swarm"} className={mode === "swarm" ? "is-active" : ""} onClick={() => onModeChange("swarm")}><ShieldCheck size={16} /><span><strong>Swarm 多源复核</strong><small>API 参数：swarm</small></span></button></div></section>
          <section className="developer-code-section"><header><div className="developer-language-tabs" role="tablist" aria-label="示例代码语言" onKeyDown={moveTabFocus}>{(Object.keys(LANGUAGE_LABELS) as CodeLanguage[]).map((item) => <button id={`developer-language-${item}`} type="button" role="tab" aria-selected={language === item} aria-controls="developer-code-panel" tabIndex={language === item ? 0 : -1} key={item} className={language === item ? "is-active" : ""} onClick={() => onLanguageChange(item)}>{LANGUAGE_LABELS[item]}</button>)}</div><button type="button" onClick={() => void onCopy(code, "code")}>{copied === "code" ? <Check size={15} /> : <Copy size={15} />}{copied === "code" ? "已复制" : "复制"}</button></header><pre id="developer-code-panel" role="tabpanel" aria-labelledby={`developer-language-${language}`} tabIndex={0}><code>{code}</code></pre></section>
          <section id="poll-task"><p className="developer-method-line"><span className="get">GET</span><code>/api/openapi/v1/image-detections/{'{task_id}'}</code></p><h3>查询任务状态</h3><p>建议从 1.5 秒间隔开始轮询，并逐步放慢；收到 429 时遵守 Retry-After。终态为 success、failed 或 rejected，只有 success 会完成额度结算。</p><div className="developer-response-grid"><div><small>status</small><code>queued · running · success · failed · rejected</code></div><div><small>billing.status</small><code>reserved · settled · released</code></div></div></section>
          <section id="download-report"><p className="developer-method-line"><span className="get">GET</span><code>/api/openapi/v1/image-detections/{'{task_id}'}/report</code></p><h3>下载 PDF 报告</h3><p>任务成功后可下载报告。报告与任务都按开发者账号隔离，轮换 Key 后仍可使用同账号的新 Key 访问。</p></section>
          <section id="agent-skill" className="developer-skill-section"><span><Code2 size={22} /></span><div><h3>慧鉴AI Agent Skill</h3><p>为 Codex 或兼容 Agent 提供图片提交、轮询、证据摘要和 PDF 下载流程。通过 HUIJIAN_API_KEY 配置密钥。</p><code>HUIJIAN_API_KEY=rg_sk_...</code></div><a href="https://github.com/MuskAI/rearguard/tree/main/skills/huijian-image-forensics" target="_blank" rel="noreferrer">查看 Skill <ExternalLink size={15} /></a></section>
          <section className="developer-endpoint-note"><SquareTerminal size={18} /><div><strong>完整端点</strong><code>{endpoint}</code></div></section>
        </div>
      </div>
    </div>
  );
}

function UsagePanel({ account, ledger, accountError, ledgerError, days, onDaysChange }: { account: DeveloperAccountResponse | null; ledger: DeveloperLedgerEntry[]; accountError: string; ledgerError: string; days: 7 | 14 | 30 | 90; onDaysChange: (days: 7 | 14 | 30 | 90) => void }) {
  const chart = account?.usage.byDay || [];
  const maxCalls = Math.max(1, ...chart.map((item) => Number(item.requests || 0)));
  return (
    <div className="developer-page">
      <section className="developer-section-heading"><div><p>账号级统计</p><h2>用量与账单</h2><small>检测次数、Token 用量和计费结算按账号汇总，不随 API Key 轮换变化。</small></div><select className="developer-days-select" value={days} onChange={(event) => onDaysChange(Number(event.target.value) as 7 | 14 | 30 | 90)}><option value={7}>近 7 天</option><option value={14}>近 14 天</option><option value={30}>近 30 天</option><option value={90}>近 90 天</option></select></section>
      <section className="developer-metric-strip usage-metrics"><article><span><Activity size={18} /></span><div><small>成功调用</small><strong>{formatNumber(accountError || !account ? undefined : account.modeSummary.fast.calls + account.modeSummary.swarm.calls)}</strong><p>仅统计已结算任务</p></div></article><article><span><Gauge size={18} /></span><div><small>快速检测</small><strong>{formatNumber(account?.modeSummary.fast.calls)}</strong><p>{formatMoney(account?.modeSummary.fast.spendFen)} 支出</p></div></article><article><span><ShieldCheck size={18} /></span><div><small>Swarm 多源复核</small><strong>{formatNumber(account?.modeSummary.swarm.calls)}</strong><p>{formatMoney(account?.modeSummary.swarm.spendFen)} 支出</p></div></article><article><span><Code2 size={18} /></span><div><small>Token 用量</small><strong>{formatNumber(account?.usage.summary.totalTokens)}</strong><p>输入与输出合计</p></div></article></section>
      <section className="developer-usage-chart"><header><div><h3>调用趋势</h3><p>每天成功记录到开发者用量系统的请求。</p></div></header><div className="developer-bars" aria-label="每日 API 调用趋势">{chart.map((item) => <div key={item.date} title={`${item.date}: ${item.requests} 次`}><span style={{ height: `${Math.max(item.requests ? 8 : 2, (Number(item.requests || 0) / maxCalls) * 100)}%` }} /><small>{chart.length <= 14 || item.date.endsWith("01") || item === chart[chart.length - 1] ? item.date.slice(5).replace("-", "/") : ""}</small></div>)}</div></section>
      <section className="developer-table-section"><header><div><h3>计费账本</h3><p>赠送额度消费、付费扣款与管理员调整均保留审计记录。</p></div></header><div className="developer-table-wrap"><table><thead><tr><th>时间</th><th>类型</th><th>模式 / 任务</th><th>额度变化</th><th>余额变化</th><th>说明</th></tr></thead><tbody>{ledgerError ? <tr><td colSpan={6} className="developer-empty-cell developer-load-error">{ledgerError}，当前不展示空账本</td></tr> : ledger.length ? ledger.map((entry) => <tr key={entry.id}><td>{compactDate(entry.createdAt)}</td><td>{entry.type === "detection_free" ? "赠送额度" : entry.type === "detection_charge" ? "检测扣款" : "后台调整"}</td><td><strong>{entry.mode === "swarm" ? "Swarm" : entry.mode === "fast" ? "快速" : "-"}</strong><small>{entry.taskId || "-"}</small></td><td className={entry.freeCallsDelta < 0 ? "negative" : "positive"}>{entry.freeCallsDelta ? `${entry.freeCallsDelta > 0 ? "+" : ""}${entry.freeCallsDelta} 次` : "-"}</td><td className={entry.balanceDeltaFen < 0 ? "negative" : entry.balanceDeltaFen > 0 ? "positive" : ""}>{entry.balanceDeltaFen ? `${entry.balanceDeltaFen > 0 ? "+" : "-"}${formatMoney(Math.abs(entry.balanceDeltaFen))}` : "-"}</td><td>{entry.note}</td></tr>) : <tr><td colSpan={6} className="developer-empty-cell">账本暂无记录</td></tr>}</tbody></table></div></section>
    </div>
  );
}
