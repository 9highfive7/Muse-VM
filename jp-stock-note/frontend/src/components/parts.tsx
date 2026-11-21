// Small building blocks shared by the technical and financial sections of the
// stock page (fact lists, notes, the 第3階層 value table, scroll targets).
import type { ReactNode } from "react";
import type { Fact } from "../api";

export function Facts({ facts }: { facts: Fact[] }) {
  return (
    <ul className="facts-list">
      {facts.map((f) => (
        <li key={f.key} className={f.available ? undefined : "unavailable"}>
          {f.text}
        </li>
      ))}
    </ul>
  );
}

export function Notes({ notes }: { notes: string[] }) {
  return (
    <ul className="notes">
      {notes.map((n) => (
        <li key={n}>注記：{n}</li>
      ))}
    </ul>
  );
}

export function ValuesTable({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <table className="values">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <th scope="row">{k}</th>
            <td className="num">{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Card sentence click -> scroll to the matching 第2階層 section. Technical
 *  stages use `stage-<id>`; financial sections use their own id (`fin-…`). */
export function scrollToTarget(target?: string) {
  if (!target) return;
  const el = document.getElementById(target) ?? document.getElementById(`stage-${target}`);
  el?.scrollIntoView({ block: "start" });
}

/** Swatch for HTML legends: filled bar, hollow dashed bar (= forecast), solid or dashed line. */
export function Swatch({ color, kind }: { color: string; kind: "filled" | "hollow" | "line" | "dashed-line" }) {
  if (kind === "line" || kind === "dashed-line")
    return (
      <svg width="28" height="12" aria-hidden="true" className="swatch">
        <line x1="1" y1="6" x2="27" y2="6" stroke={color} strokeWidth="2" strokeDasharray={kind === "dashed-line" ? "5 3" : undefined} />
      </svg>
    );
  return (
    <svg width="16" height="14" aria-hidden="true" className="swatch">
      <rect
        x="1.5" y="1.5" width="13" height="11" rx="1"
        fill={kind === "filled" ? color : "#ffffff"} stroke={color} strokeWidth="1.5"
        strokeDasharray={kind === "hollow" ? "3 2" : undefined}
      />
    </svg>
  );
}
