export type AnalyticsPage = "home" | "image" | "video" | "history";

const ANALYTICS_VISITOR_KEY = "realguard_analytics_visitor";
const ANALYTICS_EVENT_KEY = "realguard_last_page_event";
let transientAnalyticsVisitorId = "";

function randomTrackingId() {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi && typeof cryptoApi.randomUUID === "function") {
    return cryptoApi.randomUUID();
  }
  if (cryptoApi && typeof cryptoApi.getRandomValues === "function") {
    const values = new Uint32Array(4);
    cryptoApi.getRandomValues(values);
    return Array.from(values, (value) => value.toString(16).padStart(8, "0")).join("");
  }
  return Date.now().toString(36) + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
}

function storage() {
  try {
    return typeof window.localStorage === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function analyticsVisitorId() {
  const localStorage = storage();
  const existing = localStorage?.getItem(ANALYTICS_VISITOR_KEY);
  if (existing) return existing;
  if (!localStorage && transientAnalyticsVisitorId) return transientAnalyticsVisitorId;
  const created = randomTrackingId();
  if (localStorage) localStorage.setItem(ANALYTICS_VISITOR_KEY, created);
  else transientAnalyticsVisitorId = created;
  return created;
}

function analyticsEventId(page: AnalyticsPage) {
  try {
    const previous = JSON.parse(window.sessionStorage.getItem(ANALYTICS_EVENT_KEY) || "null");
    if (previous?.page === page && Date.now() - Number(previous?.at || 0) < 1500 && previous?.id) {
      return String(previous.id);
    }
    const event = { page, at: Date.now(), id: randomTrackingId() };
    window.sessionStorage.setItem(ANALYTICS_EVENT_KEY, JSON.stringify(event));
    return event.id;
  } catch {
    return randomTrackingId();
  }
}

export function initialAnalyticsPage(): AnalyticsPage {
  const page = new URLSearchParams(window.location.search).get("page");
  return page === "image" || page === "video" || page === "history" ? page : "home";
}

export function trackConfirmedPageview(page: AnalyticsPage) {
  if (typeof window === "undefined" || navigator.webdriver) return;
  if (new URLSearchParams(window.location.search).get("demo") === "1") return;
  const body = JSON.stringify({
    visitorId: analyticsVisitorId(),
    eventId: analyticsEventId(page),
    page,
  });
  void fetch("/api/analytics/pageview", {
    method: "POST",
    credentials: "omit",
    keepalive: true,
    headers: {
      "Content-Type": "application/json",
      "X-RealGuard-Browser-Event": "1",
    },
    body,
  }).catch(() => undefined);
}
