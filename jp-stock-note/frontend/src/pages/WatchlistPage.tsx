// ウォッチリスト (要件 3.9)。並び順は追加日順・コード順・タグ別のみ（騰落率順は提供しない）。
import { useCallback, useEffect, useState } from "react";
import { watchApi, type WatchList, type WatchSort } from "../api";
import { fmtDateTime } from "../components/Memo";
import { WatchAddForm } from "../components/Watch";
import { Link } from "../router";

const SORT_LABELS: Record<WatchSort, string> = { added: "追加日順", code: "コード順", tag: "タグ別" };
const yen = (v: number) => v.toLocaleString("ja-JP", { maximumFractionDigits: 2 });

export default function WatchlistPage() {
  const [sort, setSort] = useState<WatchSort>("added");
  const [data, setData] = useState<WatchList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(() => {
    watchApi
      .list(sort)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: Error) => setError(`読み込みに失敗しました：${e.message}`));
  }, [sort]);
  useEffect(load, [load]);

  const remove = (code: string, label: string) => {
    if (!window.confirm(`${label}をウォッチリストから外しますか？（追加日・解除日の履歴は残ります）`)) return;
    watchApi
      .remove(code)
      .then(() => {
        setMessage(`${label}をウォッチリストから外しました。`);
        load();
      })
      .catch((e: Error) => setError(`外せませんでした：${e.message}`));
  };

  return (
    <section>
      <h1>ウォッチリスト</h1>
      <p className="note">
        「なぜ見ているか」を監視理由として記録し、継続して観察するための一覧です。終値は日次終値（大引け後に確定した値）です。
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {message && <p aria-live="polite">{message}</p>}
      {data && (
        <>
          <div className="panel-head">
            <p>
              {data.count} / {data.max}銘柄{data.max_provisional && <span className="small note">（上限は暫定値）</span>}
            </p>
            <div className="range-switch" role="group" aria-label="並び順">
              {data.sorts.map((s) => (
                <button key={s} type="button" aria-pressed={sort === s} onClick={() => setSort(s)}>
                  {SORT_LABELS[s]}
                </button>
              ))}
            </div>
          </div>

          {data.items.length === 0 ? (
            <p className="placeholder">
              ウォッチリストに銘柄がありません。下のフォーム、または銘柄ページの「ウォッチリストに追加」から追加できます。
            </p>
          ) : (
            <div className="table-scroll">
              <table className="watch-table">
                <thead>
                  <tr>
                    <th scope="col">銘柄</th>
                    <th scope="col">監視理由</th>
                    <th scope="col">追加日</th>
                    <th scope="col" className="num">
                      終値（基準日）
                    </th>
                    <th scope="col">確認ポイント</th>
                    <th scope="col">判断メモ</th>
                    <th scope="col">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((w) => {
                    const label = `${w.display_code} ${w.name ?? ""}`;
                    return (
                      <tr key={w.code}>
                        <td>
                          <Link href={`/stocks/${w.code}`}>{label}</Link>
                        </td>
                        <td>
                          {w.tags.length > 0 && (
                            <ul className="chips">
                              {w.tags.map((t) => (
                                <li key={t} className="chip">
                                  {t}
                                </li>
                              ))}
                            </ul>
                          )}
                          {w.reason}
                        </td>
                        <td className="num">{fmtDateTime(w.added_at).slice(0, 10)}</td>
                        <td className="num">
                          {w.price ? (
                            <>
                              {yen(w.price.close)}円<span className="block small note">{w.price.as_of}</span>
                            </>
                          ) : (
                            "未取得"
                          )}
                        </td>
                        <td>
                          {w.alerts.judged ? (
                            <>
                              該当{w.alerts.count}件<span className="block small note">判定基準日：{w.alerts.as_of}</span>
                            </>
                          ) : (
                            <span className="note">決算サマリー未取得</span>
                          )}
                        </td>
                        <td>
                          {w.memos.count}件
                          {w.memos.due > 0 && <span className="badge">見直し日が到来 {w.memos.due}件</span>}
                        </td>
                        <td>
                          <button type="button" className="small-btn" onClick={() => remove(w.code, label)}>
                            外す
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          <section className="settings-box" aria-labelledby="watch-add-title">
            <h2 id="watch-add-title">銘柄を追加</h2>
            {adding ? (
              <WatchAddForm
                tags={data.tags}
                reasonMax={data.reason_max_len}
                onAdded={(e) => {
                  setAdding(false);
                  setMessage(`${e.code.slice(0, 4)}をウォッチリストに追加しました。`);
                  load();
                }}
                onCancel={() => setAdding(false)}
              />
            ) : (
              <p>
                <button type="button" onClick={() => setAdding(true)} disabled={data.count >= data.max}>
                  コードを指定して追加
                </button>
                {data.count >= data.max && <span className="small note"> 上限に達しています。</span>}
              </p>
            )}
          </section>
          <ul className="notes">
            <li>注記：確認ポイントは銘柄ページの「確認ポイント」と同じ判定で、事実の該当件数です。良否の評価ではありません。</li>
            <li>注記：ウォッチリストの銘柄は、毎週土曜の夜間バッチで決算サマリーを再取得し、訂正を検知します（訂正を検知した場合は、銘柄ページの財務の第3階層「訂正検知履歴」に記録されます）。</li>
            <li>注記：外した銘柄も、追加日・解除日の履歴はデータとして残り、エクスポートに含まれます。</li>
          </ul>
        </>
      )}
    </section>
  );
}
