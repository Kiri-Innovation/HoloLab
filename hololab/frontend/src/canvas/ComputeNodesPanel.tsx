// Right-side panel: live view of compute nodes (kiri4090 etc.) — plus a
// per-node ⚙ button that opens a "Node settings" drawer for editing
// workspace_root, legacy_workspace_roots, node_name, advertised_url.
//
// The drawer talks to GET/PATCH /api/nodes/{id}/config, which the
// gateway forwards to the node over WS. The node validates and
// persists to config.yaml, hot-restarts its file server on
// workspace-root changes, and echoes back the effective config.

import { useCallback, useEffect, useState } from "react";
import type { ComputeNode, NodeEffectiveConfig } from "../wire";
import { ApiError, getNodeConfig, patchNodeConfig } from "../api";

export interface ComputeNodesPanelProps {
  nodes: ComputeNode[];
  onConfigChanged?: (nodeId: string) => void;
}

export function ComputeNodesPanel({ nodes, onConfigChanged }: ComputeNodesPanelProps) {
  const [drawerFor, setDrawerFor] = useState<string | null>(null);
  const drawerNode = drawerFor ? nodes.find((n) => n.node_id === drawerFor) : null;

  return (
    <div className="hl-dock hl-compute-panel"
      style={{
        padding: "14px 14px 20px",
        overflow: "auto",
        height: "100%",
        fontSize: "var(--fs-sm)",
        color: "var(--text-body)",
      }}
    >
      <h2 className="hl-section-title"
        style={{
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          color: "var(--text-subtle)",
          margin: "0 0 10px",
        }}
      >
        Compute nodes
      </h2>
      {nodes.length === 0 && (
        <div style={{ color: "var(--text-muted)", fontSize: "var(--fs-xs)" }}>
          No compute nodes connected.
        </div>
      )}
      {nodes.map((n) => (
        <div
          key={n.node_id}
          className="hl-dock-card"
          style={{
            border: "1px solid var(--border)",
            borderRadius: "var(--radius-md)",
            padding: "10px 12px",
            marginBottom: 8,
            background: "var(--surface-2)",
            position: "relative",
            transition: "border-color var(--dur-fast) var(--ease)",
          }}
        >
          <div style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <span
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: "var(--radius-pill)",
                    background: "var(--success)",
                  }}
                />
                <span
                  style={{
                    fontWeight: 600,
                    fontSize: "var(--fs-sm)",
                    color: "var(--text)",
                  }}
                >
                  {n.node_name}
                </span>
              </div>
              <div
                style={{
                  fontSize: "var(--fs-micro)",
                  color: "var(--text-subtle)",
                  fontFamily: "var(--font-mono)",
                  marginTop: 3,
                }}
              >
                {n.node_id.slice(0, 8)}
              </div>
              {n.gpu.count > 0 && (
                <div
                  style={{
                    fontSize: "var(--fs-xs)",
                    color: "var(--text-body)",
                    marginTop: 6,
                  }}
                >
                  {n.gpu.name || "GPU"}
                  <span style={{ color: "var(--text-subtle)" }}> · </span>
                  <span style={{ fontVariantNumeric: "tabular-nums" }}>
                    {n.gpu.total_vram_gb?.toFixed(1)} GiB
                  </span>
                </div>
              )}
              <div
                style={{
                  fontSize: "var(--fs-micro)",
                  color: "var(--text-muted)",
                  marginTop: 4,
                }}
              >
                {n.packs.length} pack{n.packs.length === 1 ? "" : "s"}
              </div>
              {n.workspace_root && (
                <div
                  style={{
                    fontSize: "var(--fs-micro)",
                    color: "var(--text-muted)",
                    marginTop: 8,
                    fontFamily: "var(--font-mono)",
                    wordBreak: "break-all",
                    lineHeight: 1.4,
                  }}
                  title={n.workspace_root}
                >
                  ↳ {n.workspace_root}
                </div>
              )}
              {n.legacy_workspace_roots.length > 0 && (
                <div
                  style={{
                    fontSize: "var(--fs-micro)",
                    color: "var(--text-subtle)",
                    marginTop: 3,
                  }}
                  title={n.legacy_workspace_roots.join("\n")}
                >
                  + {n.legacy_workspace_roots.length} legacy root
                  {n.legacy_workspace_roots.length === 1 ? "" : "s"}
                </div>
              )}
            </div>
            <button
              type="button"
              onClick={() => setDrawerFor(n.node_id)}
              title="Node settings"
              style={{
                border: "1px solid var(--border)",
                background: "var(--surface)",
                color: "var(--text-muted)",
                borderRadius: "var(--radius-sm)",
                width: "var(--control-h-sm)",
                height: "var(--control-h-sm)",
                cursor: "pointer",
                fontSize: 13,
                padding: 0,
                lineHeight: 1,
                transition: "background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease)",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = "var(--surface-hover)";
                e.currentTarget.style.color = "var(--text)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "var(--surface)";
                e.currentTarget.style.color = "var(--text-muted)";
              }}
            >
              ⚙
            </button>
          </div>
        </div>
      ))}
      {drawerNode && (
        <NodeSettingsDrawer
          node={drawerNode}
          onClose={() => setDrawerFor(null)}
          onSaved={() => onConfigChanged?.(drawerNode.node_id)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Node settings drawer — right-edge overlay on the compute-nodes panel.
// ---------------------------------------------------------------------------

function NodeSettingsDrawer({
  node,
  onClose,
  onSaved,
}: {
  node: ComputeNode;
  onClose: () => void;
  onSaved?: () => void;
}) {
  const [config, setConfig] = useState<NodeEffectiveConfig | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  // Draft state for the editable fields (only these can be patched — the
  // rest are read-only and require config.yaml + node restart).
  const [nodeName, setNodeName] = useState("");
  const [workspaceRoot, setWorkspaceRoot] = useState("");
  const [legacyRoots, setLegacyRoots] = useState<string[]>([]);
  const [advertisedUrl, setAdvertisedUrl] = useState("");

  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await getNodeConfig(node.node_id);
      setConfig(r.config);
      setNodeName(r.config.node_name);
      setWorkspaceRoot(r.config.workspace_root);
      setLegacyRoots([...r.config.legacy_workspace_roots]);
      setAdvertisedUrl(r.config.advertised_url ?? "");
      setLoadErr(null);
    } catch (e) {
      const msg = e instanceof ApiError ? `HTTP ${e.status}` : (e as Error).message;
      setLoadErr(`could not load config: ${msg}`);
    }
  }, [node.node_id]);

  useEffect(() => {
    void load();
  }, [load]);

  const doSave = async () => {
    setSaving(true);
    setSaveErr(null);
    try {
      // Only send fields that actually changed — avoids re-validating
      // things the user didn't touch and keeps the patch minimal.
      const patch: Record<string, unknown> = {};
      if (config) {
        if (nodeName.trim() !== config.node_name) patch.node_name = nodeName.trim();
        if (workspaceRoot.trim() !== config.workspace_root) {
          patch.workspace_root = workspaceRoot.trim();
        }
        const trimmedLegacy = legacyRoots.map((s) => s.trim()).filter((s) => s.length > 0);
        const sameLegacy =
          trimmedLegacy.length === config.legacy_workspace_roots.length &&
          trimmedLegacy.every((s, i) => s === config.legacy_workspace_roots[i]);
        if (!sameLegacy) patch.legacy_workspace_roots = trimmedLegacy;
        const advNorm = advertisedUrl.trim() || null;
        if (advNorm !== (config.advertised_url ?? null)) patch.advertised_url = advNorm;
      }
      if (Object.keys(patch).length === 0) {
        setSaveErr("no changes to apply");
        setSaving(false);
        return;
      }
      const r = await patchNodeConfig(
        node.node_id,
        patch as Parameters<typeof patchNodeConfig>[1],
      );
      setConfig(r.config);
      setNodeName(r.config.node_name);
      setWorkspaceRoot(r.config.workspace_root);
      setLegacyRoots([...r.config.legacy_workspace_roots]);
      setAdvertisedUrl(r.config.advertised_url ?? "");
      onSaved?.();
    } catch (e) {
      const detail =
        e instanceof ApiError && typeof e.detail === "object" && e.detail
          ? (e.detail as { detail?: string }).detail
          : null;
      setSaveErr(detail ?? (e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <aside className="hl-node-drawer"
      style={{
        position: "absolute",
        top: 0,
        right: 0,
        bottom: 0,
        width: 360,
        background: "var(--surface)",
        border: "1px solid var(--border)",
        boxShadow: "var(--shadow-2)",
        display: "flex",
        flexDirection: "column",
        fontSize: "var(--fs-sm)",
        color: "var(--text-body)",
        zIndex: 20,
      }}
    >
      <div className="hl-drawer-header"
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid var(--border)",
          background: "var(--surface-2)",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 10,
              fontWeight: 600,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
              color: "var(--text-subtle)",
            }}
          >
            Node settings
          </div>
          <div
            style={{
              marginTop: 2,
              fontSize: "var(--fs-md)",
              fontWeight: 600,
              color: "var(--text)",
              letterSpacing: "-0.005em",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {node.node_name}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          title="close"
          style={{
            border: "none",
            background: "transparent",
            cursor: "pointer",
            padding: 4,
            fontSize: 16,
            color: "var(--text-muted)",
            lineHeight: 1,
            borderRadius: "var(--radius-sm)",
            transition: "background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease)",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "var(--surface-hover)";
            e.currentTarget.style.color = "var(--text)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "transparent";
            e.currentTarget.style.color = "var(--text-muted)";
          }}
        >
          ×
        </button>
      </div>
      <div style={{ padding: "14px 16px 8px", overflow: "auto", flex: 1 }}>
        {loadErr && <ErrorLine text={loadErr} />}
        {config === null ? (
          <div style={{ color: "var(--text-subtle)" }}>Loading…</div>
        ) : (
          <>
            <FieldLabel>Node name</FieldLabel>
            <TextInput value={nodeName} onChange={setNodeName} />
            <Hint>Display alias only — never used for identity matching.</Hint>

            <FieldLabel>Workspace root</FieldLabel>
            <TextInput value={workspaceRoot} onChange={setWorkspaceRoot} monospace />
            <Hint>
              Where new job artifacts land. Must be an absolute path. Changing this
              hot-restarts the file server; existing runs' outputs stay where they
              are — add their path below to keep serving them.
            </Hint>

            <FieldLabel>Legacy workspace roots</FieldLabel>
            <LegacyRootsEditor value={legacyRoots} onChange={setLegacyRoots} />
            <Hint>
              Read-only fallbacks the file server also searches so old artifacts
              stay resolvable after moving the primary root.
            </Hint>

            <FieldLabel>Advertised URL</FieldLabel>
            <TextInput
              value={advertisedUrl}
              onChange={setAdvertisedUrl}
              monospace
              placeholder="(auto — leave blank for loopback)"
            />
            <Hint>
              How the gateway reaches this node's file server for
              /proxy/{"{"}node{"}"}/{"{"}sub{"}"} — normally auto-derived.
            </Hint>

            <div
              style={{
                marginTop: 20,
                paddingTop: 14,
                borderTop: "1px dashed var(--border)",
              }}
            >
              <ReadOnlyRow
                label="File server"
                value={`${config.file_server_host}:${config.file_server_port}`}
              />
              <ReadOnlyRow label="Packs dir" value={config.packs_dir} />
            </div>

            {saveErr && <ErrorLine text={saveErr} />}
            <div style={{ marginTop: 16, display: "flex", gap: 8 }}>
              <button
                type="button"
                onClick={doSave}
                disabled={saving}
                style={{
                  flex: 1,
                  border: "1px solid var(--accent)",
                  background: "var(--accent)",
                  color: "var(--accent-fg)",
                  height: "var(--control-h-lg)",
                  padding: "0 12px",
                  borderRadius: "var(--radius-sm)",
                  cursor: saving ? "wait" : "pointer",
                  fontSize: "var(--fs-sm)",
                  fontWeight: 600,
                  opacity: saving ? 0.7 : 1,
                  transition: "opacity var(--dur-fast) var(--ease)",
                }}
              >
                {saving ? "Applying…" : "Apply"}
              </button>
              <button
                type="button"
                onClick={() => void load()}
                disabled={saving}
                style={{
                  border: "1px solid var(--border-strong)",
                  background: "var(--surface)",
                  color: "var(--text-body)",
                  height: "var(--control-h-lg)",
                  padding: "0 12px",
                  borderRadius: "var(--radius-sm)",
                  cursor: "pointer",
                  fontSize: "var(--fs-sm)",
                }}
              >
                Reload
              </button>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Tiny form primitives
// ---------------------------------------------------------------------------

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: "var(--fs-xs)",
        color: "var(--text-body)",
        marginTop: 14,
        marginBottom: 6,
        fontWeight: 500,
      }}
    >
      {children}
    </div>
  );
}

function TextInput({
  value,
  onChange,
  monospace,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  monospace?: boolean;
  placeholder?: string;
}) {
  return (
    <input
      type="text"
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      style={{
        width: "100%",
        fontSize: "var(--fs-sm)",
        fontFamily: monospace ? "var(--font-mono)" : "inherit",
        boxSizing: "border-box",
      }}
    />
  );
}

function Hint({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: "var(--fs-xs)",
        color: "var(--text-muted)",
        marginTop: 5,
        lineHeight: 1.5,
      }}
    >
      {children}
    </div>
  );
}

function ErrorLine({ text }: { text: string }) {
  return (
    <div
      style={{
        marginTop: 12,
        padding: "8px 10px",
        background: "var(--error-soft)",
        color: "var(--error)",
        border: "1px solid var(--error)",
        borderRadius: "var(--radius-sm)",
        fontSize: "var(--fs-xs)",
        lineHeight: 1.5,
      }}
    >
      {text}
    </div>
  );
}

function ReadOnlyRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ marginTop: 10 }}>
      <div
        style={{
          fontSize: 10,
          color: "var(--text-subtle)",
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          marginBottom: 3,
          fontWeight: 600,
        }}
      >
        {label} <span style={{ textTransform: "none", fontWeight: 400 }}>(read-only)</span>
      </div>
      <div
        style={{
          fontSize: "var(--fs-xs)",
          color: "var(--text-body)",
          fontFamily: "var(--font-mono)",
          wordBreak: "break-all",
        }}
      >
        {value}
      </div>
    </div>
  );
}

function LegacyRootsEditor({
  value,
  onChange,
}: {
  value: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <div>
      {value.map((v, i) => (
        <div key={i} style={{ display: "flex", gap: 6, marginBottom: 6 }}>
          <input
            type="text"
            value={v}
            onChange={(e) => {
              const next = [...value];
              next[i] = e.target.value;
              onChange(next);
            }}
            placeholder="/absolute/path/to/old/workspace"
            style={{
              flex: 1,
              fontSize: "var(--fs-sm)",
              fontFamily: "var(--font-mono)",
            }}
          />
          <button
            type="button"
            onClick={() => onChange(value.filter((_, j) => j !== i))}
            title="remove"
            style={{
              border: "1px solid var(--border-strong)",
              background: "var(--surface)",
              color: "var(--text-muted)",
              borderRadius: "var(--radius-sm)",
              width: "var(--control-h-md)",
              height: "var(--control-h-md)",
              cursor: "pointer",
              fontSize: 14,
              lineHeight: 1,
              transition: "background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease)",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "var(--error-soft)";
              e.currentTarget.style.color = "var(--error)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "var(--surface)";
              e.currentTarget.style.color = "var(--text-muted)";
            }}
          >
            ×
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...value, ""])}
        style={{
          marginTop: value.length ? 4 : 0,
          border: "1px dashed var(--border-strong)",
          background: "transparent",
          color: "var(--text-muted)",
          borderRadius: "var(--radius-sm)",
          height: "var(--control-h-md)",
          padding: "0 12px",
          fontSize: "var(--fs-xs)",
          cursor: "pointer",
          width: "100%",
          transition: "background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease)",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "var(--surface-hover)";
          e.currentTarget.style.color = "var(--text-body)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "transparent";
          e.currentTarget.style.color = "var(--text-muted)";
        }}
      >
        + Add legacy root
      </button>
    </div>
  );
}
