#!/usr/bin/env node
//
// litellm-cost.90s.ts — spend, errors, and cache savings from your own
// LiteLLM proxy.
//
// Two calls every 90s: `/user/info` (spend vs. budget for the current
// cycle) and a 7-day `/user/daily/activity` (today's error rate and cache
// stats, plus a model-mix breakdown across whichever days that call
// returns). LiteLLM has no "spend since budget reset" endpoint, so the
// model breakdown is scoped to the fetched days and labelled that way — see
// the note above `windowModels` for why. Renders a menu-bar gauge, a
// capacity-style donut (top models vs. remaining budget), a 7-day trend, and
// — via <vee.surface>both</vee.surface> — a widget card.
//
// Nothing is contacted until you set LITELLM_PROXY_URL yourself: there is no
// default host, so a token you configure can never leak to a domain you did
// not choose. The last good response is cached, so a transient outage shows
// a "stale" banner instead of a blank menu.
//
// Ships with `vee.ts` beside it — Vee's TypeScript SDK, vendored here with
// `vee sdk ts --out .` rather than imported as a package: a plugin has no
// build step and can't resolve `node_modules`, and Vee's own plugin scanner
// already knows to treat `vee.ts`/`vee.py` as SDK files, not plugins, when it
// walks the plugins folder. Regenerate with the same command if the SDK
// changes; every menu line below goes through its builders rather than
// hand-formatted `key=value` strings, so the escaping is the SDK's, not
// hand-rolled.
//
// <vee.title>LiteLLM Cost</vee.title>
// <vee.surface>both</vee.surface>
// <vee.desc>Daily LLM spend, errors, cache vs budget.</vee.desc>
// <vee.author>Naveen Kumar</vee.author>
// <vee.author.github>navbytes</vee.author.github>
// <vee.version>1.1</vee.version>
// <vee.dependencies>Node 24+ (runs the .ts file directly via native TypeScript type-stripping; macOS does not ship Node — install via nodejs.org, Homebrew, or nvm)</vee.dependencies>
// <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
//
// <vee.var>string(LITELLM_PROXY_URL=): Your LiteLLM proxy's base URL, e.g. https://litellm.example.com. Empty by default — nothing is contacted until you set this.</vee.var>
// <vee.var>string(LITELLM_AUTH_TOKEN=): LiteLLM auth token for that proxy. Stored in the Keychain and masked in Settings (the name contains "token").</vee.var>
//
// Trust declarations (advisory, never enforced -- see docs/trust-model.md):
// <vee.network>The host in LITELLM_PROXY_URL — user-configured, empty by default. This plugin never contacts any other domain.</vee.network>
// <vee.secrets>LITELLM_AUTH_TOKEN</vee.secrets>
// <vee.exec>open</vee.exec>
// <vee.filesystem.write>$SWIFTBAR_PLUGIN_CACHE_PATH/litellm-cost-cache.json (last good response, shown with a "stale" banner if a later run can't reach the proxy), $SWIFTBAR_PLUGIN_DATA_PATH/litellm-cost-notify-state.json (last budget threshold already notified, so a restart doesn't repeat it)</vee.filesystem.write>

import { readFileSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { realpathSync } from "node:fs";

import { Menu, Gauge, Stat, type Color, type ItemOptions, type Section, type WidgetStatus } from "./vee.ts";

const COLORS = {
  green: "#36C26E",
  amber: "#F5A623",
  red: "#FF5C5C",
  dim: "#8A8F98",
  track: "#3C4046",
} as const satisfies Record<string, Color>;

// Menlo, not "SF Mono": NSFont resolves font= by exact name, and SF Mono is not
// installed by default (it ships with Xcode/Terminal). An unresolvable name falls
// back to the proportional system font, which quietly un-aligns every padded column.
const MONO = "Menlo";
// Qualitative palette for the multicolor spend bar (model identity, not severity) — top-3 ranks.
const MODEL_PALETTE: Color[] = ["#F5A623", "#36C26E", "#4A9EFF"];

const NOTIFY_THRESHOLDS = [90, 100] as const;
const SUCCESS_RATE_WARN = 98;
const SUCCESS_RATE_ALERT = 95;
const FETCH_TIMEOUT_MS = 5000;

const CACHE_DIR = process.env.SWIFTBAR_PLUGIN_CACHE_PATH || process.env.TMPDIR || "/tmp";
const CACHE_FILE = `${CACHE_DIR}/litellm-cost-cache.json`;

// ── Types ────────────────────────────────────────────────────────────────────
// Every shape here is read off the network, so each field is optional: the
// plugin's job is to render *something* useful when the API disagrees with
// what we expect, not to throw.

interface DayMetrics {
  spend?: number | string;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  successful_requests?: number;
  failed_requests?: number;
  api_requests?: number;
  cache_read_input_tokens?: number;
  prompt_caching_savings_spend?: number;
}

interface ModelBreakdown {
  metrics?: DayMetrics;
}

interface DayResult {
  date: string;
  metrics?: DayMetrics;
  breakdown?: { models?: Record<string, ModelBreakdown | undefined> };
}

interface UserInfoResponse {
  user_info?: {
    spend?: number | string;
    max_budget?: number | string;
    budget_reset_at?: string | null;
  };
}

interface DailyActivityResponse {
  results?: DayResult[];
  metadata?: { total_spend?: number | string };
}

interface ModelRow {
  name: string;
  spend: number;
  calls: number;
  tokens: number;
  failed: number;
  color: Color;
}

interface CostData {
  today: string;
  spend: number;
  budget: number;
  pct: number;
  color: Color;
  budgetResetAt: string | null;
  results: DayResult[];
  totalSpend: number;
  numDays: number;
  avgDaily: number;
  todayFailed: number;
  successRate: number;
  cacheRead: number;
  cachePct: number;
  cacheSavings: number;
  recentDays: DayResult[];
  topModels: ModelRow[];
}

// ── Small helpers ────────────────────────────────────────────────────────────

function num(v: number | string | undefined | null, fallback = 0): number {
  const n = typeof v === "string" ? parseFloat(v) : v;
  return typeof n === "number" && Number.isFinite(n) ? n : fallback;
}

function readToken(): string {
  return (process.env.LITELLM_AUTH_TOKEN || "").trim();
}

function readBase(): string | null {
  const raw = (process.env.LITELLM_PROXY_URL || "").trim().replace(/\/+$/, "");
  return raw || null;
}

function zone(pct: number): Color {
  return pct >= NOTIFY_THRESHOLDS[0] ? COLORS.red : pct >= 50 ? COLORS.amber : COLORS.green;
}

function dayTitle(label: string, ds: number): string {
  return `${padEndTo(label, 5)}  ${padStartTo(money(ds), 5)}`;
}

function fmtTokens(n: number): string {
  if (n >= 1e6) return `${Math.floor(n / 1e6)}M`;
  if (n >= 1e3) return `${Math.floor(n / 1e3)}K`;
  return String(n);
}

function fmtReset(resetAt: string | null): string {
  if (resetAt) {
    const diff = new Date(resetAt).getTime() - Date.now();
    if (diff > 0) {
      const h = Math.floor(diff / 3600000);
      const m = Math.floor((diff % 3600000) / 60000);
      return h >= 1 ? `${h}h ${m}m` : `${m}m`;
    }
  }
  // fallback to UTC midnight
  const secsUntil = 86400 - (Math.floor(Date.now() / 1000) % 86400);
  const h = Math.floor(secsUntil / 3600);
  return h >= 1 ? `${h}h` : `${Math.floor(secsUntil / 60)}m`;
}

function cleanModel(name: string): string {
  let n = name;
  for (const pfx of ["vertex_ai/", "anthropic/", "openai/", "azure/", "gemini/", "litellm/"]) {
    if (n.startsWith(pfx)) {
      n = n.slice(pfx.length);
      break;
    }
  }
  return n.replace(/^claude-/, "");
}

function padEndTo(s: string, w: number): string {
  return s.length >= w ? s : s + " ".repeat(w - s.length);
}
function padStartTo(s: string, w: number): string {
  return s.length >= w ? s : " ".repeat(w - s.length) + s;
}
function money(n: number): string {
  return "$" + Math.round(n);
}
function calls(n: number): string {
  return `${n} ${n === 1 ? "call" : "calls"}`;
}
function pctStr(n: number): string {
  return `${n}%`;
}

/** A monospaced row — padded columns only line up in a fixed-width face. */
function monoRow(size = 11, color?: Color): ItemOptions {
  return { size, font: MONO, ...(color ? { color } : {}) };
}

function smallName(name: string): string {
  // get name before the first slash and after last slash, show dots for in-betweeen
  const parts = name.replaceAll("fireworks_ai", "fw").split("/");
  if (parts.length <= 2) return name;
  return `${parts[0]}/.../${parts[parts.length - 1]}`;
}

// ── Degraded output — no config, no token, or the proxy can't be reached ───

function degrade(menuLines: string[], widgetValue: string, widgetDetail: string, status: WidgetStatus): never {
  if (process.env.VEE_TARGET === "widget") {
    Stat({
      title: "LLM Spend",
      symbol: "questionmark.circle",
      tint: COLORS.dim,
      value: widgetValue,
      detail: widgetDetail,
      status,
    }).print();
  } else {
    const menu = new Menu();
    menu.title("$—", { color: COLORS.dim });
    for (const line of menuLines) menu.dropdown.item(line, { color: COLORS.dim });
    menu.print();
  }
  process.exit(0);
}

// ── Cache — last good response, for a "stale" banner on a failed fetch ─────

function loadCache(): CostData | null {
  try {
    const raw = JSON.parse(readFileSync(CACHE_FILE, "utf8"));
    return raw && typeof raw === "object" && raw.data ? (raw.data as CostData) : null;
  } catch {
    return null;
  }
}

function saveCache(data: CostData): void {
  try {
    writeFileSync(CACHE_FILE, JSON.stringify({ data, ts: Date.now() }));
  } catch {
    // best-effort — a read-only cache dir shouldn't break the menu
  }
}

// ── API ──────────────────────────────────────────────────────────────────────

async function apiFetch<T>(base: string, path: string, token: string): Promise<T> {
  const res = await fetch(`${base}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  });
  if (!res.ok) throw Object.assign(new Error(`HTTP ${res.status}`), { status: res.status });
  return res.json() as Promise<T>;
}

// ── Data ─────────────────────────────────────────────────────────────────────

async function fetchCostData(base: string, token: string): Promise<CostData> {
  const today = new Date().toISOString().slice(0, 10);
  const weekAgo = new Date(Date.now() - 7 * 86400e3).toISOString().slice(0, 10);

  // 2 calls: user info + 7-day daily activity (includes per-model breakdown)
  const [userInfo, history] = await Promise.all([
    apiFetch<UserInfoResponse>(base, "/user/info", token),
    apiFetch<DailyActivityResponse>(base, `/user/daily/activity?start_date=${weekAgo}&end_date=${today}`, token),
  ]);

  const ui = userInfo?.user_info ?? {};
  const spend = num(ui.spend);
  const budget = num(ui.max_budget, 100) || 100;
  const pct = Math.round((spend / budget) * 100);
  const color = zone(pct);
  const budgetResetAt = ui.budget_reset_at ?? null;

  const results = (history?.results ?? []).slice().sort((a, b) => a.date.localeCompare(b.date));
  const meta = history?.metadata ?? {};
  const totalSpend = num(meta.total_spend);
  const numDays = results.length;
  const avgDaily = numDays ? totalSpend / numDays : 0;

  const todayEntry = results.find((r) => r.date === today) ?? ({} as DayResult);
  const tm = todayEntry.metrics ?? {};
  const todaySuccessful = tm.successful_requests ?? 0;
  const todayFailed = tm.failed_requests ?? 0;
  const todayTotal = todaySuccessful + todayFailed;
  const successRateRaw = todayTotal > 0 ? (todaySuccessful / todayTotal) * 100 : 100;
  const successRate = todayFailed > 0 ? Math.min(99, Math.floor(successRateRaw)) : Math.round(successRateRaw);
  const cacheRead = tm.cache_read_input_tokens ?? 0;
  const totalInput = tm.prompt_tokens ?? 0;
  const cachePct = totalInput > 0 ? Math.round((cacheRead / totalInput) * 100) : 0;
  // LiteLLM computes this exactly server-side using real per-model pricing
  // (verified: aggregate figure = sum of per-model figures).
  const cacheSavings = tm.prompt_caching_savings_spend ?? 0;

  const fullDays = results.filter((r) => r.date !== today);
  const recentDays = fullDays.slice(-6).reverse();

  // Model mix across every day this call returned (weekAgo..today, so today
  // included). `/user/info`'s `spend` is since the account's budget last
  // reset, which LiteLLM does not expose a start date for — that window can
  // be longer than the days fetched here (a monthly budget vs. a 7-day
  // fetch, say). So this is *not* "today's" breakdown and is not claimed to
  // be the full budget-cycle breakdown either: it is the model mix over the
  // days actually returned, and "other" below can legitimately include
  // spend from earlier in the cycle than that. The row/tooltip say so.
  const windowTotals = new Map<string, ModelRow>();
  for (const day of results) {
    for (const [name, mwm] of Object.entries(day.breakdown?.models ?? {})) {
      const metrics = mwm?.metrics ?? {};
      const daySpend = num(metrics.spend);
      const dayCalls = metrics.api_requests ?? metrics.successful_requests ?? 0;
      if (daySpend <= 0 && dayCalls <= 0) continue;
      const key = cleanModel(name);
      const cur = windowTotals.get(key) ?? { name: key, spend: 0, calls: 0, tokens: 0, failed: 0, color: COLORS.dim };
      cur.spend += daySpend;
      cur.calls += dayCalls;
      cur.tokens += metrics.total_tokens ?? 0;
      cur.failed += metrics.failed_requests ?? 0;
      windowTotals.set(key, cur);
    }
  }
  // One colour per model, assigned once here — the donut segments and the
  // legend rows below both read `m.color` off this same array, so they can
  // never disagree about which colour belongs to which model (previously
  // each rebuilt its own index into MODEL_PALETTE separately). Wrapping with
  // `%` keeps every index in range even if the top-N slice size ever grows
  // past the palette length.
  const topModels: ModelRow[] = [...windowTotals.values()]
    .sort((a, b) => b.spend - a.spend)
    .slice(0, 3)
    .map((m, i) => ({ ...m, color: MODEL_PALETTE[i % MODEL_PALETTE.length] }));

  return {
    today,
    spend,
    budget,
    pct,
    color,
    budgetResetAt,
    results,
    totalSpend,
    numDays,
    avgDaily,
    todayFailed,
    successRate,
    cacheRead,
    cachePct,
    cacheSavings,
    recentDays,
    topModels,
  };
}

function notifyBudgetThreshold(data: CostData): void {
  const hit = NOTIFY_THRESHOLDS.filter((t) => data.pct >= t).pop();
  if (!hit) return;
  const dataDir = process.env.VEE_PLUGIN_DATA_PATH || process.env.SWIFTBAR_PLUGIN_DATA_PATH || process.env.TMPDIR || "/tmp";
  const stateFile = `${dataDir}/litellm-cost-notify-state.json`;
  let state: { date?: string; threshold?: number } = {};
  try {
    state = JSON.parse(readFileSync(stateFile, "utf8"));
  } catch {}
  if (state.date === data.today && (state.threshold ?? 0) >= hit) return;
  try {
    writeFileSync(stateFile, JSON.stringify({ date: data.today, threshold: hit }));
  } catch {}
  const body =
    hit >= NOTIFY_THRESHOLDS[1]
      ? `Over budget: $${data.spend.toFixed(2)} of $${data.budget.toFixed(0)}`
      : `${data.pct}% of $${data.budget.toFixed(0)} budget used`;
  const url = `vee://notify?title=${encodeURIComponent("LiteLLM Budget")}&body=${encodeURIComponent(body)}&plugin=${encodeURIComponent(process.env.VEE_PLUGIN_ID ?? "")}`;
  try {
    // execFile with an argv array, not a shell string: `url` is fully
    // percent-encoded by encodeURIComponent above, but building a `sh -c`
    // string out of it anyway is one future edit away from injection.
    execFileSync("/usr/bin/open", [url]);
  } catch {}
}

// ── Rendering ────────────────────────────────────────────────────────────────

function buildWidgetDetail(data: CostData, stale: boolean): string {
  const parts: string[] = [];
  parts.push(`${data.pct}% of $${Math.round(data.budget)} budget`);
  if (data.successRate < SUCCESS_RATE_WARN) parts.push(`${data.successRate}% success`);
  parts.push(`resets in ${fmtReset(data.budgetResetAt)}`);
  if (stale) parts.push("stale");
  return parts.join("  ·  ");
}

function renderWidget(data: CostData, base: string, stale: boolean): never {
  Gauge({
    title: "LLM Spend",
    value: `$${data.spend.toFixed(2)}`,
    progress: Math.min(1, data.spend / data.budget),
    symbol: "creditcard.fill",
    tint: data.color,
    status: stale ? "warning" : data.pct >= NOTIFY_THRESHOLDS[0] ? "error" : data.pct >= 50 ? "warning" : "ok",
    caption: data.today,
    detail: buildWidgetDetail(data, stale),
    trend: data.results.map((r) => num(r.metrics?.spend)),
    actions: [
      { kind: "refresh", label: "Refresh" },
      { kind: "href", label: "Open Dashboard", url: base + "/ui" },
    ],
    refreshAfter: 90,
  }).print();
  process.exit(0);
}

/** One day's spend as a sized progress bar. */
function progressRow(d: Section, title: string, value: number, max: number, color: Color): void {
  d.item(title, {
    ...monoRow(11, color),
    progress: { value, max },
    progressTrackColor: COLORS.track,
    progressW: 240,
    progressH: 6,
  });
}

function renderMenubar(data: CostData, base: string, stale: boolean): void {
  const menu = new Menu();

  // ── Title (menubar icon text) ──────────────────────────────────────────────
  const titleSym =
    data.pct >= NOTIFY_THRESHOLDS[0]
      ? "exclamationmark.triangle.fill"
      : data.pct >= 50
        ? "gauge.with.dots.needle.50percent"
        : "gauge.with.dots.needle.33percent";
  menu.title(`$${data.spend.toFixed(2)}`, { color: data.color, sfimage: titleSym, sfColor: data.color });

  const d = menu.dropdown;

  if (stale) {
    d.item("stale — showing the last good response", {
      size: 11,
      color: COLORS.amber,
      sfimage: "exclamationmark.triangle.fill",
      sfColor: COLORS.amber,
    });
  }

  // ── Capacity donut: per-model spend + remaining (free) budget ─────────────
  const otherSpend = Math.max(0, data.spend - data.topModels.reduce((s, m) => s + m.spend, 0));
  const remaining = Math.max(0, data.budget - data.spend);
  const modelWindow = `last ${data.numDays || 1}d`;

  {
    const values: number[] = [];
    const labels: string[] = [];
    const colors: Color[] = [];
    for (const m of data.topModels) {
      if (m.spend > 0.01) {
        values.push(m.spend);
        labels.push(m.name);
        colors.push(m.color);
      }
    }
    if (otherSpend > 0.01) {
      values.push(otherSpend);
      labels.push("other");
      colors.push(COLORS.dim);
    }
    // Disk-capacity donut: final "free" slice is the unspent budget, drawn
    // in track colour so it reads as empty capacity beside the spent slices.
    values.push(remaining);
    labels.push("free");
    colors.push(COLORS.track);

    d.item(`$${data.spend.toFixed(2)} of $${data.budget.toFixed(0)}  ·  ${pctStr(100 - data.pct)} free`, {
      size: 14,
      color: data.color,
      tooltip: `Top models by spend (${modelWindow}) · "other" may include spend from earlier in the budget cycle · remaining is free budget · resets in ${fmtReset(data.budgetResetAt)}`,
    });
    d.item("", {
      chart: { kind: "stackedbar", values, labels, colors, h: 32, w: "full" },
      tooltip: `Top models by spend (${modelWindow}) · remaining is free budget`,
    });
  }

  // ── Top models — legend directly under the segmented hero bar ─────────────
  if (data.topModels.length) {
    for (const [i, m] of data.topModels.entries()) {
      // No pct column: the stacked bar above already encodes share visually,
      // so a numeric pct would be triple-encoding. Failed rows lead with ⚠
      // in the text, but keep the model's own colour — that's what ties this
      // row back to its segment above, and the ⚠ already carries the signal.
      const errTag = m.failed > 0 ? `  ⚠${m.failed}` : "";
      const rank = m.failed > 0 ? `⚠ ${i + 1}` : `${i + 1}`;
      const label = `${rank}  ${padEndTo(smallName(m.name), 22)} ${padStartTo("$" + m.spend.toFixed(2), 7)}  ${fmtTokens(m.tokens)} · ${calls(m.calls)}${errTag}`;
      d.item(label, monoRow(11, m.color));
    }
    if (otherSpend > 0.01) {
      const label = `+  ${padEndTo("other models", 22)} ${padStartTo("$" + otherSpend.toFixed(2), 7)}`;
      d.item(label, monoRow(11, COLORS.dim));
    }
    const freeLabel = `+  ${padEndTo("remaining", 22)} ${padStartTo("$" + remaining.toFixed(2), 7)}`;
    d.item(freeLabel, monoRow(11, COLORS.track));
  }
  d.separator();

  if (data.todayFailed > 0) {
    const errColor = data.successRate < SUCCESS_RATE_ALERT ? COLORS.red : COLORS.amber;
    d.item(`${pctStr(data.successRate)} success · ${data.todayFailed} errors`, {
      size: 11,
      color: errColor,
      sfimage: "exclamationmark.triangle.fill",
      sfColor: errColor,
    });
  }
  if (data.cachePct > 0) {
    const saved = data.cacheSavings > 0.1 ? ` · ~$${data.cacheSavings.toFixed(2)} saved` : "";
    d.item(`Cache hit: ${fmtTokens(data.cacheRead)} tokens (${pctStr(data.cachePct)} input)${saved}`, {
      size: 11,
      color: COLORS.green,
      sfimage: "checkmark.seal.fill",
      sfColor: COLORS.green,
    });
  }
  if (data.pct >= NOTIFY_THRESHOLDS[0]) {
    d.item("Approaching budget cap", {
      size: 11,
      color: COLORS.red,
      sfimage: "exclamationmark.triangle.fill",
      sfColor: COLORS.red,
    });
  }

  d.separator();

  // ── 7-day trend — one native gauge per prior day ───────────────────────────
  if (data.topModels.length) {
    const top = data.topModels[0];
    d.item(`Top model (${modelWindow}): ${top.name} (${money(top.spend)})`, {
      size: 11,
      color: COLORS.dim,
      sfimage: "trophy.fill",
      sfColor: COLORS.dim,
      disabled: true,
    });
  }
  d.item(`Previous days  ·  avg ${money(data.avgDaily)}/day  ·  ${money(data.totalSpend)} total (${data.numDays}d)`, {
    size: 12,
    color: COLORS.dim,
    sfimage: "chart.bar.fill",
    sfColor: COLORS.dim,
    disabled: true,
  });
  for (const day of data.recentDays) {
    const ds = num(day.metrics?.spend);
    const label = new Date(day.date + "T12:00:00Z").toLocaleDateString("en-US", { month: "short", day: "numeric" });
    const dFailed = day.metrics?.failed_requests ?? 0;
    const errTag = dFailed > 0 ? `  (${dFailed} errs)` : "";
    progressRow(d, dayTitle(label, ds) + errTag, ds, data.budget, zone(Math.round((ds / data.budget) * 100)));
  }

  d.separator();
  d.item("Open LiteLLM dashboard", { href: `${base}/ui`, size: 12, sfimage: "arrow.up.right.square" });
  d.item("Refresh", { refresh: true, size: 12, sfimage: "arrow.clockwise" });

  menu.print();
}

async function main(): Promise<void> {
  const base = readBase();
  if (!base) {
    degrade(
      ["Not configured", "Set LITELLM_PROXY_URL in this plugin's Settings"],
      "Not configured",
      "Set LITELLM_PROXY_URL",
      "warning",
    );
  }

  const token = readToken();
  if (!token) {
    degrade(
      ["No LiteLLM token", "Set LITELLM_AUTH_TOKEN in this plugin's Settings"],
      "No token",
      "Set LITELLM_AUTH_TOKEN",
      "warning",
    );
  }

  let data: CostData;
  let stale = false;
  try {
    data = await fetchCostData(base, token);
    saveCache(data);
  } catch (e) {
    const cached = loadCache();
    if (cached) {
      data = cached;
      stale = true;
    } else {
      const status = (e as { status?: number }).status;
      const heading = status === 401 ? "Invalid or expired token" : "Couldn't reach the proxy";
      const detail =
        status === 401
          ? "Check LITELLM_AUTH_TOKEN in this plugin's Settings"
          : `Couldn't reach the proxy (${(e as Error).message})`;
      degrade([heading, detail], heading, detail, "error");
    }
  }

  if (!stale) notifyBudgetThreshold(data);

  if (process.env.VEE_TARGET === "widget") {
    renderWidget(data, base, stale);
  }

  renderMenubar(data, base, stale);
}

const _isEntry = (() => {
  try {
    return !!process.argv[1] && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url);
  } catch {
    return false;
  }
})();
if (_isEntry) await main();
