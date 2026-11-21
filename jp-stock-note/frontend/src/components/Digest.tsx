// S1 大引け後ダイジェスト: 見直し日が到来した判断メモ ＋ ウォッチリスト銘柄の前営業日からの事実の変化。
// 文はすべてサーバーのテンプレート（禁止語テスト対象）。通知はしない。
import { useEffect, useState } from "react";
import { watchApi, type Digest, type WatchList } from "../api";
import { Link } from "../router";

export function HomeDigest() {
  const [digest, setDigest] = useState<Digest | null>(null);
  const [watch, setWatch] = useState<WatchList | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([watchApi.digest(), watchApi.list("added")])
      .then(([d, w]) => {
        if (cancelled) return;
        setDigest(d);
        setWatch(w);
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, []);

  if (error)
    return (
      <p className="error" role="alert">
        ダイジェストの読み込みに失敗しました：{error}
      </p>
    );
  if (!digest) return <p aria-live="polite">ダイジェストを読み込み中…</p>;
  const byCode = new Map((watch?.items ?? []).map((w) => [w.code, w]));

  return (
    <section className="digest" aria-labelledby="digest-title">
      <h2 id="digest-title">大引け後ダイジェスト（{digest.today}）</h2>

      <div className="card">
        <h3>見直し日が到来した判断メモ</h3>
        {digest.memo_due.items.length === 0 ? (
          <p className="note">{digest.memo_due.none_text}</p>
        ) : (
          <ul className="plain due-list">
            {digest.memo_due.items.map((m) => (
              <li key={m.memo_id}>
                <Link href={`/memos/${m.memo_id}`}>{m.text}</Link>
              </li>
            ))}
          </ul>
        )}
        <p className="small note">見直し日以降に「振り返り」を追記すると、この一覧から外れます。</p>
      </div>

      <div className="card">
        <div className="panel-head">
          <h3>ウォッチリスト（{watch ? `${watch.count}/${watch.max}銘柄` : `${digest.count}銘柄`}）</h3>
          <Link href="/watchlist">ウォッチリストを開く</Link>
        </div>
        <p>{digest.summary}</p>
        {digest.items.length > 0 && (
          <ul className="plain digest-list">
            {digest.items.map((i) => {
              const w = byCode.get(i.code);
              return (
                <li key={i.code}>
                  <p className="digest-stock">
                    <Link href={`/stocks/${i.code}`}>
                      {i.display_code} {i.name ?? ""}
                    </Link>
                    {i.changed && <span className="badge">前営業日から変化あり</span>}
                    {w && w.memos.due > 0 && <span className="badge">見直し日が到来したメモ {w.memos.due}件</span>}
                  </p>
                  {w && (
                    <p className="small note">
                      監視理由：{w.tags.length > 0 ? `［${w.tags.join("・")}］` : ""}
                      {w.reason}
                      {w.alerts.judged && `／確認ポイント該当${w.alerts.count}件（判定基準日：${w.alerts.as_of}）`}
                    </p>
                  )}
                  <ul className="facts-list">
                    {i.lines.map((t) => (
                      <li key={t}>{t}</li>
                    ))}
                    {i.summary && !i.changed && <li className="unavailable">{i.summary}</li>}
                  </ul>
                </li>
              );
            })}
          </ul>
        )}
        <ul className="notes">
          {digest.notes.map((n) => (
            <li key={n}>注記：{n}</li>
          ))}
        </ul>
      </div>
    </section>
  );
}
