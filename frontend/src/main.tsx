// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Elastic License 2.0 (ELv2). See LICENSE file.
import { createRoot } from "react-dom/client";
import { App } from "./App";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<App />);
}
