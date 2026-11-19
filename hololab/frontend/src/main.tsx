import React from "react";
import { initializeInterfaceSnapshots, configureInterfacePersistence } from "./canvas/interfaceSnapshot";
import ReactDOM from "react-dom/client";
import App from "./App";
import { TooltipLayer } from "./ui/TooltipLayer";
import { ToastViewport } from "./ui/Toast";
import "./styles.css";
import { applyThemePreload } from "./theme/theme";

// Apply the saved theme BEFORE React renders so first paint matches the
// user's preference — otherwise a dark-mode user sees a flash of light
// while the ThemeProvider mounts.
applyThemePreload();

// Older gateways can recover historical declarations, but their HTTP output
// schema strips extension fields. Do not promise durable envelopes until the
// matching backend supports them; candidate recovery needs no restart.
const interfacePersistence = fetch('/openapi.json').then(r=>r.json()).then(schema=>
  configureInterfacePersistence(!!schema.components?.schemas?.GraphNodeOut?.properties?.interface_snapshot)
).catch(()=>configureInterfacePersistence(false));
void Promise.all([initializeInterfaceSnapshots(), interfacePersistence]).then(() => ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
    <ToastViewport />
    <TooltipLayer />
  </React.StrictMode>,
));
