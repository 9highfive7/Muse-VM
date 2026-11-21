// Phase 3 マクロ帯（要件 3.8）: home summary band (S1) and the stock page's
// macro section (S3 第1〜3階層). Every figure shows its as-of and a cadence
// badge; a series whose as-of is far behind the reference date gets an
// emphasised badge (colour + icon + text). The app only lays out facts the
// server already worded — no scores, no verdicts.
import { useMemo } from "react";
import type { EChartsOption } from "echarts";
import type { ChecklistRow, MacroItem, MacroKey, MacroResponse, StockMacroResponse } from "../api";
import EChart from "./EChart";
import { fmt, fmtSigned, INK, lineSeries, panel } from "./chartOptions";
import { Term } from "./Glossary";
import { Notes, ValuesTable } from "./parts";

const WINDOWS = ["20", "60", "250"] as const;

const VALUE_DIGITS: Record<string, number> = { topix: 2, usdjpy: 2 };

function valueText(it: MacroItem): string {
  if (it.value === null) return "未取得";
  if (it.key === "us_spread") return `${fmtSigned(it.value, 2)}ポイント`;
  const digits = VALUE_DIGITS[it.key] ?? 2;
  const unit = it.key === "topix" ? "" : it.unit;
  return `${fmt(it.value, digits)}${unit}`;
}

function asOfText(it: MacroItem): string {
  if (!it.as_of) return "基準日：—";
  if (it.frequency === "monthly") return `基準月：${it.as_of.slice(0, 4)}年${Number(it.as_of.slice(5, 7))}月`;
  return `基準日：${it.as_of}`;
}

function windowLabel(it: MacroItem, n: string): string {
  return it.frequency === "monthly" ? `${n}か月` : `${n}営業日`;
}

function changeText(it: MacroItem, n: "20" | "60" | "250"): string {
  const c = it.changes[n];
  if (!c.available) return "算出不可";
  if (it.key === "topix") return `${fmtSigned(c.change_pct, 1)}%`;
  return `${fmtSigned(c.change, 2)}${it.change_unit ?? ""}`;
}

/** 基準日・更新周期バッジ。古い系列は枠・背景を反転し、時計アイコンと文で示す。 */
export function CadenceBadges({ it }: { it: MacroItem }) {
  return (
    <span className="badges">
      <span className={it.stale.stale ? "badge badge-stale" : "badge"} title={it.lag_note}>
        {it.stale.stale && <span aria-hidden="true">◷ </span>}
        {it.cadence_badge}
      </span>
      {it.stale.stale && it.stale.text && <span className="badge badge-stale">◷ {it.stale.text}</span>}
      {it.fetch.failed && <span className="badge badge-failed">{it.fetch.status_text ?? "取得失敗・前回値を表示中"}</span>}
    </span>
  );
}

function MacroTile({ it }: { it: MacroItem }) {
  return (
    <div className="macro-tile">
      <div className="macro-title">{it.short}</div>
      <div className="macro-value num">{valueText(it)}</div>
      <div className="macro-asof">{asOfText(it)}</div>
      <CadenceBadges it={it} />
      {it.state === "ok" && (
        <table className="macro-changes">
          <caption className="small">変化（{it.frequency === "monthly" ? "月次の観測数" : "営業日"}）</caption>
          <tbody>
            {WINDOWS.map((n) => (
              <tr key={n}>
                <th scope="row">{windowLabel(it, n)}</th>
                <td className="num">{changeText(it, n)}</td>
              </tr>
            ))}
            {it.month_change && it.month_change.change !== null && (
              <tr>
                <th scope="row">前月差</th>
                <td className="num">{fmtSigned(it.month_change.change, 2)}ポイント</td>
              </tr>
            )}
          </tbody>
        </table>
      )}
      {it.range_250.state === "ok" ? (
        <p className="small macro-range">
          直近250観測のレンジ内：下から{fmt(it.range_250.percentile ?? null, 0)}%
        </p>
      ) : (
        it.state === "ok" && <p className="small macro-range">直近250観測のレンジ内の位置：観測不足（{it.range_250.n}観測）</p>
      )}
      {it.tankan && it.tankan.available && (
        <p className="small macro-range">
          想定為替レート{fmt(it.tankan.rate, 2)}円（{it.tankan.survey_label}）との差：
          {fmtSigned(it.tankan.gap_yen, 2)}円（{fmtSigned(it.tankan.gap_pct, 2)}%）
        </p>
      )}
      {it.note && <p className="small note">{it.note}</p>}
    </div>
  );
}

/** S1 ホームのマクロ帯。 */
export function MacroBand({ data, error }: { data: MacroResponse | null; error: string | null }) {
  if (error)
    return (
      <p className="error" role="alert">
        市場環境の読み込みに失敗しました：{error}
      </p>
    );
  if (!data) return <p aria-live="polite">市場環境を読み込み中…</p>;
  return (
    <section className="macro-band" aria-labelledby="macro-band-title">
      <h2 id="macro-band-title">市場環境（マクロ帯）</h2>
      <p className="note small">{data.reference_label}。各値の基準日・更新周期はそれぞれのバッジのとおり（系列ごとに遅れが異なる）。</p>
      {data.state === "no_data" && (
        <p className="note">金利・為替（FRED）はまだ取得していません。データ状態の画面から取得できます。</p>
      )}
      <div className="macro-grid">
        {data.headline.map((k) => (
          <MacroTile key={k} it={data.items[k]} />
        ))}
      </div>
      <p className="note small">
        用語：<Term term="long_term_rate">長期金利</Term>
        <Term term="yield_spread">長短スプレッド</Term>
        <Term term="assumed_fx_rate">想定為替レート</Term>
      </p>
      <Notes notes={data.notes} />
    </section>
  );
}

// ---------------------------------------------------------------- charts

function macroLineOption(it: MacroItem): EChartsOption {
  const series = {
    ...lineSeries(it.short, it.history.values, INK, { width: 2 }),
    ...(it.history.step ? { step: "end" as const } : {}),
  };
  return panel(it.history.dates, it.unit === "円" ? "円" : "%", [series], { digits: 2 });
}

function MacroChart({ it, height = 180 }: { it: MacroItem; height?: number }) {
  const option = useMemo(() => macroLineOption(it), [it]);
  if (it.state !== "ok") return <p className="note small">{it.short}：未取得のためグラフは表示しません。</p>;
  const span = `${it.history.dates[0]}〜${it.history.dates[it.history.dates.length - 1]}`;
  return (
    <>
      <h4 className="panel-label">
        {it.title}（{it.history.step ? "月次・ステップ線" : "日次"}、{span}）
      </h4>
      <CadenceBadges it={it} />
      {/* each macro series has its own date axis -> its own group (not linked to the stock charts) */}
      <EChart option={option} group={`macro-${it.key}`} height={height} label={`${it.title}の推移`} />
    </>
  );
}

// ---------------------------------------------------------------- stock page

function ChecklistTable({ rows }: { rows: ChecklistRow[] }) {
  return (
    <div className="table-scroll">
      <table className="checklist">
        <caption className="small">
          両論チェック表（<Term term="tailwind_headwind">見方</Term>）：要因ごとに、業種タグでプラス方向・マイナス方向とされる条件と、現在の数値（事実）を並べる
        </caption>
        <thead>
          <tr>
            <th scope="col">要因</th>
            <th scope="col">業績にプラス方向とされる条件</th>
            <th scope="col">業績にマイナス方向とされる条件</th>
            <th scope="col">現在の数値（20・60観測の変化）</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.factor} className={r.relevant ? undefined : "row-muted"}>
              <th scope="row">
                {r.title}
                <span className="small block">{r.why}</span>
              </th>
              <td>
                <ul className="plain">{r.tailwind_conditions.map((t) => <li key={t}>{t}</li>)}</ul>
              </td>
              <td>
                <ul className="plain">{r.headwind_conditions.map((t) => <li key={t}>{t}</li>)}</ul>
              </td>
              <td>
                <ul className="plain">{r.current.facts.map((t) => <li key={t}>{t}</li>)}</ul>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const RAW_KEYS: MacroKey[] = ["topix", "us10y", "us2y", "us_spread", "usdjpy", "jp10y", "call_rate"];

export function MacroSection({ data, code }: { data: StockMacroResponse; code: string }) {
  const p = data.profile;
  const it = data.macro.items;
  return (
    <section className="panel fin-section" id="macro-section" aria-labelledby="macro-section-title">
      <h2 id="macro-section-title">マクロ（金利・為替・業種の感応タグ）</h2>

      <h3>業種プロファイル</h3>
      <p>
        17業種：{data.sector17.name ?? "—"}　／　33業種：{data.sector33.name ?? "—"}
      </p>
      {p.state === "ok" ? (
        <>
          <ul className="chips" aria-label="業種の感応タグ">
            {p.cycle && (
              <li className="chip">
                <Term term="cyclical_defensive" code={code}>{p.cycle.label}</Term>
              </li>
            )}
            {p.tags.map((t) => (
              // neutral styling for every tag: no evaluative colour (要件 6.2)
              <li key={t.factor} className="chip" title={t.effect_label}>
                {t.label}
              </li>
            ))}
            {p.caution.flag && (
              <li className="chip chip-caution" title={p.caution.text ?? undefined}>
                <span aria-hidden="true">◆ </span>
                {p.caution.label}
              </li>
            )}
          </ul>
          {p.caution.flag && (
            <p className="caution-box">
              <strong>
                <span aria-hidden="true">◆ </span>
                {p.caution.label}
              </strong>
              ：{p.caution.text}（{p.caution.reason}）
            </p>
          )}
        </>
      ) : (
        <p className="note">{p.text}</p>
      )}
      <ul className="facts-list">
        {data.context.facts.map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ul>
      <Notes notes={[...p.notes, data.context.note]} />

      {data.checklist.rows.length > 0 && <ChecklistTable rows={data.checklist.rows} />}
      <Notes notes={data.checklist.notes} />

      <h3>主要マクロの推移</h3>
      <p className="note small">{data.macro.reference_label}</p>
      <MacroChart it={it.jp10y} />
      <MacroChart it={it.call_rate} height={160} />
      <MacroChart it={it.us10y} />
      <MacroChart it={it.usdjpy} />
      <Notes notes={data.macro.notes} />

      <details className="layer3">
        <summary>原数値・算出式・注記（マクロ）</summary>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>系列</th>
                <th className="num">値</th>
                <th>基準日</th>
                <th>更新周期</th>
                <th className="num">20</th>
                <th className="num">60</th>
                <th className="num">250</th>
                <th className="num">250観測内の位置</th>
              </tr>
            </thead>
            <tbody>
              {RAW_KEYS.map((k) => (
                <tr key={k}>
                  <td>
                    {it[k].title}
                    {it[k].series_id && it[k].series_id !== "topix" ? <code className="block">{it[k].series_id}</code> : null}
                  </td>
                  <td className="num">{valueText(it[k])}</td>
                  <td>{it[k].as_of ?? "—"}</td>
                  <td>{it[k].cadence_badge}</td>
                  {WINDOWS.map((n) => (
                    <td key={n} className="num">{it[k].state === "ok" ? changeText(it[k], n) : "—"}</td>
                  ))}
                  <td className="num">
                    {it[k].range_250.state === "ok" ? `${fmt(it[k].range_250.percentile ?? null, 1)}%` : "観測不足"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <ValuesTable
          rows={[
            ["変化", "最新値 − n観測前の値（TOPIXは C(t)÷C(t−n)−1 の%）。日次系列は営業日、月次系列は月で数える"],
            ["250観測内の位置", "P ＝ #{直近250観測のうち最新値以下の数} ÷ 250 × 100（250観測未満は表示しない）"],
            ["米長短スプレッド", "DGS10 − DGS2（両方に値がある日だけ）"],
            ["想定レートとの差", "ドル円 − 想定為替レート（設定値）、差の率 ＝ 差 ÷ 想定為替レート × 100"],
            [
              "「ほぼ横ばい」の幅",
              `金利 ±${data.checklist.thresholds.rate_flat_pt}ポイント未満、ドル円 ±${data.checklist.thresholds.fx_flat_pct}%未満、TOPIX ±${data.checklist.thresholds.topix_flat_pct}%未満`,
            ],
            [
              "基準日の強調",
              `日次系列は参照日より${data.macro.stale_rule.daily_business_days}営業日以上古いとき、月次系列は${data.macro.stale_rule.monthly_months}か月以上前のとき`,
            ],
          ]}
        />
        <ul className="notes">
          {RAW_KEYS.map((k) => (
            <li key={k}>
              {it[k].title}：{it[k].lag_note}（{it[k].source}）
            </li>
          ))}
        </ul>
        <p className="small">{data.macro.source_note}</p>
      </details>
    </section>
  );
}
