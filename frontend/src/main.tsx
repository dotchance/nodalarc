// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { applyTheme } from "./styles/tokens";
import { createRoot } from "react-dom/client";
import { App } from "./App";

applyTheme();

const root = document.getElementById("root");
if (root) {
  const wantsFixture =
    import.meta.env.DEV && new URLSearchParams(window.location.search).has("fixture");
  if (wantsFixture) {
    // Dev-only design review surface; the dynamic import keeps it out of
    // production bundles entirely.
    void import("./design/DesignSystemFixture").then(({ DesignSystemFixture }) => {
      createRoot(root).render(<DesignSystemFixture />);
    });
  } else {
    createRoot(root).render(<App />);
  }
}
