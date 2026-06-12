// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { applyTheme } from "./styles/tokens";
import { createRoot } from "react-dom/client";
import { App } from "./App";

applyTheme();

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<App />);
}
