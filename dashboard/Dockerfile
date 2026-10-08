# Build the React dashboard without carrying the Node toolchain into production.
FROM node:24-alpine AS build
WORKDIR /app
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
ARG VITE_API_URL=""
ENV VITE_API_URL=${VITE_API_URL}
RUN npm run build

FROM nginxinc/nginx-unprivileged:alpine
COPY dashboard/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build --chown=101:101 /app/dist/ /usr/share/nginx/html/
EXPOSE 3000
HEALTHCHECK --interval=5s --timeout=3s --retries=10 \
    CMD wget -q -O /dev/null http://127.0.0.1:3000/ || exit 1
