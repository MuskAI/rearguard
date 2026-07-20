export type AnalyticsPage = "home" | "image" | "video" | "history";

const VISITOR_KEY = "realguard_analytics_visitor";
const EVENT_KEY = "realguard_last_page_event";
let transientVisitor = "";

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

function eventId(page: AnalyticsPage): string {
  try {
    const previous = JSON.parse(window.sessionStorage.getItem(EVENT_KEY) || "null") as {
      page?: string;
      at?: number;
      id?: string;
    } | null;
    if (previous?.page === page && Date.now() - Number(previous.at || 0) < 1500 && previous.id) {
      return previous.id;
    }
    const event = { page, at: Date.now(), id: randomId() };
    window.sessionStorage.setItem(EVENT_KEY, JSON.stringify(event));
    return event.id;
  } catch {
    return randomId();
  }
}

export function trackPageview(page: AnalyticsPage): void {
  if (typeof window === "undefined" || navigator.webdriver) return;
  if (new URLSearchParams(window.location.search).get("demo") === "1") return;
  if (storage()?.getItem("realguard_analytics_consent_v1") === "denied") return;
  const body = JSON.stringify({ visitorId: visitorId(), eventId: eventId(page), page });
  void fetch("/api/analytics/pageview", {
    method: "POST",
    credentials: "omit",
    cache: "no-store",
    keepalive: true,
    headers: {
      "Content-Type": "application/json",
      "X-RealGuard-Browser-Event": "1",
    },
    body,
  }).catch(() => undefined);
}
