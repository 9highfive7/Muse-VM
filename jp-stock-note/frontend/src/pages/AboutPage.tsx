import { useEffect, useState } from "react";
import { appApi, type About } from "../api";

// S0 初回説明（要件 2.1 S0 / 3.11 拡張版免責）。文言はすべて content/about.json（サーバー側・禁止語テスト対象）。

function AboutBody({ about }: { about: About }) {
  return (
    <>
      <h1 id="about-title">{about.title}</h1>
      <p className="lead">{about.lead}</p>
      {about.sections.map((s) => (
        <section key={s.key} className={`about-section${s.key === "disclaimer" ? " about-disclaimer" : ""}`}>
          <h2>{s.heading}</h2>
          <ul>
            {s.items.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}

/** First-run screen. Rendered *instead of* every other screen until the user
 *  agrees; the agreement is stored by the server (app_settings.first_run_ack). */
export function FirstRunScreen({ onDone }: { onDone: () => void }) {
  const [about, setAbout] = useState<About | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    appApi
      .about()
      .then(setAbout)
      .catch((e: Error) => setError(`説明文の読み込みに失敗しました：${e.message}`));
  }, []);

  const agree = () => {
    setSaving(true);
    setError(null);
    appApi
      .ackFirstRun()
      .then((r) => (r.required ? setError("確認の記録に失敗しました。もう一度お試しください。") : onDone()))
      .catch((e: Error) => setError(`確認の記録に失敗しました：${e.message}`))
      .finally(() => setSaving(false));
  };

  return (
    <section className="about" aria-labelledby="about-title">
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!about && !error && <p aria-live="polite">読み込み中…</p>}
      {about && (
        <>
          <AboutBody about={about} />
          <div className="ack-box">
            <label className="check">
              <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
              {about.ack_label}
            </label>
            <button type="button" className="primary" disabled={!checked || saving} onClick={agree}>
              {saving ? "記録中…" : "確認してはじめる"}
            </button>
            <p className="note small">{about.ack_note}</p>
          </div>
        </>
      )}
    </section>
  );
}

/** Re-read view (footer link「このアプリについて」, S5). */
export default function AboutPage() {
  const [about, setAbout] = useState<About | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    appApi
      .about()
      .then(setAbout)
      .catch((e: Error) => setError(`読み込みに失敗しました：${e.message}`));
  }, []);
  if (error)
    return (
      <p className="error" role="alert">
        {error}
      </p>
    );
  if (!about) return <p aria-live="polite">読み込み中…</p>;
  const acked = about.first_run.acknowledged_at;
  return (
    <section className="about" aria-labelledby="about-title">
      <AboutBody about={about} />
      {acked && (
        <p className="note small">
          {about.reread_note}（確認日時：{new Date(acked).toLocaleString("ja-JP", { hour12: false })}）
        </p>
      )}
    </section>
  );
}
