# Site public de l'AI Racing League — image de production (Next.js « standalone »).
# Construction :  docker build -t raicing-web web/
# Exécution    :  docker run -p 3000:3000 -e RAICING_API_URL=http://api:8000 raicing-web
#                 (sans RAICING_API_URL, ou avec RAICING_MOCK=1, le site sert la ligue de démonstration)

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS run
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN addgroup -S app && adduser -S app -G app
COPY --from=build --chown=app:app /app/.next/standalone ./
COPY --from=build --chown=app:app /app/.next/static ./.next/static
COPY --from=build --chown=app:app /app/public ./public
# résultats et replay de démonstration (mode maquette)
COPY --from=build --chown=app:app /app/fixtures ./fixtures
USER app
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD wget -qO- http://127.0.0.1:3000/ >/dev/null || exit 1
CMD ["node", "server.js"]
