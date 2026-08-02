FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=America/Sao_Paulo

WORKDIR /app

# Deps primeiro: mudanca no codigo nao invalida a camada de instalacao.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps -e .

COPY watchlist.json ./

# O banco vive num volume; o app cria o diretorio se faltar.
RUN mkdir -p /app/data

# Usuario nao-root, dono do /app/data pra conseguir escrever no volume.
RUN useradd --create-home --uid 1000 promo && chown -R promo:promo /app
USER promo

ENV DB_PATH=/app/data/promos.db

ENTRYPOINT ["amazon_pay"]
CMD ["daemon"]
