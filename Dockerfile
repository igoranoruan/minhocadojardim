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
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

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
