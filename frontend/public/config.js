// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Runtime config for containerized deployment.
// This file is overwritten at container startup by docker-entrypoint.sh.
// Do not import or bundle — loaded via <script> tag in index.html.
window.NODALARC_CONFIG = {
  vsApiUrl: "",
  wsUrl: ""
};
