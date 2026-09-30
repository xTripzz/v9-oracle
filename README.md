# V9 ORACLE

O oráculo do ecossistema Tieepo. Prevê o viral antes dele virar viral.

Todo dia às 7h (Brasília) ele varre YouTube, Google Trends e Reddit, pontua cada tema de 0 a 100 e publica o ranking diário e semanal num painel.

Na esteira: ORACLE detecta o tema, MINA valida a demanda, ESCADA ajusta o título, V9 COPY escreve.

## Setup (10 minutos, custo zero)

1. **Crie um repositório no GitHub** e suba esta pasta inteira.
2. **Chave do YouTube**: console.cloud.google.com → novo projeto → ative "YouTube Data API v3" → Credenciais → Criar chave de API.
3. **Chave da Anthropic (opcional)**: console.anthropic.com. Com ela, cada tema ganha ângulo e título prontos.
4. No repositório: **Settings → Secrets and variables → Actions → New repository secret**
   - `YOUTUBE_API_KEY`
   - `ANTHROPIC_API_KEY` (opcional)
5. **Settings → Pages** → Source: "Deploy from a branch" → branch `main`, pasta `/docs`.
6. **Actions → V9 ORACLE → Run workflow** pra rodar a primeira vez agora.

Painel fica em `https://SEU-USUARIO.github.io/NOME-DO-REPO/`.

## Ajustar nichos
Edite `config.yaml`: nome, idioma, região, palavras-semente e subreddits.

## Como o score funciona (0 a 100)
| Sinal | Peso | Teto |
|---|---|---|
| Velocidade (views/hora do melhor vídeo) | 30 | 2.000 v/h |
| Outlier (views ÷ inscritos, média top 3) | 25 | 10x |
| Alta no Google Trends (7 dias vs 7 anteriores) | 20 | +300% |
| Reddit (upvotes/hora em rising) | 10 | 200/h |
| Baixa saturação (poucos vídeos concorrentes) | 15 | 0 vídeos |

75+ = pré-pico (publicar em até 10 dias) · 50 a 74 = aquecendo · abaixo = monitorar.

**Semanal**: média dos últimos 7 dias + momentum (quanto subiu) + consistência (dias no radar). Tema que aparece todo dia subindo vai pro topo.

## Quota
Cada palavra-semente custa ~100 unidades do YouTube. Limite grátis: 10.000/dia, ou seja, até ~90 sementes. A config padrão usa ~11.
