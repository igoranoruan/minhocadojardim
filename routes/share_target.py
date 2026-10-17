"""Alvo do Web Share Target do Android -- GET /share-target (17/10/2026, suporte a Android via
PWA instalável, ver static/manifest.json::share_target).

Diferente do atalho do iPhone (routes/share_ext.py, autenticado por TOKEN pessoal, porque o app
Atalhos não é um navegador com cookie): o PWA do Android roda no MESMO Chrome onde a pessoa já
está logada no site, então a identidade aqui vem da SESSÃO normal (cookie), igual ao resto do
site -- nenhum token separado precisa existir pro Android.

Esta rota é uma NAVEGAÇÃO de página inteira disparada pelo sistema operacional (o Android abre
"GET /share-target?title=...&text=...&url=..." quando a pessoa escolhe "KLANGO.MP4" na folha de
compartilhamento nativa), não uma chamada de API feita por JS -- por isso devolve sempre HTML ou
um REDIRECT (nunca JSON), igual abrir um link normal. GET está em utils.origin.SAFE_METHODS
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
verdade -- nunca confia em nada vindo da URL depois do login, só no que foi guardado ANTES dele.

17/10/2026 (ajuste pedido pelo Igor, depois de testar num Android real): no iPhone, o atalho roda
em segundo plano e só mostra uma notificação -- a pessoa nunca sai do Instagram/TikTok. No
Android, abrir o KLANGO.MP4 na folha de compartilhamento SEMPRE traz o PWA pra frente por um
instante -- isso é como o Web Share Target funciona (é uma navegação de verdade pro app-alvo,
diferente do Atalhos do iOS, que não é um app visível); não existe jeito de o navegador adicionar
à fila "por trás" sem abrir nada. O que dá pra fazer, e foi feito aqui: em vez de cair na home
inteira (JS pesado, Tailwind, rolar até a seção), a pessoa logada vê uma telinha mínima com só a
confirmação ("✅ Adicionado ao KLANGO (N na fila)") que tenta se FECHAR sozinha
(`window.close()`) menos de 1 segundo depois, voltando pro Instagram/TikTok sem precisar de mais
nenhum toque. `window.close()` só funciona de verdade quando o Chrome considera esta aba "a única
da tarefa" (o caso comum de uma PWA aberta por um intent de compartilhamento) -- quando não
funciona (silenciosamente, sem erro), o link "Voltar pro KLANGO" continua ali como saída manual."""
import html
from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from database.models import User
from database.session import get_session
from routes.deps import get_optional_authenticated_user
from services.share_queue import add_to_queue

router = APIRouter(tags=["share-target"])


def _pagina_confirmacao(mensagem: str) -> str:
    """Página mínima (sem Tailwind/fontes externas -- carrega instantânea) mostrada depois de um
    compartilhamento bem-sucedido: só a mensagem e uma tentativa de fechar a aba sozinha. Ver
    docstring do módulo pro motivo de existir em vez de simplesmente redirecionar pra "/"."""
    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>KLANGO.MP4</title>
<style>
  body {{ margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
          background: #090D0F; color: #f8fafc; font-family: system-ui, sans-serif; text-align: center; padding: 24px; }}
  .msg {{ font-size: 1.05rem; max-width: 20rem; }}
  a {{ color: #19D3AE; display: inline-block; margin-top: 1.25rem; font-size: 0.85rem; text-decoration: none; }}
</style>
</head><body>
<div>
  <div class="msg">{html.escape(mensagem)}</div>
  <a href="/">Voltar pro KLANGO</a>
</div>
<script>
  setTimeout(function () {{ try {{ window.close(); }} catch (e) {{}} }}, 700);
</script>
</body></html>"""


@router.get("/share-target", include_in_schema=False)
def get_share_target(
    url: str | None = None,
    text: str | None = None,
    title: str | None = None,  # recebido pro manifest bater com o spec do Web Share Target, mas nunca usado
    user: User | None = Depends(get_optional_authenticated_user),
    db: Session = Depends(get_session),
) -> Response:
    # (anotação é `Response`, não `HTMLResponse | RedirectResponse`: um Union de subclasses de
    # Response quebra o FastAPI na hora de registrar a rota -- ele não reconhece o Union como "já é
    # uma resposta pronta" e tenta montar um response_model a partir dele, o que falha; `Response`
    # sozinho, sendo a classe-mãe comum, cobre as duas sem esse problema. Foi exatamente essa a
    # causa do "exit code 4" na CI logo após este arquivo passar a devolver HTML além de redirect.)
    candidato = (url or "").strip() or (text or "").strip()

    if user is not None:
        if not candidato:
            # Nada foi compartilhado de fato (caso raro) -- não há o que confirmar, direto pra home.
            return RedirectResponse(url="/", status_code=302)
        # Mesma lógica de POST /api/ext/share (add_to_queue cuida de extrair a URL de dentro de
        # `text`, validar, deduplicar e respeitar o teto da fila); `resultado.message` já vem
        # pronta pra mostrar (mesmo texto que o atalho do iPhone usa como notificação).
        resultado = add_to_queue(db, user, url=url, text=text)
        return HTMLResponse(_pagina_confirmacao(resultado.message))

    if not candidato:
        return RedirectResponse(url="/", status_code=302)
    return RedirectResponse(url=f"/?compartilhar_pendente={quote(candidato, safe='')}", status_code=302)
