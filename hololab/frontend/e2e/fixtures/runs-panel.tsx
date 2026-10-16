import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import "../../src/styles.css";
import { RunsPanel } from "../../src/canvas/RunsPanel";

const query = new URLSearchParams(location.search);
document.documentElement.dataset.theme = query.get("theme") || "light";
function Fixture() {
  const [current, setCurrent] = useState<string | null>(null);
  return <div style={{ width: 420, height: 420, display: "flex", margin: 24, border: "1px solid var(--border)" }}>
    <RunsPanel workflowId="runs-favorites-test" currentSnapshotId={current}
      onOpenSnapshot={setCurrent}
      draftDiff={query.has("diff") ? [{ description: "新增节点 Camera" }] : []} />
  </div>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
