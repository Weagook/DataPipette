FROM node:22.22.1-alpine AS builder

WORKDIR /workspace

COPY clients/typescript/package.json clients/typescript/package-lock.json clients/typescript/
COPY clients/typescript/tsconfig.json clients/typescript/tsconfig.build.json clients/typescript/
COPY clients/typescript/src clients/typescript/src
RUN npm ci --prefix clients/typescript \
    && npm run build --prefix clients/typescript

COPY frontend/package.json frontend/package-lock.json frontend/
RUN npm ci --prefix frontend
COPY frontend frontend
COPY docs/assets docs/assets
RUN npm run build --prefix frontend

FROM nginx:1.28-alpine AS runtime

COPY deploy/docker/nginx.conf /etc/nginx/nginx.conf
COPY --from=builder /workspace/frontend/dist /usr/share/nginx/html

EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=12 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/api/v1/health/live || exit 1
