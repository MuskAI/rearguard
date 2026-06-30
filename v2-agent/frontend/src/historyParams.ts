import { HistorySidebarFilter } from "./api";

const HISTORY_FILTERS = new Set<HistorySidebarFilter>([
  "all",
  "vlm",
  "mock",
  "maps-only",
  "unknown",
  "real",
  "suspected",
  "highly",
  "unknownVerdict",
  "cache",
  "forensics",
  "provenance",
  "synthid",
  "watermark",
]);

export function getInitialHistoryQuery() {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get("historyQuery") || "";
}

export function getInitialHistoryFilter(): HistorySidebarFilter {
  if (typeof window === "undefined") return "all";
  const value = new URLSearchParams(window.location.search).get("historyFilter");
  return HISTORY_FILTERS.has(value as HistorySidebarFilter) ? (value as HistorySidebarFilter) : "all";
}
