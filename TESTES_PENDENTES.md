# Testes pendentes de execução

Este arquivo acumula, por etapa, o que foi commitado SEM rodar a suíte completa localmente,
porque o ambiente do Claude (sandbox) não tem rede/sqlalchemy/fastapi/pytest instalados -- só
`ast.parse` (checagem de sintaxe) foi possível aqui. Regra: nenhuma entrada deste arquivo é
removida até o Igor confirmar que rodou os testes e que passaram; só então a branch correspondente
pode ser mesclada na `main`.

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
