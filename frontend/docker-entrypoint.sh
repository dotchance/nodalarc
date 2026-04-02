#!/bin/sh
set -e

# Write runtime config.js from environment variables.
# If VS_API_URL is not set, write empty values — the browser-side
# config.ts will auto-derive from window.location.hostname.
VS_API_URL="${VS_API_URL:-}"
WS_URL="${WS_URL:-}"

cat > /usr/share/nginx/html/config.js << EOF
window.NODALARC_CONFIG = {
  vsApiUrl: "${VS_API_URL}",
  wsUrl: "${WS_URL}"
};
EOF

if [ -n "$VS_API_URL" ]; then
    echo "VF config: VS_API_URL=${VS_API_URL} WS_URL=${WS_URL}"
else
    echo "VF config: auto-derive from browser hostname (VS_API_URL not set)"
fi

exec "$@"
