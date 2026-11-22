// Demo build only: answers the app's same-origin /api/* requests from the
// responses recorded by demo/record_fixtures.py (synthetic data only).
// Nothing here is reachable from the normal build (see vite.config.ts).

export interface Recorded {
  status: number;
  body: unknown;
}

export interface SearchStock {
  code: string;
  display_code: string;
  name: string;
  name_en: string | null;
}

export interface DemoFixtures {
  meta: { stock_pages: string[]; bars_from: string; bars_to: string; recorded_on: string };
  routes: Record<string, Recorded>;
  stocks: SearchStock[];
  export: { json: unknown; md: string };
}

export const DISABLED_EVENT = "demo:disabled";
export const DISABLED_TEXT = "デモ版では無効です";

const S0_KEY = "demo-s0-confirmed";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function normCode(raw: string): string {
  const c = decodeURIComponent(raw).normalize("NFKC").trim().toUpperCase();
  return c.length === 4 ? `${c}0` : c;
}

function normText(s: string | null | undefined): string {
  return (s ?? "").normalize("NFKC").toLowerCase().replace(/\s+/g, "");
}

/** What a blocked operation is, in the words the screen uses. */
function operationLabel(method: string, path: string, params: URLSearchParams): string {
  if (/\/memos$/.test(path)) return "判断メモの保存";
  if (/\/revisions$/.test(path)) return "判断メモの書き直し";
  if (/\/reviews$/.test(path)) return "振り返りの追記";
  if (path.startsWith("/api/watchlist")) return method === "DELETE" ? "ウォッチリストからの削除" : "ウォッチリストへの追加";
  if (/\/ensure-fins$/.test(path) && params.get("force")) return "決算データの再取得";
  if (path.startsWith("/api/fetch")) return "データの取得";
  if (path === "/api/settings/tankan") return "想定為替レートの保存";
  return "この操作";
}

export function stockLabel(fx: DemoFixtures, code: string): string {
  const s = fx.stocks.find((x) => x.code === code);
  return s ? `${s.display_code} ${s.name}` : code;
}

export function makeHandler(fx: DemoFixtures) {
  const pages = new Set(fx.meta.stock_pages);
  const available = fx.meta.stock_pages.map((c) => stockLabel(fx, c)).join("、");

  const notInDemo = (code: string) =>
    json(409, {
      detail: `${DISABLED_TEXT}：銘柄「${stockLabel(fx, code)}」のデータはデモに含まれていません。デモで開ける銘柄は ${available} です。`,
    });

  const disabled = (label: string) => {
    window.dispatchEvent(new CustomEvent(DISABLED_EVENT, { detail: label }));
    return json(403, { detail: `${DISABLED_TEXT}（${label}はサンプルデータの閲覧専用のデモでは行えません）。` });
  };

  const search = (q: string) => {
    const norm = normText(q);
    if (!norm) return { query: q, normalized: norm, count: 0, results: [] };
    const rank = (s: SearchStock) => {
      const code = s.code.toLowerCase();
      if (code === norm || (norm.length === 4 && code.slice(0, 4) === norm)) return 0;
      if (code.startsWith(norm)) return 1;
      if (normText(s.name).startsWith(norm)) return 2;
      return 3;
    };
    const results = fx.stocks
      .filter((s) => s.code.toLowerCase().includes(norm) || normText(s.name).includes(norm) || normText(s.name_en).includes(norm))
      .sort((a, b) => rank(a) - rank(b) || (a.code < b.code ? -1 : a.code > b.code ? 1 : 0));
    return { query: q, normalized: norm, count: results.length, results };
  };

  return async function handle(method: string, url: string): Promise<Response> {
    const u = new URL(url, "http://demo.invalid");
    const path = u.pathname;
    const params = u.searchParams;
    const recorded = (key: string) => {
      const r = fx.routes[key];
      return r ? json(r.status, r.body) : null;
    };

    // S0 初回説明: confirmation is kept per browser tab (sessionStorage), nothing is sent anywhere.
    if (path === "/api/settings/first-run") {
      if (method === "POST") {
        sessionStorage.setItem(S0_KEY, "1");
        return recorded("GET /api/settings/first-run")!;
      }
      return sessionStorage.getItem(S0_KEY)
        ? recorded("GET /api/settings/first-run")!
        : recorded("GET /api/settings/first-run#before-ack")!;
    }

    if (method === "GET" && path === "/api/search") {
      return recorded(`GET /api/search${u.search}`) ?? json(200, search(params.get("q") ?? ""));
    }

    // per-stock routes: /api/stocks/{code}/...  and  /api/watchlist/{code}
    const stock = path.match(/^\/api\/stocks\/([^/]+)\/(.+)$/);
    if (stock) {
      const code = normCode(stock[1]);
      const key = `${method} /api/stocks/${code}/${stock[2]}${u.search}`;
      const hit = recorded(key);
      if (hit) return hit;
      if (!pages.has(code)) return notInDemo(code);
      if (method !== "GET") return disabled(operationLabel(method, path, params));
      return json(404, { detail: `${DISABLED_TEXT}（このデータはデモに含まれていません）。` });
    }
    const watch = path.match(/^\/api\/watchlist\/([^/]+)$/);
    if (watch && method === "GET") {
      return recorded(`GET /api/watchlist/${normCode(watch[1])}`) ?? notInDemo(normCode(watch[1]));
    }

    if (method === "GET" && path.startsWith("/api/glossary/")) {
      const code = params.get("code");
      const base = `GET ${path}`;
      return (
        (code ? recorded(`${base}?code=${normCode(code)}`) : null) ??
        recorded(base) ??
        json(404, { detail: "用語が見つかりません。" })
      );
    }

    if (method === "GET") {
      const hit = recorded(`GET ${path}${u.search}`);
      if (hit) return hit;
      if (/^\/api\/memos\/\d+/.test(path)) return json(404, { detail: "メモが見つかりません。" });
      console.warn(`[demo] no recorded response for GET ${path}${u.search}`);
      return json(404, { detail: `${DISABLED_TEXT}（このデータはデモに含まれていません）。` });
    }

    // every other write: ウォッチリスト追加/削除・メモ保存・振り返り・再取得・設定保存
    return disabled(operationLabel(method, path, params));
  };
}

/** Replace window.fetch for same-origin /api/* only; anything else passes through. */
export function installMockFetch(fx: DemoFixtures) {
  const handle = makeHandler(fx);
  const original = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (!url.startsWith("/api/")) return original(input, init);
    const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
    return handle(method, url);
  };
}

/** The export links are plain <a href="/api/export?..." download>: serve the recorded files as blobs. */
export function installExportLinks(fx: DemoFixtures) {
  document.addEventListener(
    "click",
    (e) => {
      const a = (e.target as Element | null)?.closest?.("a[href]");
      const href = a?.getAttribute("href") ?? "";
      if (!href.startsWith("/api/")) return;
      e.preventDefault();
      const fmt = new URL(href, "http://demo.invalid").searchParams.get("format");
      if (href.startsWith("/api/export") && (fmt === "json" || fmt === "md")) {
        const blob =
          fmt === "md"
            ? new Blob([fx.export.md], { type: "text/markdown;charset=utf-8" })
            : new Blob([JSON.stringify(fx.export.json, null, 2)], { type: "application/json" });
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = `stockapp-memos-demo.${fmt}`;
        link.click();
        setTimeout(() => URL.revokeObjectURL(link.href), 0);
        return;
      }
      window.dispatchEvent(new CustomEvent(DISABLED_EVENT, { detail: "このリンク" }));
    },
    true,
  );
}
