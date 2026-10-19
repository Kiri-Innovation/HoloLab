import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import "../../src/styles.css";
import { RunsPanel } from "../../src/canvas/RunsPanel";

const query = new URLSearchParams(location.search);
document.documentElement.dataset.theme = query.get("theme") || "light";
function Fixture() {
  const [current, setCurrent] = useState<string | null>(null);
  const [focusRequest, setFocusRequest] = useState<{ snapshotId: string; nonce: number } | null>(null);
  return <>{query.has("focus") && <nav>{["abcdef00", "abcdef03", "new-run"].map(snapshotId =>
    <button key={snapshotId} onClick={() => setFocusRequest(previous => ({ snapshotId, nonce: (previous?.nonce ?? 0) + 1 }))}>Jump {snapshotId}</button>
  )}</nav>}<div style={{ width: 420, height: 420, display: "flex", margin: 24, border: "1px solid var(--border)" }}>
    <RunsPanel focusRequest={focusRequest} workflowId="runs-favorites-test" currentSnapshotId={current}
      onOpenSnapshot={setCurrent}
      draftDiff={query.has("diff") ? [{ description: "新增节点 Camera" }] : []} />
  </div></>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
