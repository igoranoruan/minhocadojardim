// Service worker mínimo do KLANGO.MP4 (17/10/2026, suporte a Android via PWA instalável).
//
// Existe só para satisfazer o requisito de "instalabilidade" do Chrome/Android (manifest +
// service worker registrado) e deixar o escopo em "/" (por isso é servido em /sw.js, raiz do
// site, nunca em /static/sw.js -- o escopo de um service worker é o diretório de onde ele é
// servido, e o Web Share Target precisa cobrir toda a origem, não só /static/). NÃO faz cache
// nem intercepta nada: todo fetch passa direto pra rede, sem comportamento "offline" nenhum --
// decisão deliberada pra não arriscar servir uma versão velha do site/API por engano. Se um dia
// quisermos cache offline de verdade, isso muda aqui, nunca no resto do app.
self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", () => {
  // Deliberadamente vazio -- sem respondWith(), o navegador trata a requisição normalmente.
});
