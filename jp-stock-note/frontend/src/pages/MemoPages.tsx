// S4 判断メモ：全体の一覧と詳細（スナップショットと現在値の並記、リビジョン追記、振り返り追記）。
import { useCallback, useEffect, useState } from "react";
import { EXPORT_URLS, memoApi, type MemoDetail, type MemoList, type MemoReviewPayload, type ReviewRow } from "../api";
import {
  fmtDateTime,
  fmtDiff,
  fmtPoint,
  MemoContentView,
  MemoFormView,
  MemoTable,
  SelfReviewForm,
  SelfReviewList,
  useMemoForm,
} from "../components/Memo";
import { Link } from "../router";

export function MemoListPage() {
  const [list, setList] = useState<MemoList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { form } = useMemoForm();
  useEffect(() => {
    memoApi
      .all()
      .then(setList)
      .catch((e: Error) => setError(e.message));
  }, []);
  const due = list?.items.filter((m) => m.due) ?? [];
  return (
    <section>
      <h1>判断メモ</h1>
      {form && <p className="notice-box">{form.notice}</p>}
      <p className="note">
        メモは銘柄ページの「判断メモを書く」から作成します。保存したメモは編集・削除できず、書き直しは新しいリビジョンとして追記されます。
      </p>
      {error && (
        <p className="error" role="alert">
          読み込みに失敗しました：{error}
        </p>
      )}
      {list && (
        <>
          <p>
            全{list.count}件（基準日：{list.today}）{due.length > 0 && `／見直し日が到来：${due.length}件`}
          </p>
          <MemoTable list={list} showStock />
        </>
      )}
      <div className="actions">
        <a className="button-link" href={EXPORT_URLS.json} download>
          エクスポート（JSON）
        </a>
        <a className="button-link" href={EXPORT_URLS.md} download>
          エクスポート（Markdown）
        </a>
      </div>
      <p className="note small">エクスポートには全リビジョン・スナップショット・振り返りとウォッチリストの履歴が含まれます。スナップショットの数値は取得データにもとづくため、ファイルは共有・公開しないでください。</p>
    </section>
  );
}

function CompareTable({ rows, caption }: { rows: ReviewRow[]; caption: string }) {
  return (
    <div className="table-scroll">
      <table className="compare">
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">項目</th>
            <th scope="col">保存時の値</th>
            <th scope="col">保存時の基準日</th>
            <th scope="col">現在の値</th>
            <th scope="col">現在の基準日</th>
            <th scope="col">差（現在−保存時）</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className={r.core ? "row-core" : undefined}>
              <th scope="row">{r.label}</th>
              <td className="num">{fmtPoint(r.snapshot, r.unit, r.digits)}</td>
              <td className="num">{r.snapshot.as_of ?? "—"}</td>
              <td className="num">{fmtPoint(r.current, r.unit, r.digits)}</td>
              <td className="num">{r.current.as_of ?? "—"}</td>
              <td className="num">{fmtDiff(r.diff, r.unit === "%" ? "ポイント" : r.unit, r.digits)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function MemoDetailPage({ memoId }: { memoId: number }) {
  const { form, error: formError } = useMemoForm();
  const [memo, setMemo] = useState<MemoDetail | null>(null);
  const [revisionId, setRevisionId] = useState<number | null>(null);
  const [review, setReview] = useState<MemoReviewPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);

  const loadMemo = useCallback(
    (selectLatest: boolean) =>
      memoApi
        .get(memoId)
        .then((m) => {
          setMemo(m);
          if (selectLatest || revisionId === null) setRevisionId(m.latest?.revision_id ?? null);
          return m;
        })
        .catch((e: Error) => {
          setError(e.message);
          return null;
        }),
    [memoId, revisionId],
  );

  useEffect(() => {
    setMemo(null);
    setRevisionId(null);
    setError(null);
    memoApi
      .get(memoId)
      .then((m) => {
        setMemo(m);
        setRevisionId(m.latest?.revision_id ?? null);
      })
      .catch((e: Error) => setError(e.message));
  }, [memoId]);

  const [reviewTick, setReviewTick] = useState(0);
  useEffect(() => {
    if (revisionId === null) return;
    let cancelled = false;
    setReviewError(null);
    memoApi
      .review(memoId, revisionId)
      .then((r) => !cancelled && setReview(r))
      .catch((e: Error) => !cancelled && setReviewError(e.message));
    return () => {
      cancelled = true;
    };
  }, [memoId, revisionId, reviewTick]);

  if (error)
    return (
      <section>
        <p className="error" role="alert">
          判断メモを読み込めませんでした：{error}
        </p>
        <p>
          <Link href="/memos">判断メモの一覧へ</Link>
        </p>
      </section>
    );
  if (formError)
    return (
      <p className="error" role="alert">
        入力フォームの定義を読み込めませんでした：{formError}
      </p>
    );
  if (!memo || !form) return <p aria-live="polite">読み込み中…</p>;

  const rev = memo.revisions.find((r) => r.revision_id === revisionId) ?? memo.latest;
  const isLatest = rev?.revision_id === memo.latest?.revision_id;
  const core = review?.rows.filter((r) => r.core) ?? [];
  const others = review?.rows.filter((r) => !r.core) ?? [];

  return (
    <section className="memo-detail">
      <p className="small">
        <Link href="/memos">判断メモの一覧</Link> ／ <Link href={`/stocks/${memo.code}`}>{memo.display_code} {memo.name ?? ""}の銘柄ページ</Link>
      </p>
      <h1>
        判断メモ#{memo.memo_id} <span className="code">{memo.display_code} {memo.name ?? ""}</span>
      </h1>
      <dl className="facts">
        <div>
          <dt>作成日時</dt>
          <dd>{fmtDateTime(memo.created_at)}</dd>
        </div>
        <div>
          <dt>リビジョン数</dt>
          <dd className="num">{memo.revisions.length}</dd>
        </div>
        <div>
          <dt>改変検知（SHA-256照合）</dt>
          <dd>{memo.verification.text}</dd>
        </div>
      </dl>
      {!memo.verification.ok && (
        <ul className="facts-list">
          {memo.verification.problems.map((p, i) => (
            <li key={i}>
              {p.table}
              {p.id !== null ? `（ID ${p.id}）` : ""}：{p.reason}
            </li>
          ))}
        </ul>
      )}
      <p className="notice-box">{form.notice}</p>

      {memo.revisions.length > 1 && (
        <div className="range-switch revision-switch" role="group" aria-label="リビジョンの切り替え">
          {memo.revisions.map((r) => (
            <button
              key={r.revision_id}
              type="button"
              aria-pressed={r.revision_id === rev?.revision_id}
              onClick={() => setRevisionId(r.revision_id)}
            >
              リビジョン{r.revision_no}
            </button>
          ))}
        </div>
      )}

      {rev && (
        <section className="card" aria-labelledby="rev-title">
          <h2 id="rev-title">
            リビジョン{rev.revision_no}
            <span className="small note">（{fmtDateTime(rev.created_at)} 保存{isLatest ? "・最新" : ""}）</span>
          </h2>
          <MemoContentView form={form} content={rev.content} />
        </section>
      )}

      <section className="panel" aria-labelledby="cmp-title">
        <h2 id="cmp-title">{form.review_view.title}</h2>
        <ul className="notes">
          {(review?.notes ?? [form.review_view.note, form.review_view.core_note]).map((n) => (
            <li key={n}>注記：{n}</li>
          ))}
        </ul>
        {reviewError && (
          <p className="error" role="alert">
            現在値の読み込みに失敗しました：{reviewError}
          </p>
        )}
        {!review && !reviewError && <p aria-live="polite">現在値を計算中…</p>}
        {review && (
          <>
            <p className="small note">
              スナップショット保存：{fmtDateTime(review.snapshot_captured_at)}／現在値の計算：{fmtDateTime(review.current_computed_at)}
            </p>
            <CompareTable rows={core} caption="自動スナップショットの5項目（各値の基準日つき）" />
            <details className="layer3">
              <summary>そのほかの記録項目（{others.length}項目）</summary>
              <CompareTable rows={others} caption="テクニカル・バリュエーション・マクロの記録項目" />
            </details>
            <details className="layer3">
              <summary>保存時と現在の事実文（カード・段階表示・マクロ帯）</summary>
              {review.texts.map((t) => (
                <div key={t.key} className="text-compare">
                  <p className="panel-label">{t.label}</p>
                  <div className="two-col">
                    <div>
                      <p className="small note">保存時</p>
                      <ul className="facts-list">
                        {t.snapshot.length ? t.snapshot.map((s) => <li key={s}>{s}</li>) : <li>記録なし</li>}
                      </ul>
                    </div>
                    <div>
                      <p className="small note">現在</p>
                      <ul className="facts-list">
                        {t.current.length ? t.current.map((s) => <li key={s}>{s}</li>) : <li>記録なし</li>}
                      </ul>
                    </div>
                  </div>
                </div>
              ))}
            </details>
          </>
        )}
      </section>

      <section className="panel" aria-labelledby="sr-title">
        <h2 id="sr-title">振り返りの追記（このリビジョン）</h2>
        {review && <SelfReviewList form={form} reviews={review.reviews} />}
        {rev && (
          <SelfReviewForm
            form={form}
            memoId={memo.memo_id}
            revisionId={rev.revision_id}
            onSaved={() => {
              loadMemo(false);
              setReviewTick((n) => n + 1);
            }}
          />
        )}
      </section>

      <section className="panel" aria-labelledby="edit-title">
        <h2 id="edit-title">書き直す（新しいリビジョンとして追記）</h2>
        <p className="note small">{form.immutability_note}</p>
        {!editing && memo.latest && (
          <button type="button" onClick={() => setEditing(true)}>
            最新リビジョンをもとに書き直す
          </button>
        )}
        {editing && memo.latest && (
          <MemoFormView
            form={form}
            initial={memo.latest.content}
            title={`リビジョン${memo.revisions.length + 1}を追記`}
            submitLabel="新しいリビジョンとして保存する"
            onCancel={() => setEditing(false)}
            onSubmit={(c) =>
              memoApi.addRevision(memo.memo_id, c).then((m) => {
                setMemo(m);
                setRevisionId(m.latest?.revision_id ?? null);
                setEditing(false);
                window.scrollTo(0, 0);
              })
            }
          />
        )}
      </section>
    </section>
  );
}
