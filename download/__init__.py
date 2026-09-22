"""Camada de download (Etapa 5).

Responsabilidade exclusiva: receber uma URL do usuário (TikTok, Instagram, Pinterest, YouTube) e
devolver um arquivo temporário validado, ou um erro padronizado (download.errors.DownloadError).

Este pacote NÃO conhece planos, gerações, pagamentos, sessão, banco ou frontend — é usado por uma
etapa futura (processamento), nunca o contrário. Nada aqui expõe rota HTTP própria.

Ponto de entrada único: download.service.download_video(url).
"""
