"""Camada de processamento (Etapa 6).

Responsabilidade exclusiva: transformar o arquivo de vídeo entregue pela Etapa 5 (download/) em
um novo MP4 validado (H.264 + AAC quando houver áudio), com metadados removidos, ou devolver um
erro padronizado (processor.errors.ProcessorError).

Este pacote NÃO conhece rotas, planos, gerações, pagamentos, banco ou frontend — é usado por uma
etapa futura (que liga download → processor → entrega ao usuário), nunca o contrário. Nada aqui
expõe rota HTTP própria, nem lê/grava no banco.

Ponto de entrada único: processor.service.process_video(input_path).
"""
