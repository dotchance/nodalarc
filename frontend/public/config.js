// Runtime config for containerized deployment.
// This file is overwritten at container startup by docker-entrypoint.sh.
// Do not import or bundle — loaded via <script> tag in index.html.
window.NODALARC_CONFIG = {
  vsApiUrl: "",
  wsUrl: ""
};
