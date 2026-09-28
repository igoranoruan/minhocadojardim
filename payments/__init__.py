"""Camada de pagamentos: fronteira dedicada, no mesmo espírito de download/ (yt-dlp) e
processor/ (FFmpeg) -- um domínio externo/de negócio próprio, separado de services/ (que hoje
concentra regra de negócio interna: cota, entitlement, catálogo de planos).

Etapa 10.1 (esta etapa): só a FUNDAÇÃO interna -- criar um Payment "pending" correto, com preço
sempre vindo do catálogo (services.plans) e nunca do chamador. Nenhum código aqui fala com o
Mercado Pago, nenhuma rota HTTP existe ainda, nenhum entitlement é concedido automaticamente.

A integração real com o Mercado Pago (SDK, checkout, PIX, cartão, webhook, validação de
assinatura) é responsabilidade de etapas futuras (10.2+), e deve ser adicionada como uma camada
ABAIXO desta (ex.: um módulo de gateway que payments/service.py passaria a chamar), nunca
misturada com a criação do Payment interno que payments/service.py já resolve.
"""
