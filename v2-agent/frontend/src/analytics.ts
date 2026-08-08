export type AnalyticsPage = "home" | "workspace" | "developer" | "playground" | "image" | "video" | "history";

const VISITOR_KEY = "realguard_analytics_visitor";
const EVENT_KEY = "realguard_last_page_event";
const CONSENT_KEY = "realguard_analytics_consent_v1";
let transientVisitor = "";

export type AnalyticsConsent = "granted" | "denied" | null;

function randomId(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  if (globalThis.crypto?.getRandomValues) {
    const values = new Uint32Array(4);
    globalThis.crypto.getRandomValues(values);
    return Array.from(values, (value) => value.toString(16).padStart(8, "0")).join("");
  }
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
}

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function analyticsConsent(): AnalyticsConsent {
  const value = storage()?.getItem(CONSENT_KEY);
  if (value === "granted" || value === "denied") return value;
  storage()?.setItem(CONSENT_KEY, "granted");
  return "granted";
}

export function setAnalyticsConsent(value: Exclude<AnalyticsConsent, null>): void {
  storage()?.setItem(CONSENT_KEY, value);
  if (value === "denied") {
    storage()?.removeItem(VISITOR_KEY);
    transientVisitor = "";
  }
}

function visitorId(): string {
  const store = storage();
  const existing = store?.getItem(VISITOR_KEY);
  if (existing) return existing;
  if (!store && transientVisitor) return transientVisitor;
  const created = randomId();
  if (store) store.setItem(VISITOR_KEY, created);
  else transientVisitor = created;
  return created;
}

function eventId(page: AnalyticsPage, forceNew = false): string {
  try {
    const previous = JSON.parse(window.sessionStorage.getItem(EVENT_KEY) || "null") as {
      page?: string;
      at?: number;
      id?: string;
    } | null;
    if (!forceNew && previous?.page === page && Date.now() - Number(previous.at || 0) < 1500 && previous.id) {
      return previous.id;
    }
    const event = { page, at: Date.now(), id: randomId() };
    window.sessionStorage.setItem(EVENT_KEY, JSON.stringify(event));
    return event.id;
  } catch {
    return randomId();
  }
}

export function trackPageview(page: AnalyticsPage, forceNew = false): void {
  if (
    typeof window === "undefined"
    || navigator.webdriver
    || /HeadlessChrome|Playwright|Puppeteer/i.test(navigator.userAgent)
  ) return;
  if (new URLSearchParams(window.location.search).get("demo") === "1") return;
  if (analyticsConsent() !== "granted") return;
  const body = JSON.stringify({ visitorId: visitorId(), eventId: eventId(page, forceNew), page });
  void fetch("/api/analytics/pageview", {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    keepalive: true,
    headers: {
      "Content-Type": "application/json",
      "X-RealGuard-Browser-Event": "1",
    },
    body,
  }).catch(() => undefined);
}
