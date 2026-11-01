"""Nome de arquivo a partir do @ do criador original (ETAPA B8, pedido do Igor, 01/11/2026 --
opção "Nomear com o @ do criador", desmarcada por padrão no site).

Regras (decididas com o Igor):
- Formato: "@klango.mp4"; repetições no MESMO lote viram "@klango 2.mp4", "@klango 3.mp4", ...
  O número vem separado por ESPAÇO de propósito: "@klango1"/"@klango_2" seriam @ válidos de OUTRAS
  contas (o crédito poderia ir pro perfil errado); espaço não existe em @ de nenhuma rede.
- Nunca colide com um nome já usado no lote (digitado pela pessoa ou já gerado antes): o
  alocador pula para o próximo número livre.
- Prioridade (aplicada por quem chama, services/generation_flow.py): nome digitado > @ do criador
  (só com a opção marcada) > padrão "klango-xxxx.mp4" de sempre.

O @ chega aqui já validado por download/creator_handle.py (só [A-Za-z0-9._-], sem "@") -- este
módulo não revalida nem conhece plataformas. Puro: sem banco, sem rede, sem relógio.
"""
from collections.abc import Iterable

_MP4_SUFFIX = ".mp4"


def creator_filename(handle: str, ordinal: int = 1) -> str:
    """`ordinal` 1 -> "@handle.mp4"; 2 -> "@handle 2.mp4"; e assim por diante."""
    if ordinal < 1:
        raise ValueError("ordinal deve ser >= 1")
    if ordinal == 1:
        return f"@{handle}{_MP4_SUFFIX}"
    return f"@{handle} {ordinal}{_MP4_SUFFIX}"


class CreatorFilenameAllocator:
    """Distribui nomes por @ dentro de UM lote (ou de uma geração avulsa), na ordem em que
    `allocate` é chamado. `used` são nomes já ocupados (digitados pela pessoa ou gerados numa
    execução anterior do mesmo lote) -- nunca reaproveitados. Comparação sem diferenciar
    maiúsculas/minúsculas, porque Windows/macOS tratam "@A.mp4" e "@a.mp4" como o mesmo arquivo."""

    def __init__(self, used: Iterable[str | None] = ()) -> None:
        self._used: set[str] = {nome.lower() for nome in used if nome}

    def allocate(self, handle: str) -> str:
        ordinal = 1
        while True:
            nome = creator_filename(handle, ordinal)
            if nome.lower() not in self._used:
                self._used.add(nome.lower())
                return nome
            ordinal += 1
