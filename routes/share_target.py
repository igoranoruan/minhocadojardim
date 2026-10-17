"""Alvo do Web Share Target do Android -- GET /share-target (17/10/2026, suporte a Android via
PWA instalável, ver static/manifest.json::share_target).

Diferente do atalho do iPhone (routes/share_ext.py, autenticado por TOKEN pessoal, porque o app
Atalhos não é um navegador com cookie): o PWA do Android roda no MESMO Chrome onde a pessoa já
está logada no site, então a identidade aqui vem da SESSÃO normal (cookie), igual ao resto do
site -- nenhum token separado precisa existir pro Android.

Esta rota é uma NAVEGAÇÃO de página inteira disparada pelo sistema operacional (o Android abre
"GET /share-target?title=...&text=...&url=..." quando a pessoa escolhe "KLANGO.MP4" na folha de
compartilhamento nativa), não uma chamada de API feita por JS -- por isso devolve sempre um
REDIRECT (nunca JSON), igual clicar num link normal. GET está em utils.origin.SAFE_METHODS
(isento do OriginProtectionMiddleware por padrão, nenhuma entrada nova precisa em
config.ORIGIN_CHECK_EXEMPT_PREFIXES) -- essa isenção é a mesma de qualquer GET do site; o pior
cenário de abuso (alguém induzir a pessoa a clicar num link forjado) só consegue adicionar um
link de VÍDEO VÁLIDO (validate_url + detect_platform, dentro de add_to_queue) à fila PRÓPRIA da
pessoa, que ela ainda precisa confirmar manualmente antes de qualquer download -- nunca lê nem
muda nada fora da fila.

Sem sessão (pessoa nunca logou neste Chrome, ou a sessão expirou): não dá pra identificar o dono
da fila aqui no servidor, então o link é devolvido pra "/" dentro de "?compartilhar_pendente=" --
static/index.html guarda isso em localStorage até o login completar (capturarCompartilharPendenteDaUrl
+ consumo em aplicarSessao(), mesmo padrão do "?ref=CODIGO" do programa de indicação) e só então
chama POST /api/share/queue (routes/share.py, autenticado por cookie normalmente) pra adicionar de
verdade -- nunca confia em nada vindo da URL depois do login, só no que foi guardado ANTES dele."""
from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from database.models import User
from database.session import get_session
from routes.deps import get_optional_authenticated_user
from services.share_queue import add_to_queue

router = APIRouter(tags=["share-target"])


@router.get("/share-target", include_in_schema=False)
def get_share_target(
    url: str | None = None,
    text: str | None = None,
    title: str | None = None,  # recebido pro manifest bater com o spec do Web Share Target, mas nunca usado
    user: User | None = Depends(get_optional_authenticated_user),
    db: Session = Depends(get_session),
) -> RedirectResponse:
    candidato = (url or "").strip() or (text or "").strip()

    if user is not None:
        # Mesma lógica de POST /api/ext/share (add_to_queue cuida de extrair a URL de dentro de
        # `text`, validar, deduplicar e respeitar o teto da fila) -- só que aqui ninguém vê a
        # mensagem de retorno (é um redirect, não uma notificação); "?compartilhado=1" faz
        # carregarAtalho() (static/index.html) rolar até a fila pra pessoa conferir com os
        # próprios olhos se o link entrou.
        if candidato:
            add_to_queue(db, user, url=url, text=text)
        return RedirectResponse(url="/?compartilhado=1", status_code=302)

    if not candidato:
        return RedirectResponse(url="/", status_code=302)
    return RedirectResponse(url=f"/?compartilhar_pendente={quote(candidato, safe='')}", status_code=302)
