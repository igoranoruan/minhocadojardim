# Testes pendentes de execução

Este arquivo acumula, por etapa, o que foi commitado SEM rodar a suíte completa localmente,
porque o ambiente do Claude (sandbox) não tem rede/sqlalchemy/fastapi/pytest instalados -- só
`ast.parse` (checagem de sintaxe) foi possível aqui. Regra: nenhuma entrada deste arquivo é
removida até o Igor confirmar que rodou os testes e que passaram; só então a branch correspondente
pode ser mesclada na `main`.

---

## Como testar as Etapas 2 + 3 juntas (atalho "Enviar para o KLANGO")

### Confirmação da base da branch (pedido do Igor, 2026-10-15)
`feature/atalho-ui-token` foi criada a partir da `main` DEPOIS que a `main` já tinha recebido o
merge completo da Etapa 2 (commit `44f8194`, que já inclui `19e8c8e` + `caf31de` da
`feature/atalho-compartilhamento`) e o fix pré-existente dos testes de arquitetura (`a94b375`).
Ou seja: a Etapa 2 inteira (backend + migration 0013) já está contida em `feature/atalho-ui-token`
-- **nenhum rebase foi necessário** (confirmado com `git merge-base --is-ancestor`). A ordem de
merge Etapa 2 → Etapa 3 já está embutida na própria história da branch:

```
* 079e358 feat(share): tela de UI do atalho "Enviar para o KLANGO" -- token + fila (Etapa 3)
*   44f8194 merge: atalho "Enviar para o KLANGO" -- Etapa 2 (backend + migration 0013)
|\
| * caf31de fix(share): corrige 3 das 8 falhas que o Igor encontrou rodando a suíte completa
| * 19e8c8e feat(share): atalho "Enviar para o KLANGO" -- backend + migration 0013 (Etapa 2)
* | a94b375 test(arquitetura): ignora scripts/ na varredura de isolamento download/processor
|/
* f3161ec chore(diag): scripts de diagnóstico do OOM em lotes (sem alteração de produção)
```

Por isso, quando tudo estiver aprovado, **um único merge** de `feature/atalho-ui-token` na `main`
já traz as duas etapas, na ordem certa (é um fast-forward, já que `main` é ancestral direto desta
branch) -- não precisa mesclar `feature/atalho-compartilhamento` separadamente (ela já está contida
aqui). Depois do merge, a branch `feature/atalho-compartilhamento` fica obsoleta e pode ser
apagada.

### Roteiro único (Windows/PowerShell, VS Code)

**1) Preparar o ambiente e rodar a suíte completa**
```powershell
cd caminho\para\minhocadojardim
git fetch origin
git checkout feature/atalho-ui-token
git pull origin feature/atalho-ui-token

# Se ainda não tiver o ambiente virtual desta pasta:
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-dev.txt

pytest -q
```
Esperado: **0 failed** (mesmo resultado de quando a Etapa 2 foi aprovada, 1276 passed -- a Etapa 3
não mexeu em nenhum arquivo Python/teste, só em `static/index.html`).

**2) Subir o app localmente**
Sem nenhuma variável de ambiente especial: em desenvolvimento (`ENV` não definido = `development`)
o app já usa SQLite local (`./data/minhoca.db`, pasta criada sozinha) e e-mail "console" (os
códigos de login aparecem no terminal em vez de serem enviados de verdade).
```powershell
# Ainda na mesma pasta, com o .venv ativado:
alembic upgrade head
uvicorn main:app --reload
```
Abra `http://localhost:8000` no navegador.

**3) Checklist visual da tela "Enviar para o KLANGO"**
1. Faça login (o código de verificação aparece no terminal onde o `uvicorn` está rodando, já que
   `EMAIL_SENDER=console` em dev).
2. Role até a seção **"Enviar para o KLANGO"** (entre "Importar pasta do Pinterest" e "Histórico").
3. Clique **"Gerar token"** -- deve aparecer o token em texto, com aviso amarelo "copie agora" e
   botão "Copiar". **Copie o token** (vai precisar dele no passo 4).
4. Recarregue a página (F5) -- a seção deve mostrar "Token ativo desde [data]", sem mostrar o
   token de novo (ele não é guardado em texto puro, só o hash).
5. Clique **"Gerar novo token"** -- deve pedir confirmação avisando que revoga o atual; confirme e
   copie o NOVO token (o antigo some).
6. Clique **"Revogar token"** -- deve pedir confirmação; depois de confirmar, a seção volta para o
   estado "Você ainda não gerou um token."
7. Gere um token de novo e siga para o passo 4 abaixo (fila) antes de continuar.

**4) Colocar um link na fila via `/api/ext/share` (simula o atalho, que ainda não existe --
Etapas 4/5)**
```powershell
curl.exe -X POST "http://localhost:8000/api/ext/share" `
  -H "Authorization: Bearer COLE_SEU_TOKEN_AQUI" `
  -H "Content-Type: application/json" `
  -d '{\"url\": \"https://www.tiktok.com/@algumusuario/video/123456789\"}'
```
Deve devolver texto puro tipo `✅ Adicionado ao KLANGO (1 na fila)`. Teste também sem Origin
nenhum (é exatamente assim que o comando acima já funciona -- `curl.exe` não manda Origin) e com
um token errado (`Authorization: Bearer token-invalido`), que deve devolver 401.

**5) Checklist visual da fila**
1. Recarregue a página (F5) com a sessão do MESMO usuário que gerou o token usado no curl -- o
   link deve aparecer na lista da fila, com o ícone da plataforma (TikTok, no exemplo acima).
2. Clique no ícone de lixeira de um item -- deve sumir da lista.
3. Adicione 2-3 itens de novo (repita o passo 4) e clique **"Limpar fila"** -- deve pedir
   confirmação e esvaziar tudo.
4. Adicione itens de novo e clique **"Enviar fila para o lote"**:
   - Numa conta **sem plano pago (Free)**: deve mostrar o aviso "Enviar para o lote é um recurso
     dos planos com lote...", SEM quebrar nada e SEM mexer na seção de lote.
   - Numa conta **com plano de lote** (Semanal, Mensal ou VIP Batch -- pode usar o admin ou
     conceder um plano de teste): deve preencher `#secaoBatch` com as URLs da fila e já disparar o
     processamento (mesmo comportamento de "Importar pasta do Pinterest" → "Baixar selecionados").
     Itens acima do teto do plano devem continuar na fila, com o aviso "X continuam na fila".

**6) Antes de aprovar o merge**
- [ ] `pytest -q` = 0 failed.
- [ ] Os dois branches de teste do passo 5 (Free e plano pago) se comportaram como descrito.
- [ ] Nenhum erro no console do navegador (F12) ao usar a seção.

---

## Etapa 3 -- Atalho "Enviar para o KLANGO": tela de UI (token + fila)
**Branch:** `feature/atalho-ui-token` (NÃO mesclada na `main` -- não fazer merge/deploy até confirmação)
**Data:** 2026-10-15

**Arquivo alterado:** `static/index.html` (só este -- front-end puro, sem arquivo de produção
Python tocado). Nova seção `#secaoAtalho` ("Enviar para o KLANGO"), visível só com sessão (mesmo
padrão de `#secaoIndicacao`):
- Token: estado sem token / com token (data de criação, último uso) / token recém-gerado (mostrado
  UMA vez, com aviso e botão copiar). Botões "Gerar token", "Gerar novo token" (confirma antes,
  avisa que revoga o atual) e "Revogar token" (confirma antes).
- Fila: lista os itens pendentes (ícone da plataforma + URL + remover), "Limpar fila", e "Enviar
  fila para o lote" -- que reaproveita `criarCampoLote`/`atualizarContagemLote`/`processarLote()`
  já existentes (mesmo padrão de `baixarSelecaoPinterest()`), preenchendo `#batchItems` com as URLs
  devolvidas por `POST /api/share/queue/send-to-batch` em vez de duplicar qualquer lógica de lote.
  Free (sem plano com lote) recebe aviso amigável, nunca é bloqueado de acumular na fila.

**Este projeto NÃO tem suíte de testes de frontend** (sem package.json, sem JS test runner, sem
Playwright/Cypress -- só pytest no backend). O que foi verificado neste ambiente:
- `node --check` no `<script>` inline inteiro do arquivo: sintaxe JS válida.
- Checagem de balanceamento de tags HTML do arquivo inteiro (parser Python, não visual): nenhuma
  tag aberta/fechada incorretamente.
- Todos os `id` novos (`secaoAtalho`, `atalhoToken...`, `atalhoFila...`) conferidos como únicos no
  arquivo; todas as funções/variáveis JS novas conferidas como declaradas uma única vez, sem
  colidir com nome já existente.
- Reli `tests/test_static_pages.py` (único teste de backend que toca `static/index.html`): só
  confere substrings genéricas ("KLANGO.MP4", links do rodapé) -- nada que esta mudança quebre.

### O que NÃO foi verificado (precisa de olho humano no navegador)
- Nenhuma captura de tela / execução real no navegador -- não visualizei a seção renderizada.
- Fluxo completo ponta a ponta: logar, gerar token, copiar, revogar, gerar de novo; adicionar um
  link à fila de verdade (via `POST /api/ext/share` com o token, ex. curl/Postman -- a Etapa 4/5 do
  atalho de verdade no celular ainda não existe) e confirmar que aparece na lista; remover um item;
  limpar a fila; "Enviar fila para o lote" preenchendo `#batchItems` e dando certo com um plano
  pago, e mostrando o aviso correto com uma conta Free (sem lote).
- Responsividade mobile da nova seção (o resto do site é mobile-first; segui as mesmas classes
  Tailwind de `#secaoIndicacao`/`#secaoPinterestBoard`, mas não há como confirmar visualmente aqui).

### Comando/passos para o Igor conferir
```powershell
git fetch origin
git checkout feature/atalho-ui-token
git pull origin feature/atalho-ui-token
```
Depois, com o servidor local rodando (ou direto em produção depois do merge): logar no site, ver a
seção "Enviar para o KLANGO" (aparece entre "Importar pasta do Pinterest" e "Histórico"), gerar o
token, copiar, e testar o fluxo de ponta a ponta descrito acima.

### Checklist antes do merge na `main`
- [ ] Seção aparece certinho (visual/responsivo) ao logar.
- [ ] Gerar/copiar/revogar/gerar-de-novo token funciona e os textos batem com o estado real.
- [ ] Um link adicionado via `/api/ext/share` (com o token) aparece na fila na hora que a página
      recarrega/a seção é recarregada.
- [ ] Remover item e limpar fila funcionam.
- [ ] "Enviar fila para o lote" preenche `#batchItems` e dispara `processarLote()` corretamente
      para uma conta com plano de lote; mostra aviso amigável (sem quebrar nada) para uma conta
      Free.
- [ ] Só depois disso: merge na `main` (deploy do Render sobe o HTML novo -- sem migration/risco de
      banco nesta etapa, é só front-end).

---

## Etapa 2 -- Atalho "Enviar para o KLANGO": backend + migration 0013 + testes
**Branch:** `feature/atalho-compartilhamento` (NÃO mesclada na `main` -- não fazer merge/deploy até confirmação)
**Data:** 2026-10-15

### Atualização 2026-10-15 20:33 -- Igor rodou a suíte completa localmente
Resultado: **8 failed, 1268 passed**. Deste total, 3 falhas eram bugs meus nesta etapa (corrigidos
nesta branch, ver abaixo) e 2 falhas são de um problema PRÉ-EXISTENTE em `main`, sem relação com
esta etapa (reportado ao Igor, aguardando autorização para corrigir -- ver seção própria no final
deste arquivo).

**Corrigido nesta branch:**
- `tests/test_share_ext_routes.py` (5 falhas, 1 causa raiz): meu helper `_cliente_sem_origin()`
  criava um `TestClient(app)` sem sobrescrever `get_session` -- a requisição caía no banco padrão
  da aplicação (sem a migration 0013), não no SQLite de teste criado pela fixture `engine`/`session`
  (que roda as migrations reais). Corrigido trocando por uma fixture `ext_client(maker)` que
  sobrescreve `app.dependency_overrides[get_session]` (mesmo padrão da fixture `auth_client` em
  `conftest.py`), mantendo a ausência de Origin/Referer (é esse o ponto do teste).
- `tests/test_db_schema.py::test_metadata_dos_modelos_tem_as_tabelas_esperadas`: esqueci de somar
  `share_tokens`/`share_queue_items` ao `EXPECTED_TABLES` deste arquivo (só tinha atualizado o
  conjunto equivalente em `test_usage_architecture.py`). Corrigido.

**Ainda não re-executado:** nenhum dos dois fixes acima foi rodado de verdade neste ambiente (sem
pytest) -- só `ast.parse`. Pede-se rodar a suíte completa de novo antes do merge.

### Arquivos de teste novos nesta etapa
- `tests/test_share_token_service.py` -- geração/hash/revogação/regeneração-revoga-anterior/autenticação do token.
- `tests/test_share_queue_service.py` -- normalização de URL, extração de link de texto, add/list/remove/clear da fila, dedup, limite de fila, rate-limit, `send_to_batch` respeitando o teto do plano.
- `tests/test_share_routes.py` -- rotas `/api/share/...` (cookie): token e fila por HTTP, e o teste obrigatório de que `DELETE /api/share/queue` e `POST /api/share/queue/send-to-batch` (e as demais rotas de `/api/share`) continuam 403 sem Origin válido.
- `tests/test_share_ext_routes.py` -- rota `/api/ext/share` (token): funciona sem Origin, aceita Bearer/corpo, 401 sem token, e o teste obrigatório de que um cookie de sessão válido SEM token também dá 401 (a rota ignora cookie).

### Arquivos de teste alterados nesta etapa (para acomodar a migration 0013 / novas tabelas)
- `tests/test_download_architecture.py` -- `services/share_queue.py` adicionado a `DOWNLOAD_ALLOWED_CONSUMERS`; lista de migrations atualizada.
- `tests/test_processor_architecture.py` -- lista de migrations atualizada.
- `tests/test_generation_flow_architecture.py` -- lista de migrations atualizada.
- `tests/test_usage_architecture.py` -- lista de migrations atualizada + `share_tokens`/`share_queue_items` em `Base.metadata.tables`.
- `tests/test_db_migrations.py` -- head `"0012"` → `"0013"`.
- `tests/test_generation_output_fields.py` -- head `"0012"` → `"0013"` em `test_migration_0003_esta_no_historico_e_o_head_atual_e_posterior`.

### O que NÃO foi executado (sandbox sem rede/dependências)
- **pytest completo** (nenhum teste do projeto, novo ou antigo, foi executado de verdade -- só sintaxe via `ast.parse`).
- **Migration 0013 em banco real** -- não foi testado `alembic upgrade head` / `alembic downgrade -1` contra um banco de verdade (nem SQLite nem Postgres), nem `alembic check` (que compara o modelo ORM com a migration).
- **Import/startup do app** -- `main.py` nunca foi efetivamente importado/instanciado (FastAPI, routers, exception handler); um erro de import só apareceria rodando o app ou a suíte.
- Qualquer teste pré-existente que possa ter sido afetado indiretamente pelas mudanças em `config.py`, `database/models/__init__.py` e `main.py`.

### Comando exato para rodar (Windows/PowerShell, no VS Code)
```powershell
cd caminho\para\minhocadojardim
git fetch origin
git checkout feature/atalho-compartilhamento
git pull origin feature/atalho-compartilhamento
pytest -q
```
Se quiser rodar só os arquivos novos/alterados desta etapa primeiro (mais rápido para um primeiro feedback):
```powershell
pytest -q tests/test_share_token_service.py tests/test_share_queue_service.py tests/test_share_routes.py tests/test_share_ext_routes.py tests/test_download_architecture.py tests/test_processor_architecture.py tests/test_generation_flow_architecture.py tests/test_usage_architecture.py tests/test_db_migrations.py tests/test_generation_output_fields.py
```

### Checklist antes do merge na `main`
- [ ] `pytest -q` completo passa (0 failed).
- [ ] `alembic upgrade head` sobe sem erro a partir de um banco vazio (ou do estado atual de dev).
- [ ] `alembic downgrade -1` a partir do head desce sem erro (remove `share_queue_items` e `share_tokens`).
- [ ] `alembic check` não acusa diferença entre modelo e migration (coberto por `test_alembic_check_sem_diferenca_entre_modelo_e_migration`, mas vale rodar à parte se quiser confirmar fora do pytest).
- [ ] App sobe normalmente (`uvicorn main:app` ou equivalente) sem erro de import/registro de rota.
- [ ] `GET/POST/DELETE /api/share/token` e as rotas de `/api/share/queue` continuam exigindo cookie de sessão e devolvem 403 sem Origin válido (CSRF intacto).
- [ ] `POST /api/ext/share` funciona sem nenhum Origin (cenário real do atalho) e devolve 401 sem token válido, mesmo com cookie de sessão presente.
- [ ] Nenhuma rota de pagamento, nenhuma migration antiga (0001-0012) e `utils/time_sp.py` não foram tocados (ver `git diff main...feature/atalho-compartilhamento --stat`).
- [ ] Só depois de tudo isso: merge na `main` (o push na `main` dispara deploy automático no Render e roda a migration 0013 em produção).

---

## Pendência PRÉ-EXISTENTE em `main` -- sem relação com esta etapa (fora de escopo, aguardando autorização)
**Origem:** commit `f3161ec` (scripts de diagnóstico do OOM, já em `main`), não a etapa do atalho.
**Encontrada:** na mesma rodada de testes do Igor em 2026-10-15, junto com as falhas da etapa 2.

Dois testes falham em `main` (não foram tocados nesta branch, e eu não os corrigi aqui porque não
são do escopo desta etapa -- regra "só alterar o que está no escopo da etapa atual, sem
autorização"):

- `tests/test_download_architecture.py::test_resto_do_sistema_nao_importa_download`
- `tests/test_processor_architecture.py::test_nada_no_projeto_ainda_importa_processor`

Causa: esses testes varrem TODOS os `.py` do projeto (exceto uma lista fixa de pastas ignoradas:
`tests`, `migrations`, `download`, `.venv`, `venv`, `__pycache__`, `data`, `static`) e barram
qualquer arquivo fora da lista de permissão que importe `download.*`/`processor.*`. A pasta
`scripts/diagnostico/` NÃO está na lista de pastas ignoradas, e `diag_pico_memoria.py` importa
`download.service`/`processor.service` diretamente de propósito (é um script de diagnóstico real,
não mockado) -- então ele é pego pela varredura e reprovado.

**Correção precisa de autorização** (é mudança em teste de arquitetura/isolamento, fora da etapa
do atalho): a opção mais simples é adicionar `"scripts"` à lista `skip_dirs` dos dois testes (o
script de diagnóstico tem motivo legítimo para importar essas camadas diretamente e já não é
produção). Posso fazer esse commit à parte, direto em `main` (é só nos dois arquivos de teste, sem
tocar em código de produção), assim que você aprovar.
