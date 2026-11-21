import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { api, type GlossaryEntry } from "../api";

/**
 * 用語のその場解説（要件 2.4）: label + "？" button; the popover shows
 * 1) a one-sentence definition, 2) the formula with this stock's actual values,
 * 3) a common misconception. Text comes from the server (content/glossary.json).
 */
export function Term({ term, code, children }: { term: string; code?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [entry, setEntry] = useState<GlossaryEntry | null>(null);
  const [error, setError] = useState<string | null>(null);
  const wrap = useRef<HTMLSpanElement>(null);
  const id = useId();

  useEffect(() => {
    setEntry(null);
    setError(null);
  }, [term, code]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && !entry) {
      setError(null);
      api
        .glossary(term, code)
        .then(setEntry)
        .catch((e: Error) => setError(`解説を読み込めませんでした：${e.message}`));
    }
  };

  return (
    <span className="term" ref={wrap}>
      {children}
      <button
        type="button"
        className="term-q"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-label="用語の解説を表示"
        onClick={toggle}
      >
        ？
      </button>
      {open && (
        <span className="popover" role="dialog" id={id} aria-label="用語の解説">
          {error && <span className="pop-row">{error}</span>}
          {!error && !entry && <span className="pop-row">読み込み中…</span>}
          {entry && (
            <>
              <span className="pop-title">{entry.label}</span>
              <span className="pop-row">
                <span className="pop-head">定義</span>
                {entry.definition}
              </span>
              <span className="pop-row">
                <span className="pop-head">
                  {entry.formula_is_generic ? "計算式" : "この銘柄の値で計算すると"}
                </span>
                <span className="formula">{entry.formula}</span>
                {entry.formula_note && <span className="pop-sub">{entry.formula_note}</span>}
              </span>
              <span className="pop-row">
                <span className="pop-head">誤解しやすい点</span>
                {entry.misconception}
              </span>
            </>
          )}
          <button type="button" className="pop-close" onClick={() => setOpen(false)}>
            閉じる
          </button>
        </span>
      )}
    </span>
  );
}
