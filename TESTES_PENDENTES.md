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

## Home: bug do "Ver planos" pra cliente pago + copy dos planos + carrossel mobile
**Status:** Implementado e commitado na `main`; `pytest -q` pelo GitHub Actions a confirmar.
**Branch:** `main` (direto).
**Data:** 20/10/2026
**Origem:** Feedback do Igor logo depois de usar o site de verdade pelo celular, já logado como
cliente VIP Batch (prints anexados na conversa). 4 pedidos, confirmados por ele antes de eu mexer
em qualquer coisa.

### 1 e 2 (mesma causa raiz) -- "Ver planos" force-mostrava a vitrine pra quem já é cliente pago
**Bug real, não visual:** existe uma função correta (`irParaMeuPlano()`) que decide entre rolar até
`#areaAssinatura` ("Meu acesso", pra quem já tem plano pago) ou `#secaoPlanos` (a vitrine de 3
cards, pra quem é Free) -- usada corretamente só pelo item "Meu plano" do menu "Minha conta". Todos
os OUTROS links/botões "Ver planos"/"Planos" da página (nav do header, item "Ver planos" do mesmo
menu, CTAs de bloqueio de áudio/Pinterest/comentário, link do rodapé) chamavam
`mostrarSecaoSeEscondida('secaoPlanos')` direto -- que só REMOVE a classe "hidden" da vitrine, sem
checar se o usuário já é pago. Como `atualizarVisibilidadeSecaoPlanos()` esconde a vitrine (e
mostra "Meu acesso" no lugar) só quando o status real do plano chega do servidor, o efeito prático
era: clicar em qualquer um desses links forçava a vitrine a reaparecer (poluindo a tela) **e**,
como a vitrine e "Meu acesso" se excluem mutuamente, escondia "Meu acesso" -- que é onde mora a
validade do plano -- até o próximo recarregamento de status real (F5, ou qualquer ação que chame
`carregarStatusConta()` de novo). Isso explica os dois problemas relatados (vitrine não sumia +
validade do plano não aparecia) com uma causa única.

**O que mudou:** todos os 7 lugares que levavam pra Planos agora chamam `irParaMeuPlano()` (a
mesma função que já funcionava certo em "Meu plano"):
- Nav do header (`static/index.html`, link "Planos").
- Item "Ver planos" do menu "Minha conta".
- CTA de bloqueio: extrair áudio sem plano pago.
- CTA de bloqueio: importar pasta do Pinterest sem plano com lote.
- CTA de bloqueio: comentar sem plano pago.
- Link "Planos" do rodapé -- esse nem tinha handler nenhum antes (bug equivalente, mas ainda mais
  antigo: pra cliente pago, o clique só pulava via âncora de URL pra uma seção escondida, sem fazer
  nada visível).

Os 3 CTAs de bloqueio continuam só aparecendo pra quem NÃO tem o plano necessário (não mudou
comportamento nenhum pra eles, já que nesse caso a vitrine já estava corretamente visível) -- a
troca ali é só por consistência/defesa (nunca erra, em nenhum estado de plano).

**Nota sobre a validade do plano:** uma vez corrigido o bug acima, a validade volta a aparecer --
mas como uma DATA ("Acesso válido até DD/MM/AAAA", com destaque e botão "Renovar" quando faltar
≤7 dias), não como contagem de dias crua. Isso foi uma decisão sua de uma etapa anterior (Etapa 3,
dossiê 4.2) pra não repetir a mesma informação de formas diferentes -- avisado a você antes de
mexer, e você confirmou que resolve.

### 3 -- copy dos planos atualizada
As 3 listas de recursos (Semanal/Mensal/VIP Batch) ganharam 2 mudanças, iguais nos 3 planos (as 3
têm exatamente os mesmos recursos, só a cota muda -- conferido em `services/plans.py` e
`services/audio_usage.py::_require_paid_plan`, que checa `plan.paid` genericamente, não por plano
específico):
- "Downloads em lote" virou "Downloads em lote, direto do Instagram, TikTok, Pinterest e YouTube"
  (mesmo texto já usado em outros lugares da página pra essas 4 plataformas).
- Nova linha: "Extração de áudio (MP3) incluída".

### 4 -- carrossel horizontal no mobile
A grade de 3 cards (`grid grid-cols-1 md:grid-cols-3`, empilhada verticalmente no mobile) virou um
carrossel horizontal com scroll-snap abaixo do breakpoint `md` (768px): o card Mensal centralizado
ao carregar a página, com as bordas do Semanal e do VIP Batch visíveis dos dois lados (arrastável
com o dedo). Acima de 768px nada muda -- volta a ser o grid de 3 colunas de sempre. Centralização
inicial feita por `centralizarCarrosselPlanos()`, chamada uma vez no `DOMContentLoaded`, só em
telas mobile (`scrollIntoView` instantâneo, não suave, já que acontece antes de qualquer interação).

### O que foi verificado
- [x] HTML inteiro passa por um parser real (`html.parser.HTMLParser`): zero tags desbalanceadas.
- [x] Sintaxe do único bloco `<script>` inline (`node --check`) OK.
- [x] Teste de lógica isolado em Node para `irParaMeuPlano()` (DOM simulado à mão): 6 asserções --
      cliente VIP logado rola pra "Meu acesso" e NUNCA força a vitrine a aparecer; Free logado e
      visitante deslogado continuam indo pra vitrine normalmente (comportamento de sempre
      preservado). Todas bateram.
- [x] `grep` confirma que nenhum link/botão da página ainda chama
      `mostrarSecaoSeEscondida('secaoPlanos')` diretamente -- os 7 lugares usam `irParaMeuPlano()`.
- [x] `tests/test_frontend.py::test_planos_conforme_especificacao` continua passando:
      `html.count("Downloads em lote") == 3` não quebrou (o texto "Downloads em lote" continua
      aparecendo literalmente 3 vezes, só com texto adicional depois -- a contagem não mudou).
- [ ] `pytest -q` pelo GitHub Actions -- a confirmar após o push.
- [ ] **Teste de verdade no celular -- mesma limitação de sempre, sandbox sem navegador real.**
      Vale o Igor conferir: logado como VIP, clicar em "Ver planos" (menu "Minha conta") e no link
      "Planos" do rodapé e confirmar que vai direto pro card "Meu acesso" (com a validade
      aparecendo), sem a vitrine de 3 cards aparecer; arrastar o carrossel de planos no mobile (pra
      quem ainda não é pago, ou simulando) e ver o Mensal centralizado com as pontas visíveis; ler a
      copy nova dos 3 planos.

---

## Página de instalação do Atalho no iPhone (/atalho-ios) -- ajustes pós-teste do Igor
**Status:** Implementado e commitado na `main`; `pytest -q` pelo GitHub Actions a confirmar.
**Branch:** `main` (direto).
**Data:** 20/10/2026
**Origem:** Feedback do Igor depois de testar a Instalação Rápida pelo celular de verdade (prints
anexados na conversa). Pediu 4 ajustes pontuais, todos em `static/atalho-ios.html`.

**O que mudou:**
1. **Botão "Ir gerar meu token" → "Gerar token", sem sair da página.** Antes ele navegava pra
   `/?atalho=1` (fechando a página de instruções -- a pessoa tinha que gerar o token na home e
   depois voltar manualmente pra `/atalho-ios` pra continuar a instalação). Agora o botão chama
   `gerarTokenRapido()`, uma função nova que gera o token (`POST /api/share/token`) e copia pro
   clipboard (`navigator.clipboard`, com fallback via `execCommand("copy")` se a API não estiver
   disponível) direto nesta página, mostrando o token num campo e uma mensagem de status -- nunca
   navega pra lugar nenhum. Se já existir um token ativo, confirma antes de gerar um novo (mesmo
   texto/raciocínio de `gerarTokenAtalho()` em `static/index.html`, já que gerar revoga o anterior).
   Se a pessoa não estiver logada (401), mostra aviso pedindo pra fazer login no site primeiro.
2. **Ordem dos passos invertida:** "Instale o atalho" agora é o passo 1, "Gere o token" é o passo
   2 (era o contrário). Raciocínio do Igor: ao instalar, o próprio app Atalhos já pede o token
   durante a importação -- nesse momento a pessoa se liga que precisa de um e só então vai gerar.
   Como o passo 2 não navega mais pra fora da página (ajuste 1 acima), dá pra alternar entre Atalhos
   e Safari sem perder o lugar.
3. **Seção "Prefere configurar manualmente?" escondida.** Não foi removida -- o `<details>` inteiro
   (8 passos de configuração + 3 de teste, 18 fotos) foi envolvido num comentário HTML, preservado
   palavra por palavra, fácil de restaurar se precisar de novo (ex.: alguém com um app Atalhos mais
   antigo). Raciocínio do Igor: agora que a instalação rápida funciona de ponta a ponta e já foi
   confirmada, o passo a passo manual completo traz mais objeção do que ajuda pra quem está
   chegando. A seção "Problemas comuns" (que referenciava passos do manual) foi reescrita pra não
   depender mais dele.
4. **Espaçamento do texto de apoio em "Instalação rápida".** O parágrafo pequeno logo abaixo do
   título tinha uma margem negativa (`-mt-1`) que colava ele no título -- trocado por `mt-2` (mais
   distância) e o texto também foi encurtado (a frase "use 'Configurar manualmente' mais abaixo"
   não fazia mais sentido com a seção escondida).

**Fora do escopo / decisão consciente:** o mecanismo que consumia a flag `?atalho=1` em
`static/index.html` (`capturarAutoFluxoAtalhoDaUrl()`, `carregarAtalho()`) não foi removido --
ficou órfão (nenhum botão manda mais `?atalho=1`) mas inofensivo, só rodaria se alguém digitasse
isso na URL manualmente. Não mexi porque é fora do pedido específico do Igor desta vez; fica
registrado aqui caso ele prefira que eu limpe depois.

### O que foi verificado
- [x] HTML inteiro passa por um parser real (`html.parser.HTMLParser`, que entende limites de
      comentário corretamente, diferente de uma contagem de tags por regex): zero tags
      desbalanceadas, zero erros -- confirma que o `<details>` da configuração manual está
      corretamente dentro do novo comentário HTML (não vazou nem quebrou a estrutura da página).
- [x] Sintaxe do único bloco `<script>` inline (`node --check`) OK.
- [x] Teste de lógica isolado em Node para `gerarTokenRapido()` (fetch/clipboard/confirm mockados
      à mão): 9 asserções cobrindo (a) sem token ainda -- gera direto, sem confirmação; (b) já tem
      token, usuário confirma -- gera e substitui; (c) já tem token, usuário cancela -- não chama a
      API de gerar nem mexe na tela; (d) não logado (401) -- avisa pra fazer login, nunca tenta
      gerar; (e) clipboard falha mas o fallback `execCommand` funciona -- ainda conta como copiado;
      (f) os dois falham -- token aparece no campo mesmo assim, com mensagem de copiar na mão.
      Todas as 9 bateram.
- [x] `tests/test_static_pages.py` atualizado: o teste que verificava a página antiga foi reescrito
      (nova ordem dos passos, botão local em vez do link pra `/?atalho=1`, textos novos) e ganhou um
      teste novo que confirma estruturalmente que o `<details>` manual está dentro do comentário
      HTML que o esconde.
- [ ] `pytest -q` pelo GitHub Actions -- a confirmar após o push.
- [ ] **Teste de verdade no iPhone -- mesma limitação de sempre, sandbox sem navegador/app
      Atalhos reais.** Vale o Igor conferir: tocar em "Gerar token" sem sair da página e ver o
      token aparecer + a mensagem de copiado; tentar de novo já tendo um token ativo e confirmar que
      pede confirmação antes de revogar; conferir visualmente o espaçamento novo do texto de apoio;
      e confirmar que a seção manual realmente não aparece mais na tela (só o atalho rápido +
      problemas comuns).

---

## Etapa 4 da revisão de UX/copy -- Card Baixar, parte 5 (ícone de plataforma + ID curto no lote)
**Status:** Implementado e commitado na `main`; `pytest -q` pelo GitHub Actions a confirmar.
**Branch:** `main` (direto).
**Data:** 20/10/2026
**Origem:** Dossiê de UX e Copy do KLANGO.MP4, seção 4.3 "Aba Vários (lote)" -- item que tinha
ficado de fora nas partes 1-4 ("Fora do escopo desta etapa" listava explicitamente). Aprovação do
Igor: "Sim meu amigo, você tem total autonomia, pode seguir com tudo", reforçada pelo pedido de
continuar adiantando o que desse enquanto ele estava fora do computador.

**O que mudou:**
- `static/index.html`, função nova `gerarRotuloCurtoUrl(url, plataforma)`: gera um identificador
  curto a partir da URL (ex.: "Reel DenfGW7x" para Instagram, "Pin 123..." para Pinterest, "Vídeo
  ..." para TikTok/YouTube/qualquer outro domínio), reaproveitando a detecção de plataforma que já
  existia (`DOMINIOS_PLATAFORMA`, `detectarPlataformaCliente`, `ICONE_FONTAWESOME_POR_PLATAFORMA`)
  -- a mesma usada hoje só no ícone do campo de link avulso.
- Nas linhas de progresso do lote (vídeo em `processarLote()` e áudio em `processarLoteAudio()`,
  mesmo tratamento nos dois): a URL crua (que muitas vezes vem com `?xtok=`/`?mdxt=` de rastreio)
  deixou de aparecer em texto na linha. No lugar: um ícone da plataforma (quando reconhecida) +
  o nome dado pelo usuário (se ele preencheu um) ou o identificador curto gerado. A URL completa
  continua acessível via `title` (tooltip ao passar o mouse/segurar no celular) no próprio elemento
  da linha -- nada foi escondido de vez, só tirado da exibição direta.
- O valor de fato enviado ao backend (`entry.url`) não mudou em nada -- é só uma mudança de
  exibição no HTML/JS do cliente.

**Fora do escopo desta etapa** (ver lista já publicada nas partes 1-4): fusão da aba "Um link".

**Decisão do Igor (20/10/2026):** o botão "Tentar de novo" em item que falhou no lote pode ficar de
fora -- confirmado de novo, mesma posição já tomada lá na Etapa 2 ("Esse botão pode bugar o
aplicativo? ... pode deixar de fora"). Deixa de ser um item pendente de decisão/endpoint e passa a
ser uma exclusão de escopo definitiva; não deve voltar a ser levantado a menos que ele mude de
ideia.

### O que foi verificado
- [x] Sintaxe do único bloco `<script>` inline (`node --check`) OK.
- [x] Balanço de `<div>`/`<section>` no arquivo inteiro: mesmo saldo residual de sempre (`<div>`:
      301 abre / 298 fecha, igual antes desta etapa; `<section>`: 13/13).
- [x] Teste de lógica isolado em Node para `gerarRotuloCurtoUrl()`: 10 asserções cobrindo Instagram
      (reel/post/IGTV), TikTok, YouTube (link `watch?v=` e link curto `youtu.be`), Pinterest,
      domínio desconhecido (cai no genérico truncado) e URL inválida -- todas bateram.
- [x] Teste de lógica isolado (fake DOM) para a construção da linha (ícone + rótulo + tooltip):
      8 asserções confirmando que (a) a URL completa fica em `title`; (b) o ícone da plataforma
      aparece com a classe certa quando reconhecida; (c) nome dado pelo usuário tem prioridade sobre
      o rótulo gerado, mas o ícone continua aparecendo; (d) plataforma não reconhecida não gera
      ícone e cai no rótulo genérico -- todas bateram.
- [x] `grep` em `tests/` confirma que nenhum teste assertava o formato antigo
      (`${position}. ${entry.filename || entry.url}`) ou o texto da linha de exibição.
- [ ] `pytest -q` pelo GitHub Actions -- a confirmar após o push.
- [ ] **Prints antes/depois -- mesma limitação de sempre, sandbox sem Tailwind/navegador de
      verdade.** Vale o Igor conferir rodando um lote de verdade com links de plataformas
      diferentes (Instagram, TikTok, YouTube, Pinterest) e um link de domínio não reconhecido, e
      checando: o ícone aparece certo por plataforma, o texto fica curto e legível (sem a URL
      rastreada aparecendo crua), o nome que ele mesmo digitar continua tendo prioridade, e passar o
      mouse (ou segurar no celular) mostra a URL completa no tooltip.

---

## Etapa 4 da revisão de UX/copy -- Card Baixar, parte 4 (aviso de processamento + resumo + estados)
**Status:** Implementado e commitado na `main`; `pytest -q` pelo GitHub Actions a confirmar.
**Branch:** `main` (direto).
**Data:** 20/10/2026
**Origem:** Dossiê de UX e Copy do KLANGO.MP4, seção 4.3 "Aba Vários (lote)". Aprovação do Igor:
"Sim meu amigo, você tem total autonomia, pode seguir com tudo".

**`[confirmar]` do próprio dossiê, resolvido lendo o backend (`routes/generations.py`,
`routes/audio.py`):** a dúvida era se o processamento continua rodando com a tela do celular
bloqueada ou a aba em segundo plano. **Sim, continua** -- tanto o lote de vídeo
(`_run_batch_in_background`) quanto o de áudio (`routes/audio.py`, mesmo desenho) rodam numa
THREAD DO SERVIDOR (`threading.Thread`), totalmente independente da conexão SSE que alimenta a
tela. Travar o celular ou perder a conexão não cancela nem pausa nada -- só faz a lista de
progresso na tela parar de atualizar até reconectar. **Ressalva que ficou de fora da promessa**:
`GenerationHistoryOut` (`routes/me.py`) deliberadamente não expõe `batch_id`, então hoje não existe
um jeito de voltar depois pela tela de histórico e re-achar o link do ZIP de um lote que terminou
enquanto a pessoa estava longe da tela -- por isso o texto novo diz só "pode deixar em segundo
plano", nunca promete "o ZIP fica te esperando no histórico" (isso seria prometer um recurso que o
produto ainda não tem).

**O que mudou:**
- `static/index.html` (lote de vídeo e de áudio, mesmo tratamento nos dois): o aviso grande colorido
  ("O KLANGO.MP4 está preparando/extraindo... Isso pode levar alguns minutos.") virou uma linha
  cinza simples e fixa: "Leva alguns minutos. Pode deixar esta aba em segundo plano — o
  processamento continua normalmente." Separado dele, um resumo geral NOVO ("N de M prontos",
  elemento próprio `resumoProgresso`) que só aparece quando o primeiro evento de progresso chega --
  antes, o resumo SUBSTITUÍA o aviso inteiro (um sumia quando o outro aparecia); agora os dois
  convivem.
- Mensagens de status por item: "Baixando... (40%)" -> "Baixando 40%" (sem reticências antes do
  percentual, sem parênteses); "Processando e limpando..." -> "Limpando..." (texto mudado na
  origem, em `routes/generations.py::_run_batch_in_background`, já que é um literal de string no
  backend, não um dado dinâmico -- único ajuste de Python desta etapa, só a mensagem exibida, zero
  mudança de lógica/fluxo). O lote de áudio usa `services`/`routes/audio.py`, que já dizia
  "Extraindo o áudio..." (nunca teve o "Processando e limpando..." redundante) -- não precisou de
  mudança no backend, só o mesmo tratamento de formatação (sem reticências, sem parênteses) no
  frontend.

**Fora do escopo desta etapa** (ver lista já publicada nas partes 1/2/3): fusão da aba "Um link",
troca da URL crua por ícone de plataforma + ID curto durante o processamento, botão "Tentar de
novo".

### O que foi verificado
- [x] Sintaxe do único bloco `<script>` inline (`node --check`) OK; `ast.parse` em
      `routes/generations.py` OK.
- [x] Balanço de `<div>` no arquivo inteiro: mesmo saldo residual de sempre.
- [x] Leitura de `routes/generations.py` e `routes/audio.py` confirmando o desenho
      thread-do-servidor-independente-da-SSE para os dois tipos de lote (vídeo e áudio) -- base do
      `[confirmar]` resolvido acima.
- [x] Teste de lógica isolado em Node pra função de formatação de mensagem (percentual sem
      parênteses, sem reticências antes do número; textos sem percentual -- como o aviso do
      YouTube -- continuam intactos): 5 asserções, todas bateram.
- [x] `grep` em `tests/` confirma que nenhum teste assertava o texto antigo do aviso colorido ou de
      "Processando e limpando...".
- [ ] `pytest -q` pelo GitHub Actions -- a confirmar após o push.
- [ ] **Prints antes/depois -- mesma limitação de sempre, sandbox sem Tailwind.** Vale o Igor
      conferir de verdade: rodar um lote de vídeo e de áudio e ver o aviso cinza + o resumo "N de M
      prontos" aparecendo separados, as mensagens por item ("Baixando 40%", "Limpando..."), e
      idealmente testar na prática deixar a aba em segundo plano / bloquear o celular no meio de um
      lote, pra confirmar visualmente que volta mostrando itens prontos (não travado/perdido).

---

## Etapa 4 da revisão de UX/copy -- Card Baixar, parte 3 (linha compacta + "Colar vários links")
**Status:** Implementado e commitado na `main`; `pytest -q` pelo GitHub Actions a confirmar.
**Branch:** `main` (direto).
**Data:** 20/10/2026
**Origem:** Dossiê de UX e Copy do KLANGO.MP4, seção 4.3 "Aba Vários (lote)". Aprovação do Igor:
"Sim meu amigo, você tem total autonomia, pode seguir com tudo".

**O que mudou (tudo em `static/index.html`, dentro da aba "Vários (lote)" do card "Baixar em
lote"):**
- Linha compacta por vídeo (`criarCampoLote()`): o bloco antigo (rótulo "VÍDEO 01" numa linha,
  campo de link embaixo, campo de nome SEMPRE visível embaixo disso -- ~170px por item) virou uma
  linha só: número (`01`) + campo de link + lixeira. O campo de nome começa escondido atrás de um
  link pequeno "+ nome", revelado só quando alguém clica (função nova `revelarNomeLote()` também
  usada por `baixarSelecaoPinterest()`, pra nunca esconder um nome já sugerido pelo título do pin
  do Pinterest). Nenhuma classe usada por outro código mudou (`.batch-item`/`.batch-url`/
  `.batch-name` continuam exatamente iguais -- só o layout ao redor).
- Novo botão "Colar vários links" ao lado de "Adicionar vídeo": abre uma textarea onde cada linha
  não vazia vira um vídeo do lote de uma vez, no lugar de clicar em "Adicionar vídeo" repetidas
  vezes. Respeita o MESMO teto de itens por vez do plano que "Adicionar vídeo" já respeitava --
  links além do teto são descartados (nunca adicionados silenciosamente além do limite), com um
  aviso contando quantos ficaram de fora. Se só existir a linha vazia inicial (nunca tocada), o
  colar substitui em vez de empilhar em cima dela.
- O painel de demonstração pública "Lote na Prática" (`criarCampoLotePreview()`, função SEPARADA,
  nunca compartilhada com a de verdade) não foi tocado -- continua no formato antigo ("VÍDEO 01" +
  campo de nome sempre visível). É cosmético (card travado/exemplo, não funcional) -- fica
  catalogado como inconsistência visual menor, não uma mudança funcional pendente.

**Fora do escopo desta etapa** (ver lista já publicada nas partes 1/2): fusão da aba "Um link",
separação dos passos de processamento, resumo "N de M prontos", aviso de processamento rebaixado,
botão "Tentar de novo".

### O que foi verificado
- [x] Sintaxe do único bloco `<script>` inline (`node --check`) OK.
- [x] Balanço de `<div>`/`<section>` no arquivo inteiro: mesmo saldo residual de sempre, nenhum
      desbalanceamento novo.
- [x] Testes de lógica isolados em Node, desta vez com um DOM falso próprio (sandbox sem
      jsdom/Playwright nesta sessão -- `createElement`/`appendChild`/`querySelector(All)`/
      `classList`/`remove()` reimplementados à mão, só o suficiente pras funções testadas), 21
      asserções cobrindo: forma da linha compacta (rótulo só o número, campo de nome escondido,
      lixeira escondida só no item 1), clique em "+ nome" revela o campo e esconde o link,
      `revelarNomeLote()` revelando um nome pré-preenchido (cenário do Pinterest),
      `renumerarCamposLote()` renumerando certo depois de remover um item do meio, "Colar vários
      links" substituindo a linha vazia inicial com 3 links (ignorando linhas em branco), e
      respeitando o teto do plano (descarta o excedente com aviso, nunca ultrapassa o limite).
      Todos bateram o esperado (2 cenários de teste mal montados por mim na primeira tentativa
      foram corrigidos e re-testados antes de fechar esta etapa).
- [x] `grep` confirma que nenhum teste em `tests/` dependia do formato antigo do rótulo
      ("VÍDEO 01") ou das classes novas.
- [ ] `pytest -q` pelo GitHub Actions -- a confirmar após o push.
- [ ] **Prints antes/depois -- mesma limitação de sempre, sandbox sem Tailwind.** Vale o Igor
      conferir de verdade: adicionar/remover vídeos e ver a linha compacta (número + link +
      lixeira), clicar em "+ nome" e ver o campo aparecer, usar "Colar vários links" com uma lista
      de verdade, e conferir que um nome vindo do Pinterest já aparece revelado (sem precisar
      clicar em "+ nome").

---

## Etapa 4 da revisão de UX/copy -- Card Baixar, parte 2 (card único "Baixar em lote", com abas)
**Status:** Implementado e commitado na `main`; `pytest -q` pelo GitHub Actions a confirmar.
**Branch:** `main` (direto).
**Data:** 20/10/2026
**Origem:** Dossiê de UX e Copy do KLANGO.MP4, seção 4.1 "Nova estrutura da página logada" + 4.3 +
4.4. Aprovação do Igor: "Sim meu amigo, você tem total autonomia, pode seguir com tudo" (em
resposta direto à pergunta sobre como tratar a fusão em abas, a mudança estrutural de maior risco
deste projeto até aqui).

**Decisão de escopo tomada sozinho (documentando o porquê, pra você poder desfazer se discordar):**
O dossiê pede as 3 ferramentas (Um link / Vários / Pinterest) num card só com 3 abas. Implementei a
fusão de **2 das 3** (Vários + Pinterest) e deixei o formulário de "Um link" de fora, por 2 motivos
concretos encontrados no próprio código, não por excesso de cautela genérica:
1. **"Um link" está na posição de maior destaque da página** (dentro do hero, estilizado como
   `hero-glow`, com vários comentários de "rodada de conversão" no histórico) -- é o fluxo mais
   usado e o mais testado/ajustado ao longo do projeto. Tirar ele dali pra um card mais abaixo seria
   uma mudança de conversão pra visitante anônimo, não só uma reorganização visual.
2. **Você mesmo pediu em 04/10/2026** que o card de lote ficasse "logo depois do próprio status
   'Meu acesso'" (comentário original ainda estava no código, citado linha por linha). O card único
   que fiz manteve exatamente essa posição -- fundir "Um link" ali também teria empurrado ele pra
   longe do topo da página, o oposto do que o pedido original de 04/10 queria.
Se você quiser a fusão completa das 3 mesmo assim (inclusive mover o formulário do topo), é só
pedir -- a base (sistema de abas, função `atualizarVisibilidadeSecaoBaixarLote()`) já está pronta
pra receber uma 3ª aba.

**O que mudou (tudo em `static/index.html`):**
- Os 2 cards separados "Processamento em lote" (`#secaoBatch`) e "Importar pasta do Pinterest"
  (`#secaoPinterestBoard`) viraram 1 card só, `#secaoBaixarLote` ("Baixar em lote"), com 2 abas:
  "Vários (lote)" e "Pasta do Pinterest". Fica exatamente na mesma posição de antes (logo depois do
  card "Meu acesso").
- Nenhum id interno mudou -- `#secaoBatch` e `#secaoPinterestBoard` continuam existindo, com todo o
  conteúdo/ids de dentro intactos (campos, botões, textos dinâmicos), só que agora são `<div>`s
  dentro das abas em vez de `<section>`s soltas. Nenhuma função JS de dentro de cada ferramenta foi
  reescrita -- só ganharam um wrapper de aba por fora.
- Removido o cabeçalho duplicado (ícone + título + subtítulo) de cada card -- vira 1 cabeçalho só
  ("Baixar em lote") + a barra de abas; o texto funcional de cada ferramenta (ex.: "Adicione até N
  vídeos por vez...", "Cole o link de uma pasta pública...") continua, só o título decorativo saiu.
- 3 funções novas: `selecionarAbaBaixarLote(aba)` (clique do usuário), `aplicarAbaBaixarLote()`
  (desenha qual painel/botão fica visível/ativo) e `atualizarVisibilidadeSecaoBaixarLote()`
  (decide se o card aparece e se cada aba está disponível pra esta conta -- chamada por
  `atualizarVisibilidadeBatch()`/`esconderBatch()` quando o lote muda, e por
  `carregarStatusPinterestBoard()` quando o Pinterest muda; as duas rodam de forma independente e
  em ordem não garantida, então a função nunca assume que a outra já rodou).
- **Duas razões de "escondido" nunca se misturam**: a aba não selecionada esconde o PAINEL por
  fora; o plano não habilitado continua escondendo a FERRAMENTA por dentro (exatamente como já
  funcionava) -- uma nunca sobrescreve a outra.
- Troca automática de aba se a aba ativa ficar sem conteúdo pra esta conta (ex.: só Pinterest
  disponível) -- nunca deixa o usuário numa aba selecionada vazia.
- As 2 chamadas que inseriam itens no lote e rolavam a tela até `#secaoBatch` (vindas da fila do
  atalho e de "Baixar selecionados" do Pinterest) agora também trocam pra aba "Vários (lote)" antes
  de rolar -- senão a tela rolaria até um painel escondido (ainda na aba Pinterest).

**Fora do escopo desta etapa** (catalogado, ver "Decisão de escopo" acima e a lista já publicada na
parte 1): fusão da aba "Um link", linha compacta por vídeo, "Colar vários links", separação dos
passos de processamento, resumo "N de M prontos", aviso de processamento rebaixado, botão "Tentar
de novo".

### O que foi verificado
- [x] Sintaxe do único bloco `<script>` inline (`node --check`) OK.
- [x] Balanço de tags `<div>` E `<section>` no arquivo inteiro conferido antes/depois: `<section>`
      13 abre/13 fecha (sem órfã -- os 2 cards antigos viraram 1, -1 líquido, bateu); `<div>` com o
      mesmo saldo residual pré-existente de sempre (+3), nenhum desbalanceamento novo.
- [x] `grep` em todo o arquivo por seletor de CSS que dependesse de `#secaoBatch`/
      `#secaoPinterestBoard` serem especificamente `<section>` (em vez de `<div>`) -- nenhum
      encontrado (tudo no arquivo usa `getElementById`, nunca um seletor combinando tag+id).
- [x] `grep` por qualquer código que dependa da ORDEM/posição dos 2 cards no DOM (seletores tipo
      `parentElement`/`nextElementSibling`/`closest` apontando pra eles) -- nenhum encontrado; todo
      acesso no projeto inteiro é por id (`getElementById`), nunca posicional -- por isso mover o
      conteúdo de lugar no documento era seguro.
- [x] Testes de lógica isolados em Node (document/elementos simulados à mão -- sandbox sem
      pip/npm/Playwright nesta sessão), 17 asserções cobrindo: card visível quando as 2 ferramentas
      estão disponíveis (aba padrão "Vários" ativa, botões com a classe certa); troca de aba manual
      (clique do usuário); Free com só Pinterest disponível (botão "Vários" escondido, troca
      automática de aba); as 2 ferramentas indisponíveis ao mesmo tempo (card inteiro escondido);
      lote volta a ficar disponível depois de indisponível (troca automática de volta, sem ficar
      preso na aba antiga). Todos bateram o esperado.
- [x] `grep` confirma que nenhum teste em `tests/` dependia de `secaoBatch`/`secaoPinterestBoard`
      serem seções soltas na página.
- [ ] `pytest -q` pelo GitHub Actions -- a confirmar após o push.
- [ ] **Prints antes/depois -- mesma limitação de sempre, sandbox sem Tailwind, e esta é a mudança
      estrutural de maior risco do projeto até aqui.** Vale MUITO o Igor conferir de verdade:
      entrar com uma conta de plano pago com lote, ver o card único "Baixar em lote" logo depois de
      "Meu acesso", clicar nas 2 abas e ver cada ferramenta funcionando normalmente (adicionar
      vídeo, trocar formato, listar pasta do Pinterest), e testar os 2 fluxos que inserem itens e
      rolam a tela (enviar a fila do atalho pro lote; selecionar pins do Pinterest e "Baixar
      selecionados") pra confirmar que a aba certa abre e a rolagem chega no lugar certo. Testar
      também com uma conta Free/sem plano de lote, pra ver só a aba Pinterest (com o estado
      bloqueado) e a aba "Vários" escondida.

---

## Etapa 4 da revisão de UX/copy -- Card Baixar, parte 1 (textos/contadores do lote e do Pinterest)
**Status:** Implementado e commitado na `main`; `pytest -q` pelo GitHub Actions a confirmar.
**Branch:** `main` (direto).
**Data:** 20/10/2026
**Origem:** Dossiê de UX e Copy do KLANGO.MP4, seção 4.3 "Aba Vários (lote)" + 4.4 "Aba Pasta do
Pinterest". Aprovação do Igor: "Pode prosseguir".

**Recorte desta parte:** o dossiê pede, entre outras coisas, mesclar as 3 ferramentas de download
(link único / lote / Pinterest) num card só com 3 abas -- a mudança estrutural de maior risco deste
projeto até aqui (envolve relocar seções com `id` referenciado por âncora/`mostrarSecaoSeEscondida()`,
ou construir um seletor de abas por cima das 3 seções existentes sem mover nada). Separei essa
decisão (ver "Fora do escopo" abaixo) do que já dava pra fazer com segurança: os textos/contadores
do card de lote e do card do Pinterest, que o dossiê também pede e não dependem da fusão em abas.

**O que mudou (tudo em `static/index.html`):**
- Removido o selo de plano (`#batchPlanoTag`, ex.: "VIP BATCH" ao lado do contador) do card de
  lote -- pedido explícito do dossiê ("Sem selo de plano aqui"). O contador agora é texto normal:
  "N vídeos · até M por vez" (ou "· sem limite por vez nesta conta" pra admin), no lugar de "N de M
  vídeo(s)" + selo.
- Botão principal do lote (`#batchButton`) ganhou rótulo dinâmico: "Baixar N vídeos" (ou "Baixar N
  áudios" no formato áudio), no lugar do texto fixo "Processar e preparar lote"/"Processar e
  extrair áudio do lote" -- atualizado a cada item adicionado/removido e a cada troca de formato
  (função nova `atualizarTextoBotaoLote()`, chamada de `atualizarContagemLote()` e de
  `atualizarVisibilidadeAudioLote()`, nos mesmos pontos que já atualizavam o botão).
- Novo contador "Vai usar N dos seus M vídeos de hoje."/"Você usou seus M vídeos de hoje. Amanhã
  libera de novo." para o lote de VÍDEO (`#videoContadorLote`, função nova
  `atualizarContadorVideoLoteTexto()`) -- o lote de ÁUDIO já tinha esse texto desde a Parte B
  (`#audioContadorLote`); o de vídeo usava só "N de M vídeos" sem falar da cota diária. Usa o
  mesmo dado que já chega em `/api/me/status` (`uso.remaining`/`uso.limit`, quando `period ===
  "day"`) -- nenhuma chamada de rede nova.
- Painel de exemplo público "Lote na Prática" (`#loteNaPraticaItems`, card travado/cadeado):
  rótulo do botão estático atualizado de "Processar e preparar lote" para "Baixar vídeos", só para
  acompanhar o rótulo real (não lê contagem nenhuma, é mock fixo).
- "Na fila..." -> "Na fila" (sem reticências) nos status por item do lote de vídeo e do lote de
  áudio durante o processamento -- eco do trabalho de unificação de estados já sinalizado (não
  terminado) na Etapa 2.
- Pinterest (`#secaoPinterestBoard`): placeholder do campo de link de
  "https://www.pinterest.com/usuario/pasta/" (URL de exemplo completa) para "Link da pasta do
  Pinterest"; botão "Listar pasta" -> "Ver o que tem na pasta"; texto de intro simplificado para
  "Cole o link de uma pasta pública e escolha o que baixar." (cor já era verde-água desde a Etapa
  2, altura do campo já era 48px -- o dossiê cita "~40px", mas o código real já estava em 48px;
  nenhuma das duas precisava de mudança). O contador de consumo do Pinterest ("Vai usar N dos seus
  M vídeos de hoje.") já estava correto desde antes desta etapa -- conferido, não alterado.

**Fora do escopo desta parte (catalogado, não esquecido):**
- **Card único "Baixar" com 3 abas** (fusão de #secaoBatch, #secaoPinterestBoard e o formulário de
  link único). Ainda não decidi nem propus ao Igor a estratégia de execução -- minha inclinação é
  por um seletor de abas que só alterna `hidden` nas 3 seções já existentes, sem mover nenhum nó do
  DOM (evita quebrar âncoras `#secaoBatch`/`#secaoPinterestBoard` e chamadas de
  `mostrarSecaoSeEscondida()` que já apontam pra esses ids), mas isso precisa ser confirmado antes
  de mexer -- é a mudança estrutural de maior risco do projeto até aqui.
- **Linha compacta por vídeo** (substituir o bloco atual de ~170px por item por uma linha só, com
  link pequeno "+ nome" pra revelar o campo de nome sob demanda) -- não implementado, requer
  reescrever `criarCampoLote()`.
- **"Colar vários links"** (textarea que vira uma linha por vídeo) -- recurso novo, não existe
  ainda.
- Durante o processamento: trocar a URL crua (com `?xtok=`/`?mdxt=`) por ícone de plataforma + ID
  curto ou nome dado pelo usuário (URL completa só no title/tooltip); separar "Processando e
  limpando… (100%)" em passos distintos ("Baixando 40%" -> "Limpando…" -> "Pronto"); resumo geral
  no topo da lista ("2 de 6 prontos"); aviso de processamento rebaixado para linha cinza simples
  ("Leva alguns minutos. Pode deixar esta tela aberta.") -- o `[confirmar]` do próprio dossiê aqui
  (o processamento continua com a tela do celular bloqueada?) ainda não foi verificado contra o
  fluxo real de SSE do backend, então não troquei o aviso por um texto que pode estar prometendo
  algo que não é verdade.
- **Botão "Tentar de novo" em item que falhou** -- mesma lacuna já sinalizada e deixada de fora na
  Etapa 2 ("Esse botão pode bugar o aplicativo? ... pode deixar de fora"): continua sem endpoint de
  backend para reprocessar um único item de um lote (`grep` confirmado de novo nesta etapa, nenhum
  resultado novo em `routes/`/`services/`). Mantenho fora até esse endpoint existir ou você decidir
  diferente.

### O que foi verificado
- [x] Sintaxe do único bloco `<script>` inline (`node --check`) OK.
- [x] Testes de lógica isolados em Node (document/elementos simulados à mão -- sandbox sem
      pip/npm/Playwright nesta sessão): `atualizarContagemLote()` (texto normal e "sem teto",
      singular/plural, chamando os 3 contadores/botão certos), `atualizarContadorVideoLoteTexto()`
      (texto normal, saldo esgotado, contador escondido quando a cota ainda não chegou),
      `atualizarTextoBotaoLote()` (vídeo x áudio, singular x plural), e a lógica nova de
      `atualizarVisibilidadeBatch()` que guarda o saldo diário (`uso.remaining`/`uso.limit`) em
      `loteVideoRestanteHoje`/`loteVideoLimiteHoje` -- cobrindo VIP Batch, Semanal, conta admin
      (`unlimited: true`), Free (`period: "week"`, cai pro fallback) e `uso` nulo. Confirmado que
      `atualizarContadorAudioLoteTexto()` (não tocada) continua com o mesmo resultado de antes
      (teste de regressão). Todos bateram o esperado.
- [x] `grep` confirma que nenhum teste em `tests/` dependia de `batchPlanoTag`, do texto "Processar
      e preparar lote"/"Processar e extrair áudio do lote", de "Na fila..." (com reticências) ou de
      "Listar pasta".
- [x] Balanço de tags `<div>` no arquivo inteiro conferido antes/depois (mesmo saldo residual
      pré-existente de sempre, nenhum novo desbalanceamento -- só troquei um `<span>` por um `<p>`,
      nenhum `<div>` novo).
- [ ] `pytest -q` pelo GitHub Actions -- a confirmar após o push.
- [ ] **Prints antes/depois -- mesma limitação de sempre, sandbox sem Tailwind.** Vale o Igor
      conferir de verdade: adicionar/remover vídeos no lote e ver o contador/botão mudarem juntos
      ("3 vídeos · até 5 por vez" / "Baixar 3 vídeos"), trocar pra "Só áudio" e ver o botão virar
      "Baixar N áudios", e abrir a aba do Pinterest pra ver o placeholder/botão/texto novos.

---

## Etapa 3 da revisão de UX/copy -- Painel logado (header, hero, faixa do atalho, Meu acesso)
**Status:** Implementado e commitado na `main` (`11285eb`); `pytest -q` pelo GitHub Actions confirmado.
**Branch:** `main` (direto).
**Data:** 20/10/2026
**Origem:** Dossiê de UX e Copy do KLANGO.MP4, seção "Painel logado" (4.1 "Nova estrutura da página
logada" + 4.2 "Plano e limite"). Aprovação do Igor: "Pode seguir para próxima etapa".

**[confirmar] da seção 4.2, resolvido sem perguntar:** a dúvida era se "Sem limite de itens por
operação nesta conta" (texto do card de lote) é um bug de conta admin ou um texto real pra todo
cliente VIP Batch. Conferido em `services/plans.py`: VIP Batch tem `batch_limit=None` (operações de
lote ilimitadas) por desenho do plano -- não é exclusividade de admin. A isenção de admin
(`_is_admin`/`loteSemTeto`) é outra coisa, já corretamente isolada só pra conta de teste. Nenhuma
mudança de texto necessária.

**O que mudou (tudo em `static/index.html`):**
- Header: "Acesso" vira "Minha conta" (menu: Meu plano / Ver planos / Sair) quando há sessão ativa;
  sem sessão continua "Acesso" como sempre. Corrigido de passagem: o link "Planos" da nav não tinha
  o `mostrarSecaoSeEscondida()` que os outros 3 já tinham -- rolava pra uma seção invisível pra
  cliente pago.
- Hero de venda (título, subtítulo, tira de passos, mascote) escondido pra qualquer sessão ativa
  (grátis ou paga) -- diferente do funil frio existente, que só escondia pra quem já pagou. Badge
  de status e formulário continuam sempre visíveis.
- Faixa "N vídeos chegaram pelo atalho" no topo, só com itens na fila -- reaproveita a contagem que
  a seção do Atalho já busca.
- Card "Meu acesso": removida a linha duplicada de capacidade de lote; barra de uso sempre
  contínua (nunca mais segmentada); destaque âmbar + botão "Renovar" quando faltam ≤7 dias pra
  expirar (reaproveita o checkout de upgrade, comprando o mesmo plano de novo). Aposentado o badge
  "Expira em N dias" do topo da página (3ª repetição da mesma informação).

**Fora do escopo** (por desenho do dossiê, não esquecimento): o card único "Baixar" com 3 abas
(Um link / Vários / Pinterest) é Etapa 4 -- nenhuma das 3 ferramentas foi movida/mesclada aqui.

### O que foi verificado
- [x] Sintaxe do único bloco `<script>` inline (`node --check`) OK.
- [x] Testes de lógica isolados em Node (document/elementos simulados à mão -- sandbox sem
      pip/npm/Playwright nesta sessão) para as 9 funções novas/alteradas: toggle do hero por
      sessão, abrir/fechar do menu da conta (incl. `aria-expanded`), "Meu plano" indo pro alvo
      certo (card pago vs vitrine Free), barra sempre contínua, destaque âmbar + "Renovar" no
      limiar de 7 dias, `renovarPlanoAtual()` chamando o checkout com o plano certo, texto/
      visibilidade da faixa do atalho com `plural()` correto -- todos bateram o esperado.
- [x] `grep` confirma que nenhum teste em `tests/` dependia dos elementos removidos/renomeados
      (`assinaturaStatus`, `statusContaValidade`, `headerAcessoBtn`).
- [x] Balanço de tags `<div>` no arquivo inteiro conferido antes/depois (mesmo saldo residual
      pré-existente, nenhum novo desbalanceamento introduzido).
- [x] `pytest -q` = 0 failed pelo GitHub Actions (commit `11285eb`, confirmado).
- [ ] **Prints antes/depois -- mesma limitação de sempre, sandbox sem Tailwind.** Esta etapa mexeu
      em comportamento visível mais do que as anteriores (um componente de menu novo, uma faixa
      nova, textos que somem/aparecem por sessão) -- vale a pena o Igor testar de verdade: entrar
      logado, ver o header virar "Minha conta", abrir o menu (Meu plano/Ver planos/Sair), ver o
      hero de venda sumir, e -- se tiver como simular uma conta perto do vencimento -- conferir o
      destaque âmbar e o botão "Renovar" no card.

---

## Etapa 1 da revisão de UX/copy -- Correções rápidas de texto
**Status:** Implementado e commitado na `main`; `pytest -q` pelo GitHub Actions a confirmar.
**Branch:** `main` (direto).
**Data:** 19/10/2026
**Origem:** Dossiê de UX e Copy do KLANGO.MP4 (Oct 19, 2026, @Tuxi), entregue pelo Igor. Plano de
execução em 7 etapas; esta é a primeira ("correções rápidas de texto, baixo risco"). Só mudança
visual/texto -- nenhuma lógica de pagamento, autenticação, limites ou migrations tocada.

**[confirmar] levantados antes de implementar:** dos 8 itens da seção "Plano de execução" do
dossiê, só um bloqueava esta etapa -- "áudio e vídeo dividem o mesmo limite diário?" --, resolvido
direto no código (`services/plans.py::Plan.limit` varia 5/10/15 por plano; `config.py::AUDIO_EXTRACTIONS_PER_DAY`
é fixo em 15, ledger próprio `audio_extractions`): **não dividem**, então o texto do contador de
áudio usa "áudios" (não "vídeos") e o número vem de `GET /api/audio/allowance.limit` (nunca
hardcoded). Os outros 7 itens ficam para as etapas em que o dossiê já os previa (4, 5, 6 ou 7).

**O que mudou (tudo em `static/index.html`):**
- `plural(n, singular, plural)` (função nova): elimina "1 vídeos"/"1 extrações" fixo. Aplicada em
  todo contador visível que fala de vídeo/áudio (lote de vídeo, lote de áudio, Pinterest, "Meu
  acesso", validação de excesso de itens do lote).
- "Renova automaticamente amanhã/semana que vem" -> "Seu limite volta amanhã/na próxima semana"
  (contradizia o checkout, que promete "sem renovação automática").
- `abrirAcesso()`: limpa a mensagem de status do modal ("Você saiu da conta.") toda vez que abre --
  antes ficava visível de uma sessão anterior.
- Emojis 🎬/🎵 dos botões "Baixar vídeos"/"Extrair áudios" (fila do atalho) -> ícones Font Awesome
  (`fa-film`/`fa-music`), como no resto do site.
- Glossário (jargão -> termo comum), em todas as mensagens geradas por JS que não pertencem às
  seções reescritas nas Etapas 6/7 (planos, FAQ, hero, checkout -- essas ficam como estão, serão
  substituídas por conteúdo novo lá): "gerações"/"extrações" -> "vídeos"/"áudios"; "por operação"
  -> "por vez"; único "--" visível encontrado (dentro de um desses textos) -> "—".
- Seção Comunidade (comentários) escondida: `const MOSTRAR_COMENTARIOS = false` controla a seção
  inteira, os links de nav (header e rodapé) e evita a chamada de rede de `carregarComentarios()`.
  Backend/tabela/endpoints intactos -- religar é só trocar a constante pra `true`.

**Mensagens com o jargão que vêm do BACKEND (não alteradas nesta etapa -- listadas pro Igor decidir
depois, conforme pedido):**
- `routes/generations.py:133` (429) -- "Você atingiu o limite de gerações do seu plano."
- `routes/generations.py:139` (400) -- "Não foi possível processar esta geração."
- `routes/generations.py:153,194` (500) -- "Não foi possível concluir o registro desta geração."
- `routes/generations.py:180` (429, caminho de lote) -- mesmo texto de 133.
- `routes/generations.py:196` (400, caminho de lote) -- mesmo texto de 139.
- `routes/audio.py:111,247` (500) -- "Não foi possível concluir o registro desta extração."
- `services/audio_usage.py:66` (`AudioUsageError`, 400 default) -- "Não foi possível processar
  esta extração de áudio."
- `services/audio_usage.py:84` (`AudioQuotaExceededError`, 429) -- "Você usou suas 15 extrações de
  hoje. Amanhã libera de novo." (a versão do FRONTEND desta mesma mensagem, mostrada antes de
  qualquer chamada ao backend, já foi corrigida nesta etapa -- ver acima.)
- `services/audio_usage.py:97` (`AudioConcurrentOperationError`, 409) -- "Já existe uma extração
  de áudio em andamento. Aguarde terminar."

### O que foi verificado
- [x] Sintaxe do JS inline (`node --check`) e `ast.parse` dos arquivos Python tocados.
- [x] Verificação estrutural via Playwright (DOM, sem depender de CSS): `#comentarios` e os dois
      links de nav têm a classe `hidden`; `plural(1, ...)` devolve singular e `plural(5, ...)`
      devolve plural; `abrirAcesso()` limpa a mensagem de status antiga; `atualizarCardAcessoDetalhes()`
      produz "Seu limite volta amanhã"/"...na próxima semana" para period "day"/"week";
      `atualizarContadorAudioLoteTexto()` produz "Vai usar 1 dos seus 15 áudios de hoje."/"Você
      usou seus 15 áudios de hoje..."; nenhum emoji 🎬/🎵 restante no HTML; botões da fila mostram
      os ícones Font Awesome certos.
- [x] `pytest -q` = 0 failed pelo GitHub Actions (commit `cf2be5d`, confirmado).
- [ ] **Prints antes/depois (390px e desktop) -- NÃO foi possível produzir no sandbox.** O sandbox
      bloqueia toda saída de rede externa além de alguns registries (`npm`, `pip`): `cdn.tailwindcss.com`,
      `fonts.googleapis.com` e `cdnjs.cloudflare.com` (Font Awesome) deram `ERR_TUNNEL_CONNECTION_FAILED`
      ao tentar renderizar a página aqui -- sem Tailwind carregado, qualquer print mostraria a
      página sem nenhum estilo (ou, pior, com elementos certos escondidos por engano por um CSS
      de verificação manual que criei e descartei, que não reproduz as regras responsivas reais do
      Tailwind). Por isso a verificação acima foi feita direto no DOM/JS (prova que a lógica está
      certa), não por imagem. Pede-se ao Igor uma conferência visual rápida (navegador de verdade,
      390px e desktop) nos pontos: cabeçalho/rodapé sem "Comentários", seção Comunidade ausente,
      botões da fila do atalho com ícone (sem emoji), "Meu acesso" mostrando "Seu limite volta
      amanhã/na próxima semana", contador do lote sem "1 vídeos"/"1 extrações".

---

## Etapa 2 da revisão de UX/copy -- Fundamentos visuais (parte 1: cores e toque mínimo)
**Status:** Implementado e commitado na `main` (`17eef27`); `pytest -q` pelo GitHub Actions confirmado.
**Branch:** `main` (direto).
**Data:** 20/10/2026
**Origem:** Dossiê de UX e Copy do KLANGO.MP4, seção "Fundamentos visuais" (Etapa 2, "baixo risco mas
toca tudo"). Aprovação do Igor: "Pode seguir para etapa 2 meu amigo" (sem [confirmar] pendente --
esta seção do dossiê não tinha nenhum). Primeira fatia: só os itens mecânicos/objetivos, que dá pra
conferir sem precisar ver a página renderizada (CSS/classe presente ou não, altura de um botão em
px) -- deixei de fora, de propósito, a parte que depende de julgamento visual (ver "Fica para a
próxima fatia" abaixo).

**O que mudou (tudo em `static/index.html`):**
- **Cores -- roxo (violet) só na marca do plano VIP:** o dossiê reserva violeta exclusivamente para
  o selo/card do plano VIP Batch; o resto do site deve usar verde-água (`#19D3AE`), a cor primária.
  Corrigido: o card/seção de processamento em lote (`#secaoBatch`), o card de importar pasta do
  Pinterest (`#secaoPinterestBoard`), o painel de exemplo "como funciona o lote" (seção pública), o
  toggle de formato vídeo/áudio do lote (HTML estático e a função `selecionarFormatoLote()`), os
  avisos de "preparando os vídeos um por um" (lote de vídeo e de áudio), os campos de URL/nome de
  cada item do lote, e o destaque de item selecionado na grade do Pinterest. Confirmado que o card
  do plano VIP (seção Planos) e o badge de `setStatusConta(..., "vip")` já usavam violeta
  corretamente (só aparecem quando o plano É o VIP) -- nenhum dos dois foi tocado.
- **Altura mínima de toque (44px) em botões abaixo disso:** "Ver mais" do painel de exemplo do lote
  (h-8/32px -> h-11/44px) e "Baixar novamente" da lista de downloads (h-9/36px -> h-11/44px). Os
  botões "Gerar novo token"/"Revogar token", citados no dossiê como ~36px, já estavam em h-11 (44px)
  no código atual -- nenhuma mudança necessária ali (dossiê parece ter sido escrito contra uma
  versão anterior).

**Fica para a próxima fatia de Etapa 2 (ainda não tocado, precisa de escopo/conferência antes):**
- **Escala tipográfica:** o dossiê define 5 papéis de texto (título hero, título de seção, título de
  card, corpo, label) com tamanho/peso fixos. O arquivo tem hoje ~300+ ocorrências espalhadas de
  `text-xs`/`text-sm`/`text-[9px..18px]` -- cada uma precisa de um julgamento (qual papel ela cumpre
  de verdade) que não dá pra fazer com segurança sem ver a página renderizada, e o Tailwind CDN
  continua bloqueado neste sandbox (mesma limitação da Etapa 1). Proposta: ou eu faço em sub-lotes
  menores por seção (com o Igor conferindo cada um no navegador dele antes do próximo) ou o Igor
  prefere revisar o resultado todo de uma vez ao final -- preciso da decisão dele antes de avançar
  aqui.
- **Espaçamento:** gap entre seções (64px mobile/96px desktop) e padding interno de card (20px/24px)
  -- mesma dependência de conferência visual acima.
- **Estados de processamento:** unificar os 5 estados (Na fila/Baixando/Pronto/Erro/Expirado) com a
  mesma cor/ícone nas 4 superfícies que os usam (lote de vídeo, lote de áudio, Pinterest, fila do
  atalho) -- ainda não catalogado em detalhe.
- **Links terciários sem altura de toque definida:** "Limpar fila" e "Trocar e-mail" não têm classe
  `h-*` nenhuma (ficam do tamanho natural do texto, bem abaixo de 44px) -- diferente dos botões
  acima, que tinham uma altura explícita curta, esses não têm altura nenhuma, então a correção exige
  adicionar padding/área de toque sem alterar o tamanho visual do texto (jeito de fazer isso com
  segurança ainda não decidido).

### O que foi verificado
- [x] Sintaxe do único bloco `<script>` inline (`node --check`) OK, antes e depois de cada edição.
- [x] `grep` confirma: nenhum `violet` restante fora do card do plano VIP (linhas 563-575) e do
      badge já corretamente escopado de `setStatusConta()`.
- [x] Nenhum teste em `tests/` referenciava as classes `violet`/alturas alteradas (`grep` vazio).
- [x] `pytest -q` = 0 failed pelo GitHub Actions (commit `17eef27`, confirmado).
- [ ] **Prints antes/depois -- mesma limitação da Etapa 1, não produzidos no sandbox.** Mudança
      desta fatia é só cor (classe trocada) e altura de 3 botões -- baixo risco de ter saído
      diferente do esperado, mas uma conferência visual rápida do Igor (lote, Pinterest, lista de
      downloads) continua valendo, especialmente pra confirmar que o verde-água não ficou esbranquiçado
      ou mal contrastado em algum desses fundos escuros.

---

## Etapa 2 da revisão de UX/copy -- Fundamentos visuais (parte 2: piso tipográfico, gap, toque)
**Status:** Implementado e commitado na `main` (`e716a33`); `pytest -q` pelo GitHub Actions confirmado.
**Branch:** `main` (direto).
**Data:** 20/10/2026
**Origem:** Mesma fonte da parte 1. Igor escolheu (quando perguntado como preferia seguir) fazer o
resto da Etapa 2 de uma vez e revisar o resultado completo no final, em vez de sub-lotes com
conferência a cada um.

**O que mudou (tudo em `static/index.html`):**
- **Piso de 13px em texto legível** (dossiê: "nada abaixo de 13px"): `text-[9px]` (1), `text-[10px]`
  (23), `text-[11px]` (52) e `text-xs`/12px (171) -- total de 247 ocorrências revisadas uma a uma
  por grep antes da troca em lote; 225 viraram `text-[13px]`, 22 ficaram como estavam por serem
  dimensionamento de ícone Font Awesome isolado (isento pelo dossiê) -- lista completa conferida
  manualmente (setas do "como funciona", cadeado do campo desabilitado, ícones de
  reenviar/trocar e-mail, check do Pinterest selecionado, check de "Pagamento único"/"Sem renovação
  automática").
- **Gap entre seções (64px mobile / 96px desktop):** as 7 seções que ainda usavam `py-8`/`py-10`
  (`#paraQuemE`, `#areaAssinatura`, `#secaoBatch`, `#secaoPinterestBoard`, `#secaoAtalho`,
  `#secaoHistorico`, `#secaoIndicacao`) foram unificadas para `py-16 sm:py-24`, igual às outras 6
  que já estavam certas.
- **Toque mínimo (44px) em links terciários sem altura definida:** "Limpar fila", "Reenviar código"
  e "Trocar e-mail" ganharam `h-11 inline-flex items-center` (visual do texto/ícone inalterado, só
  a área clicável). "Ver todos os downloads" já estava correto.

**Catalogado, mas NÃO alterado nesta fatia (decisão pendente do Igor):**
- **Padding interno dos cards (dossiê pede 20/24px):** hoje a maioria usa 28px fixo (`p-7`) ou
  24/28px responsivo (`p-6 sm:p-7`) -- só um card (linha 988) já está no padrão exato. Mudar ~15
  cards de uma vez, vários com selos em posição `absolute` (ex.: "Recomendado", "Maior lote
  disponível"), é um risco real de colisão visual que só dá pra checar olhando a página de verdade
  -- não mudei às cegas.
- **Altura única de campo de texto (dossiê pede 52px):** o campo de URL do hero (o mais importante
  e mais testado do site) está em `h-14`/56px; outros campos estão em `h-11`/44px ou `h-12`/48px.
  Unificar em 52px exige reduzir o campo principal, que é o elemento de maior visibilidade do site
  -- preferi não arriscar isso sem conferência visual.
- **5 estados de processamento (Na fila/Em andamento/Pronto/Erro/Expirado):** as duas filas de lote
  (vídeo e áudio) já usam as MESMAS cores (verde-água = sucesso, vermelho = erro, cinza = na
  fila/processando) e os mesmos textos entre si -- não há inconsistência de cor pra corrigir ali.
  A diferença real pro dossiê é de redação ("Na fila..." em vez de "Na fila", "Pronto." em vez de
  "Pronto") e, principalmente, que o dossiê pede um estado de ERRO com link "Tentar de novo" e um
  de EXPIRADO com link "Processar de novo" -- **isso não existe hoje** (um item que falha só mostra
  o texto de erro, sem botão de tentar de novo). Adicionar esse botão é lógica nova (teria que
  reenviar aquele item específico pro backend), não só estilo -- não implementei sem confirmar que
  está dentro do escopo "só visual" desta etapa. Pinterest e fila do atalho ainda não comparados em
  detalhe com os dois lotes.

### O que foi verificado
- [x] Sintaxe do único bloco `<script>` inline (`node --check`) OK antes e depois de cada lote de
      substituição.
- [x] `grep`, depois de cada substituição em massa, confirma que as 22 exceções de ícone
      permaneceram exatamente como estavam.
- [x] Nenhum teste em `tests/` referencia as classes de espaçamento/tamanho/altura alteradas.
- [x] `pytest -q` = 0 failed pelo GitHub Actions (commit `e716a33`, confirmado).
- [ ] **Prints antes/depois -- mesma limitação de sempre, sandbox sem Tailwind.** Esta fatia mexeu
      em 225 pontos de tamanho de fonte (todos +1px ou mais, nunca redução) e no espaçamento entre
      7 seções (todos aumentando o espaço em branco, nunca diminuindo) -- mudanças de baixo risco
      de quebra, mas numerosas o bastante para merecer uma passada visual geral do Igor pela home
      inteira (não só pontos específicos como nas etapas anteriores).

---

## Etapa 8 -- "Enviar para o KLANGO" no Android: PWA instalável + Web Share Target
**Status:** Concluído e confirmado pelo Igor num Android real (17/10/2026): instalou o PWA via
Chrome, compartilhou um link do Instagram escolhendo "KLANGO.MP4" na folha de compartilhamento --
apareceu a confirmação ("Adicionado à fila"), a aba fechou sozinha (`window.close()` funcionou) e
voltou direto pro Instagram, sem precisar tocar em nada. `pytest -q` confirmado verde pelo GitHub
Actions.
**Branch:** `main` (direto).
**Data:** 17/10/2026

**Por que Android é mais simples que iPhone:** o app Atalhos do iOS não é um navegador logado no
site, por isso precisa de um token pessoal + arquivo local pra "lembrar" quem é o usuário (ver
Etapa 7). O PWA do Android roda no MESMO Chrome onde a pessoa já está logada em klango.site --
então a identidade vem da sessão normal (cookie), sem token nenhum. A instalação também não
depende de nenhum link gerado manualmente pelo Igor (como o link do iCloud do iPhone): é só
"Instalar app" no menu do próprio Chrome, sempre disponível.

**O que mudou:**
- `static/manifest.json` (novo) -- nome, ícones (`static/icon-192.png`/`icon-512.png`, gerados de
  `static/symbol-master.png` via Pillow), e `share_target` apontando pra `GET /share-target`.
- `static/sw.js` (novo) -- service worker mínimo, sem cache/offline nenhum, só pra satisfazer o
  requisito de instalabilidade do Chrome; servido em `/sw.js` (raiz, não `/static/sw.js`) pra ter
  escopo `/` inteiro.
- `routes/share_target.py` (novo) -- `GET /share-target`: autenticado por SESSÃO (cookie, via
  `get_optional_authenticated_user`), nunca token. Logado: adiciona à fila (reaproveita
  `add_to_queue`, mesma função de `POST /api/ext/share`) e devolve uma página HTML mínima de
  confirmação (sem Tailwind/fontes externas -- carrega instantânea), que tenta se fechar sozinha
  (`window.close()`) menos de 1s depois, voltando pro Instagram/TikTok sem mais nenhum toque
  (ajuste de 17/10/2026, pedido do Igor depois de testar num Android real -- ver próxima entrada).
  Sem sessão: redireciona pra `/?compartilhar_pendente=<link>` -- o frontend guarda em
  localStorage até o login completar.
- `routes/share.py` -- `POST /api/share/queue` (novo) -- adicionar à fila por cookie, usada pelo
  frontend depois do login pra consumir um link pendente.
- `static/index.html` -- `<link rel="manifest">`, registro do service worker, e os helpers
  `capturarAutoFluxoAtalhoDaUrl` (estendido com o modo `"so-rolar"` pro Android, que NUNCA gera
  token sozinho) e `capturarCompartilharPendenteDaUrl`/`consumirCompartilharPendente` (mesmo
  padrão do `"?ref=CODIGO"` do programa de indicação). Novo link "Como configurar no Android" ao
  lado do link do iPhone já existente.
- `static/atalho-android.html` (novo) -- instruções de instalação (3 passos: logar, instalar via
  menu do Chrome, testar compartilhando) + problemas comuns, servida em `/atalho-android`.

**Decisão de segurança (mesmo espírito do GET exempt de Origin já usado no resto do site):**
`GET /share-target` fica fora do `OriginProtectionMiddleware` porque GET já está em
`utils.origin.SAFE_METHODS` -- nenhuma entrada nova precisou em `ORIGIN_CHECK_EXEMPT_PREFIXES`. O
pior cenário de abuso (um link forjado que, clicado por alguém logado, adiciona uma URL à fila
dessa pessoa) só consegue adicionar um link de VÍDEO VÁLIDO (`add_to_queue` continua validando
plataforma) à fila própria dela, que ainda precisa ser confirmada manualmente antes de qualquer
download -- risco equivalente a "add to cart" por GET, não a uma ação destrutiva ou de leitura de
dado sensível.

### O que foi verificado
- [x] `pytest -q` = 0 failed pelo GitHub Actions (commit `d184419`, depois de corrigir a quebra do
      Union no retorno de `routes/share_target.py` -- ver histórico do commit).
- [x] Sintaxe (`ast.parse`) e JSON do manifest conferidos no sandbox (sem rede pra rodar o app de
      verdade).
- [x] Instalar via Chrome ("Instalar app") e compartilhar um link real do Instagram escolhendo
      "KLANGO.MP4" -- confirmado pelo Igor num Android real: caiu na fila, mostrou a confirmação e
      fechou sozinho, voltando pro Instagram sem precisar de mais nenhum toque.
- [x] Service worker em `/sw.js` registrou sem erro (consequência do PWA ter instalado e
      funcionado de ponta a ponta no teste acima).

### O que ainda NÃO foi testado explicitamente (não bloqueia o fechamento da etapa)
- O caso de NÃO estar logado no momento de compartilhar (deve guardar o link pendente, pedir
  login, e adicionar à fila automaticamente assim que o login completar) -- o Igor já estava
  logado em todos os testes feitos até aqui.

---

## Etapa 7 -- /atalho-ios: instalação rápida (3 passos) + manual recolhido + problemas comuns
**Status:** Concluído e mesclado na `main` (merge `53eb0f1`). As 3 pendências do Igor foram
resolvidas ao vivo em 17/10/2026: (1) Pergunta de Importação configurada no atalho real (campo do
token do passo "Pedir Texto"), (2) link do iCloud gerado e testado -- apagou o
`klango_token.txt` local, compartilhou um post (pediu o token, colou, caiu na fila), compartilhou
um segundo post logo depois (não pediu mais nada, token salvo localmente e reaproveitado), (3) link
confirmado: `https://www.icloud.com/shortcuts/e02b6e62f2654acf8b17765878d3f721`. Botão "Instale o
atalho" trocado de "Em breve" pro link real (commit separado, nesta mesma `main`).

**17/10/2026 (ajuste de UX pedido pelo Igor, mesmo dia):** o passo 1 ("Copie seu token") mandava
pra "/" simples -- a pessoa caía na home e precisava rolar até achar a seção "Enviar para o
KLANGO" e tocar em "Gerar token" por conta própria. Trocado pra "/?atalho=1": `index.html` agora
lê essa flag (`capturarAutoFluxoAtalhoDaUrl()`, mesmo padrão do "?ref=CODIGO" do programa de
indicação) e, assim que a sessão carrega (`carregarAtalho()`), já rola a tela até a seção e --
só se a pessoa AINDA não tiver um token -- gera um sozinho. Decisão de segurança: se a pessoa já
tem um token ativo, não gera um novo automaticamente (isso revogaria o antigo e quebraria
qualquer atalho já configurado) -- só rola até a seção e deixa a pessoa decidir, o
`confirm()` de `gerarTokenAtalho()` continua protegendo esse caso.
**Branch:** `main` (direto, troca de link pequena e já coberta pelo smoke test existente).
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
- Visual real no iPhone do Igor da tela de instalação rápida em si (o fluxo do atalho -- pedir
  token, salvar, reusar -- já foi testado ao vivo e confirmado; falta só conferir a própria página
  `/atalho-ios` renderizada no Safari do iPhone, nunca só via Playwright/mockup).

~~**Pergunta de Importação no atalho real:** configurar em Ajustes do atalho → "Configurar" →
"Adicionar Nova Pergunta" → selecionar o parâmetro da ação Texto do passo 1 (o token).~~ **Não foi
esse o caminho usado** -- em vez da "Pergunta de Importação" nativa do app Atalhos, a lógica de
"perguntar só uma vez" foi implementada dentro do próprio atalho (Contar Itens num arquivo local
`klango_token.txt` no iCloud Drive/Shortcuts → Se Contagem é 0 → Pedir Texto + Salvar; senão pula
direto pra leitura do arquivo salvo). Testado e confirmado funcionando ao vivo em 17/10/2026.

~~**Gerar e testar o link do iCloud**~~ Feito -- link confirmado:
`https://www.icloud.com/shortcuts/e02b6e62f2654acf8b17765878d3f721`.

### Checklist antes do merge na `main`
- [x] `pytest -q` = 0 failed (GitHub Actions) -- junto com a Etapa 6 (já mesclado, `53eb0f1`).
- [x] Botão "Instale o atalho" trocado de "Em breve" pro link real do iCloud.

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
