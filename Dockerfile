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

# API_HOST=0.0.0.0 e obrigatorio dentro do container: o padrao do app e
# loopback, e ligado em 127.0.0.1 aqui dentro a porta publicada nao alcanca
# nada. Quem restringe o acesso e o bind do compose, no host.
ENV DB_PATH=/app/data/promos.db \
    API_HOST=0.0.0.0 \
    API_PORT=8000

EXPOSE 8000

ENTRYPOINT ["amazon_pay"]
CMD ["serve"]
