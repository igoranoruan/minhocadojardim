# syntax=docker/dockerfile:1

# Minhoca de Jardim V2 -- imagem de produção (Render, Web Service via Docker).
# FastAPI (main:app) + FFmpeg/ffprobe no PATH para o pipeline de download/reencode.

FROM python:3.13-slim

# Evita .pyc e força stdout/stderr sem buffer (logs aparecem em tempo real no Render).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# FFmpeg (inclui ffprobe) via apt -- fica em /usr/bin, já no PATH por padrão.
# config.py usa FFMPEG_PATH="ffmpeg" e FFPROBE_PATH="ffprobe" (busca pelo PATH, não caminho absoluto).
# curl/ca-certificates: necessários só para baixar e instalar o Deno abaixo (runtime JS).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Deno (runtime JavaScript exigido pelo yt-dlp para negociar os clients do YouTube que usam
# PO Token -- sem ele, o yt-dlp registra "No supported JavaScript runtime could be found",
# degrada a negociação de client, e o fluxo de PO Token/BGUTIL já configurado em
# download/ytdlp_downloader.py nunca chega a ser acionado; diagnóstico confirmado em produção
# em 2026-10-01). DENO_INSTALL define onde o script oficial instala o binário -- apontando para
# /usr/local, ele fica em /usr/local/bin/deno, que já está no PATH padrão de qualquer imagem
# baseada em Debian (herança do python:3.13-slim), sem precisar de ENV PATH adicional nem de
# depender de $HOME (que mudaria se a imagem rodasse como outro usuário).
# A checagem de versão abaixo falha o build (em vez de seguir silenciosamente) se o Deno
# instalado vier abaixo do mínimo exigido pelo yt-dlp atual (2.3).
ENV DENO_INSTALL=/usr/local
RUN curl -fsSL https://deno.land/install.sh | sh -s -- -y \
    && deno --version \
    && DENO_MAJOR=$(deno --version | head -n1 | sed -E 's/deno ([0-9]+)\.([0-9]+).*/\1/') \
    && DENO_MINOR=$(deno --version | head -n1 | sed -E 's/deno ([0-9]+)\.([0-9]+).*/\2/') \
    && if [ "$DENO_MAJOR" -lt 2 ] || { [ "$DENO_MAJOR" -eq 2 ] && [ "$DENO_MINOR" -lt 3 ]; }; then \
         echo "ERRO: Deno instalado ($DENO_MAJOR.$DENO_MINOR) é menor que o mínimo exigido pelo yt-dlp (2.3)" >&2; \
         exit 1; \
       fi

# Dependências primeiro (cache de camada) -- requirements.txt não é alterado por este Dockerfile.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Resto do projeto.
COPY . .

# Render injeta PORT dinamicamente; 10000 é o fallback para execução local/sem a variável.
ENV PORT=10000
EXPOSE 10000

# uvicorn direto (sem gunicorn, sem start.sh) -- escuta em 0.0.0.0 na porta indicada por PORT.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
