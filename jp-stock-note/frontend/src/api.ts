// Same-origin API client. The browser never talks to any external host;
// all data comes from our own FastAPI server under /api/*.

export interface CodeName {
  code: string | null;
  name: string | null;
}

export interface Stock {
  code: string;
  display_code: string;
  name: string;
  name_en: string | null;
  market: CodeName;
  sector17: CodeName;
  sector33: CodeName;
  scale_category: string | null;
  master_date: string;
}

export interface SearchResponse {
  query: string;
  normalized: string;
  count: number;
  results: Stock[];
}

export interface Price {
  as_of: string;
  close: number;
  close_unadjusted: number | null;
  prev_date: string | null;
  prev_close: number | null;
  change: number | null;
  change_pct: number | null;
  change_unavailable_reason: string | null;
}

export type HeaderResponse =
  | {
      state: "ok";
      stock: Stock;
      price: Price;
      latest_business_day: string | null;
      today_not_reflected: boolean | null;
    }
  | { state: "not_fetched"; stock: Stock; price: null; message: string };

export interface FetchLogEntry {
  id: number;
  job: string;
  endpoint: string;
  params: Record<string, string>;
  priority: number | null;
  started_at: string;
  finished_at: string | null;
  result: string;
  http_status: number | null;
  error_reason: string | null;
  attempts: number;
  row_count: number | null;
}

export interface SourceMeta {
  source: string;
  series_id: string;
  update_cycle: string | null;
  as_of_date: string | null;
  last_attempt_at: string | null;
  last_success_at: string | null;
  last_result: string | null;
  last_error: string | null;
}

export interface StatusResponse {
  tankan: TankanSetting;
  fred_series_ids: string[];
  history: FetchLogEntry[];
  sources: SourceMeta[];
  counts: Record<string, number>;
  requests_last_60s: number;
  rate_limit_per_minute: number;
  api_key_configured: boolean;
}

// ---------------------------------------------------------------- Phase 1

export type RangeKey = "3m" | "6m" | "1y" | "3y" | "5y";
export type Num = number | null;

export interface Candle {
  date: string;
  open: Num;
  high: Num;
  low: Num;
  close: Num;
  volume: Num;
  upper_limit: boolean;
  lower_limit: boolean;
}

export type MaKey = "sma5" | "sma25" | "sma75" | "sma200";

export interface MaStyle {
  key: MaKey;
  label: string;
  line: "thin_solid" | "thick_solid" | "dashed" | "dotted";
}

export interface EarningsMarker {
  date: string;
  bar_date: string;
  label: string;
  disclosures: { disclosure_no: string; period_type: string | null; doc_type: string | null }[];
}

export interface ChartResponse {
  code: string;
  range: RangeKey;
  state: "ok" | "no_data";
  as_of: string | null;
  range_start?: string;
  range_end?: string;
  history_start?: string;
  dates: string[];
  candles: Candle[];
  ma: Record<MaKey, Num[]>;
  ma_styles: MaStyle[];
  markers: { earnings: EarningsMarker[]; stop_high: string[]; stop_low: string[] };
  notes: string[];
}

export interface Fact {
  key: string;
  text: string;
  as_of: string | null;
  available: boolean;
  target?: string;
}

export interface Stage {
  title: string;
  facts: Fact[];
  notes: string[];
  values: Record<string, Num>;
}

export interface ExcessRow {
  n: number;
  from_date: string | null;
  to_date: string | null;
  stock_pct: Num;
  topix_pct: Num;
  excess_pt: Num;
  as_of: string | null;
  text: string;
}

export interface Stage4 extends Omit<Stage, "values"> {
  t0: string | null;
  topix_available: boolean;
  topix_message: string | null;
  excess_returns: ExcessRow[];
  beta: {
    beta: Num;
    correlation: Num;
    observations: number;
    window_from: string | null;
    window_to: string | null;
    min_observations: number;
    window: number;
    as_of: string | null;
  };
}

export interface CrossEvent {
  date: string;
  kind: "golden" | "dead";
  label: string;
  close: Num;
  business_days_ago: number;
  after_20d: { date: string | null; return_pct: Num };
  after_60d: { date: string | null; return_pct: Num };
}

export interface CrossHistory {
  events: CrossEvent[];
  counts: Record<string, { up: number; down: number; flat: number; pending: number; total: number }>;
  count_texts: string[];
  period: string | null;
  notes: string[];
}

export type SeriesKey =
  | "close" | "rsi" | "bb_mid" | "bb_upper" | "bb_lower" | "pct_b"
  | "macd" | "macd_signal" | "macd_hist" | "idx_stock" | "idx_topix" | "rs";

export interface TechnicalsResponse {
  code: string;
  state: "ok" | "no_data";
  as_of: string | null;
  range: RangeKey;
  dates: string[];
  series: Record<SeriesKey, Num[]>;
  stage1: Stage & { latest_cross: { kind: string; date: string; business_days_ago: number } | null };
  stage2: Stage;
  stage3: Stage;
  stage4: Stage4;
  cross_history: CrossHistory;
  values: Record<string, Num>;
}

export interface PriceCard {
  code: string;
  state: "ok" | "no_data";
  as_of: string | null;
  title?: string;
  sentences: Fact[];
}

export interface GlossaryEntry {
  term: string;
  label: string;
  definition: string;
  formula: string;
  formula_is_generic: boolean;
  formula_note: string | null;
  misconception: string;
  code: string | null;
  as_of: string | null;
}

export interface EnsureHistoryResponse {
  code: string;
  fetched: boolean;
  state: "failed" | "fetched" | "stored";
  has_bars: boolean;
  steps: { series: string; result: string; error_reason: string | null; stored: number | null }[];
  error_reason: string | null;
}

// ---------------------------------------------------------------- Phase 2 (財務・バリュエーション)

export type FinItem = "net_sales" | "operating_profit" | "profit";
export type PeriodType = "1Q" | "2Q" | "3Q" | "FY";

export interface EnsureFinsResponse {
  code: string;
  state: "failed" | "fetched" | "stored";
  has_fins: boolean;
  last_fetched_at: string | null;
  revisions_detected: number;
  error_reason: string | null;
}

export interface AnnualRow {
  kind: "actual";
  fy_end: string;
  fiscal_year: string;
  months: number | null;
  irregular_year: boolean;
  accounting_standard: string | null;
  standard_changed: boolean;
  disclosed_date: string;
  disclosure_no: string;
  net_sales: Num;
  operating_profit: Num;
  profit: Num;
  operating_margin: Num;
  total_assets: Num;
  equity: Num;
  equity_ratio_pct: Num;
  cf_operating: Num;
  cf_investing: Num;
  cf_financing: Num;
  simple_fcf: Num;
  ocf_to_profit: Num;
  eps: Num;
  dividend_annual: Num;
}

export interface AnnualForecast {
  kind: "forecast";
  fy_end: string;
  fiscal_year: string;
  disclosed_date: string;
  disclosure_no: string;
  net_sales: Num;
  operating_profit: Num;
  profit: Num;
  dividend?: Num;
  eps?: Num;
  operating_margin: Num;
}

export interface QuarterRow {
  fy_end: string;
  fiscal_year: string;
  quarter: number;
  period_type: PeriodType;
  period_end: string;
  disclosed_date: string;
  disclosure_no: string;
  prev_disclosure_no: string | null;
  accounting_standard: string | null;
  special_factors_note: boolean;
  irregular_year: boolean;
  label: string;
  values: Record<FinItem, Num>;
  reasons: Record<FinItem, string | null>;
}

export interface YoyItem {
  current: Num;
  prior: Num;
  pct: Num;
  kind: "rate" | "turned_positive" | "nonpositive_both" | "not_disclosed" | string;
}

export interface YoyBlock {
  state: "ok" | "unavailable" | "no_data";
  period_type?: PeriodType;
  period_label?: string;
  fy_end?: string;
  disclosed_date?: string;
  disclosure_no?: string;
  reason?: string | null;
  items?: Record<FinItem, YoyItem>;
}

export interface PatternYear {
  fy_end: string;
  fiscal_year: string;
  cumulative: Num;
  full_year: Num;
  pct: Num;
  reason: string | null;
  pct_vs_forecast_then: Num;
}

export interface ProgressItem {
  label: string;
  actual_pct: Num;
  cumulative: Num;
  forecast: Num;
  forecast_disclosed_date: string | null;
  actual_reason: string | null;
  pattern_state: string;
  pattern_avg_pct: Num;
  pattern_min_pct: Num;
  pattern_max_pct: Num;
  pattern_years: PatternYear[];
  pattern_n_valid: number;
  pattern_required: number;
  deviation_pt: Num;
  beyond_threshold: boolean;
}

export interface ProgressBlock {
  state: "ok" | "not_applicable" | "no_data" | string;
  threshold_pt: number;
  provisional: boolean;
  notes: string[];
  period_type?: PeriodType;
  period_label?: string;
  fy_end?: string;
  fiscal_year?: string;
  disclosed_date?: string | null;
  disclosure_no?: string;
  primary_item: FinItem | null;
  items: Partial<Record<FinItem, ProgressItem>>;
}

export type ForecastKey = FinItem | "dividend";

export interface TimelineEntry {
  disclosed_date: string;
  disclosure_no: string;
  doc_type: string | null;
  source: "next_year" | "current_year";
  values: Partial<Record<ForecastKey, number>>;
  rates: Partial<Record<ForecastKey, Num>>;
  kind: "initial" | "revision" | "unchanged" | "forecast";
}

export interface RevisionEvent {
  fy_end: string;
  item: ForecastKey;
  item_label: string;
  disclosed_date: string;
  disclosure_no: string;
  old: number;
  new: number;
  rate: Num;
}

export interface RevisionYear {
  fy_end: string;
  fiscal_year: string;
  completed: boolean;
  initial_from_prior_fy_statement: boolean;
  timeline: TimelineEntry[];
  revisions: RevisionEvent[];
  actual: { disclosed_date: string; disclosure_no: string; values: Record<FinItem, Num> } | null;
  landing_pct_operating_profit: Num;
}

export interface RevisionsBlock {
  years: RevisionYear[];
  type: {
    type: string;
    n: number;
    m: number;
    counts: { n: number; above: number; below: number; within: number; upward_revision_years: number };
    text: string;
    counts_text: string;
    years: { fy_end: string; fiscal_year: string; landing_pct: Num; upward_revision: boolean }[];
    threshold_pct: number;
  };
  notes: string[];
}

export interface CfBlock {
  state: "ok" | "no_data";
  fy_end?: string;
  fiscal_year?: string;
  disclosed_date?: string;
  disclosure_no?: string;
  cf_operating?: Num;
  cf_investing?: Num;
  cf_financing?: Num;
  profit?: Num;
  simple_fcf?: Num;
  pattern?: Record<"operating" | "investing" | "financing", "+" | "-" | "0"> | null;
  guideposts?: { reference: number; check: number };
  ocf_to_profit?: Num;
  ocf_to_profit_reason?: string | null;
  simple_fcf_reason?: string | null;
  pattern_label?: string | null;
  notes: string[];
}

export interface EquityRatioBlock {
  state: "ok" | "no_data";
  period_type?: PeriodType;
  fy_end?: string;
  disclosed_date?: string;
  disclosure_no?: string;
  current_pct?: Num;
  prior_pct?: Num;
  diff_pt?: Num;
  reason?: string | null;
  prior_disclosed_date?: string | null;
}

export interface AlertItem {
  rule: string;
  text: string;
  as_of: string | null;
  check: string;
  provisional: boolean;
}

export interface AlertsBlock {
  as_of: string | null;
  items: AlertItem[];
  none_text: string | null;
  thresholds: Record<string, number | boolean>;
  notes?: string[];
}

export interface CorrectionEntry {
  disclosure_no: string;
  detected_at: string;
  disclosed_date: string | null;
  period_type: string | null;
  doc_type: string | null;
  changes: { field: string; column: string | null; old: unknown; new: unknown }[];
}

export type FinancialsResponse =
  | {
      code: string;
      state: "ok";
      as_of: string | null;
      latest_statement: {
        disclosed_date: string;
        disclosure_no: string;
        period_type: PeriodType;
        fy_end: string;
        doc_type: string;
      } | null;
      annual: { rows: AnnualRow[]; forecast: AnnualForecast | null; notes: string[] };
      quarterly: { rows: QuarterRow[]; notes: string[] };
      yoy: YoyBlock;
      progress: ProgressBlock;
      revisions: RevisionsBlock;
      cf: CfBlock;
      equity_ratio: EquityRatioBlock;
      alerts: AlertsBlock;
      correction_log: CorrectionEntry[];
      source_note: string;
    }
  | {
      code: string;
      state: "no_data";
      as_of: null;
      alerts: AlertsBlock;
      correction_log: CorrectionEntry[];
      source_note: string;
    };

export interface Percentile {
  state: "ok" | "insufficient" | "current_unavailable";
  n: number;
  min_observations?: number;
  years?: number;
  from?: string | null;
  to?: string | null;
  current?: Num;
  percentile?: Num;
  p25?: Num;
  p50?: Num;
  p75?: Num;
}

export interface Metric {
  value: Num;
  available: boolean;
  reason: string | null;
  as_of: string | null;
  inputs?: Record<string, number | string | null>;
  percentile?: Percentile;
  estimated?: boolean;
  fiscal_year?: string | null;
  opening_missing?: boolean | null;
}

export interface ValuationResponse {
  code: string;
  state: "ok" | "no_data";
  as_of: string | null;
  market_cap: { value_yen: Num; as_of: string | null; unit_yen: number; unit_estimated: boolean };
  estimated_shares: { value: Num; estimated: boolean; as_of: string | null };
  per: Metric;
  pbr: Metric;
  dividend_yield: Metric;
  roe: { actual: Metric; forecast: Metric; notes: string[] };
  payout: { actual: Metric; forecast: Metric };
  series: { dates: string[]; per: Num[]; pbr: Num[]; dividend_yield: Num[] };
  notes: string[];
  source_note: string;
}

export interface BusinessCard {
  key: "performance" | "health" | "valuation";
  title: string;
  sentences: Fact[];
  notes?: string[];
  // valuation card (Phase 3): sector sensitivity tags as chips
  chips?: string[];
  chips_note?: string;
  caution?: SectorCaution;
}

// ---------------------------------------------------------------- Phase 3: macro

export interface MacroChange {
  n: number;
  available: boolean;
  from_date: string | null;
  from_value: Num;
  change: Num;
  change_pct: Num;
}

export interface MacroRange {
  state: "ok" | "insufficient";
  n: number;
  window: number;
  from?: string;
  to?: string;
  min?: Num;
  max?: Num;
  percentile?: Num;
  range_pos?: Num;
}

export interface MacroItem {
  key: string;
  title: string;
  short: string;
  unit: string;
  change_unit: string | null;
  frequency: "daily" | "monthly";
  cadence_badge: string;
  lag_note: string;
  source: string;
  source_url: string | null;
  note: string | null;
  series_id: string | null;
  state: "ok" | "no_data";
  value: Num;
  as_of: string | null;
  as_of_month?: string | null;
  month_change?: { from_date: string | null; change: Num } | null;
  changes: Record<"20" | "60" | "250", MacroChange>;
  range_250: MacroRange;
  stale: { stale: boolean; text: string | null; lag: number | null };
  fetch: { failed: boolean; status_text: string | null; last_success_at: string | null; last_result: string | null };
  facts: Fact[];
  history: { dates: string[]; values: Num[]; step: boolean };
  sign?: "positive" | "negative" | "zero" | null;
  tankan?: {
    rate: number;
    survey_label: string;
    updated_at: string | null;
    available: boolean;
    gap_yen: Num;
    gap_pct: Num;
    note: string;
  };
}

export type MacroKey = "topix" | "us10y" | "us2y" | "us_spread" | "usdjpy" | "jp10y" | "call_rate";

export interface MacroResponse {
  state: "ok" | "no_data";
  reference_date: string | null;
  reference_kind: "stock" | "topix" | "today";
  reference_label: string;
  headline: MacroKey[];
  items: Record<MacroKey, MacroItem>;
  windows: number[];
  thresholds: Record<string, number>;
  stale_rule: Record<string, number>;
  notes: string[];
  source_note: string;
}

export type Effect = "tailwind" | "headwind" | "neutral";

export interface SectorCaution {
  flag: boolean;
  label?: string | null;
  text?: string | null;
  reason?: string | null;
}

export interface SectorProfile {
  state: "ok" | "unmapped";
  sector17: CodeName;
  cycle: { value: string; label: string } | null;
  tags: { factor: string; condition: string; effect: Effect; effect_label: string; label: string }[];
  why?: Record<string, string>;
  caution: SectorCaution;
  notes: string[];
  text?: string;
}

export interface ChecklistRow {
  factor: "rates" | "fx" | "cycle";
  title: string;
  tags: Record<string, string>;
  tailwind_conditions: string[];
  headwind_conditions: string[];
  why: string;
  current: { facts: string[]; direction: Record<string, Record<string, string | null>>; series: string[] };
  relevant: boolean;
  caution: boolean;
}

export interface StockMacroResponse {
  code: string;
  state: "ok";
  sector17: CodeName;
  sector33: CodeName;
  profile: SectorProfile;
  context: { per: { value: Num; as_of: string | null }; equity_ratio: { value: Num; as_of: string | null }; facts: string[]; note: string };
  checklist: { rows: ChecklistRow[]; notes: string[]; thresholds: Record<string, number> };
  macro: MacroResponse;
}

export interface TankanSetting {
  value: number;
  survey_label: string;
  origin: string;
  updated_at: string | null;
}

export interface FredFetchResponse {
  result: "ok" | "partial" | "failed";
  series: { series_id: string; result: string; error_reason?: string; row_count?: number; as_of?: string | null }[];
}

export interface BusinessCardsResponse {
  code: string;
  fins_as_of: string | null;
  cards: BusinessCard[];
}

// 「テクニカルで見る」(GET /api/stocks/{code}/technical-backtest). Every label and
// sentence comes from the server (content/technical_view.json, the one wording
// file with the 要件 6.1 exception, see wording.TECH_VIEW_ALLOWED); the screen only displays.
export type TechIndicator = "rsi" | "macd" | "ma_cross" | "bollinger";
export type TechSide = "buy" | "sell";

export interface TechBacktestEntry {
  date: string;
  exit_date: string;
  entry_close: number;
  exit_close: number;
  return_pct: number;
  result: "win" | "loss" | "draw";
}

export interface TechBacktestCell {
  side: TechSide;
  side_label: string;
  horizon: number;
  signals: number;
  samples: number;
  pending: number;
  wins: number;
  losses: number;
  draws: number;
  win_rate_pct: number | null;
  win_rate_display: number | null;
  avg_return_pct: number | null;
  avg_return_display: string | null;
  small_sample: boolean;
  note: string;
  entries: TechBacktestEntry[];
}

export interface TechBacktestResponse {
  code: string;
  state: "ok" | "no_data";
  as_of: string | null;
  indicator: TechIndicator;
  indicators: { key: TechIndicator; label: string }[];
  labels: Record<string, string>;
  evaluation: {
    side: "buy" | "sell" | "neutral" | "unavailable";
    text: string;
    basis: string;
    values: Record<string, unknown>;
    as_of: string | null;
  };
  headline: {
    kind: "rate" | "no_samples" | "neutral" | "unavailable";
    label: string | null;
    text: string;
    detail: string | null;
    win_rate_pct: number | null;
    small_sample: boolean | null;
    small_sample_text: string | null;
  };
  backtest: null | {
    period: { from: string; to: string; bars: number; text: string; price: string };
    horizons: number[];
    main_horizon: number;
    min_samples: number;
    signal_rule: string;
    raw_signal_days: Record<TechSide, number>;
    results: Record<TechSide, TechBacktestCell[]>;
    baseline: { horizon: number; days: number; up_days: number; up_pct: number | null }[];
    costs_included: boolean;
    conditions: string[];
    table_caption: string;
  };
  notes: string[];
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (!path.startsWith("/api/")) throw new Error("only same-origin /api/* is allowed");
  const res = await fetch(path, { ...init, headers: { Accept: "application/json", ...(init?.headers ?? {}) } });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const api = {
  search: (q: string) => request<SearchResponse>(`/api/search?q=${encodeURIComponent(q)}`),
  header: (code: string) => request<HeaderResponse>(`/api/stocks/${encodeURIComponent(code)}/header`),
  status: () => request<StatusResponse>("/api/status"),
  ensureHistory: (code: string) =>
    request<EnsureHistoryResponse>(`/api/stocks/${encodeURIComponent(code)}/ensure-history`, { method: "POST" }),
  chart: (code: string, range: RangeKey) =>
    request<ChartResponse>(`/api/stocks/${encodeURIComponent(code)}/chart?range=${range}`),
  technicals: (code: string, range: RangeKey) =>
    request<TechnicalsResponse>(`/api/stocks/${encodeURIComponent(code)}/technicals?range=${range}`),
  technicalBacktest: (code: string, indicator: TechIndicator) =>
    request<TechBacktestResponse>(
      `/api/stocks/${encodeURIComponent(code)}/technical-backtest?indicator=${indicator}`,
    ),
  pricePosition: (code: string) =>
    request<PriceCard>(`/api/stocks/${encodeURIComponent(code)}/cards/price-position`),
  glossary: (term: string, code?: string) =>
    request<GlossaryEntry>(
      `/api/glossary/${encodeURIComponent(term)}${code ? `?code=${encodeURIComponent(code)}` : ""}`,
    ),
  ensureFins: (code: string, force = false) =>
    request<EnsureFinsResponse>(
      `/api/stocks/${encodeURIComponent(code)}/ensure-fins${force ? "?force=true" : ""}`,
      { method: "POST" },
    ),
  financials: (code: string) => request<FinancialsResponse>(`/api/stocks/${encodeURIComponent(code)}/financials`),
  valuation: (code: string) => request<ValuationResponse>(`/api/stocks/${encodeURIComponent(code)}/valuation`),
  businessCards: (code: string) =>
    request<BusinessCardsResponse>(`/api/stocks/${encodeURIComponent(code)}/cards/business`),
  macro: () => request<MacroResponse>("/api/macro"),
  stockMacro: (code: string) => request<StockMacroResponse>(`/api/stocks/${encodeURIComponent(code)}/macro`),
  tankan: () => request<TankanSetting>("/api/settings/tankan"),
  saveTankan: (value: number, survey_label: string) =>
    request<TankanSetting>("/api/settings/tankan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value, survey_label }),
    }),
  runFredFetch: () => request<FredFetchResponse>("/api/fetch-fred", { method: "POST" }),
  runFetch: (job: "master" | "calendar") =>
    request<{ fetch_id: number; result: string; error_reason?: string }>(`/api/fetch/${job}`, { method: "POST" }),
};

// ---------------------------------------------------------------- Phase 4: memo / watchlist / digest / S0

export interface Option {
  key: string;
  label: string;
}

export interface MemoForm {
  notice: string;
  immutability_note: string;
  stance: { label: string; help: string; options: Option[] };
  evidence: {
    label: string;
    help: string;
    note_max_len: number;
    areas: { key: string; label: string; items: Option[] }[];
  };
  scenario: { label: string; expected_label: string; different_label: string; max_len: number };
  conditions: { label: string; help: string; max_items: number; max_len: number; kinds: Option[] };
  review_date: { label: string; help: string };
  self_review: {
    label: string;
    help: string;
    process: { label: string; options: Option[] };
    result: { label: string; options: Option[] };
    note_label: string;
    note_max_len: number;
  };
  review_view: { title: string; note: string; core_note: string };
}

export interface MemoContent {
  stance: string;
  evidence: { area: string; item: string; note: string }[];
  scenario: { expected: string; different: string };
  conditions: { kind: string; text: string }[];
  review_date: string | null;
}

export interface SnapshotField {
  key: string;
  label: string;
  group: string;
  value: Num;
  available: boolean;
  as_of: string | null;
  unit: string;
  digits: number;
  reason: string | null;
  core: boolean;
}

export interface SnapshotValues {
  schema: number;
  code: string;
  name: string;
  captured_date_jst: string;
  fields: SnapshotField[];
  texts: { key: string; label: string; lines: string[] }[];
}

export interface MemoRevision {
  revision_id: number;
  revision_no: number;
  created_at: string;
  content: MemoContent;
  content_sha256: string;
  snapshot: { snapshot_id: number; captured_at: string; sha256: string; values: SnapshotValues } | null;
}

export interface MemoSelfReview {
  review_id: number;
  revision_id: number;
  created_at: string;
  process: string;
  result: string;
  note: string;
}

export interface Verification {
  ok: boolean;
  checked: number;
  problems: { table: string; id: number | null; reason: string }[];
  text: string;
}

export interface MemoDetail {
  memo_id: number;
  code: string;
  display_code: string;
  name: string | null;
  created_at: string;
  revisions: MemoRevision[];
  latest: MemoRevision | null;
  reviews: MemoSelfReview[];
  verification: Verification;
}

export interface MemoListItem {
  memo_id: number;
  code: string;
  display_code: string;
  name: string | null;
  created_at: string;
  revision_count: number;
  revised_at: string;
  stance: string;
  stance_label: string | null;
  review_date: string | null;
  review_count: number;
  due: boolean;
}

export interface MemoList {
  today: string;
  count: number;
  items: MemoListItem[];
}

export interface PointValue {
  value: Num;
  available: boolean;
  as_of: string | null;
  reason: string | null;
}

export interface ReviewRow {
  key: string;
  label: string;
  group: string;
  unit: string;
  digits: number;
  core: boolean;
  snapshot: PointValue;
  current: PointValue;
  diff: Num;
}

export interface MemoReviewPayload {
  memo_id: number;
  code: string;
  display_code: string;
  name: string | null;
  revision: { revision_id: number; revision_no: number; created_at: string; content: MemoContent };
  revisions: { revision_id: number; revision_no: number; created_at: string }[];
  snapshot_captured_at: string | null;
  current_computed_at: string;
  rows: ReviewRow[];
  texts: { key: string; label: string; snapshot: string[]; current: string[] }[];
  reviews: MemoSelfReview[];
  verification: Verification;
  notes: string[];
}

export interface VerifyAll {
  ok: boolean;
  memos: number;
  tampered_memo_ids: number[];
  orphan_snapshots: number;
  note: string;
}

export interface WatchEntry {
  code: string;
  reason: string;
  tags: string[];
  added_at: string;
  removed_at: string | null;
}

export interface WatchItem extends WatchEntry {
  display_code: string;
  name: string | null;
  price: Pick<Price, "as_of" | "close" | "change" | "change_pct" | "prev_date" | "change_unavailable_reason"> | null;
  alerts: { count: number; as_of: string | null; judged: boolean };
  memos: { count: number; due: number };
}

export type WatchSort = "added" | "code" | "tag";

export interface WatchList {
  max: number;
  max_provisional: boolean;
  count: number;
  sort: WatchSort;
  sorts: WatchSort[];
  tags: string[];
  reason_max_len: number;
  items: WatchItem[];
}

export interface WatchEntryResponse {
  entry: WatchEntry | null;
  max: number;
  count: number;
  tags: string[];
  reason_max_len: number;
}

export interface DigestItem {
  code: string;
  display_code: string;
  name: string | null;
  as_of: string | null;
  prev_date: string | null;
  changed: boolean;
  changes: { kind: string; date: string | null; text: string }[];
  lines: string[];
  summary: string | null;
}

export interface Digest {
  today: string;
  count: number;
  changed_count: number;
  summary: string;
  items: DigestItem[];
  memo_due: {
    items: { memo_id: number; code: string; display_code: string; name: string | null; review_date: string; text: string }[];
    none_text: string | null;
  };
  notes: string[];
}

export interface FirstRun {
  required: boolean;
  version: number;
  acknowledged_at: string | null;
  acknowledged_version: number | null;
}

export interface About {
  version: number;
  title: string;
  lead: string;
  sections: { key: string; heading: string; items: string[] }[];
  ack_label: string;
  ack_note: string;
  reread_note: string;
  first_run: FirstRun;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
const enc = encodeURIComponent;

export const memoApi = {
  form: () => request<MemoForm>("/api/memo-form"),
  forStock: (code: string) => request<MemoList>(`/api/stocks/${enc(code)}/memos`),
  all: () => request<MemoList>("/api/memos?limit=1000"),
  create: (code: string, content: MemoContent) => request<MemoDetail>(`/api/stocks/${enc(code)}/memos`, json(content)),
  get: (id: number) => request<MemoDetail>(`/api/memos/${id}`),
  addRevision: (id: number, content: MemoContent) => request<MemoDetail>(`/api/memos/${id}/revisions`, json(content)),
  review: (id: number, revisionId?: number) =>
    request<MemoReviewPayload>(`/api/memos/${id}/review${revisionId ? `?revision_id=${revisionId}` : ""}`),
  addSelfReview: (id: number, body: { revision_id: number; process: string; result: string; note: string }) =>
    request<MemoDetail>(`/api/memos/${id}/reviews`, json(body)),
  verifyAll: () => request<VerifyAll>("/api/memos/verify"),
};

export const watchApi = {
  list: (sort: WatchSort) => request<WatchList>(`/api/watchlist?sort=${sort}`),
  entry: (code: string) => request<WatchEntryResponse>(`/api/watchlist/${enc(code)}`),
  add: (code: string, reason: string, tags: string[]) => request<WatchEntry>("/api/watchlist", json({ code, reason, tags })),
  remove: (code: string) =>
    request<{ code: string; removed: boolean }>(`/api/watchlist/${enc(code)}`, { method: "DELETE" }),
  digest: () => request<Digest>("/api/digest"),
};

export const appApi = {
  about: () => request<About>("/api/about"),
  firstRun: () => request<FirstRun>("/api/settings/first-run"),
  ackFirstRun: () => request<FirstRun>("/api/settings/first-run", { method: "POST" }),
};

export const EXPORT_URLS = { json: "/api/export?format=json", md: "/api/export?format=md" } as const;
