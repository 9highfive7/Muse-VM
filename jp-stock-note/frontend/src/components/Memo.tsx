// Phase 4: 構造化判断メモ (要件 3.10). The form only checks completeness; it never
// pre-selects, suggests or evaluates anything. No buy/sell inputs, no profit/loss.
import { useEffect, useState, type FormEvent } from "react";
import {
  memoApi,
  type MemoContent,
  type MemoForm,
  type MemoList,
  type MemoSelfReview,
  type PointValue,
} from "../api";
import { Link, navigate } from "../router";

export const fmtDateTime = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("ja-JP", { hour12: false });
};

export function fmtPoint(p: PointValue, unit: string, digits: number) {
  if (!p.available || p.value === null) return `算出不可（${p.reason ?? "理由不明"}）`;
  return `${p.value.toLocaleString("ja-JP", { minimumFractionDigits: digits, maximumFractionDigits: digits })}${unit}`;
}

export function fmtDiff(v: number | null, unit: string, digits: number) {
  if (v === null) return "—";
  return `${v.toLocaleString("ja-JP", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    signDisplay: "exceptZero",
  })}${unit}`;
}

/** Shared loader: the form definition is static per server start. */
let formCache: Promise<MemoForm> | null = null;
export function useMemoForm() {
  const [form, setForm] = useState<MemoForm | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    formCache ??= memoApi.form();
    formCache.then(setForm).catch((e: Error) => {
      formCache = null;
      setError(e.message);
    });
  }, []);
  return { form, error };
}

// ---------------------------------------------------------------- create / revision form

interface Draft {
  stance: string;
  evidence: Record<string, { checked: boolean; note: string }>; // key = area|item
  expected: string;
  different: string;
  conditions: { kind: string; text: string }[];
  review_date: string;
}

const evKey = (area: string, item: string) => `${area}|${item}`;

function toDraft(c?: MemoContent): Draft {
  const evidence: Draft["evidence"] = {};
  c?.evidence.forEach((e) => (evidence[evKey(e.area, e.item)] = { checked: true, note: e.note }));
  return {
    stance: c?.stance ?? "",
    evidence,
    expected: c?.scenario.expected ?? "",
    different: c?.scenario.different ?? "",
    conditions: c?.conditions.length ? c.conditions.map((x) => ({ ...x })) : [{ kind: "", text: "" }],
    review_date: c?.review_date ?? "",
  };
}

function toContent(form: MemoForm, d: Draft): MemoContent {
  const evidence: MemoContent["evidence"] = [];
  for (const a of form.evidence.areas)
    for (const i of a.items) {
      const e = d.evidence[evKey(a.key, i.key)];
      if (e?.checked) evidence.push({ area: a.key, item: i.key, note: e.note.trim() });
    }
  return {
    stance: d.stance,
    evidence,
    scenario: { expected: d.expected.trim(), different: d.different.trim() },
    conditions: d.conditions.filter((c) => c.text.trim()).map((c) => ({ kind: c.kind, text: c.text.trim() })),
    review_date: d.review_date || null,
  };
}

/** Mirrors stockapp.memos.validate_content so the user sees every gap at once
 *  (the server re-validates and stays the authority). */
function validate(form: MemoForm, d: Draft): string[] {
  const errs: string[] = [];
  if (!form.stance.options.some((o) => o.key === d.stance)) errs.push(`${form.stance.label}を自分で選んでください。`);
  for (const a of form.evidence.areas) {
    const checked = a.items.filter((i) => d.evidence[evKey(a.key, i.key)]?.checked);
    if (checked.length === 0) errs.push(`根拠チェックリスト「${a.label}」で1項目以上に印を付けてください。`);
    for (const i of checked) {
      const note = d.evidence[evKey(a.key, i.key)].note.trim();
      if (!note) errs.push(`「${i.label}」の所見を入力してください。`);
      else if (note.length > form.evidence.note_max_len)
        errs.push(`「${i.label}」の所見は${form.evidence.note_max_len}文字以内で入力してください。`);
    }
  }
  const max = form.scenario.max_len;
  if (!d.expected.trim()) errs.push(`${form.scenario.expected_label}を入力してください。`);
  else if (d.expected.trim().length > max) errs.push(`想定シナリオ（想定どおりの場合）は${max}文字以内です。`);
  if (!d.different.trim()) errs.push(`${form.scenario.different_label}を入力してください。`);
  else if (d.different.trim().length > max) errs.push(`想定シナリオ（想定と異なる場合）は${max}文字以内です。`);
  const filled = d.conditions.filter((c) => c.text.trim());
  if (filled.length === 0) errs.push("見直し条件を1件以上入力してください。");
  if (filled.some((c) => !form.conditions.kinds.some((k) => k.key === c.kind)))
    errs.push("見直し条件の種類を選んでください。");
  if (filled.some((c) => c.text.trim().length > form.conditions.max_len))
    errs.push(`見直し条件は1件${form.conditions.max_len}文字以内です。`);
  return errs;
}

export function MemoFormView({
  form,
  initial,
  title,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  form: MemoForm;
  initial?: MemoContent;
  title: string;
  submitLabel: string;
  onSubmit: (c: MemoContent) => Promise<void>;
  onCancel?: () => void;
}) {
  const [d, setD] = useState<Draft>(() => toDraft(initial));
  const [errors, setErrors] = useState<string[]>([]);
  const [serverError, setServerError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const setEv = (k: string, patch: Partial<{ checked: boolean; note: string }>) =>
    setD((s) => ({ ...s, evidence: { ...s.evidence, [k]: { ...(s.evidence[k] ?? { checked: false, note: "" }), ...patch } } }));
  const setCond = (i: number, patch: Partial<{ kind: string; text: string }>) =>
    setD((s) => ({ ...s, conditions: s.conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)) }));

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const errs = validate(form, d);
    setErrors(errs);
    setServerError(null);
    if (errs.length) return;
    setSaving(true);
    onSubmit(toContent(form, d))
      .catch((err: Error) => setServerError(err.message))
      .finally(() => setSaving(false));
  };

  return (
    <form className="memo-form" onSubmit={submit} noValidate aria-label={title}>
      <h3>{title}</h3>
      <p className="notice-box">{form.notice}</p>
      <p className="note small">{form.immutability_note}</p>

      <fieldset>
        <legend>{form.stance.label}（必須）</legend>
        <p className="hint small">{form.stance.help}</p>
        <div className="choice-row">
          {form.stance.options.map((o) => (
            <label key={o.key} className="check">
              <input
                type="radio"
                name="stance"
                value={o.key}
                checked={d.stance === o.key}
                onChange={() => setD((s) => ({ ...s, stance: o.key }))}
              />
              {o.label}
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend>{form.evidence.label}（必須）</legend>
        <p className="hint small">{form.evidence.help}</p>
        {form.evidence.areas.map((a) => (
          <div key={a.key} className="ev-area">
            <p className="ev-area-title">{a.label}</p>
            {a.items.map((i) => {
              const k = evKey(a.key, i.key);
              const e = d.evidence[k];
              return (
                <div key={k} className="ev-item">
                  <label className="check">
                    <input type="checkbox" checked={!!e?.checked} onChange={(x) => setEv(k, { checked: x.target.checked })} />
                    確認した：{i.label}
                  </label>
                  {e?.checked && (
                    <textarea
                      aria-label={`「${i.label}」の所見`}
                      placeholder="所見（短文）"
                      rows={2}
                      maxLength={form.evidence.note_max_len}
                      value={e.note}
                      onChange={(x) => setEv(k, { note: x.target.value })}
                    />
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </fieldset>

      <fieldset>
        <legend>{form.scenario.label}（必須）</legend>
        <label className="block-label">
          {form.scenario.expected_label}
          <textarea
            rows={3}
            maxLength={form.scenario.max_len}
            value={d.expected}
            onChange={(x) => setD((s) => ({ ...s, expected: x.target.value }))}
          />
        </label>
        <label className="block-label">
          {form.scenario.different_label}
          <textarea
            rows={3}
            maxLength={form.scenario.max_len}
            value={d.different}
            onChange={(x) => setD((s) => ({ ...s, different: x.target.value }))}
          />
        </label>
      </fieldset>

      <fieldset>
        <legend>{form.conditions.label}</legend>
        <p className="hint small">{form.conditions.help}</p>
        {d.conditions.map((c, i) => (
          <div key={i} className="cond-row">
            <select aria-label={`見直し条件${i + 1}の種類`} value={c.kind} onChange={(x) => setCond(i, { kind: x.target.value })}>
              <option value="">種類を選ぶ</option>
              {form.conditions.kinds.map((k) => (
                <option key={k.key} value={k.key}>
                  {k.label}
                </option>
              ))}
            </select>
            <input
              type="text"
              aria-label={`見直し条件${i + 1}`}
              maxLength={form.conditions.max_len}
              value={c.text}
              onChange={(x) => setCond(i, { text: x.target.value })}
            />
            {d.conditions.length > 1 && (
              <button
                type="button"
                className="small-btn"
                onClick={() => setD((s) => ({ ...s, conditions: s.conditions.filter((_, j) => j !== i) }))}
              >
                この行を消す
              </button>
            )}
          </div>
        ))}
        {d.conditions.length < form.conditions.max_items && (
          <button
            type="button"
            className="small-btn"
            onClick={() => setD((s) => ({ ...s, conditions: [...s.conditions, { kind: "", text: "" }] }))}
          >
            見直し条件を追加
          </button>
        )}
      </fieldset>

      <fieldset>
        <legend>{form.review_date.label}</legend>
        <p className="hint small">{form.review_date.help}</p>
        <input
          type="date"
          aria-label={form.review_date.label}
          value={d.review_date}
          onChange={(x) => setD((s) => ({ ...s, review_date: x.target.value }))}
        />
      </fieldset>

      {errors.length > 0 && (
        <div className="error" role="alert">
          <p>保存できません。次の項目を確認してください。</p>
          <ul>
            {errors.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        </div>
      )}
      {serverError && (
        <p className="error" role="alert">
          保存できませんでした：{serverError}
        </p>
      )}
      <div className="actions">
        <button type="submit" className="primary" disabled={saving}>
          {saving ? "保存中…" : submitLabel}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel} disabled={saving}>
            閉じる
          </button>
        )}
      </div>
    </form>
  );
}

// ---------------------------------------------------------------- read-only content view

export function MemoContentView({ form, content }: { form: MemoForm; content: MemoContent }) {
  const stance = form.stance.options.find((o) => o.key === content.stance)?.label ?? content.stance;
  const kinds = Object.fromEntries(form.conditions.kinds.map((k) => [k.key, k.label]));
  return (
    <dl className="memo-content">
      <dt>{form.stance.label}</dt>
      <dd>{stance}</dd>
      <dt>{form.evidence.label}</dt>
      <dd>
        {form.evidence.areas.map((a) => (
          <div key={a.key}>
            <span className="ev-area-title">{a.label}</span>
            <ul className="facts-list">
              {content.evidence
                .filter((e) => e.area === a.key)
                .map((e) => (
                  <li key={e.item}>
                    {a.items.find((i) => i.key === e.item)?.label ?? e.item}：{e.note}
                  </li>
                ))}
            </ul>
          </div>
        ))}
      </dd>
      <dt>{form.scenario.expected_label}</dt>
      <dd className="pre">{content.scenario.expected}</dd>
      <dt>{form.scenario.different_label}</dt>
      <dd className="pre">{content.scenario.different}</dd>
      <dt>見直し条件</dt>
      <dd>
        <ul className="facts-list">
          {content.conditions.map((c, i) => (
            <li key={i}>
              ［{kinds[c.kind] ?? c.kind}］{c.text}
            </li>
          ))}
        </ul>
      </dd>
      <dt>見直し日</dt>
      <dd>{content.review_date ?? "設定なし"}</dd>
    </dl>
  );
}

// ---------------------------------------------------------------- self review (追記)

export function SelfReviewForm({
  form,
  memoId,
  revisionId,
  onSaved,
}: {
  form: MemoForm;
  memoId: number;
  revisionId: number;
  onSaved: () => void;
}) {
  const sr = form.self_review;
  const [process, setProcess] = useState("");
  const [result, setResult] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!process || !result) {
      setError("プロセスと結果の両方を自分で選んでください。");
      return;
    }
    setSaving(true);
    setError(null);
    memoApi
      .addSelfReview(memoId, { revision_id: revisionId, process, result, note: note.trim() })
      .then(() => {
        setProcess("");
        setResult("");
        setNote("");
        onSaved();
      })
      .catch((err: Error) => setError(`保存できませんでした：${err.message}`))
      .finally(() => setSaving(false));
  };
  return (
    <form className="memo-form" onSubmit={submit} aria-label={sr.label}>
      <h3>{sr.label}</h3>
      <p className="hint small">{sr.help}</p>
      {[
        { name: "process", cfg: sr.process, value: process, set: setProcess },
        { name: "result", cfg: sr.result, value: result, set: setResult },
      ].map((g) => (
        <fieldset key={g.name}>
          <legend>{g.cfg.label}</legend>
          <div className="choice-row">
            {g.cfg.options.map((o) => (
              <label key={o.key} className="check">
                <input type="radio" name={`sr-${g.name}`} checked={g.value === o.key} onChange={() => g.set(o.key)} />
                {o.label}
              </label>
            ))}
          </div>
        </fieldset>
      ))}
      <label className="block-label">
        {sr.note_label}
        <textarea rows={3} maxLength={sr.note_max_len} value={note} onChange={(x) => setNote(x.target.value)} />
      </label>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="actions">
        <button type="submit" className="primary" disabled={saving}>
          {saving ? "保存中…" : "振り返りを追記する"}
        </button>
      </div>
    </form>
  );
}

export function SelfReviewList({ form, reviews }: { form: MemoForm; reviews: MemoSelfReview[] }) {
  if (reviews.length === 0) return <p className="note">振り返りの追記はまだありません。</p>;
  const lab = (opts: { key: string; label: string }[], k: string) => opts.find((o) => o.key === k)?.label ?? k;
  return (
    <ul className="plain review-list">
      {reviews.map((v) => (
        <li key={v.review_id}>
          <span className="small note">{fmtDateTime(v.created_at)} 追記</span>
          <span className="block">
            プロセス：{lab(form.self_review.process.options, v.process)}／結果：
            {lab(form.self_review.result.options, v.result)}
          </span>
          {v.note && <span className="block pre">{v.note}</span>}
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------- memo list table

export function MemoTable({ list, showStock }: { list: MemoList; showStock: boolean }) {
  if (list.items.length === 0) return <p className="note">判断メモはまだありません。</p>;
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th scope="col">メモ</th>
            {showStock && <th scope="col">銘柄</th>}
            <th scope="col">区分（自分の記録）</th>
            <th scope="col">作成</th>
            <th scope="col">最新リビジョン</th>
            <th scope="col">見直し日</th>
          </tr>
        </thead>
        <tbody>
          {list.items.map((m) => (
            <tr key={m.memo_id}>
              <td>
                <Link href={`/memos/${m.memo_id}`}>メモ#{m.memo_id}</Link>
              </td>
              {showStock && (
                <td>
                  <Link href={`/stocks/${m.code}`}>
                    {m.display_code} {m.name ?? ""}
                  </Link>
                </td>
              )}
              <td>{m.stance_label ?? m.stance}</td>
              <td className="num">{fmtDateTime(m.created_at)}</td>
              <td className="num">
                {m.revision_count}（{fmtDateTime(m.revised_at)}）
              </td>
              <td>
                {m.review_date ?? "—"}
                {m.due && <span className="badge">見直し日が到来</span>}
                {m.review_count > 0 && <span className="small note block">振り返り{m.review_count}件</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- stock page section

export function StockMemoSection({ code, name }: { code: string; name: string }) {
  const { form, error: formError } = useMemoForm();
  const [list, setList] = useState<MemoList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    memoApi
      .forStock(code)
      .then((l) => !cancelled && setList(l))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [code]);

  return (
    <section className="panel memo-section" id="memo" aria-labelledby="memo-title">
      <div className="panel-head">
        <h2 id="memo-title">判断メモ</h2>
        {form && !open && (
          <button type="button" onClick={() => setOpen(true)}>
            判断メモを書く
          </button>
        )}
      </div>
      <p className="note small">
        チャート・財務・市場環境の各画面で確認したことを、自分の言葉で記録します。保存時点の主な数値は自動で記録されます。
        {form ? ` ${form.notice}` : ""}
      </p>
      {(formError || error) && (
        <p className="error" role="alert">
          判断メモの読み込みに失敗しました：{formError ?? error}
        </p>
      )}
      {open && form && (
        <MemoFormView
          form={form}
          title={`${name}の判断メモを書く`}
          submitLabel="保存する（保存後は編集・削除できません）"
          onCancel={() => setOpen(false)}
          onSubmit={(c) => memoApi.create(code, c).then((m) => navigate(`/memos/${m.memo_id}`))}
        />
      )}
      {list && <MemoTable list={list} showStock={false} />}
    </section>
  );
}
