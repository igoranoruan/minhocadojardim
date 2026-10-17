# Testes pendentes de execução

Este arquivo acumula, por etapa, o que foi commitado sem a suíte completa confirmada.

**Atualização 15/10/2026 (pedido do Igor):** até aqui, "testes executados" dependia do Igor rodar
`pytest -q` localmente no VS Code, porque o ambiente do Claude (sandbox) não tem rede para instalar
fastapi/sqlalchemy/pytest -- só `ast.parse` (checagem de sintaxe) era possível aqui. Isso mudou: a
partir do commit `edecfa9`, toda branch/push roda a suíte completa sozinha no GitHub Actions
(`.github/workflows/tests.yml`), e o Claude confirma "0 failed" direto pelo resultado (via
`gh api`), sem precisar do computador do Igor. Regra atualizada: nenhuma entrada deste arquivo é
fechada até o pytest estar confirmado **verde** (pelo Actions, ou pelo Igor quando ele preferir
rodar à parte) -- e qualquer coisa que só dê pra verificar num navegador/iPhone de verdade (visual,
responsividade, o app Atalhos) continua precisando do Igor, exatamente como antes.

---

## Etapa 7 -- /atalho-ios: instalação rápida (3 passos) + manual recolhido + problemas comuns
**Status:** Implementado e commitado na branch `feature/fila-extrair-audio` (mesma branch da
Etapa 6, ainda não mesclada); aguardando `pytest -q` verde e, depois, 3 coisas do Igor: (1)
configurar a Pergunta de Importação no atalho real dele, (2) gerar o link do iCloud e testar a
instalação, (3) me mandar o link + o que observou (quando a pergunta aparece, se o token dele veio
vazio) -- só então o botão "Instale o atalho" sai de "Em breve" e vira o link de verdade.
**Branch:** `feature/fila-extrair-audio` (em cima da `main`).
**Data:** 17/10/2026

**O que mudou:** `static/atalho-ios.html` ganhou uma seção "Instalação rápida" no topo (3 cards:
copiar token → instalar atalho pronto → testar compartilhando), no estilo visual de um mockup que
o Igor trouxe como referência (passos numerados, destaque verde, selo "ilustração") -- mas com
conteúdo 100% baseado no comportamento real do projeto, nunca copiado do mockup (que tinha URL/JSON
inventados). As 3 capturas reais de teste (16/17/18, já existentes) ganharam uma cópia com um
destaque verde desenhado por cima (`16/17/18-destaque.jpg`, gerado com Pillow, círculo+selo
numerado sobre o elemento certo de cada tela) -- os originais continuam intactos e são reusados na
seção manual. O passo a passo manual completo (8 passos de configuração + 3 de teste, conteúdo
idêntico ao da Etapa 4) foi movido para dentro de um `<details>` recolhido "Prefere configurar
manualmente?". Seção nova "Problemas comuns", baseada na leitura real de `routes/share_ext.py` (só
token ausente/inválido/revogado é erro HTTP de verdade; duplicado/fila cheia/plataforma não
suportada sempre voltam 200 com aviso amigável na notificação -- documentado pra quem achar que
"não deu erro mas não sei se funcionou").

**Decisão de produto tomada sem perguntar (risco baixo, reversível):** o botão "Instale o atalho"
(passo 2) fica visualmente desabilitado ("Em breve", sem link) em vez de apontar pra um link
inventado/quebrado -- a página é pública, visitada por usuários reais agora, e um botão de
destaque que não funciona seria pior que não ter o atalho rápido ainda. A seção manual (que já
funciona de ponta a ponta) continua sempre acessível por um toque. Assim que o Igor mandar o link
real do iCloud, é só trocar esse botão (já deixei marcado com comentário `TODO` no HTML).

**Arquivos alterados:**
- `static/atalho-ios.html` -- reestruturado (instalação rápida + manual recolhido + problemas
  comuns), ver acima.
- `static/img/atalho/16-destaque.jpg`, `17-destaque.jpg`, `18-destaque.jpg` -- novos, gerados a
  partir das 3 capturas reais já existentes (16/17/18), com destaque verde + selo numerado
  desenhados por cima via script Python/Pillow.
- `main.py` -- docstring da rota `/atalho-ios` atualizado (motivo da reestruturação).
- `tests/test_static_pages.py` -- smoke test novo: confirma que os 3 passos da instalação rápida,
  o `<details>` recolhido com o manual, "Problemas comuns" e o botão "Em breve" (sem link ainda)
  estão todos presentes.

### O que foi verificado
- [ ] `pytest -q` = 0 failed pelo GitHub Actions (confirmar antes do merge -- mesma branch da
      Etapa 6, então os dois ficam prontos juntos).
- [x] Visual conferido via Playwright em largura de celular (390px): instalação rápida legível,
      botão "Em breve" claramente diferente (cinza, tracejado) do botão "pronto" (verde), `<details>`
      abre/fecha corretamente, seção "Problemas comuns" renderiza bem.

### O que NÃO foi verificado ainda (precisa do Igor)
- **Pergunta de Importação no atalho real:** configurar em Ajustes do atalho → "Configurar" →
  "Adicionar Nova Pergunta" → selecionar o parâmetro da ação Texto do passo 1 (o token) -- a
  documentação da Apple confirma que esse campo é limpo automaticamente ao compartilhar (o token do
  Igor não vai junto no link), mas NÃO deixa claro se a pergunta aparece só uma vez na instalação ou
  no primeiro uso -- precisa confirmar na prática.
- **Gerar e testar o link do iCloud** (idealmente instalando numa segunda conta/dispositivo, pra
  simular um usuário novo de verdade) e me mandar o link + o resultado.
- Visual real no iPhone do Igor (esta seção só foi conferida via mockup/Playwright, nunca num
  Safari de verdade).

### Checklist antes do merge na `main`
- [ ] `pytest -q` = 0 failed (GitHub Actions) -- junto com a Etapa 6.
- [ ] Pode mesclar com o botão ainda "Em breve" (não bloqueia; é seguro pro usuário real) -- trocar
      pelo link de verdade é um commit pequeno à parte, depois que o Igor confirmar.

---

## Etapa 6 -- Fila do atalho "Enviar para o KLANGO" também extrai ÁUDIO
**Status:** Implementado e commitado; aguardando `pytest -q` verde no GitHub Actions e, depois do
merge/deploy, o teste manual do Igor no celular (único jeito de confirmar de verdade o atalho + o
layout dos botões no celular).
**Branch:** `feature/fila-extrair-audio` (em cima da `main`).
**Data:** 17/10/2026

**O que mudou:** o botão único "Enviar fila para o lote" virou dois botões lado a lado, mesmo
destaque visual: "🎬 Baixar vídeos" (comportamento de sempre, inalterado) e "🎵 Extrair áudios"
(novo -- reaproveita o fluxo de lote de áudio já existente, `processarLoteAudio()`).

**Ajuste #2 do Igor (proteção contra perda de item da fila):** antes desta mudança,
`send_to_batch()` já marcava os itens como "consumidos" (fora da fila) assim que o botão era
clicado, independente do que acontecesse depois -- e `reserve_audio_batch` (e também
`reserve_batch`, de vídeo, mesmo padrão) é tudo-ou-nada: se a quantidade pedida passar do saldo
diário, rejeita o lote inteiro. Ou seja, mandar pra extração de áudio mais links do que o saldo
diário aceitava faria esses itens sumirem da fila sem nenhum áudio ter sido extraído. Corrigido com
um parâmetro novo opcional (`max_items`) em `send_to_batch()`: quando o botão é "Extrair áudios", a
rota consulta o saldo diário de áudio REAL no servidor antes de separar os itens, e nunca separa
mais do que esse saldo aceita -- o excedente continua pendente na fila. O botão de vídeo não mudou
em nada (mesmo comportamento de antes, testado de regressão).

**Arquivos alterados:**
- `static/index.html` -- botão único → dois botões lado a lado (`#atalhoFilaVideoBtn`/
  `#atalhoFilaAudioBtn`); `enviarFilaParaLote()` agora recebe o formato e manda
  `?formato=video|audio` pro backend; depois de preencher o formulário de lote, troca o toggle
  vídeo/áudio (`selecionarFormatoLote`) e dispara `processarLoteFormato()` (que já decide entre
  `processarLote()`/`processarLoteAudio()`, nenhum dos dois alterado).
- `services/share_queue.py` -- `send_to_batch()` ganhou o parâmetro opcional `max_items` (default
  `None` = comportamento idêntico ao de antes).
- `routes/share.py` -- `POST /api/share/queue/send-to-batch` ganhou o parâmetro de query opcional
  `formato` (default `"video"`, sem mudança de comportamento); quando `"audio"`, calcula o saldo
  diário de áudio restante (`services.audio_usage.get_audio_allowance`) e passa como `max_items`.
- `tests/test_share_queue_service.py` -- 4 testes novos de `send_to_batch(..., max_items=...)`
  (respeita o menor entre teto do plano e `max_items`; `max_items` nunca AUMENTA o teto do plano;
  `max_items=0` não separa nada e não perde a fila; sem `max_items` continua idêntico a antes).
- `tests/test_share_routes.py` -- 2 testes novos: `?formato=audio` respeita o saldo diário de áudio
  e mantém o excedente na fila (consome o saldo até sobrarem 3, confirma que só 3 são enviados e 7
  continuam pendentes); `?formato=video` continua usando só o teto do plano (regressão).
- `tests/test_static_pages.py` -- smoke test novo: confirma que os dois botões existem na página
  inicial, com os rótulos e `onclick` certos, e que o botão antigo não sobrou no HTML.

### O que foi verificado
- [ ] `pytest -q` = 0 failed pelo GitHub Actions (confirmar antes do merge).
- [x] Os textos "🎬 Baixar vídeos" e "🎵 Extrair áudios" cabem numa linha só, lado a lado, na
      largura mínima comum de iPhone (360px, iPhone SE) -- verificado renderizando as classes
      exatas usadas no HTML (grid de 2 colunas) num mockup via Playwright nessa largura, já que
      este sandbox não alcança o CDN do Tailwind pra renderizar a página real com estilo completo.

### O que NÃO foi verificado ainda (precisa do Igor, no celular de verdade)
- **Teste manual pedido pelo Igor:** compartilhar 2 links pelo atalho do iPhone (`/atalho-ios`) →
  na seção "Enviar para o KLANGO" do site, tocar **"🎵 Extrair áudios"** → conferir que os 2 MP3s
  são gerados e dá pra baixar (individualmente e/ou pelo ZIP `klango_audios.zip`).
- Visual real dos dois botões lado a lado no iPhone do Igor (cores, toque, alinhamento) -- a
  verificação acima foi só das classes/medidas, não da página renderizada de verdade com o CSS do
  Tailwind carregado.
- Cenário de saldo de áudio insuficiente (fila com mais links do que o saldo diário restante) --
  coberto por teste automatizado, mas vale o Igor confirmar visualmente o aviso "X continuam na
  fila (acima do limite ou do saldo diário de áudio)" pelo menos uma vez, se for prático simular
  (ex.: extrair várias avulsas primeiro pra sobrar pouco saldo).

### Checklist antes do merge na `main`
- [ ] `pytest -q` = 0 failed (GitHub Actions).
- [ ] Teste manual do Igor (2 links → "Extrair áudios" → baixar os MP3s) confirmado.
- [ ] Visual dos dois botões no celular aprovado pelo Igor.

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
- [x] `pytest -q` = 0 failed (1276 passed, confirmado por Igor em 2026-10-15).
- [x] Checklist de token (gerar/recarregar-persiste/gerar-novo-invalida/revogar) e de fila
      (adicionar via curl/remover/limpar) confirmados por Igor em 2026-10-15.
- [ ] Branch de teste "Enviar fila para o lote" com conta de plano pago (Semanal/Mensal/VIP Batch)
      não foi testado explicitamente -- só o caso Free (aviso de recurso pago). Não bloqueia (a
      lógica reaproveita `send_to_batch`/`processarLote()` já testados no backend e em outras
      seções da tela), mas fica registrado como o único item do roteiro não percorrido.

---

## Etapa 4 -- Atalho "Enviar para o KLANGO": instruções de configuração no iPhone
**Status:** CONFIRMADA de ponta a ponta. `pytest -q` verde pelo GitHub Actions. Opção 1 (atalho
pronto para importar) foi testada pelo Igor em iPhone real em 16/10/2026 e confirmada quebrada --
a Apple recusa importar qualquer `.shortcut` baixado da web ("Não é possível importar arquivos de
atalho não assinados."); foi removida (arquivo `static/atalho-klango.shortcut` excluído). O passo a
passo manual (único caminho possível) foi montado ao vivo pelo Igor comigo guiando tela por tela, e
testado com **3 vídeos reais** (2 Reels do Instagram + 1 vídeo do TikTok) no mesmo dia -- os 3
entraram certinho na fila do site. Depois do teste ao vivo, reescrevi `static/atalho-ios.html` com
as **capturas de tela reais** desse atendimento (pasta `static/img/atalho/`) e corrigi os nomes de
botão que na primeira versão estavam errados/desatualizados (ex.: "Obter Conteúdo de URL" →
"Obter Conteúdo do URL"; "Mostrar na Tela de Compartilhamento" → "Mostrar ao Compartilhar") --
mais um pega-ratão do próprio Atalhos documentado (o campo da URL vem preenchido sozinho com a
variável errada; agora a página já avisa disso antes de acontecer).
**Branch:** `fix/atalho-ios-sem-shortcut-pronto` (Opção 1 removida) + `docs/atalho-ios-passo-a-passo-real`
(reescrita com fotos) -- ambas em cima da `main`.
**Data:** 2026-10-15 (original) / 2026-10-16 (correção + teste real + reescrita com fotos)

**Arquivos alterados (correção de 16/10, acumulado):**
- `static/atalho-ios.html` -- reescrito do zero: passo a passo com nomes de botão corretos, 18
  capturas de tela reais embutidas, seção "Como testar" nova, aviso específico sobre o campo de URL
  que se autopreenche errado.
- `static/img/atalho/*.jpg` -- 18 capturas de tela (comprimidas, ~45KB cada) do processo real de
  montagem e teste do atalho, tiradas pelo Igor durante o atendimento.
- `static/atalho-klango.shortcut` -- removido (não tinha como funcionar, ver acima).
- `main.py` -- docstring da rota `/atalho-ios` atualizado duas vezes (motivo da remoção da Opção 1;
  depois, a reescrita com fotos reais).
- `tests/test_static_pages.py` -- `test_pagina_de_configuracao_do_atalho_ios` (sem mudança nesta
  rodada -- as asserções já cobertas continuam valendo com o novo conteúdo).

### O que foi verificado
- [x] `pytest -q` = 0 failed (confirmado pelo GitHub Actions).
- [x] Confirmado (pelo Igor, em iPhone real) que a Opção 1 (atalho pronto) não funciona.
- [x] Passo a passo manual testado de ponta a ponta no iPhone: atalho montado, aparece ao
      compartilhar, e os 3 links compartilhados (2 Instagram + 1 TikTok) apareceram na fila do site.

### O que NÃO foi verificado ainda
- Visual/responsividade da página reescrita (com as fotos) -- vale uma olhada rápida em
  `https://klango.site/atalho-ios` pelo celular depois do deploy, só pra conferir que as imagens
  carregam bem e o layout não quebrou.

### Checklist antes do merge na `main`
- [x] `pytest -q` = 0 failed (confirmado pelo GitHub Actions).
- [x] Confirmado (pelo Igor, em iPhone real) que a Opção 1 (atalho pronto) não funciona -- por
      isso foi removida.
- [x] Passo a passo manual testado de ponta a ponta no iPhone: atalho monta, aparece ao
      compartilhar, e o link compartilhado aparece na fila do site (3 vídeos testados).
- [x] Etapa fechada.

---

## Etapa 3 -- Atalho "Enviar para o KLANGO": tela de UI (token + fila)
**Status:** CONFIRMADA -- Igor rodou localmente em 2026-10-15 22:xx (VS Code/Windows): `pytest -q`
= **1276 passed, 0 failed**, e todo o checklist visual/funcional da seção "Enviar para o KLANGO"
(gerar/recarregar-persiste/gerar-novo-invalida-anterior/revogar token; adicionar via
`POST /api/ext/share` com curl e ver aparecer na fila; remover item; limpar fila) confirmado
funcionando. Já tinha passado também no smoke test de produção logo após o deploy (ver histórico
abaixo). Nenhuma pendência restante desta etapa.
**Branch:** `feature/atalho-ui-token` (mesclada na `main` em 2026-10-15, commit `64b3e7d`)
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
- [x] Seção aparece certinho ao logar (confirmado por Igor em 2026-10-15).
- [x] Gerar/copiar/revogar/gerar-de-novo token funciona e os textos batem com o estado real.
- [x] Um link adicionado via `/api/ext/share` (com o token) aparece na fila na hora que a página
      recarrega/a seção é recarregada.
- [x] Remover item e limpar fila funcionam.
- [x] "Enviar fila para o lote" mostra aviso amigável (sem quebrar nada) para uma conta Free
      (caso testado). Caso de conta com plano de lote (preenche `#batchItems` e dispara
      `processarLote()`) NÃO foi testado manualmente -- ver nota na seção "6) Antes de aprovar o
      merge" acima.
- [x] Merge na `main` já foi feito (autorizado por Igor em 2026-10-15 21:46, commit `64b3e7d`) --
      este checklist foi concluído DEPOIS do merge, por decisão explícita do Igor.

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
