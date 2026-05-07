# VF container — React/Vite SPA served by nginx.
#
# Build context must be the frontend/ directory:
#   docker build --build-arg BUILD_HASH=$(git rev-parse --short=8 HEAD) \
#     -t nodalarc/visualization:latest frontend/
#
# Runtime config: VS_API_URL and WS_URL env vars are written to
# config.js by docker-entrypoint.sh at container startup.

FROM node:22-alpine@sha256:8ea2348b068a9544dae7317b4f3aafcdc032df1647bb7d768a05a5cad1a7683f AS build
ARG BUILD_HASH=unknown
ENV VITE_BUILD_HASH=${BUILD_HASH}
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine@sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/nodalarc.conf
COPY --from=build /app/dist /usr/share/nginx/html
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
EXPOSE 80
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
