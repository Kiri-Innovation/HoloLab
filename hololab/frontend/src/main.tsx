import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";
import { applyThemePreload } from "./theme/theme";

// Apply the saved theme BEFORE React renders so first paint matches the
// user's preference — otherwise a dark-mode user sees a flash of light
// while the ThemeProvider mounts.
applyThemePreload();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
