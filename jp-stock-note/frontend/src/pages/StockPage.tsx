import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  api,
  ApiError,
  type BusinessCardsResponse,
  type ChartResponse,
  type EnsureFinsResponse,
  type EnsureHistoryResponse,
  type FinancialsResponse,
  type HeaderResponse,
  type MaKey,
  type PriceCard,
  type RangeKey,
  type StockMacroResponse,
  type TechnicalsResponse,
  type ValuationResponse,
} from "../api";
import EChart from "../components/EChart";
import FinancialSection, { AlertsBand, BusinessCardView } from "../components/FinancialSection";
import { MacroSection } from "../components/Macro";
import { Facts, Notes, scrollToTarget, ValuesTable } from "../components/parts";
import { Term } from "../components/Glossary";
import {
  bollingerOption,
  fmt,
  fmtSigned,
  indexedOption,
  macdOption,
  MA_LOOK,
  priceVolumeOption,
  rsiOption,
  rsLineOption,
  trendOption,
  DOWN,
  UP,
} from "../components/chartOptions";
import { Link } from "../router";
import { StockMemoSection } from "../components/Memo";
import { StockWatchControl } from "../components/Watch";
import TechnicalView from "../components/TechnicalView";

const yen = (v: number) => v.toLocaleString("ja-JP", { maximumFractionDigits: 2 });
const signed = (v: number, digits: number) =>
  v.toLocaleString("ja-JP", { minimumFractionDigits: digits, maximumFractionDigits: digits, signDisplay: "exceptZero" });

const RANGES: { key: RangeKey; label: string }[] = [
  { key: "3m", label: "3か月" },
  { key: "6m", label: "6か月" },
  { key: "1y", label: "1年" },
  { key: "3y", label: "3年" },
  { key: "5y", label: "5年" },
];

const ENDPOINT_NOTE = "データ出所：日足 /v2/equities/bars/daily（調整後OHLC・調整後出来高・ストップ高安フラグ）、TOPIX /v2/indices/bars/daily/topix、決算開示日 /v2/fins/summary";

// S3 銘柄ページ。前日比に色の強調・点滅は付けない（2.3）。
export default function StockPage({ code }: { code: string }) {
  const [header, setHeader] = useState<HeaderResponse | null>(null);
  const [headerError, setHeaderError] = useState<string | null>(null);
  const [ensure, setEnsure] = useState<EnsureHistoryResponse | null>(null);
  const [ensureError, setEnsureError] = useState<string | null>(null);
  const [ready, setReady] = useState(0); // bumps after the on-demand fetch finishes
  const [range, setRange] = useState<RangeKey>("1y");
  const [chart, setChart] = useState<ChartResponse | null>(null);
  const [tech, setTech] = useState<TechnicalsResponse | null>(null);
  const [card, setCard] = useState<PriceCard | null>(null);
  const [dataError, setDataError] = useState<string | null>(null);
  // Phase 2: financial summaries (on-demand fetch, then financials / valuation / cards)
  const [finEnsure, setFinEnsure] = useState<EnsureFinsResponse | null>(null);
  const [finEnsureError, setFinEnsureError] = useState<string | null>(null);
  const [finReady, setFinReady] = useState(0);
  const [fin, setFin] = useState<FinancialsResponse | null>(null);
  const [val, setVal] = useState<ValuationResponse | null>(null);
  const [bcards, setBcards] = useState<BusinessCardsResponse | null>(null);
  const [finError, setFinError] = useState<string | null>(null);
  const [refetching, setRefetching] = useState(false);
  const [refetchMessage, setRefetchMessage] = useState<string | null>(null);
  // Phase 3: sector profile + checklist + macro series (DB only, no external call)
  const [macro, setMacro] = useState<StockMacroResponse | null>(null);
  const [macroError, setMacroError] = useState<string | null>(null);

  // header (and again once the on-demand fetch has run)
  useEffect(() => {
    let cancelled = false;
    if (ready === 0) {
      setHeader(null);
      setHeaderError(null);
    }
    api
      .header(code)
      .then((d) => !cancelled && setHeader(d))
      .catch((e: Error) => {
        if (cancelled) return;
        setHeaderError(
          e instanceof ApiError && e.status === 404
            ? `銘柄コード「${code}」は銘柄マスタにありません。`
            : `読み込みに失敗しました：${e.message}`,
        );
      });
    return () => {
      cancelled = true;
    };
  }, [code, ready]);

  // on-demand history fetch (P1). Stored & current -> the server makes no API call.
  useEffect(() => {
    let cancelled = false;
    setEnsure(null);
    setEnsureError(null);
    setReady(0);
    api
      .ensureHistory(code)
      .then((d) => !cancelled && setEnsure(d))
      .catch((e: Error) => !cancelled && setEnsureError(e.message))
      .finally(() => !cancelled && setReady((n) => n + 1));
    return () => {
      cancelled = true;
    };
  }, [code]);

  // on-demand financial-summary fetch (P1): none stored or older than 7 days.
  useEffect(() => {
    let cancelled = false;
    setFinEnsure(null);
    setFinEnsureError(null);
    setFinReady(0);
    setFin(null);
    setVal(null);
    setBcards(null);
    setFinError(null);
    setRefetchMessage(null);
    api
      .ensureFins(code)
      .then((d) => !cancelled && setFinEnsure(d))
      .catch((e: Error) => !cancelled && setFinEnsureError(e.message))
      .finally(() => !cancelled && setFinReady((n) => n + 1));
    return () => {
      cancelled = true;
    };
  }, [code]);

  // financials / valuation / business cards: after both on-demand steps
  // (valuation needs the daily bars for market cap as well as the summaries).
  useEffect(() => {
    if (ready === 0 || finReady === 0) return;
    let cancelled = false;
    setFinError(null);
    Promise.all([api.financials(code), api.valuation(code), api.businessCards(code)])
      .then(([f, v, c]) => {
        if (cancelled) return;
        setFin(f);
        setVal(v);
        setBcards(c);
      })
      .catch((e: Error) => !cancelled && setFinError(e.message));
    return () => {
      cancelled = true;
    };
  }, [code, ready, finReady]);

  useEffect(() => {
    if (ready === 0 || finReady === 0) return;
    let cancelled = false;
    setMacroError(null);
    api
      .stockMacro(code)
      .then((d) => !cancelled && setMacro(d))
      .catch((e: Error) => !cancelled && setMacroError(e.message));
    return () => {
      cancelled = true;
    };
  }, [code, ready, finReady]);

  const refetchFins = () => {
    setRefetching(true);
    setRefetchMessage(null);
    api
      .ensureFins(code, true)
      .then((d) => {
        setFinEnsure(d);
        setRefetchMessage(
          d.state === "failed"
            ? `再取得できませんでした：${d.error_reason ?? "原因不明"}`
            : `再取得しました（訂正を検知した開示：${d.revisions_detected}件）。`,
        );
        setFinReady((n) => n + 1);
      })
      .catch((e: Error) => setRefetchMessage(`再取得できませんでした：${e.message}`))
      .finally(() => setRefetching(false));
  };

  useEffect(() => {
    if (ready === 0) return;
    let cancelled = false;
    setDataError(null);
    Promise.all([api.chart(code, range), api.technicals(code, range)])
      .then(([c, t]) => {
        if (cancelled) return;
        setChart(c);
        setTech(t);
      })
      .catch((e: Error) => !cancelled && setDataError(`チャートデータの読み込みに失敗しました：${e.message}`));
    return () => {
      cancelled = true;
    };
  }, [code, range, ready, finReady]);

  useEffect(() => {
    if (ready === 0) return;
    let cancelled = false;
    api
      .pricePosition(code)
      .then((c) => !cancelled && setCard(c))
      .catch(() => !cancelled && setCard(null));
    return () => {
      cancelled = true;
    };
  }, [code, ready]);

  if (headerError)
    return (
      <section>
        <p className="error" role="alert">
          {headerError}
        </p>
        <p>
          <Link href="/">検索に戻る</Link>
        </p>
      </section>
    );
  if (!header) return <p aria-live="polite">読み込み中…</p>;

  const code5 = header.stock.code;
  const fetching = ready === 0;
  const hasData = chart?.state === "ok";

  return (
    <section>
      <StockHeader data={header} fetching={fetching} />
      <div className="stock-actions">
        <StockWatchControl code={code5} />
        <p>
          <a href="#memo" onClick={(e) => { e.preventDefault(); scrollToTarget("memo"); }}>
            判断メモ（この銘柄）へ移動
          </a>
        </p>
      </div>

      <FetchStatus fetching={fetching} ensure={ensure} error={ensureError} />
      <FinFetchStatus fetching={finReady === 0} ensure={finEnsure} error={finEnsureError} />

      <AlertsBand alerts={fin?.alerts ?? null} loading={!fin && !finError} error={finError} />

      {dataError && (
        <p className="error" role="alert">
          {dataError}
        </p>
      )}

      {!fetching && chart && !hasData && (
        <div className="placeholder">
          <p>この銘柄の日足が保存されていないため、チャートとテクニカル指標は表示できません。</p>
          <p className="small">
            取得の状況は <Link href="/status">データ状態</Link> で確認できます。
          </p>
        </div>
      )}

      <div className="cards-grid">
        {hasData && card && <PriceCardView card={card} code={code5} />}
        {bcards?.cards.map((c) => (
          <BusinessCardView key={c.key} card={c} code={code5} />
        ))}
      </div>

      {hasData && chart && tech && (
        <>
          <ChartSection chart={chart} code={code5} range={range} onRange={setRange} />
          <StagePanels chart={chart} tech={tech} code={code5} />
          <CrossHistorySection tech={tech} code={code5} />
        </>
      )}

      {fin && (
        <FinancialSection
          fin={fin}
          val={val}
          code={code5}
          onRefetch={refetchFins}
          refetching={refetching}
          refetchMessage={refetchMessage}
        />
      )}

      {macroError && (
        <p className="error" role="alert">
          マクロ（業種の感応タグ・金利・為替）の読み込みに失敗しました：{macroError}
        </p>
      )}
      {macro && <MacroSection data={macro} code={code5} />}

      <StockMemoSection code={code5} name={header.stock.name} />
    </section>
  );
}

// ---------------------------------------------------------------- header

function StockHeader({ data, fetching }: { data: HeaderResponse; fetching: boolean }) {
  const s = data.stock;
  return (
    <div className="stock-header" aria-label="銘柄ヘッダー">
      <h1>
        {s.name} <span className="code">（{s.display_code}）</span>
      </h1>
      <dl className="facts">
        <div>
          <dt>市場区分</dt>
          <dd>{s.market.name ?? "—"}</dd>
        </div>
        <div>
          <dt>17業種</dt>
          <dd>{s.sector17.name ?? "—"}</dd>
        </div>
        <div>
          <dt>33業種</dt>
          <dd>{s.sector33.name ?? "—"}</dd>
        </div>
        {data.state === "ok" ? (
          <>
            <div>
              <dt>最新終値（調整後）</dt>
              <dd className="num">{yen(data.price.close)}円</dd>
            </div>
            <div>
              <dt>前日比</dt>
              <dd className="num">
                {data.price.change !== null && data.price.change_pct !== null
                  ? `${signed(data.price.change, 1)}円（${signed(data.price.change_pct, 2)}%）`
                  : "算出不可"}
              </dd>
            </div>
          </>
        ) : (
          <div>
            <dt>最新終値</dt>
            <dd>未取得</dd>
          </div>
        )}
      </dl>
      {data.state === "ok" ? (
        <>
          <p className="asof">データ基準日：{data.price.as_of}（終値）</p>
          {data.price.change_unavailable_reason && <p className="note">{data.price.change_unavailable_reason}</p>}
          {data.today_not_reflected === true && (
            <p className="note">本日分は未反映（直近の営業日：{data.latest_business_day}）</p>
          )}
          {data.today_not_reflected === null && (
            <p className="note">営業日カレンダーが未取得のため、本日分の反映状況は判定できません。</p>
          )}
        </>
      ) : (
        <p className="note" role="status">
          {data.message}
          {fetching ? "" : "現在は数値を表示していません。"}
        </p>
      )}
      <p className="note small">銘柄マスタ適用日：{s.master_date}</p>
    </div>
  );
}

function FetchStatus({
  fetching,
  ensure,
  error,
}: {
  fetching: boolean;
  ensure: EnsureHistoryResponse | null;
  error: string | null;
}) {
  if (fetching)
    return (
      <p className="progress" role="status" aria-live="polite">
        株価データを確認・取得しています…（初回は日足の全履歴とTOPIXを取得するため、少し時間がかかります）
      </p>
    );
  if (error)
    return (
      <p className="error" role="alert">
        株価データの取得処理に失敗しました：{error}（保存済みのデータがあれば表示します）
      </p>
    );
  if (ensure?.state === "failed")
    return (
      <p className="error" role="alert">
        株価データを取得できませんでした：{ensure.error_reason ?? "原因不明"}
        {ensure.has_bars ? "（保存済みのデータを表示しています）" : ""}　<Link href="/status">データ状態を見る</Link>
      </p>
    );
  if (ensure?.state === "fetched") return <p className="note small">株価データを取得しました。</p>;
  return null;
}

function FinFetchStatus({
  fetching,
  ensure,
  error,
}: {
  fetching: boolean;
  ensure: EnsureFinsResponse | null;
  error: string | null;
}) {
  if (fetching)
    return (
      <p className="progress" role="status" aria-live="polite">
        決算サマリーを確認・取得しています…
      </p>
    );
  if (error)
    return (
      <p className="error" role="alert">
        決算サマリーの取得処理に失敗しました：{error}（保存済みのデータがあれば表示します）
      </p>
    );
  if (ensure?.state === "failed")
    return (
      <p className="error" role="alert">
        決算サマリーを取得できませんでした：{ensure.error_reason ?? "原因不明"}
        {ensure.has_fins ? "（保存済みのデータを表示しています）" : ""}　<Link href="/status">データ状態を見る</Link>
      </p>
    );
  if (ensure?.state === "fetched") return <p className="note small">決算サマリーを取得しました。</p>;
  return null;
}

// ---------------------------------------------------------------- card (第1階層)

function PriceCardView({ card, code }: { card: PriceCard; code: string }) {
  return (
    <section className="card" aria-labelledby="card-price-title">
      <h2 id="card-price-title">{card.title ?? "価格位置"}</h2>
      <ul className="card-lines">
        {card.sentences.map((s) => (
          <li key={s.key}>
            <button type="button" className="card-line" onClick={() => scrollToTarget(s.target)}>
              {s.text}
            </button>
          </li>
        ))}
      </ul>
      <p className="note small">
        各文をクリックすると対応するテクニカルの段階へ移動します。用語：
        <Term term="deviation" code={code}>乖離率</Term>
        <Term term="high_low_52w" code={code}>52週レンジ</Term>
        <Term term="excess_return" code={code}>超過リターン</Term>
      </p>
    </section>
  );
}

// ---------------------------------------------------------------- chart (第2階層)

function LineSwatch({ k }: { k: MaKey }) {
  const look = MA_LOOK[k];
  const dash = look.type === "dashed" ? "7 4" : look.type === "dotted" ? "1.5 3.5" : undefined;
  return (
    <svg width="36" height="12" aria-hidden="true" className="swatch">
      <line
        x1="1" y1="6" x2="35" y2="6"
        stroke={look.color} strokeWidth={look.width} strokeDasharray={dash}
        strokeLinecap={look.type === "dotted" ? "round" : "butt"}
      />
    </svg>
  );
}

function CandleSwatch({ up }: { up: boolean }) {
  return (
    <svg width="14" height="18" aria-hidden="true" className="swatch">
      <line x1="7" y1="1" x2="7" y2="17" stroke={up ? UP : DOWN} strokeWidth="1" />
      <rect x="2.5" y="4" width="9" height="10" fill={up ? "#ffffff" : DOWN} stroke={up ? UP : DOWN} strokeWidth="1.25" />
    </svg>
  );
}

function ChartSection({
  chart,
  code,
  range,
  onRange,
}: {
  chart: ChartResponse;
  code: string;
  range: RangeKey;
  onRange: (r: RangeKey) => void;
}) {
  const option = useMemo(() => priceVolumeOption(chart), [chart]);
  return (
    <section className="panel" aria-labelledby="chart-title">
      <div className="panel-head">
        <h2 id="chart-title">
          チャート（<Term term="adjusted_price" code={code}>調整後</Term>日足）
        </h2>
        <div className="range-switch" role="group" aria-label="表示期間">
          {RANGES.map((r) => (
            <button key={r.key} type="button" aria-pressed={range === r.key} onClick={() => onRange(r.key)}>
              {r.label}
            </button>
          ))}
        </div>
      </div>
      <p className="note small">
        表示期間：{chart.range_start}〜{chart.range_end}（{chart.dates.length}営業日、基準日：{chart.as_of}）
      </p>
      <ul className="legend" aria-label="凡例">
        <li><CandleSwatch up />陽線（終値≧始値・白抜き）</li>
        <li><CandleSwatch up={false} />陰線（終値＜始値・塗りつぶし）</li>
        {chart.ma_styles.map((s) => (
          <li key={s.key}>
            <LineSwatch k={s.key} />
            {MA_LOOK[s.key].label}
          </li>
        ))}
        <li><span className="mk">▼決</span>決算開示日</li>
        <li><span className="mk">▲</span>ストップ高</li>
        <li><span className="mk">▽</span>ストップ安</li>
      </ul>
      <p className="note small">
        用語：<Term term="sma" code={code}>移動平均</Term>
        <Term term="stop_high_low" code={code}>ストップ高・安</Term>
      </p>
      <EChart option={option} group={`stock-${code}`} height={480} label="ローソク足・移動平均・出来高のチャート" />
      <ul className="notes">
        {chart.notes.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
      <details className="layer3">
        <summary>原数値・注記（チャート）</summary>
        <p className="small">{ENDPOINT_NOTE}</p>
        <RawTable
          dates={chart.dates}
          columns={[
            { label: "始値", values: chart.candles.map((c) => c.open) },
            { label: "高値", values: chart.candles.map((c) => c.high) },
            { label: "安値", values: chart.candles.map((c) => c.low) },
            { label: "終値", values: chart.candles.map((c) => c.close) },
            { label: "出来高", values: chart.candles.map((c) => c.volume), digits: 0 },
            { label: "5日", values: chart.ma.sma5 },
            { label: "25日", values: chart.ma.sma25 },
            { label: "75日", values: chart.ma.sma75 },
            { label: "200日", values: chart.ma.sma200 },
          ]}
          flags={chart.candles.map((c) => [c.upper_limit ? "ストップ高" : "", c.lower_limit ? "ストップ安" : ""].filter(Boolean).join("・"))}
        />
      </details>
    </section>
  );
}

function RawTable({
  dates,
  columns,
  flags,
}: {
  dates: string[];
  columns: { label: string; values: (number | null)[]; digits?: number }[];
  flags?: string[];
}) {
  const [all, setAll] = useState(false);
  const n = dates.length;
  const start = all ? 0 : Math.max(0, n - 20);
  const rows = [];
  for (let i = n - 1; i >= start; i--) rows.push(i);
  return (
    <>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>日付</th>
              {columns.map((c) => (
                <th key={c.label} className="num">{c.label}</th>
              ))}
              {flags && <th>フラグ</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((i) => (
              <tr key={dates[i]}>
                <td>{dates[i]}</td>
                {columns.map((c) => (
                  <td key={c.label} className="num">{fmt(c.values[i], c.digits ?? 2)}</td>
                ))}
                {flags && <td>{flags[i]}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {n > 20 && (
        <button type="button" className="small-btn" onClick={() => setAll(!all)}>
          {all ? "直近20営業日だけ表示" : `表示期間の全${n}営業日を表示`}
        </button>
      )}
    </>
  );
}

// ---------------------------------------------------------------- 4 stages

function Stage({ id, title, terms, children }: { id: string; title: string; terms: ReactNode; children: ReactNode }) {
  return (
    <section className="panel stage" id={`stage-${id}`} aria-labelledby={`stage-${id}-title`}>
      <h3 id={`stage-${id}-title`}>{title}</h3>
      <p className="note small terms">用語：{terms}</p>
      {children}
    </section>
  );
}

function StagePanels({ chart, tech, code }: { chart: ChartResponse; tech: TechnicalsResponse; code: string }) {
  const group = `stock-${code}`;
  const opts = useMemo(
    () => ({
      trend: trendOption(chart, tech.cross_history.events),
      rsi: rsiOption(tech),
      bb: bollingerOption(tech),
      macd: macdOption(tech),
      idx: indexedOption(tech),
      rs: rsLineOption(tech),
    }),
    [chart, tech],
  );
  const v = tech.values;
  const s4 = tech.stage4;
  const asOf = `基準日：${tech.as_of}`;
  return (
    <section aria-label="テクニカルの段階表示">
      <h2>テクニカル（トレンド→過熱感→転換・支持抵抗→相対比較の順）</h2>

      <Stage
        id="stage1"
        title={tech.stage1.title}
        terms={
          <>
            <Term term="sma" code={code}>移動平均</Term>
            <Term term="deviation" code={code}>乖離率</Term>
            <Term term="golden_cross" code={code}>ゴールデンクロス</Term>
            <Term term="dead_cross" code={code}>デッドクロス</Term>
          </>
        }
      >
        <Facts facts={tech.stage1.facts} />
        <EChart option={opts.trend} group={group} height={240} label="終値と25日・75日移動平均、クロス発生日" />
        <p className="note small">○上抜け＝25日線が75日線を上抜けた日、■下抜け＝下抜けた日。全件は下の「クロス発生履歴」に掲載。</p>
        <Notes notes={tech.stage1.notes} />
        <details className="layer3">
          <summary>原数値・算出式</summary>
          <ValuesTable
            rows={[
              ...(["sma5", "sma25", "sma75", "sma200"] as const).map(
                (k): [string, string] => [`${k.replace("sma", "")}日移動平均（円）`, fmt(v[k], 2)],
              ),
              ...([5, 25, 75, 200] as const).map((n): [string, string] => [`${n}日線との乖離率（%）`, fmtSigned(v[`dev${n}`], 2)]),
            ]}
          />
          <p className="small">SMA_n(t) ＝ 直近n営業日の調整後終値の合計 ÷ n。乖離率 ＝ (終値 − SMA) ÷ SMA × 100。{asOf}</p>
        </details>
      </Stage>

      <Stage
        id="stage2"
        title={tech.stage2.title}
        terms={
          <>
            <Term term="rsi" code={code}>RSI</Term>
            <Term term="bollinger" code={code}>ボリンジャーバンド</Term>
            <Term term="percent_b" code={code}>%B</Term>
          </>
        }
      >
        <Facts facts={tech.stage2.facts} />
        <h4 className="panel-label"><Term term="rsi" code={code}>RSI(14)</Term></h4>
        <EChart option={opts.rsi} group={group} height={180} label="RSI(14)の推移" />
        <h4 className="panel-label"><Term term="bollinger" code={code}>ボリンジャーバンド(20日, ±2σ)</Term></h4>
        <EChart option={opts.bb} group={group} height={220} label="終値とボリンジャーバンドの推移" />
        <Notes notes={tech.stage2.notes} />
        <details className="layer3">
          <summary>原数値・算出式</summary>
          <ValuesTable
            rows={[
              ["RSI(14)", fmt(v.rsi, 2)],
              ["平均上昇幅（円）", fmt(v.avg_u, 4)],
              ["平均下落幅（円）", fmt(v.avg_d, 4)],
              ["中心（20日移動平均、円）", fmt(v.bb_mid, 2)],
              ["標準偏差σ（母標準偏差、円）", fmt(v.bb_sd, 4)],
              ["+2σ（円）", fmt(v.bb_upper, 2)],
              ["−2σ（円）", fmt(v.bb_lower, 2)],
              ["%B", fmt(v.pct_b, 4)],
            ]}
          />
          <p className="small">
            RSIはワイルダー方式（初回は14個の単純平均、以後 (前回×13＋当日)÷14）。σは直近20本の母標準偏差（nで割る）。{asOf}
          </p>
        </details>
      </Stage>

      <Stage
        id="stage3"
        title={tech.stage3.title}
        terms={
          <>
            <Term term="macd" code={code}>MACD</Term>
            <Term term="high_low_52w" code={code}>52週高値・安値</Term>
          </>
        }
      >
        <Facts facts={tech.stage3.facts} />
        <h4 className="panel-label"><Term term="macd" code={code}>MACD(12,26,9)・シグナル・ヒストグラム</Term></h4>
        <EChart option={opts.macd} group={group} height={200} label="MACD・シグナル・ヒストグラムの推移" />
        <Notes notes={tech.stage3.notes} />
        <details className="layer3">
          <summary>原数値・算出式</summary>
          <ValuesTable
            rows={[
              ["12日EMA（円）", fmt(v.ema12, 2)],
              ["26日EMA（円）", fmt(v.ema26, 2)],
              ["MACD", fmt(v.macd, 4)],
              ["シグナル", fmt(v.macd_signal, 4)],
              ["ヒストグラム", fmt(v.macd_hist, 4)],
              ["52週高値（円）", fmt(v.h52, 2)],
              ["52週安値（円）", fmt(v.l52, 2)],
              ["52週高値からの距離（%）", fmtSigned(v.from_high_pct, 2)],
              ["レンジ内位置（下から、%）", fmt(v.range_pos, 2)],
            ]}
          />
          <p className="small">
            EMA：α＝2÷(n＋1)、初期値は最初のn本のSMA。52週＝基準日から暦日364日前までの調整後高値・安値。{asOf}
          </p>
        </details>
      </Stage>

      <Stage
        id="stage4"
        title={s4.title}
        terms={
          <>
            <Term term="relative_strength" code={code}>相対力ライン</Term>
            <Term term="excess_return" code={code}>超過リターン</Term>
            <Term term="beta" code={code}>ベータ</Term>
            <Term term="correlation" code={code}>相関係数</Term>
          </>
        }
      >
        <Facts facts={s4.facts} />
        {!s4.topix_available ? (
          // Phase 2 leftover: no empty chart frames without TOPIX
          <p className="placeholder" role="status">
            {s4.topix_message ?? "TOPIXデータ未取得"}
          </p>
        ) : (
          <>
        <h4 className="panel-label">銘柄とTOPIXの指数化（同一軸、{s4.t0 ?? "—"}＝100）</h4>
        <EChart option={opts.idx} group={group} height={220} label="銘柄とTOPIXの指数化チャート" />
        <h4 className="panel-label"><Term term="relative_strength" code={code}>相対力ライン</Term></h4>
        <EChart option={opts.rs} group={group} height={180} label="相対力ラインの推移" />
        <div className="table-scroll">
          <table>
            <caption className="small">
              期間別の騰落率（<Term term="excess_return" code={code}>超過リターン</Term>、基準日：{s4.excess_returns[0]?.as_of ?? tech.as_of}）
            </caption>
            <thead>
              <tr>
                <th>期間</th>
                <th>起点日</th>
                <th className="num">銘柄（%）</th>
                <th className="num">TOPIX（%）</th>
                <th className="num">差（ポイント）</th>
              </tr>
            </thead>
            <tbody>
              {s4.excess_returns.map((r) => (
                <tr key={r.n}>
                  <td>{r.n}営業日</td>
                  <td>{r.from_date ?? "—"}</td>
                  <td className="num">{r.stock_pct === null ? "算出不可" : fmtSigned(r.stock_pct, 1)}</td>
                  <td className="num">{r.topix_pct === null ? "算出不可" : fmtSigned(r.topix_pct, 1)}</td>
                  <td className="num">{r.excess_pt === null ? "算出不可" : fmtSigned(r.excess_pt, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
          </>
        )}
        <Notes notes={s4.notes} />
        <details className="layer3">
          <summary>原数値・算出式</summary>
          <ValuesTable
            rows={[
              ["ベータ", fmt(s4.beta.beta, 4)],
              ["相関係数", fmt(s4.beta.correlation, 4)],
              ["有効な日次リターン（件）", String(s4.beta.observations)],
              ["窓", `${s4.beta.window_from ?? "—"}〜${s4.beta.window_to ?? "—"}（直近${s4.beta.window}営業日、${s4.beta.min_observations}件未満は算出しない）`],
            ]}
          />
          <p className="small">
            r(t)＝C(t)÷C(t−1)−1。β＝Cov(r銘柄, rTOPIX)÷Var(rTOPIX)。出来高ゼロの日のリターンは除外。指数化＝C(t)÷C(t0)×100、相対力ライン＝指数化(銘柄)÷指数化(TOPIX)×100。
          </p>
        </details>
      </Stage>

      {/* 既存の4段階表示はそのまま。その下に「テクニカルで見る」（今の評価＋過去の検証） */}
      <TechnicalView code={code} />
    </section>
  );
}

// ---------------------------------------------------------------- cross history (第3階層)

function CrossHistorySection({ tech, code }: { tech: TechnicalsResponse; code: string }) {
  const ch = tech.cross_history;
  const ret = (v: number | null) => (v === null ? "未到来" : `${fmtSigned(v, 2)}%`);
  return (
    <details className="panel layer3 cross-history">
      <summary>
        クロス発生履歴（全{ch.events.length}件・
        <Term term="golden_cross" code={code}>ゴールデンクロス</Term>／
        <Term term="dead_cross" code={code}>デッドクロス</Term>）
      </summary>
      {ch.period && <p className="small">{ch.period}</p>}
      <ul className="facts-list">
        {ch.count_texts.map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ul>
      {ch.events.length === 0 ? (
        <p>保有データ期間にクロスの発生はありません（または判定に必要な76営業日分の終値がありません）。</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>発生日</th>
                <th>内容</th>
                <th className="num">発生日終値（円）</th>
                <th>20営業日後</th>
                <th className="num">騰落率</th>
                <th>60営業日後</th>
                <th className="num">騰落率</th>
              </tr>
            </thead>
            <tbody>
              {ch.events.map((e) => (
                <tr key={e.date}>
                  <td>{e.date}</td>
                  <td>{e.label}</td>
                  <td className="num">{fmt(e.close, 2)}</td>
                  <td>{e.after_20d.date ?? "—"}</td>
                  <td className="num">{ret(e.after_20d.return_pct)}</td>
                  <td>{e.after_60d.date ?? "—"}</td>
                  <td className="num">{ret(e.after_60d.return_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Notes notes={ch.notes} />
    </details>
  );
}
