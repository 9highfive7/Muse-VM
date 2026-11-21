import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, EXPORT_URLS, memoApi, type FredFetchResponse, type StatusResponse, type TankanSetting, type VerifyAll } from "../api";
import { Link } from "../router";

const RESULT_LABELS: Record<string, string> = {
  ok: "成功",
  running: "実行中",
  plan_limit: "プラン制限（403）",
  rate_limited: "レート制限（429）",
  timeout: "タイムアウト",
  server_error: "サーバーエラー（5xx）",
  network_error: "通信エラー",
  auth_error: "認証エラー（401）",
  no_api_key: "APIキー未設定",
  http_error: "HTTPエラー",
  error: "内部エラー",
  parse_error: "形式エラー（CSVを解釈できず）",
};

const JOB_LABELS: Record<string, string> = {
  stock_master: "銘柄マスタ",
  trading_calendar: "営業日カレンダー",
  daily_bars_by_date: "日足（日付指定）",
  daily_bars_by_code: "日足（コード指定）",
  topix_bars: "TOPIX日足",
  fins_summary_by_code: "決算サマリー（コード指定）",
  fins_summary_by_date: "決算サマリー（日付指定）",
  fred_series: "FRED系列",
  DGS10: "米10年金利（FRED DGS10）",
  DGS2: "米2年金利（FRED DGS2）",
  DEXJPUS: "ドル円（FRED DEXJPUS）",
  IRLTLT01JPM156N: "日本10年金利（FRED IRLTLT01JPM156N）",
  IRSTCI01JPM156N: "日本短期金利・コールレート（FRED IRSTCI01JPM156N）",
};

const CYCLE_LABELS: Record<string, string> = {
  daily: "毎営業日",
  weekly: "週1回",
  monthly: "月1回",
  on_demand: "画面を開いたとき",
};

const label = (map: Record<string, string>, key: string | null) => (key ? (map[key] ?? key) : "—");

const fmt = (iso: string | null) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("ja-JP", { hour12: false });
};

// S5 データ状態（フェーズ0：取得履歴と系列ごとの最終取得）。
export default function StatusPage() {
  const [data, setData] = useState<StatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .status()
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: Error) => setError(`状態の取得に失敗しました：${e.message}`));
  }, []);

  useEffect(load, [load]);

  const [fredRunning, setFredRunning] = useState(false);
  const [fredResult, setFredResult] = useState<FredFetchResponse | null>(null);
  const runFred = async () => {
    setFredRunning(true);
    setFredResult(null);
    try {
      setFredResult(await api.runFredFetch());
    } catch (e) {
      setError(`FREDの取得に失敗しました：${(e as Error).message}`);
    } finally {
      setFredRunning(false);
      load();
    }
  };

  const run = async (job: "master" | "calendar") => {
    setRunning(job);
    try {
      await api.runFetch(job);
    } catch (e) {
      setError(`取得の実行に失敗しました：${(e as Error).message}`);
    } finally {
      setRunning(null);
      load();
    }
  };

  return (
    <section>
      <h1>データ状態・設定</h1>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {data && (
        <>
          <dl className="facts">
            <div>
              <dt>J-Quants APIキー</dt>
              <dd>{data.api_key_configured ? "設定済み" : "未設定（README の「APIキーの設定」を参照）"}</dd>
            </div>
            <div>
              <dt>直近60秒の送出数</dt>
              <dd className="num">
                {data.requests_last_60s} / {data.rate_limit_per_minute}
              </dd>
            </div>
            <div>
              <dt>銘柄マスタ</dt>
              <dd className="num">{data.counts.stock_master_codes}銘柄</dd>
            </div>
            <div>
              <dt>営業日カレンダー</dt>
              <dd className="num">{data.counts.trading_calendar_days}日分</dd>
            </div>
          </dl>

          <div className="actions">
            <button type="button" disabled={running !== null} onClick={() => run("master")}>
              {running === "master" ? "取得中…" : "銘柄マスタを取得"}
            </button>
            <button type="button" disabled={running !== null} onClick={() => run("calendar")}>
              {running === "calendar" ? "取得中…" : "営業日カレンダーを取得"}
            </button>
            <button type="button" disabled={fredRunning} onClick={runFred}>
              {fredRunning ? "FREDを取得中…（1系列ずつ）" : "金利・為替（FRED）を取得"}
            </button>
            <button type="button" onClick={load}>
              表示を更新
            </button>
          </div>
          {fredResult && (
            <ul className="facts-list" aria-live="polite">
              {fredResult.series.map((r) => (
                <li key={r.series_id}>
                  {label(JOB_LABELS, r.series_id)}：{label(RESULT_LABELS, r.result)}
                  {r.result === "ok" ? `（${r.row_count}行、基準日 ${r.as_of ?? "—"}）` : `（${r.error_reason ?? ""}）`}
                </li>
              ))}
            </ul>
          )}

          <TankanForm current={data.tankan} onSaved={load} />
          <MemoDataBox />

          <h2>系列ごとの最終取得</h2>
          {data.sources.length === 0 ? (
            <p className="note">まだ取得記録がありません。</p>
          ) : (
            <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">系列</th>
                  <th scope="col">最終成功日時</th>
                  <th scope="col">基準日</th>
                  <th scope="col">更新周期</th>
                  <th scope="col">直近の結果</th>
                  <th scope="col">原因</th>
                </tr>
              </thead>
              <tbody>
                {data.sources.map((s) => (
                  <tr key={`${s.source}/${s.series_id}`}>
                    <td>{label(JOB_LABELS, s.series_id)}</td>
                    <td>{fmt(s.last_success_at)}</td>
                    <td>{s.as_of_date ?? "—"}</td>
                    <td>{s.update_cycle ? (CYCLE_LABELS[s.update_cycle] ?? s.update_cycle) : "—"}</td>
                    <td>{label(RESULT_LABELS, s.last_result)}</td>
                    <td>{s.last_error ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}

          <h2>取得履歴</h2>
          {data.history.length === 0 ? (
            <p className="note">まだ取得履歴がありません。</p>
          ) : (
            <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">開始</th>
                  <th scope="col">終了</th>
                  <th scope="col">対象</th>
                  <th scope="col">エンドポイント</th>
                  <th scope="col">結果</th>
                  <th scope="col">試行</th>
                  <th scope="col">件数</th>
                  <th scope="col">原因</th>
                </tr>
              </thead>
              <tbody>
                {data.history.map((h) => (
                  <tr key={h.id}>
                    <td>{fmt(h.started_at)}</td>
                    <td>{fmt(h.finished_at)}</td>
                    <td>{h.job === "fred_series" && h.params.id ? label(JOB_LABELS, h.params.id) : label(JOB_LABELS, h.job)}</td>
                    <td>
                      <code>{h.endpoint}</code>
                    </td>
                    <td>{label(RESULT_LABELS, h.result)}</td>
                    <td className="num">{h.attempts}</td>
                    <td className="num">{h.row_count ?? "—"}</td>
                    <td>{h.error_reason ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}

// S5：判断メモ・ウォッチリストのデータ（エクスポート、改変検知、バックアップ、初回説明の再表示）。
function MemoDataBox() {
  const [verify, setVerify] = useState<VerifyAll | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    memoApi
      .verifyAll()
      .then(setVerify)
      .catch((e: Error) => setError(e.message));
  }, []);
  return (
    <section className="settings-box" aria-labelledby="memo-data-title">
      <h2 id="memo-data-title">判断メモ・ウォッチリストのデータ</h2>
      {error && (
        <p className="error" role="alert">
          改変検知の確認に失敗しました：{error}
        </p>
      )}
      {verify && (
        <p>
          改変検知（全{verify.memos}件のメモのSHA-256を照合）：
          {verify.ok
            ? "すべて保存時のハッシュと一致"
            : `一致しない記録あり（メモID：${verify.tampered_memo_ids.join("、") || "—"}、対応するリビジョンのないスナップショット：${verify.orphan_snapshots}件）`}
        </p>
      )}
      <p className="note small">
        {verify?.note ??
          "アプリ経由では変更・削除できません。ハッシュの照合で改変を検知しますが、ローカルのデータファイルを直接書き換える操作そのものは防げません。"}
      </p>
      <div className="actions">
        <a className="button-link" href={EXPORT_URLS.json} download>
          エクスポート（JSON）
        </a>
        <a className="button-link" href={EXPORT_URLS.md} download>
          エクスポート（Markdown）
        </a>
      </div>
      <ul className="notes">
        <li>注記：エクスポートには判断メモの全リビジョン・スナップショット・振り返りと、ウォッチリストの履歴（解除済みを含む）が入ります。スナップショットの数値はJ-Quants等の取得データにもとづくため、ファイルは個人利用の範囲にとどめ、共有・公開しないでください。</li>
        <li>注記：DBのバックアップは夜間バッチの最後に data/backup/ へ7世代まで自動で保存されます（手動：backend で python -m stockapp.backup）。</li>
      </ul>
      <p>
        <Link href="/about">初回説明と拡張版免責を読み返す</Link>
      </p>
    </section>
  );
}

// S5 設定：日銀短観の想定為替レート（四半期ごとの手入力, 要件 3.8 / 9.1-8）。
function TankanForm({ current, onSaved }: { current: TankanSetting; onSaved: () => void }) {
  const [value, setValue] = useState(String(current.value));
  const [labelText, setLabelText] = useState(current.survey_label);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const v = Number(value);
    if (!value.trim() || !Number.isFinite(v) || v <= 0) {
      setMessage("想定為替レートは0より大きい数値で入力してください。");
      return;
    }
    setSaving(true);
    setMessage(null);
    try {
      const saved = await api.saveTankan(v, labelText);
      setValue(String(saved.value));
      setLabelText(saved.survey_label);
      setMessage(`保存しました（${saved.survey_label}：${saved.value}円）。`);
      onSaved();
    } catch (err) {
      setMessage(`保存できませんでした：${(err as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="settings-box" aria-labelledby="tankan-title">
      <h2 id="tankan-title">設定：想定為替レート（日銀短観）</h2>
      <p className="note small">
        現在の設定：{current.value}円（{current.survey_label}、入力日：{fmt(current.updated_at)}
        {current.origin === "default" ? "・初期値" : ""}）。全規模・全産業の平均であり、個別銘柄の想定レートではありません。短観の公表（四半期ごと）に合わせて更新してください。
      </p>
      <form className="settings-form" onSubmit={submit}>
        <label>
          ドル円の想定レート（円）
          <input
            type="number"
            inputMode="decimal"
            step="0.01"
            min="0.01"
            required
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        </label>
        <label>
          調査回
          <input type="text" required maxLength={60} value={labelText} onChange={(e) => setLabelText(e.target.value)} />
        </label>
        <button type="submit" disabled={saving}>
          {saving ? "保存中…" : "保存"}
        </button>
      </form>
      {message && (
        <p className="note" role="status">
          {message}
        </p>
      )}
    </section>
  );
}
