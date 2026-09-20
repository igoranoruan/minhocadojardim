"""Configuração de logs (saída padrão, que o Render captura).

Convenção de mensagens: prefixo entre colchetes por fase, por exemplo
[STARTUP], [DOWNLOAD], [YT-DLP], [PROCESSAMENTO], [RESULTADO].
Assim, quando algo falha, o log mostra em qual fase e o erro real.
"""
import logging
import sys

_VALID = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def setup_logging(level: str = "INFO") -> None:
    level = level if level in _VALID else "INFO"
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
