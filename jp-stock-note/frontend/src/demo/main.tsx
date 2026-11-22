import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import fixturesUrl from "./fixtures.json?url";
import { installExportLinks, installMockFetch, stockLabel, type DemoFixtures } from "./mockApi";
import DemoBanner from "./DemoBanner";
import "../styles.css";
import "./demo.css";

// Entry of the static demo build (`npm run build:demo`, vite.config.ts swaps
// it in for ../main.tsx). Loads the recorded synthetic responses, answers
// /api/* from them, and renders the unchanged App under a permanent banner.

async function boot() {
  const root = createRoot(document.getElementById("root")!);
  let fx: DemoFixtures;
  try {
    const res = await fetch(fixturesUrl);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    fx = (await res.json()) as DemoFixtures;
  } catch (e) {
    root.render(<p className="error">デモ用のサンプルデータを読み込めませんでした：{String(e)}</p>);
    return;
  }
  installMockFetch(fx);
  installExportLinks(fx);
  const { default: App } = await import("../App");
  root.render(
    <StrictMode>
      <DemoBanner
        stocks={fx.meta.stock_pages.map((c) => stockLabel(fx, c)).join("、")}
        barsFrom={fx.meta.bars_from}
        barsTo={fx.meta.bars_to}
      />
      <App />
    </StrictMode>,
  );
}

void boot();
