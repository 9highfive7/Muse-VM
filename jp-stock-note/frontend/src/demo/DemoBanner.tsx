import { useEffect, useState } from "react";
import { DISABLED_EVENT, DISABLED_TEXT } from "./mockApi";

// Demo build only. Shown above every screen (incl. S0) and never dismissible.
// Neutral colours and no motion (要件 6.2: no blinking / no evaluative colours).

export default function DemoBanner({ stocks, barsFrom, barsTo }: { stocks: string; barsFrom: string; barsTo: string }) {
  const [blocked, setBlocked] = useState<string | null>(null);
  useEffect(() => {
    const on = (e: Event) => setBlocked(String((e as CustomEvent).detail ?? "この操作"));
    window.addEventListener(DISABLED_EVENT, on);
    return () => window.removeEventListener(DISABLED_EVENT, on);
  }, []);

  return (
    <div className="demo-banner" role="note" aria-label="デモ版のお知らせ">
      <p className="demo-banner-lead">これはサンプルデータによるデモです。実際のデータではありません。</p>
      <p className="demo-banner-sub">
        株価・決算・金利・為替などの数値と、ウォッチリスト・判断メモの内容は、すべて動作確認用に作った架空の値です（銘柄名は表示例として使っています）。
        デモで開ける銘柄：{stocks}（架空の日足 {barsFrom}〜{barsTo}）。保存・追加・削除・データ再取得などの操作は{DISABLED_TEXT}。
      </p>
      {blocked && (
        <p className="demo-banner-blocked" role="status">
          「{blocked}」は{DISABLED_TEXT}。表示はサンプルデータのまま変わりません。
          <button type="button" onClick={() => setBlocked(null)}>
            閉じる
          </button>
        </p>
      )}
    </div>
  );
}
