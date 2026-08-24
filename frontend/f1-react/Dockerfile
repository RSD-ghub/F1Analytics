# ─────────────────────────────────────────────────────────────────────────────
# F1 Analytics — Frontend Dockerfile (multi-stage)
#
# Stage 1 (build):   Node 20 builds the Vite/React bundle into /app/dist
# Stage 2 (runtime): nginx serves the static bundle and proxies /f1/ to backend
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Node build ───────────────────────────────────────────────────────
FROM node:20-alpine AS build

WORKDIR /app

# Install dependencies first (better layer caching — only re-runs when
# package-lock.json changes, not on every source change)
COPY package*.json ./
RUN npm ci --silent

# Copy source and build.
# Override outDir so Vite outputs to /app/dist instead of the Java classpath path.
COPY . .
RUN npm run build -- --outDir dist --emptyOutDir

# ── Stage 2: nginx ────────────────────────────────────────────────────────────
FROM nginx:1.27-alpine AS runtime

# Remove the default nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Copy our reverse-proxy config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy the built static assets from stage 1
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost/ || exit 1
