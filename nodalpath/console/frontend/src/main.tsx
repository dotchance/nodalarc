import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { loadConfig } from "./config";

// Load runtime config from server (ports from platform.yaml) before rendering.
loadConfig().finally(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
});
