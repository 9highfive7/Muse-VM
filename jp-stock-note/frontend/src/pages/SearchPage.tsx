import { useEffect, useState, type FormEvent } from "react";
import { api, type MacroResponse, type SearchResponse } from "../api";
import { HomeDigest } from "../components/Digest";
import { MacroBand } from "../components/Macro";
import { Link, navigate } from "../router";

// S1 ホーム（検索ボックス）と S2 銘柄検索結果。
export default function SearchPage({ query }: { query: string }) {
  const [input, setInput] = useState(query);
  const [data, setData] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [macro, setMacro] = useState<MacroResponse | null>(null);
  const [macroError, setMacroError] = useState<string | null>(null);

  // S1: market-environment band (home only)
  useEffect(() => {
    if (query) return;
    let cancelled = false;
    api
      .macro()
      .then((d) => !cancelled && setMacro(d))
      .catch((e: Error) => !cancelled && setMacroError(e.message));
    return () => {
      cancelled = true;
    };
  }, [query]);

  useEffect(() => {
    setInput(query);
    setError(null);
    if (!query.trim()) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .search(query)
      .then((d) => !cancelled && setData(d))
      .catch((e: Error) => !cancelled && setError(`検索に失敗しました：${e.message}`))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [query]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const q = input.trim();
    navigate(q ? `/search?q=${encodeURIComponent(q)}` : "/");
  };

  return (
    <section>
      <h1>{query ? "銘柄検索結果" : "銘柄を探す"}</h1>
      <form className="search" role="search" onSubmit={submit}>
        <label htmlFor="q">証券コードまたは銘柄名（部分一致）</label>
        <div className="search-row">
          <input
            id="q"
            type="search"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="例：7203、トヨタ、KDDI"
            autoComplete="off"
            maxLength={100}
          />
          <button type="submit">検索</button>
        </div>
        <p className="hint">全角・半角、大文字・小文字は区別しません。</p>
      </form>

      {!query && (
        <>
          <p className="note">
            このアプリは日次終値ベースで、銘柄の事実を整理して表示します（リアルタイム価格は扱いません）。
          </p>
          <HomeDigest />
          <MacroBand data={macro} error={macroError} />
        </>
      )}
      {loading && <p aria-live="polite">検索中…</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {data && !loading && (
        <>
          <p aria-live="polite">
            「{data.query}」の検索結果：{data.count}件{data.count >= 50 ? "（上位50件まで表示）" : ""}
          </p>
          {data.count === 0 ? (
            <p className="note">
              該当する銘柄がありません。銘柄マスタが未取得の場合は<Link href="/status">データ状態</Link>
              で取得状況を確認してください。
            </p>
          ) : (
            <table className="results">
              <thead>
                <tr>
                  <th scope="col">コード</th>
                  <th scope="col">銘柄名</th>
                  <th scope="col">市場区分</th>
                  <th scope="col">17業種</th>
                  <th scope="col">33業種</th>
                </tr>
              </thead>
              <tbody>
                {data.results.map((s) => (
                  <tr key={s.code}>
                    <td>
                      <Link href={`/stocks/${s.code}`}>{s.display_code}</Link>
                    </td>
                    <td>
                      <Link href={`/stocks/${s.code}`}>{s.name}</Link>
                    </td>
                    <td>{s.market.name ?? "—"}</td>
                    <td>{s.sector17.name ?? "—"}</td>
                    <td>{s.sector33.name ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  );
}
