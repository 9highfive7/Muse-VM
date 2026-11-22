// 「テクニカルで見る」ブロック（銘柄ページのテクニカル節）。
//
// この画面だけは利用者の明示の要望で「買いです／売りです」と過去の検証結果の割合を
// 表示する（要件 6.1 の例外）。文言はすべてサーバー（content/technical_view.json）
// から受け取って表示するだけで、このファイルには評価語を書かない。そのため
// tests/test_banned_words.py のフロント静的文言チェックは、このファイルにも
// 従来どおり全禁止語で適用される。
// 配色は評価を意味させない（要件 6.2）：買い・売りで色を変えず、数値は本文色。
import { useEffect, useState } from "react";
import { api, type TechBacktestCell, type TechBacktestResponse, type TechIndicator, type TechSide } from "../api";
import { fmt, fmtSigned } from "./chartOptions";
import { Notes } from "./parts";

const SIDES: TechSide[] = ["buy", "sell"];
const RESULT_LABEL = { win: "勝ち", loss: "負け", draw: "引き分け" } as const;

export default function TechnicalView({ code }: { code: string }) {
  const [indicator, setIndicator] = useState<TechIndicator>("rsi");
  const [data, setData] = useState<TechBacktestResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .technicalBacktest(code, indicator)
      .then((d) => !cancelled && setData(d))
      .catch((e: Error) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [code, indicator]);

  const L = data?.labels ?? {};
  const bt = data?.backtest ?? null;
  const ev = data?.evaluation;
  const h = data?.headline;
  const mainSide = ev && (ev.side === "buy" || ev.side === "sell") ? ev.side : null;

  return (
    <section className="panel stage tech-view" id="technical-view" aria-labelledby="technical-view-title" aria-busy={loading}>
      <div className="panel-head">
        <h3 id="technical-view-title">{L.title ?? "テクニカルで見る"}</h3>
        <label className="tv-select">
          {L.select ?? "指標"}{" "}
          <select value={indicator} onChange={(e) => setIndicator(e.target.value as TechIndicator)}>
            {(data?.indicators ?? [{ key: indicator, label: indicator }]).map((i) => (
              <option key={i.key} value={i.key}>
                {i.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {L.lead && <p className="note small">{L.lead}</p>}
      {error && (
        <p className="error" role="alert">
          読み込みに失敗しました：{error}
        </p>
      )}
      {loading && (
        <p className="progress small" role="status" aria-live="polite">
          {L.loading ?? "計算しています…"}
        </p>
      )}

      {data && ev && h && (
        <>
          <div className="tv-grid">
            <div className="tv-box">
              <p className="tv-kicker">{L.evaluation}</p>
              <p className="tv-eval">{ev.text}</p>
              <p className="small">
                {L.basis}：{ev.basis}
              </p>
            </div>
            <div className="tv-box">
              <p className="tv-kicker">{h.label ?? L.backtest}</p>
              {h.win_rate_pct !== null && (
                <p className="tv-big num">
                  {h.win_rate_pct}
                  <span className="tv-unit">%</span>
                </p>
              )}
              {h.small_sample_text && <p className="tv-flag">{h.small_sample_text}</p>}
              <p className="tv-headline">{h.text}</p>
              {h.detail && <p className="small">{h.detail}</p>}
            </div>
          </div>

          {bt && (
            <>
              <div className="table-scroll">
                <table className="tv-table">
                  <caption className="small">{bt.table_caption}</caption>
                  <thead>
                    <tr>
                      <th>{L.col_side}</th>
                      <th>{L.col_horizon}</th>
                      <th className="num">{L.col_samples}</th>
                      <th className="num">{L.col_record}</th>
                      <th className="num">{L.col_win_rate}</th>
                      <th className="num">{L.col_avg}</th>
                      <th>{L.col_note}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {SIDES.flatMap((s) =>
                      bt.results[s].map((c) => (
                        <ResultRow key={`${s}-${c.horizon}`} c={c} main={s === mainSide && c.horizon === bt.main_horizon} />
                      )),
                    )}
                  </tbody>
                </table>
              </div>

              <h4 className="panel-label">{L.conditions}</h4>
              <ul className="notes">
                <li>
                  {L.signal_rule}：{bt.signal_rule}
                </li>
                <li>{bt.period.text}</li>
                {bt.conditions.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            </>
          )}
          <Notes notes={data.notes} />

          {bt && <EntryList bt={bt} />}
        </>
      )}
    </section>
  );
}

function ResultRow({ c, main }: { c: TechBacktestCell; main: boolean }) {
  return (
    <tr className={main ? "tv-main-row" : undefined}>
      <td>{c.side_label}</td>
      <td>{c.horizon}営業日</td>
      <td className="num">{c.samples}</td>
      <td className="num">
        {c.wins}・{c.losses}
        {c.draws ? `（引き分け${c.draws}）` : ""}
      </td>
      <td className="num">{c.win_rate_display === null ? "—" : `${c.win_rate_display}%`}</td>
      <td className="num">{c.avg_return_display === null ? "—" : `${c.avg_return_display}%`}</td>
      <td className="small">{c.note}</td>
    </tr>
  );
}

/** 第3階層：検証に数えた回を全件（主表示の保有期間）。 */
function EntryList({ bt }: { bt: NonNullable<TechBacktestResponse["backtest"]> }) {
  const rows = SIDES.flatMap((s) =>
    (bt.results[s].find((c) => c.horizon === bt.main_horizon)?.entries ?? []).map((e) => ({ ...e, side: s })),
  ).sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  return (
    <details className="layer3">
      <summary>
        検証に数えた回の一覧（{bt.main_horizon}営業日、全{rows.length}件）
      </summary>
      {rows.length === 0 ? (
        <p className="small">該当する回はありません。</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>判定日</th>
                <th>判定</th>
                <th className="num">判定日終値（円）</th>
                <th>手仕舞い日</th>
                <th className="num">手仕舞い日終値（円）</th>
                <th className="num">騰落率</th>
                <th>結果</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr key={`${e.side}-${e.date}`}>
                  <td>{e.date}</td>
                  <td>{bt.results[e.side][0].side_label}</td>
                  <td className="num">{fmt(e.entry_close, 2)}</td>
                  <td>{e.exit_date}</td>
                  <td className="num">{fmt(e.exit_close, 2)}</td>
                  <td className="num">{fmtSigned(e.return_pct, 2)}%</td>
                  <td>{RESULT_LABEL[e.result]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  );
}
