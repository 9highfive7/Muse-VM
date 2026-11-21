// 銘柄ページの財務・バリュエーション部分（要件 2.3・3.4〜3.7、フェーズ2）。
//
// * Every sentence that describes the company comes from the server
//   (templates.json). This file only lays out numbers, labels and the
//   server's sentences — no evaluative words, no recommendations (6.1).
// * Charts: one Y axis per panel; amounts / % / 倍 are separate panels (2.3).
// * 必須表示: 簡易フリーCFの注記とPBRの「推定値」バッジは常時表示（受け入れ条件⑥）.
import { useMemo, useState, type ReactNode } from "react";
import type { EChartsOption } from "echarts";
import type {
  AlertsBlock,
  AnnualRow,
  BusinessCard,
  FinancialsResponse,
  FinItem,
  Metric,
  Percentile,
  ProgressItem,
  RevisionYear,
  ValuationResponse,
  YoyItem,
} from "../api";
import EChart from "./EChart";
import { Term } from "./Glossary";
import { fmt, fmtSigned, INK } from "./chartOptions";
import {
  annualItemOption,
  balanceOption,
  CF_LOOK,
  cashFlowOption,
  equityRatioOption,
  fcfOption,
  FIN_COLORS,
  fmtYen,
  marginOption,
  ocfRatioOption,
  progressOption,
  quarterlyOption,
  valuationSeriesOption,
} from "./finChartOptions";
import { Notes, scrollToTarget, Swatch, ValuesTable } from "./parts";

type FinOk = Extract<FinancialsResponse, { state: "ok" }>;

const ITEMS: { key: FinItem; label: string; color: string }[] = [
  { key: "net_sales", label: "売上高", color: FIN_COLORS.teal },
  { key: "operating_profit", label: "営業利益", color: FIN_COLORS.purple },
  { key: "profit", label: "純利益", color: FIN_COLORS.ochre },
];

const PERIOD_TYPE_LABEL: Record<string, string> = { "1Q": "第1四半期", "2Q": "第2四半期", "3Q": "第3四半期", FY: "通期" };

const unavailable = (reason: string | null | undefined) =>
  reason === "未開示" ? "未開示" : `算出不可（${reason ?? "理由不明"}）`;

const pct = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? "—" : `${fmt(v, digits)}%`);

// ---------------------------------------------------------------- 事実フラグ帯（確認ポイント, 3.7）

const THRESHOLD_LABELS: Record<string, (v: number | boolean) => string> = {
  ocf_to_profit_below: (v) => `営業CF÷純利益が${v}倍未満（純利益が0より大きい場合のみ）`,
  progress_deviation_pt: (v) => `進捗率と過去3年同四半期の平均の差が±${v}ポイント以上`,
  progress_deviation_provisional: (v) => (v ? "進捗率の基準は暫定値" : "進捗率の基準は確定値"),
  payout_ratio_above_pct: (v) => `実績または予想の配当性向が${v}%超`,
  equity_ratio_drop_pt: (v) => `自己資本比率が前年同期比で${v}ポイント以上低下`,
  forecast_revision_abs_pct: (v) => `今期の通期予想（売上高・営業利益・純利益）の修正率の絶対値が${v}%以上`,
};

export function AlertsBand({ alerts, loading, error }: { alerts: AlertsBlock | null; loading: boolean; error: string | null }) {
  return (
    <section className="alerts-band" aria-labelledby="alerts-title">
      <h2 id="alerts-title">確認ポイント（決算サマリーから判定した事実）</h2>
      {loading && !alerts && <p className="note" role="status">決算サマリーを確認しています…</p>}
      {error && !alerts && <p className="note">確認ポイントを読み込めませんでした：{error}</p>}
      {alerts && (
        <>
          {alerts.items.length === 0 ? (
            <p>{alerts.none_text}</p>
          ) : (
            <ul className="alert-list">
              {alerts.items.map((a, i) => (
                <li key={`${a.rule}-${i}`}>
                  <span className="alert-text">確認ポイント：{a.text}</span>
                  {a.provisional && <span className="badge">暫定基準</span>}
                  <span className="alert-check">{a.check}</span>
                </li>
              ))}
            </ul>
          )}
          {alerts.notes && <Notes notes={alerts.notes} />}
          <details className="layer3">
            <summary>判定条件（閾値）</summary>
            <ul className="small">
              {Object.entries(alerts.thresholds).map(([k, v]) => (
                <li key={k}>{THRESHOLD_LABELS[k] ? THRESHOLD_LABELS[k](v) : `${k}: ${String(v)}`}</li>
              ))}
            </ul>
            <p className="small">閾値は設定ファイル（fin_rules.json）で管理している。</p>
          </details>
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- 第1階層 カード（業績方向・財務健全性・バリュエーション）

const CARD_TERMS: Record<BusinessCard["key"], [string, string][]> = {
  performance: [["yoy", "前年同期比"], ["progress_rate", "進捗率"], ["forecast_revision", "予想修正"]],
  health: [["equity_ratio", "自己資本比率"], ["operating_cf", "営業CF"], ["fcf", "簡易フリーCF"]],
  valuation: [
    ["per", "予想PER"], ["pbr", "PBR"], ["dividend_yield", "配当利回り"], ["valuation_percentile", "レンジ内の位置"],
    ["long_term_rate", "長期金利"], ["assumed_fx_rate", "想定為替レート"],
  ],
};

export function BusinessCardView({ card, code }: { card: BusinessCard; code: string }) {
  const id = `card-${card.key}-title`;
  return (
    <section className="card" aria-labelledby={id}>
      <h2 id={id}>{card.title}</h2>
      <ul className="card-lines">
        {card.sentences.map((s) => (
          <li key={s.key}>
            <button
              type="button"
              className={s.available ? "card-line" : "card-line unavailable"}
              onClick={() => scrollToTarget(s.target)}
            >
              {s.text}
            </button>
          </li>
        ))}
      </ul>
      {card.chips && card.chips.length > 0 && (
        <div className="card-chips">
          <ul className="chips" aria-label="業種の感応タグ">
            {card.chips.map((c) => (
              <li key={c} className="chip">{c}</li>
            ))}
            {card.caution?.flag && (
              <li className="chip chip-caution">
                <span aria-hidden="true">◆ </span>
                {card.caution.label}
              </li>
            )}
          </ul>
          {card.chips_note && <p className="note small">{card.chips_note}</p>}
        </div>
      )}
      {card.notes && card.notes.length > 0 && <Notes notes={card.notes} />}
      <p className="note small terms">
        用語：
        {CARD_TERMS[card.key].map(([t, l]) => (
          <Term key={t} term={t} code={code}>{l}</Term>
        ))}
      </p>
    </section>
  );
}

// ---------------------------------------------------------------- 第2・3階層

export default function FinancialSection({
  fin,
  val,
  code,
  onRefetch,
  refetching,
  refetchMessage,
}: {
  fin: FinancialsResponse;
  val: ValuationResponse | null;
  code: string;
  onRefetch: () => void;
  refetching: boolean;
  refetchMessage: string | null;
}) {
  return (
    <section aria-label="財務・バリュエーション" className="fin-section">
      <h2>業績・財務・バリュエーション（決算サマリー）</h2>
      {fin.state === "no_data" ? (
        <div className="placeholder">
          <p>この銘柄の決算サマリーが保存されていないため、業績・財務の数値は表示していません。</p>
        </div>
      ) : (
        <>
          <p className="note small">
            決算サマリーの最新開示：{fin.latest_statement?.disclosed_date ?? "—"}
            {fin.latest_statement ? `（${PERIOD_TYPE_LABEL[fin.latest_statement.period_type] ?? fin.latest_statement.period_type}、開示番号 ${fin.latest_statement.disclosure_no}）` : ""}
          </p>
          <AnnualPanel fin={fin} code={code} />
          <QuarterlyPanel fin={fin} code={code} />
          <ProgressPanel fin={fin} code={code} />
          <RevisionsPanel fin={fin} code={code} />
          <CashFlowPanel fin={fin} code={code} />
        </>
      )}
      <ValuationPanel val={val} code={code} />
      <details className="panel layer3">
        <summary>訂正検知履歴・決算サマリーの再取得</summary>
        <p className="small">{fin.source_note}</p>
        {fin.correction_log.length === 0 ? (
          <p className="small">保存済みの開示に対する訂正（同一開示番号での数値の変更）は検知されていません。</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>検知日時</th>
                  <th>開示番号</th>
                  <th>開示日</th>
                  <th>項目</th>
                  <th>旧値</th>
                  <th>新値</th>
                </tr>
              </thead>
              <tbody>
                {fin.correction_log.flatMap((c, i) =>
                  c.changes.map((ch, j) => (
                    <tr key={`${i}-${j}`}>
                      <td>{j === 0 ? c.detected_at : ""}</td>
                      <td>{j === 0 ? c.disclosure_no : ""}</td>
                      <td>{j === 0 ? c.disclosed_date ?? "—" : ""}</td>
                      <td>{ch.field}</td>
                      <td>{ch.old === null || ch.old === undefined || ch.old === "" ? "（空）" : String(ch.old)}</td>
                      <td>{ch.new === null || ch.new === undefined || ch.new === "" ? "（空）" : String(ch.new)}</td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </div>
        )}
        <p className="small">
          決算サマリーは7日ごとに自動で再取得します。手動で再取得すると、訂正の有無を確認できます（APIを1回呼びます）。
        </p>
        <button type="button" className="small-btn" onClick={onRefetch} disabled={refetching}>
          {refetching ? "再取得中…" : "決算サマリーを再取得"}
        </button>
        {refetchMessage && <p className="small" role="status">{refetchMessage}</p>}
      </details>
    </section>
  );
}

function FinPanel({ id, title, terms, children }: { id: string; title: string; terms: ReactNode; children: ReactNode }) {
  return (
    <section className="panel stage" id={id} aria-labelledby={`${id}-title`}>
      <h3 id={`${id}-title`}>{title}</h3>
      <p className="note small terms">用語：{terms}</p>
      {children}
    </section>
  );
}

function ForecastLegend() {
  return (
    <ul className="legend" aria-label="凡例">
      <li><Swatch color={INK} kind="filled" />実績（塗りつぶし）</li>
      <li><Swatch color={INK} kind="hollow" />今期予想（枠線のみ）</li>
      <li><span className="mk">基準変更</span>会計基準が前年度と異なる期</li>
    </ul>
  );
}

// ---------------------------------------------------------------- 通期業績推移

function yoyCell(v: YoyItem | undefined): string {
  if (!v) return "—";
  if (v.kind === "rate") return `${fmtSigned(v.pct, 1)}%`;
  if (v.kind === "turned_positive") return "率は算出しない（前年同期が0以下、当期は0超）";
  if (v.kind === "nonpositive_both") return "率は算出しない（前年同期が0以下）";
  if (v.kind === "not_disclosed") return "未開示";
  return "算出不可";
}

function AnnualPanel({ fin, code }: { fin: FinOk; code: string }) {
  const { rows, forecast } = fin.annual;
  const group = `fin-annual-${code}`;
  const opts = useMemo(
    () => ({
      items: ITEMS.map((it) => annualItemOption(rows, forecast, it.key, it.label, it.color)),
      margin: marginOption(rows, forecast),
    }),
    [rows, forecast],
  );
  const y = fin.yoy;
  return (
    <FinPanel
      id="fin-annual"
      title="通期業績の推移（最大5期＋今期予想）"
      terms={
        <>
          <Term term="operating_margin" code={code}>営業利益率</Term>
          <Term term="yoy" code={code}>前年同期比</Term>
        </>
      }
    >
      {y.state === "ok" && y.items ? (
        <div className="table-scroll">
          <table>
            <caption className="small">
              {y.period_label}の前年同期比（{y.disclosed_date}開示・基準日：{y.disclosed_date}）
            </caption>
            <thead>
              <tr>
                <th>項目</th>
                <th className="num">当期累計</th>
                <th className="num">前年同期累計</th>
                <th className="num">前年同期比</th>
              </tr>
            </thead>
            <tbody>
              {ITEMS.map((it) => {
                const v = y.items?.[it.key];
                return (
                  <tr key={it.key}>
                    <td>{it.label}</td>
                    <td className="num">{v?.current === null ? "未開示" : fmtYen(v?.current)}</td>
                    <td className="num">{v?.prior === null ? "未開示" : fmtYen(v?.prior)}</td>
                    <td className="num">{yoyCell(v)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : y.state === "unavailable" ? (
        <p className="note">{y.period_label}の前年同期比は算出不可（{y.reason ?? "理由不明"}）</p>
      ) : null}
      <ForecastLegend />
      {rows.length === 0 && !forecast ? (
        <p className="note">通期決算の開示が保存されていません。</p>
      ) : (
        ITEMS.map((it, i) => {
          const allNull = rows.every((r) => r[it.key] === null) && (!forecast || forecast[it.key] === null);
          return (
            <div key={it.key}>
              <h4 className="panel-label">{it.label}</h4>
              {allNull ? (
                <p className="note">{it.label}は未開示（この銘柄の決算サマリーに値がない）</p>
              ) : (
                <EChart option={opts.items[i]} group={group} height={200} label={`${it.label}の通期推移（実績と今期予想）`} />
              )}
            </div>
          );
        })
      )}
      <h4 className="panel-label"><Term term="operating_margin" code={code}>営業利益率</Term></h4>
      {rows.every((r) => r.operating_margin === null) && (!forecast || forecast.operating_margin === null) ? (
        <p className="note">営業利益率は算出不可（営業利益または売上高が未開示）</p>
      ) : (
        <>
          <ul className="legend" aria-label="凡例">
            <li><Swatch color={INK} kind="line" />実績（●）</li>
            <li><Swatch color={INK} kind="dashed-line" />今期予想（○）</li>
          </ul>
          <EChart option={opts.margin} group={group} height={180} label="営業利益率の通期推移" />
        </>
      )}
      <Notes notes={fin.annual.notes.slice(0, 2)} />
      <details className="layer3">
        <summary>原数値・算出式（通期）</summary>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>年度</th>
                <th>開示日</th>
                <th>開示番号</th>
                <th>会計基準</th>
                <th className="num">売上高</th>
                <th className="num">営業利益</th>
                <th className="num">純利益</th>
                <th className="num">営業利益率</th>
                <th className="num">EPS（円）</th>
                <th className="num">年間配当（円）</th>
                <th>印</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.fy_end}>
                  <td>{r.fiscal_year}</td>
                  <td>{r.disclosed_date}</td>
                  <td>{r.disclosure_no}</td>
                  <td>{r.accounting_standard ?? "—"}</td>
                  <td className="num">{r.net_sales === null ? "未開示" : fmtYen(r.net_sales)}</td>
                  <td className="num">{r.operating_profit === null ? "未開示" : fmtYen(r.operating_profit)}</td>
                  <td className="num">{r.profit === null ? "未開示" : fmtYen(r.profit)}</td>
                  <td className="num">{r.operating_margin === null ? "算出不可" : pct(r.operating_margin)}</td>
                  <td className="num">{fmt(r.eps, 2)}</td>
                  <td className="num">{fmt(r.dividend_annual, 2)}</td>
                  <td>{[r.standard_changed ? "基準変更" : "", r.irregular_year ? `変則決算（${r.months ?? "?"}か月）` : ""].filter(Boolean).join("・")}</td>
                </tr>
              ))}
              {forecast && (
                <tr>
                  <td>{forecast.fiscal_year}（今期予想）</td>
                  <td>{forecast.disclosed_date}</td>
                  <td>{forecast.disclosure_no}</td>
                  <td>—</td>
                  <td className="num">{forecast.net_sales === null ? "未開示" : fmtYen(forecast.net_sales)}</td>
                  <td className="num">{forecast.operating_profit === null ? "未開示" : fmtYen(forecast.operating_profit)}</td>
                  <td className="num">{forecast.profit === null ? "未開示" : fmtYen(forecast.profit)}</td>
                  <td className="num">{forecast.operating_margin === null ? "算出不可" : pct(forecast.operating_margin)}</td>
                  <td className="num">—</td>
                  <td className="num">—</td>
                  <td>予想</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="small">
          営業利益率 ＝ 営業利益 ÷ 売上高 × 100。前年同期比 ＝ (当期累計 − 前年同期累計) ÷ |前年同期累計| × 100（前年同期が0以下なら率は出さない）。
          今期予想は {forecast ? `${forecast.disclosed_date}開示（${forecast.disclosure_no}）` : "未開示"} の最新値。{fin.source_note}
        </p>
      </details>
    </FinPanel>
  );
}

// ---------------------------------------------------------------- 四半期単独値

function QuarterlyPanel({ fin, code }: { fin: FinOk; code: string }) {
  const rows = fin.quarterly.rows;
  const group = `fin-quarterly-${code}`;
  const opts = useMemo(() => ITEMS.map((it) => quarterlyOption(rows, it.key, it.label, it.color)), [rows]);
  return (
    <FinPanel
      id="fin-quarterly"
      title="四半期単独値（過去8四半期）"
      terms={<Term term="quarterly_standalone" code={code}>四半期単独値</Term>}
    >
      {rows.length === 0 ? (
        <p className="note">四半期の開示が保存されていません。</p>
      ) : (
        <>
          <ul className="legend" aria-label="凡例">
            <li><span className="mk">※</span>第4四半期（通期−第3四半期累計）</li>
            <li>棒がない四半期は算出不可（理由は下の表）</li>
          </ul>
          {ITEMS.map((it, i) =>
            rows.every((q) => q.values[it.key] === null) ? (
              <p key={it.key} className="note">{it.label}の四半期単独値：{rows[rows.length - 1].reasons[it.key] ?? "算出不可"}</p>
            ) : (
              <div key={it.key}>
                <h4 className="panel-label">{it.label}（単独）</h4>
                <EChart option={opts[i]} group={group} height={190} label={`${it.label}の四半期単独値`} />
              </div>
            ),
          )}
        </>
      )}
      <Notes notes={fin.quarterly.notes} />
      <details className="layer3">
        <summary>原数値・算出式（四半期単独値）</summary>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>四半期</th>
                <th>期間末</th>
                <th>開示日</th>
                <th>開示番号（前四半期）</th>
                {ITEMS.map((it) => (
                  <th key={it.key} className="num">{it.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...rows].reverse().map((q) => (
                <tr key={q.disclosure_no}>
                  <td>{q.label}{q.special_factors_note ? " ※" : ""}</td>
                  <td>{q.period_end}</td>
                  <td>{q.disclosed_date}</td>
                  <td>{q.disclosure_no}（{q.prev_disclosure_no ?? "—"}）</td>
                  {ITEMS.map((it) => (
                    <td key={it.key} className="num">
                      {q.values[it.key] === null ? unavailable(q.reasons[it.key]) : fmtYen(q.values[it.key])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="small">
          Q1単独＝1Q累計、Qk単独＝kQ累計−(k−1)Q累計（k=2,3）、Q4単独＝通期−3Q累計。同一会計年度・同一会計基準・同一の連結区分の場合だけ算出する。
        </p>
      </details>
    </FinPanel>
  );
}

// ---------------------------------------------------------------- 進捗率

function progressCell(it: ProgressItem): string {
  return it.actual_pct === null ? unavailable(it.actual_reason) : pct(it.actual_pct);
}

function patternCell(it: ProgressItem): string {
  if (it.pattern_state !== "ok")
    return `比較不足（比較できる年度${it.pattern_n_valid}年分、必要${it.pattern_required}年分）`;
  return `${pct(it.pattern_min_pct)}〜${pct(it.pattern_max_pct)}（平均${pct(it.pattern_avg_pct)}）`;
}

function ProgressPanel({ fin, code }: { fin: FinOk; code: string }) {
  const p = fin.progress;
  const primary = p.primary_item ? p.items[p.primary_item] : undefined;
  const option = useMemo(
    () => (primary && p.fiscal_year && p.period_label ? progressOption(primary, p.fiscal_year, p.period_label) : null),
    [primary, p.fiscal_year, p.period_label],
  );
  return (
    <FinPanel id="fin-progress" title="進捗率（今期の通期予想に対する累計実績）" terms={<Term term="progress_rate" code={code}>進捗率</Term>}>
      {p.state === "not_applicable" ? (
        <p className="note">直近の開示は通期決算のため、今期の進捗率は次の第1四半期の開示から算出します（基準日：{p.disclosed_date ?? "—"}）。</p>
      ) : p.state !== "ok" ? (
        <p className="note">進捗率を算出できる開示がありません。</p>
      ) : (
        <>
          <p className="small">
            {p.period_label}・{p.fiscal_year}（{p.disclosed_date}開示、開示番号 {p.disclosure_no}）。過去3年は同じ四半期の累計実績 ÷ 同年度の通期実績。
          </p>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>項目</th>
                  <th className="num">累計実績</th>
                  <th className="num">通期予想（開示日）</th>
                  <th className="num">進捗率</th>
                  <th className="num">過去3年同四半期</th>
                  <th className="num">平均との差</th>
                </tr>
              </thead>
              <tbody>
                {ITEMS.map(({ key, label }) => {
                  const it = p.items[key];
                  if (!it) return null;
                  return (
                    <tr key={key}>
                      <td>{label}</td>
                      <td className="num">{it.cumulative === null ? "未開示" : fmtYen(it.cumulative)}</td>
                      <td className="num">
                        {it.forecast === null ? "未開示" : fmtYen(it.forecast)}
                        {it.forecast_disclosed_date ? `（${it.forecast_disclosed_date}）` : ""}
                      </td>
                      <td className="num">{progressCell(it)}</td>
                      <td className="num">{patternCell(it)}</td>
                      <td className="num">
                        {it.deviation_pt === null ? "—" : `${fmtSigned(it.deviation_pt, 1)}ポイント`}
                        {it.beyond_threshold ? `（±${p.threshold_pt}ポイント以上）` : ""}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {primary && option && (
            <>
              <h4 className="panel-label">
                {primary.label}の進捗率：今期と過去3年の同四半期
                {p.primary_item === "profit" ? "（営業利益が未開示のため純利益）" : ""}
              </h4>
              <ul className="legend" aria-label="凡例">
                <li><Swatch color={FIN_COLORS.teal} kind="filled" />今期（通期予想に対する比率）</li>
                <li><Swatch color={FIN_COLORS.gray} kind="filled" />過去の年度（通期実績に対する比率）</li>
                {primary.pattern_avg_pct !== null && (
                  <li><Swatch color="#8a8a8a" kind="dashed-line" />過去{primary.pattern_n_valid}年の平均（{pct(primary.pattern_avg_pct)}）</li>
                )}
              </ul>
              <EChart option={option} group={`fin-progress-${code}`} height={220} label="進捗率の今期と過去3年の比較" />
            </>
          )}
        </>
      )}
      <Notes notes={p.notes} />
      {p.state === "ok" && (
        <details className="layer3">
          <summary>原数値・算出式（過去3年の進捗率）</summary>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>項目</th>
                  <th>年度</th>
                  <th className="num">同四半期累計</th>
                  <th className="num">通期実績</th>
                  <th className="num">実績比</th>
                  <th className="num">当時の予想比（補助）</th>
                </tr>
              </thead>
              <tbody>
                {ITEMS.flatMap(({ key, label }) =>
                  (p.items[key]?.pattern_years ?? []).map((y) => (
                    <tr key={`${key}-${y.fy_end}`}>
                      <td>{label}</td>
                      <td>{y.fiscal_year}</td>
                      <td className="num">{y.cumulative === null ? "未開示" : fmtYen(y.cumulative)}</td>
                      <td className="num">{y.full_year === null ? "未開示" : fmtYen(y.full_year)}</td>
                      <td className="num">{y.pct === null ? unavailable(y.reason) : pct(y.pct)}</td>
                      <td className="num">{pct(y.pct_vs_forecast_then)}</td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </div>
          <p className="small">
            進捗率（今期）＝ kQ累計 ÷ その開示時点の通期予想 × 100（分母が0以下なら算出不可）。過去パターン ＝ 過去3年度の kQ累計実績 ÷ 同年度の通期実績 × 100。
            差 ＝ 今期 − 過去3年の平均（ポイント）。過去が2年分以下なら比較不足。
          </p>
        </details>
      )}
    </FinPanel>
  );
}

// ---------------------------------------------------------------- 予想修正履歴

const KIND_LABEL: Record<string, string> = { initial: "期初予想", revision: "修正", unchanged: "変更なし", forecast: "予想" };
const FC_COLS: { key: "net_sales" | "operating_profit" | "profit" | "dividend"; label: string }[] = [
  { key: "net_sales", label: "売上高" },
  { key: "operating_profit", label: "営業利益" },
  { key: "profit", label: "純利益" },
  { key: "dividend", label: "年間配当" },
];

function RevisionYearTable({ y }: { y: RevisionYear }) {
  const cell = (k: (typeof FC_COLS)[number]["key"], v: number | undefined, rate: number | null | undefined) => {
    if (v === undefined) return "—";
    const base = k === "dividend" ? `${fmt(v, 2)}円` : fmtYen(v);
    if (rate === undefined) return base;
    return rate === null ? `${base}（前回0のため率なし）` : `${base}（${fmtSigned(rate, 1)}%）`;
  };
  return (
    <div className="table-scroll">
      <table>
        <caption className="small">
          {y.fiscal_year}：{y.completed ? "完了" : "進行中"}・修正{y.revisions.length}件
          {!y.initial_from_prior_fy_statement && y.timeline.length > 0 ? "（前年度の通期決算が未取得のため、最初に取得できた開示の予想を期初予想としている）" : ""}
        </caption>
        <thead>
          <tr>
            <th>開示日</th>
            <th>区分</th>
            {FC_COLS.map((c) => (
              <th key={c.key} className="num">{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {y.timeline.map((e) => (
            <tr key={e.disclosure_no}>
              <td>{e.disclosed_date}</td>
              <td>{KIND_LABEL[e.kind] ?? e.kind}</td>
              {FC_COLS.map((c) => (
                <td key={c.key} className="num">{cell(c.key, e.values[c.key], e.rates[c.key])}</td>
              ))}
            </tr>
          ))}
          {y.actual && (
            <tr className="row-actual">
              <td>{y.actual.disclosed_date}</td>
              <td>実績</td>
              {FC_COLS.map((c) => (
                <td key={c.key} className="num">
                  {c.key === "dividend" ? "—" : y.actual!.values[c.key] === null ? "未開示" : fmtYen(y.actual!.values[c.key])}
                </td>
              ))}
            </tr>
          )}
        </tbody>
      </table>
      {y.completed && (
        <p className="small">
          期初予想比の着地率（営業利益）：{y.landing_pct_operating_profit === null ? "算出不可" : `${fmtSigned(y.landing_pct_operating_profit, 1)}%`}
        </p>
      )}
    </div>
  );
}

function RevisionsPanel({ fin, code }: { fin: FinOk; code: string }) {
  const r = fin.revisions;
  const [all, setAll] = useState(false);
  const shown = all ? r.years : r.years.slice(0, 2);
  return (
    <FinPanel
      id="fin-revisions"
      title="予想修正の履歴（期初予想→各修正→実績）"
      terms={
        <>
          <Term term="forecast_revision" code={code}>予想修正</Term>
          <Term term="forecast_type" code={code}>予想の型</Term>
        </>
      }
    >
      <dl className="type-box">
        <dt>予想の型（過去の事実の集計、完了済み{r.type.n}期）</dt>
        <dd>{r.type.text}</dd>
        <dd className="small">{r.type.counts_text}</dd>
      </dl>
      <Notes notes={r.notes} />
      {r.years.length === 0 ? (
        <p className="note">通期予想の開示が保存されていません。</p>
      ) : (
        <>
          {shown.map((y) => (
            <RevisionYearTable key={y.fy_end} y={y} />
          ))}
          {r.years.length > 2 && (
            <button type="button" className="small-btn" onClick={() => setAll(!all)}>
              {all ? "直近2年度だけ表示" : `全${r.years.length}年度を表示`}
            </button>
          )}
        </>
      )}
      <details className="layer3">
        <summary>原数値・算出式（予想の型）</summary>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>年度</th>
                <th className="num">期初予想比の着地率（営業利益）</th>
                <th>営業利益予想の上方修正</th>
              </tr>
            </thead>
            <tbody>
              {r.type.years.map((y) => (
                <tr key={y.fy_end}>
                  <td>{y.fiscal_year}</td>
                  <td className="num">{y.landing_pct === null ? "算出不可" : `${fmtSigned(y.landing_pct, 1)}%`}</td>
                  <td>{y.upward_revision ? "あり" : "なし"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="small">
          修正率 ＝ (新予想 − 旧予想) ÷ |旧予想| × 100。着地率 ＝ (通期実績 − 期初予想) ÷ |期初予想| × 100。
          ±{r.type.threshold_pct}%を境に「上回った・下回った・差が小さい」を数え、完了済み年度が3期未満なら型は判定しない。
        </p>
      </details>
    </FinPanel>
  );
}

// ---------------------------------------------------------------- 簡易財務・キャッシュフロー

function CashFlowPanel({ fin, code }: { fin: FinOk; code: string }) {
  const rows: AnnualRow[] = fin.annual.rows;
  const cf = fin.cf;
  const eq = fin.equity_ratio;
  const group = `fin-cf-${code}`;
  const opts = useMemo(
    () => ({
      balance: balanceOption(rows),
      eqr: equityRatioOption(rows),
      cf: cashFlowOption(rows),
      fcf: fcfOption(rows),
      ocf: ocfRatioOption(rows, cf.guideposts?.reference ?? 1, cf.guideposts?.check ?? 0.5),
    }),
    [rows, cf.guideposts],
  );
  const anyCf = rows.some((r) => r.cf_operating !== null || r.cf_investing !== null || r.cf_financing !== null);
  return (
    <FinPanel
      id="fin-cf"
      title="簡易財務・キャッシュフロー（通期）"
      terms={
        <>
          <Term term="equity_ratio" code={code}>自己資本比率</Term>
          <Term term="cash_flow" code={code}>キャッシュフロー</Term>
          <Term term="operating_cf" code={code}>営業CF÷純利益</Term>
          <Term term="fcf" code={code}>簡易フリーCF</Term>
        </>
      }
    >
      {eq.state === "ok" && (
        <p className="small">
          直近の自己資本比率：{pct(eq.current_pct)}（{eq.period_type ? PERIOD_TYPE_LABEL[eq.period_type] : ""}末、{eq.disclosed_date}開示）
          {eq.diff_pt === null || eq.diff_pt === undefined
            ? `、前年同期との差は算出不可（${eq.reason ?? "理由不明"}）`
            : `、前年同期 ${pct(eq.prior_pct)} との差 ${fmtSigned(eq.diff_pt, 1)}ポイント`}
        </p>
      )}
      {rows.length === 0 ? (
        <p className="note">通期決算の開示が保存されていません。</p>
      ) : (
        <>
          <h4 className="panel-label">総資産・自己資本</h4>
          <ul className="legend" aria-label="凡例">
            <li><Swatch color={FIN_COLORS.gray} kind="filled" />総資産</li>
            <li><Swatch color={FIN_COLORS.teal} kind="filled" />自己資本</li>
          </ul>
          <EChart option={opts.balance} group={group} height={190} label="総資産と自己資本の推移" />
          <h4 className="panel-label"><Term term="equity_ratio" code={code}>自己資本比率</Term>（期末）</h4>
          <EChart option={opts.eqr} group={group} height={170} label="自己資本比率の推移" />
          <Notes notes={fin.annual.notes.slice(2, 3)} />

          <h4 className="panel-label"><Term term="cash_flow" code={code}>営業・投資・財務キャッシュフロー</Term></h4>
          {anyCf ? (
            <>
              <ul className="legend" aria-label="凡例">
                {Object.values(CF_LOOK).map((c) => (
                  <li key={c.label}><Swatch color={c.color} kind="filled" />{c.label}</li>
                ))}
              </ul>
              <EChart option={opts.cf} group={group} height={210} label="営業・投資・財務キャッシュフローの推移" />
              <h4 className="panel-label"><Term term="fcf" code={code}>簡易フリーCF</Term>（営業CF＋投資CF）</h4>
              <EChart option={opts.fcf} group={group} height={170} label="簡易フリーキャッシュフローの推移" />
              <h4 className="panel-label"><Term term="operating_cf" code={code}>営業CF÷純利益</Term></h4>
              <EChart option={opts.ocf} group={group} height={170} label="営業CF÷純利益の推移" />
            </>
          ) : (
            <p className="note">通期のキャッシュフローは未開示です。</p>
          )}
        </>
      )}
      {cf.state === "ok" && (
        <ValuesTable
          rows={[
            [`営業CF（${cf.fiscal_year}）`, fmtYen(cf.cf_operating)],
            ["投資CF", fmtYen(cf.cf_investing)],
            ["財務CF", fmtYen(cf.cf_financing)],
            ["純利益", fmtYen(cf.profit)],
            ["営業CF÷純利益", cf.ocf_to_profit === null || cf.ocf_to_profit === undefined ? unavailable(cf.ocf_to_profit_reason) : `${fmt(cf.ocf_to_profit, 2)}倍`],
            ["簡易フリーCF（営業CF＋投資CF）", cf.simple_fcf === null || cf.simple_fcf === undefined ? unavailable(cf.simple_fcf_reason ?? "営業CFまたは投資CFが未開示") : fmtYen(cf.simple_fcf)],
            ["CFの符号", cf.pattern_label ?? "—"],
            ["開示日（開示番号）", `${cf.disclosed_date}（${cf.disclosure_no}）`],
          ]}
        />
      )}
      {/* 必須表示（受け入れ条件⑥）: always visible, never collapsed */}
      <Notes notes={cf.notes} />
      <details className="layer3">
        <summary>原数値・算出式（財務・CF）</summary>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>年度</th>
                <th className="num">総資産</th>
                <th className="num">自己資本</th>
                <th className="num">自己資本比率</th>
                <th className="num">営業CF</th>
                <th className="num">投資CF</th>
                <th className="num">財務CF</th>
                <th className="num">簡易フリーCF</th>
                <th className="num">営業CF÷純利益</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.fy_end}>
                  <td>{r.fiscal_year}</td>
                  <td className="num">{fmtYen(r.total_assets)}</td>
                  <td className="num">{fmtYen(r.equity)}</td>
                  <td className="num">{pct(r.equity_ratio_pct)}</td>
                  <td className="num">{r.cf_operating === null ? "未開示" : fmtYen(r.cf_operating)}</td>
                  <td className="num">{r.cf_investing === null ? "未開示" : fmtYen(r.cf_investing)}</td>
                  <td className="num">{r.cf_financing === null ? "未開示" : fmtYen(r.cf_financing)}</td>
                  <td className="num">{r.simple_fcf === null ? "算出不可" : fmtYen(r.simple_fcf)}</td>
                  <td className="num">{r.ocf_to_profit === null ? "算出不可" : `${fmt(r.ocf_to_profit, 2)}倍`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="small">
          自己資本比率は開示値。営業CF÷純利益 ＝ 通期営業CF ÷ 通期純利益（純利益が0以下なら算出しない）。簡易フリーCF ＝ 営業CF ＋ 投資CF。{fin.source_note}
        </p>
      </details>
    </FinPanel>
  );
}

// ---------------------------------------------------------------- バリュエーション

function percentileText(p: Percentile | undefined): string {
  if (!p) return "—";
  if (p.state === "ok")
    return `下から${fmt(p.percentile, 0)}%の位置（${(p.from ?? "").slice(0, 7)}〜${(p.to ?? "").slice(0, 7)}、${p.n.toLocaleString("ja-JP")}営業日）`;
  if (p.state === "current_unavailable") return "現在の値が算出不可のため表示しない";
  return `観測不足のため表示しない（${p.n.toLocaleString("ja-JP")}営業日、必要${(p.min_observations ?? 250).toLocaleString("ja-JP")}営業日）`;
}

/** "今期予想純利益 112億円（2026-11-10開示）"; "—" when nothing was disclosed. */
function used(label: string, value: string, date: unknown): string {
  if (date === null || date === undefined || date === "") return "—";
  return `${label}${value ? ` ${value}` : ""}（${String(date)}開示）`;
}

function metricValue(m: Metric, unit: string, digits = 2): string {
  return m.value === null ? `算出不可（${m.reason ?? "理由不明"}）` : `${fmt(m.value, digits)}${unit}`;
}

function ValuationPanel({ val, code }: { val: ValuationResponse | null; code: string }) {
  const group = `fin-val-${code}`;
  const opts = useMemo(() => {
    if (!val || val.series.dates.length === 0) return null;
    const s = val.series;
    const any = (xs: (number | null)[]) => xs.some((x) => x !== null);
    // the last drawn panel carries the date labels
    const showXFor = any(s.dividend_yield) ? "yld" : any(s.pbr) ? "pbr" : "per";
    return {
      per: any(s.per) ? valuationSeriesOption(s.dates, s.per, "予想PER", "倍", val.per.percentile, showXFor === "per") : null,
      pbr: any(s.pbr) ? valuationSeriesOption(s.dates, s.pbr, "PBR（推定）", "倍", val.pbr.percentile, showXFor === "pbr") : null,
      yld: any(s.dividend_yield)
        ? valuationSeriesOption(s.dates, s.dividend_yield, "予想配当利回り", "%", val.dividend_yield.percentile, true)
        : null,
    };
  }, [val]);
  return (
    <FinPanel
      id="fin-valuation"
      title="バリュエーション（自社の過去との比較）"
      terms={
        <>
          <Term term="per" code={code}>予想PER</Term>
          <Term term="pbr" code={code}>PBR</Term>
          <Term term="roe" code={code}>ROE</Term>
          <Term term="dividend_yield" code={code}>予想配当利回り</Term>
          <Term term="payout_ratio" code={code}>配当性向</Term>
          <Term term="valuation_percentile" code={code}>レンジ内の位置</Term>
        </>
      }
    >
      {/* 必須表示（受け入れ条件⑥）: PBR estimate badge, always visible */}
      <p>
        <span className="badge">PBRは推定値</span>
        <span className="small note">　時価総額 ÷ 自己資本（推定株式数＝時価総額÷終値）で求めている。</span>
      </p>
      {!val ? (
        <p className="note" role="status">読み込み中…</p>
      ) : val.state !== "ok" ? (
        <div className="placeholder">
          <p>{val.per.reason ?? "日足が未取得"}のため、PER・PBR・配当利回りは表示していません。</p>
        </div>
      ) : (
        <>
          <div className="table-scroll">
            <table>
              <caption className="small">基準日：{val.as_of}（終値・時価総額）</caption>
              <thead>
                <tr>
                  <th>指標</th>
                  <th className="num">値</th>
                  <th>自社過去レンジ内の位置</th>
                  <th>使用した開示</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td><Term term="per" code={code}>予想PER</Term></td>
                  <td className="num">{metricValue(val.per, "倍")}</td>
                  <td>{val.per.value === null ? "—" : percentileText(val.per.percentile)}</td>
                  <td className="small">{used("今期予想純利益", fmtYen(val.per.inputs?.forecast_profit as number | null), val.per.inputs?.forecast_disclosed_date)}</td>
                </tr>
                <tr>
                  <td><Term term="pbr" code={code}>PBR</Term> <span className="badge">推定</span></td>
                  <td className="num">{metricValue(val.pbr, "倍")}</td>
                  <td>{val.pbr.value === null ? "—" : percentileText(val.pbr.percentile)}</td>
                  <td className="small">{used("自己資本", fmtYen(val.pbr.inputs?.equity as number | null), val.pbr.inputs?.equity_disclosed_date)}</td>
                </tr>
                <tr>
                  <td><Term term="dividend_yield" code={code}>予想配当利回り</Term></td>
                  <td className="num">{metricValue(val.dividend_yield, "%")}</td>
                  <td>{val.dividend_yield.value === null ? "—" : percentileText(val.dividend_yield.percentile)}</td>
                  <td className="small">{used("予想年間配当", `${fmt(val.dividend_yield.inputs?.dividend_forecast as number | null, 2)}円`, val.dividend_yield.inputs?.dividend_disclosed_date)}</td>
                </tr>
                <tr>
                  <td><Term term="roe" code={code}>ROE</Term>（実績）</td>
                  <td className="num">{metricValue(val.roe.actual, "%")}</td>
                  <td>—</td>
                  <td className="small">{used(val.roe.actual.fiscal_year ?? "", "", val.roe.actual.as_of)}</td>
                </tr>
                <tr>
                  <td>ROE（予想・参考） <span className="badge">推定</span></td>
                  <td className="num">{metricValue(val.roe.forecast, "%")}</td>
                  <td>—</td>
                  <td className="small">{used("通期予想", "", val.roe.forecast.as_of)}</td>
                </tr>
                <tr>
                  <td><Term term="payout_ratio" code={code}>配当性向</Term>（実績）</td>
                  <td className="num">{metricValue(val.payout.actual, "%", 1)}</td>
                  <td>—</td>
                  <td className="small">{used(val.payout.actual.fiscal_year ?? "", "", val.payout.actual.as_of)}</td>
                </tr>
                <tr>
                  <td>配当性向（予想）</td>
                  <td className="num">{metricValue(val.payout.forecast, "%", 1)}</td>
                  <td>—</td>
                  <td className="small">{used(val.payout.forecast.fiscal_year ?? "", "", val.payout.forecast.as_of)}</td>
                </tr>
              </tbody>
            </table>
          </div>
          {opts && !opts.per && !opts.pbr && !opts.yld && (
            <p className="note">表示期間に予想PER・PBR・予想配当利回りを算出できる日がないため、日次の推移は表示していません。</p>
          )}
          {opts && (opts.per || opts.pbr || opts.yld) && (
            <>
              <ul className="legend" aria-label="凡例">
                <li><Swatch color={INK} kind="line" />日々の値（その日までに開示された数値で計算）</li>
                <li><Swatch color="#8a8a8a" kind="dashed-line" />自社過去レンジの25%点・中央値・75%点（観測が250営業日以上の場合）</li>
              </ul>
              <h4 className="panel-label"><Term term="per" code={code}>予想PER</Term></h4>
              <SeriesOrNote option={opts.per} metric={val.per} group={group} height={170} label="予想PERの日次推移" unit="倍" />
              <h4 className="panel-label"><Term term="pbr" code={code}>PBR</Term>（推定）</h4>
              <SeriesOrNote option={opts.pbr} metric={val.pbr} group={group} height={170} label="PBR（推定）の日次推移" unit="倍" />
              <h4 className="panel-label"><Term term="dividend_yield" code={code}>予想配当利回り</Term></h4>
              <SeriesOrNote option={opts.yld} metric={val.dividend_yield} group={group} height={190} label="予想配当利回りの日次推移" unit="%" />
              <p className="note small">線が途切れている期間は算出不可（予想純利益が0以下・未開示など）。</p>
            </>
          )}
        </>
      )}
      {val && <Notes notes={[...val.notes, ...val.roe.notes]} />}
      {val && val.state === "ok" && (
        <details className="layer3">
          <summary>原数値・算出式（バリュエーション）</summary>
          <ValuesTable
            rows={[
              ["時価総額", `${fmtYen(val.market_cap.value_yen)}${val.market_cap.unit_estimated ? "（単位は推定）" : ""}`],
              ["推定株式数（時価総額÷終値）", val.estimated_shares.value === null ? "—" : `${fmt(val.estimated_shares.value, 0)}株`],
              ["推定BPS（自己資本÷推定株式数）", `${fmt(val.pbr.inputs?.estimated_bps as number | null, 2)}円`],
              ["終値（未調整）", `${fmt(val.pbr.inputs?.close_unadjusted as number | null, 1)}円`],
              ["分割調整係数（予想配当の開示日→基準日）", fmt(val.dividend_yield.inputs?.adjustment_multiplier as number | null, 6)],
              ["ROEの純利益", fmtYen(val.roe.actual.inputs?.profit as number | null)],
              ["ROEの自己資本（期首／期末）", `${fmtYen(val.roe.actual.inputs?.equity_start as number | null)}／${fmtYen(val.roe.actual.inputs?.equity_end as number | null)}`],
              ["予想PERの25%点／中央値／75%点", pctBand(val.per.percentile)],
              ["PBRの25%点／中央値／75%点", pctBand(val.pbr.percentile)],
              ["配当利回りの25%点／中央値／75%点", pctBand(val.dividend_yield.percentile)],
            ]}
          />
          <p className="small">
            予想PER ＝ 時価総額 ÷ 今期の通期予想純利益。PBR（推定）＝ 時価総額 ÷ 自己資本。予想配当利回り ＝ 予想年間配当 × 分割調整係数 ÷ 未調整終値 × 100。
            ROE ＝ 純利益 ÷ ((期首自己資本 ＋ 期末自己資本) ÷ 2) × 100。配当性向 ＝ 同一開示内の 年間配当 ÷ EPS × 100。
            レンジ内の位置 P ＝ #{"{"}x_i ≤ 現在値{"}"} ÷ N × 100（最大5年、算出不可の日は除外、N＜250は表示しない）。
          </p>
          <p className="small">{val.source_note}</p>
        </details>
      )}
    </FinPanel>
  );
}

function SeriesOrNote({
  option,
  metric,
  group,
  height,
  label,
  unit,
}: {
  option: EChartsOption | null;
  metric: Metric;
  group: string;
  height: number;
  label: string;
  unit: string;
}) {
  if (!option) return <p className="note small">表示期間に算出できる日がない（{metric.reason ?? "算出不可"}）</p>;
  return (
    <>
      <BandCaption p={metric.percentile} unit={unit} />
      <EChart option={option} group={group} height={height} label={label} />
    </>
  );
}

function BandCaption({ p, unit }: { p: Percentile | undefined; unit: string }) {
  if (!p || p.state !== "ok")
    return <p className="note small">破線なし（自社過去レンジは{percentileText(p)}）</p>;
  return (
    <p className="note small">
      破線（下から）：25%点 {fmt(p.p25, 2)}{unit}／中央値 {fmt(p.p50, 2)}{unit}／75%点 {fmt(p.p75, 2)}{unit}
      （{(p.from ?? "").slice(0, 7)}〜{(p.to ?? "").slice(0, 7)}、{p.n.toLocaleString("ja-JP")}営業日）
    </p>
  );
}

function pctBand(p: Percentile | undefined): string {
  if (!p || p.state !== "ok") return "—";
  return `${fmt(p.p25, 2)}／${fmt(p.p50, 2)}／${fmt(p.p75, 2)}`;
}
