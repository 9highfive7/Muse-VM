import { useCallback, useEffect, useState } from "react";
import { appApi } from "./api";
import { Link, useLocation } from "./router";
import SearchPage from "./pages/SearchPage";
import StockPage from "./pages/StockPage";
import StatusPage from "./pages/StatusPage";
import WatchlistPage from "./pages/WatchlistPage";
import { MemoDetailPage, MemoListPage } from "./pages/MemoPages";
import AboutPage, { FirstRunScreen } from "./pages/AboutPage";

// 3.11 常設版免責（全画面フッター）— exact text from the requirements.
export const DISCLAIMER =
  "本アプリは情報提供を目的とし、投資勧誘・助言を行うものではありません。データの正確性・完全性は保証されません。投資の最終判断はご自身の責任で行ってください。";

const NAV: { href: string; label: string; match: (p: string) => boolean }[] = [
  { href: "/", label: "ホーム", match: (p) => p === "/" || p === "/search" },
  { href: "/watchlist", label: "ウォッチリスト", match: (p) => p === "/watchlist" },
  { href: "/memos", label: "判断メモ", match: (p) => p.startsWith("/memos") },
  { href: "/status", label: "データ状態・設定", match: (p) => p === "/status" },
];

function Route({ path, search }: { path: string; search: string }) {
  const stock = path.match(/^\/stocks\/([^/]+)\/?$/);
  if (stock) return <StockPage code={decodeURIComponent(stock[1])} />;
  const memo = path.match(/^\/memos\/(\d+)\/?$/);
  if (memo) return <MemoDetailPage memoId={Number(memo[1])} />;
  if (path === "/memos") return <MemoListPage />;
  if (path === "/watchlist") return <WatchlistPage />;
  if (path === "/status") return <StatusPage />;
  if (path === "/about") return <AboutPage />;
  if (path === "/search") return <SearchPage query={new URLSearchParams(search).get("q") ?? ""} />;
  if (path === "/") return <SearchPage query="" />;
  return (
    <section>
      <h1>ページが見つかりません</h1>
      <p>
        <Link href="/">ホームへ戻る</Link>
      </p>
    </section>
  );
}

type Gate = "checking" | "required" | "ok" | "error";

export default function App() {
  const { path, search } = useLocation();
  // 要件 8 フェーズ4 受け入れ条件④: S0 blocks every other screen until agreed.
  const [gate, setGate] = useState<Gate>("checking");
  const [gateError, setGateError] = useState<string | null>(null);

  const check = useCallback(() => {
    setGate("checking");
    setGateError(null);
    appApi
      .firstRun()
      .then((r) => setGate(r.required ? "required" : "ok"))
      .catch((e: Error) => {
        setGateError(e.message);
        setGate("error");
      });
  }, []);
  useEffect(check, [check]);

  const open = gate === "ok";

  return (
    <div className="layout">
      <header className="site-header">
        <Link href="/" className="brand">
          日本株 分析ノート
        </Link>
        {open && (
          <nav aria-label="メイン">
            {NAV.map((n) => (
              <Link key={n.href} href={n.href} aria-current={n.match(path) ? "page" : undefined}>
                {n.label}
              </Link>
            ))}
          </nav>
        )}
      </header>
      <main>
        {gate === "checking" && <p aria-live="polite">読み込み中…</p>}
        {gate === "error" && (
          <section>
            <p className="error" role="alert">
              サーバーに接続できませんでした：{gateError}
            </p>
            <button type="button" onClick={check}>
              再試行
            </button>
          </section>
        )}
        {gate === "required" && <FirstRunScreen onDone={() => setGate("ok")} />}
        {open && <Route path={path} search={search} />}
      </main>
      <footer className="site-footer">
        <p>{DISCLAIMER}</p>
        {open && (
          <p className="footer-links">
            <Link href="/about">このアプリについて（初回説明・拡張版免責）</Link>
          </p>
        )}
      </footer>
    </div>
  );
}
