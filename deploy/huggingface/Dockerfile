# AI Racing League — « Rencontres » sur Hugging Face Spaces (SDK Docker, offre gratuite CPU).
# Ce fichier et README.md sont les deux seuls fichiers du Space : le code est récupéré sur GitHub.
# Mettre à jour le Space après un changement sur GitHub : Settings > « Factory rebuild ».
# Dépôt privé : ajouter un secret GITHUB_TOKEN (jeton en lecture seule) dans les réglages du Space.

FROM alpine/git:latest AS src
ARG REPO=github.com/Joks6669/rAIcing
ARG REF=main
RUN --mount=type=secret,id=GITHUB_TOKEN,mode=0444,required=false \
    if [ -s /run/secrets/GITHUB_TOKEN ]; then \
      git clone --depth 1 --branch "$REF" "https://x-access-token:$(cat /run/secrets/GITHUB_TOKEN)@$REPO" /src; \
    else \
      git clone --depth 1 --branch "$REF" "https://$REPO" /src; \
    fi && rm -rf /src/.git

# ---- site (Next.js « standalone »), réduit à la page Rencontres
FROM node:22-bookworm-slim AS web
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 RAICING_SITE=rencontres
COPY --from=src /src/web ./
RUN npm ci --no-audit --no-fund && npm run build

# ---- image finale : simulation (Python) + site (Node)
FROM python:3.11-slim-bookworm
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
RUN useradd -m -u 1000 user
WORKDIR /home/user/app
COPY --from=src /src/pyproject.toml ./
COPY --from=src /src/raicing ./raicing
RUN pip install --no-cache-dir ".[api]" numba
COPY --from=web /app/.next/standalone ./web
COPY --from=web /app/.next/static ./web/.next/static
COPY --from=web /app/public ./web/public
COPY --from=src /src/deploy/huggingface/start.sh ./start.sh
RUN chown -R user:user /home/user/app
ENV RAICING_SITE=rencontres \
    RAICING_DATA_DIR=/tmp/raicing-data \
    NUMBA_CACHE_DIR=/tmp/numba-cache \
    NEXT_TELEMETRY_DISABLED=1 \
    PYTHONUNBUFFERED=1
USER user
EXPOSE 7860
CMD ["bash", "start.sh"]
