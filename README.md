# V9 ORACLE MAXX

by Algoritmo Secreto · @tieepo

O Google Trends de cada país (BR, EUA, México, Alemanha) descobre os temas. YouTube, Reddit e buscas validam.
O core (`collector/oracle_core_maxx.py`) decide veredito, janela, temperatura e score, sem IA na decisão.
A IA (opcional) só lê o resultado e escreve formato, ângulo e título.

Roda sozinho a cada 4 horas no GitHub Actions e publica o painel no GitHub Pages.

## Secrets (Settings, Secrets and variables, Actions)
| Nome | Obrigatório | Pra quê |
|---|---|---|
| `YOUTUBE_API_KEY` | sim | oferta, outliers e novatos |
| `ANTHROPIC_API_KEY` | não | formato, ângulo e título por tema |
| `SERPAPI_API_KEY` | não | série do Google Trends quando o pytrends é bloqueado (só Trends, nunca Reddit). Free: 250 buscas/mês |
| `REDDIT_CLIENT_ID` e `REDDIT_CLIENT_SECRET` | não | só se o Reddit aprovar o acesso (ver abaixo). Sem eles o Reddit fica desligado |

Reddit: a Responsible Builder Policy exige aprovação antes de qualquer acesso à API, e uso comercial exige aprovação por escrito. Sem credenciais aprovadas o Reddit fica desligado e o core trata como fonte não verificada, sem penalizar o tema.

## Arquivos
- `collector/oracle_core_maxx.py`: o motor, sem alteração
- `collector/run.py`: adapters (Google, YouTube, Reddit, buscas) + orquestração + rankings
- `config.yaml`: países e limites
- `state/`: histórico por tema (momentum e aceleração dependem disso)
- `docs/`: painel

## Como ler
- Hoje: tudo que foi analisado no dia
- Esquentando: temperatura quente do core, salto de score ou salto de busca no Google desde a leitura anterior
- Semana e Ano: score agregado por persistência e momentum (o ano enche com o tempo)
- Clique no tema: briefing completo
