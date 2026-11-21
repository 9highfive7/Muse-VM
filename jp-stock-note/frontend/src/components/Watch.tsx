// Phase 4: ウォッチリスト (要件 3.9). 追加時は監視理由が必須（タグ1つ以上、または自由入力のみ）。
import { useEffect, useState, type FormEvent } from "react";
import { watchApi, type WatchEntry, type WatchEntryResponse } from "../api";
import { fmtDateTime } from "./Memo";

/** Pseudo tag: "the free-text reason is my tag" (要件 3.9 例：…／自由入力). Not sent to the server. */
export const FREE_TAG = "自由入力（監視理由の文のみ）";

export function WatchAddForm({
  tags,
  reasonMax,
  fixedCode,
  onAdded,
  onCancel,
}: {
  tags: string[];
  reasonMax: number;
  fixedCode?: string;
  onAdded: (e: WatchEntry) => void;
  onCancel?: () => void;
}) {
  const [code, setCode] = useState(fixedCode ?? "");
  const [reason, setReason] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const toggle = (t: string) => setPicked((p) => (p.includes(t) ? p.filter((x) => x !== t) : [...p, t]));

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const errs: string[] = [];
    if (!code.trim()) errs.push("証券コードを入力してください。");
    if (picked.length === 0) errs.push("監視理由タグを1つ以上選んでください（タグが合わない場合は「自由入力」）。");
    if (!reason.trim()) errs.push("監視理由を入力してください（空欄では追加できません）。");
    if (errs.length) {
      setError(errs.join(" "));
      return;
    }
    setSaving(true);
    setError(null);
    watchApi
      .add(
        code.trim(),
        reason.trim(),
        picked.filter((t) => t !== FREE_TAG),
      )
      .then(onAdded)
      .catch((err: Error) => setError(err.message))
      .finally(() => setSaving(false));
  };

  return (
    <form className="watch-form" onSubmit={submit} noValidate aria-label="ウォッチリストに追加">
      {!fixedCode && (
        <label className="block-label">
          証券コード（4桁または5桁）
          <input type="text" value={code} maxLength={12} onChange={(x) => setCode(x.target.value)} placeholder="例：7203" />
        </label>
      )}
      <fieldset>
        <legend>監視理由タグ（1つ以上必須）</legend>
        <div className="choice-row">
          {[...tags, FREE_TAG].map((t) => (
            <label key={t} className="check">
              <input type="checkbox" checked={picked.includes(t)} onChange={() => toggle(t)} />
              {t}
            </label>
          ))}
        </div>
      </fieldset>
      <label className="block-label">
        監視理由（必須・{reasonMax}文字以内）：なぜこの銘柄を見ているか
        <textarea rows={2} maxLength={reasonMax} value={reason} onChange={(x) => setReason(x.target.value)} />
      </label>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="actions">
        <button type="submit" className="primary" disabled={saving}>
          {saving ? "追加中…" : "ウォッチリストに追加"}
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

/** Stock page (S3): 「ウォッチリストに追加」→ 監視理由タグ入力 → S3. */
export function StockWatchControl({ code }: { code: string }) {
  const [data, setData] = useState<WatchEntryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = () =>
    watchApi
      .entry(code)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  useEffect(() => {
    setOpen(false);
    load();
  }, [code]);

  const remove = () => {
    if (!window.confirm("この銘柄をウォッチリストから外しますか？（履歴は残ります）")) return;
    setBusy(true);
    watchApi
      .remove(code)
      .then(load)
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  if (error)
    return (
      <p className="error small" role="alert">
        ウォッチリストの状態を読み込めませんでした：{error}
      </p>
    );
  if (!data) return null;
  const e = data.entry;
  return (
    <div className="watch-control" aria-label="ウォッチリスト">
      {e ? (
        <p>
          <span className="badge">ウォッチリスト追加済み</span> {fmtDateTime(e.added_at).slice(0, 10)}追加
          {e.tags.length > 0 && `／${e.tags.join("・")}`}／監視理由：{e.reason}{" "}
          <button type="button" className="small-btn" onClick={remove} disabled={busy}>
            ウォッチリストから外す
          </button>
        </p>
      ) : open ? (
        <WatchAddForm
          tags={data.tags}
          reasonMax={data.reason_max_len}
          fixedCode={code}
          onAdded={() => {
            setOpen(false);
            load();
          }}
          onCancel={() => setOpen(false)}
        />
      ) : (
        <p>
          <button type="button" onClick={() => setOpen(true)} disabled={data.count >= data.max}>
            ウォッチリストに追加
          </button>{" "}
          <span className="small note">
            （{data.count}/{data.max}銘柄）
            {data.count >= data.max && "上限に達しています。追加するには先に別の銘柄を外してください。"}
          </span>
        </p>
      )}
    </div>
  );
}
