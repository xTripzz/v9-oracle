"""V9 ORACLE v3: Google Trends primeiro.
Descobre o que cada país está buscando, valida no YouTube e no Reddit e ranqueia em hoje, semana e ano."""
import os, re, json, math, time, datetime as dt
from pathlib import Path
import xml.etree.ElementTree as ET
import requests, yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
HIST = DATA / "history"
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
YT_KEY = os.environ.get("YOUTUBE_API_KEY", "")
AI_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AGORA = dt.datetime.utcnow()
HOJE_D = (AGORA - dt.timedelta(hours=3)).date()  # dia no horário de Brasília
HOJE = HOJE_D.isoformat()
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ler(p, padrao):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return padrao


def salvar(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------- 1. GOOGLE TRENDS (fonte principal) ----------------
def num_trafego(s):
    s = (s or "").upper().replace("+", "").strip()
    m = re.match(r"([\d.,]+)\s*([KM]?)", s)
    if not m:
        return 0
    n, suf = m.groups()
    try:
        if suf:
            return int(float(n.replace(",", ".")) * (1000 if suf == "K" else 1_000_000))
        return int(re.sub(r"\D", "", n) or 0)
    except ValueError:
        return 0


def filho(el, nome):
    for c in el:
        if c.tag.split("}")[-1] == nome:
            return c
    return None


def coleta_trends(geo):
    urls = [f"https://trends.google.com/trending/rss?geo={geo}",
            f"https://trends.google.com/trends/trendingsearches/daily/rss?geo={geo}"]
    for url in urls:
        try:
            r = requests.get(url, headers=UA, timeout=30)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            itens = []
            for it in root.iter("item"):
                titulo = (it.findtext("title") or "").strip()
                if not titulo:
                    continue
                tr = filho(it, "approx_traffic")
                noticias = []
                for ni in it:
                    if ni.tag.split("}")[-1] != "news_item":
                        continue
                    t, u, f = filho(ni, "news_item_title"), filho(ni, "news_item_url"), filho(ni, "news_item_source")
                    if t is not None and u is not None:
                        noticias.append({"titulo": (t.text or "").strip(), "url": (u.text or "").strip(),
                                         "fonte": (f.text or "").strip() if f is not None else ""})
                itens.append({"termo": titulo, "trafego": num_trafego(tr.text if tr is not None else ""),
                              "noticias": noticias[:3]})
            if itens:
                return itens
        except Exception as e:
            print(f"   trends {geo} falhou em {url}: {e}")
    return []


# ---------------- 2. YOUTUBE (validação: vira vídeo? tá saturado?) ----------------
def yt(path, **p):
    p["key"] = YT_KEY
    r = requests.get(f"https://www.googleapis.com/youtube/v3/{path}", params=p, timeout=30)
    r.raise_for_status()
    return r.json()


def valida_youtube(termo, geo, idioma):
    depois = (AGORA - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = yt("search", part="id", q=termo, type="video", order="viewCount", publishedAfter=depois,
           maxResults=25, regionCode=geo, relevanceLanguage=idioma)
    ids = [i["id"]["videoId"] for i in r.get("items", [])]
    if not ids:
        return {"videos_7d": 0, "com_10k": 0, "top_vph": 0, "outlier": 0, "melhor": None}
    vids = yt("videos", part="snippet,statistics", id=",".join(ids)).get("items", [])
    cids = list({v["snippet"]["channelId"] for v in vids})
    subs = {c["id"]: int(c["statistics"].get("subscriberCount", 0) or 0)
            for c in yt("channels", part="statistics", id=",".join(cids[:50])).get("items", [])}
    lista = []
    for v in vids:
        pub = dt.datetime.strptime(v["snippet"]["publishedAt"], "%Y-%m-%dT%H:%M:%SZ")
        h = max((AGORA - pub).total_seconds() / 3600, 1)
        views = int(v["statistics"].get("viewCount", 0))
        lista.append({"id": v["id"], "titulo": v["snippet"]["title"], "canal": v["snippet"]["channelTitle"],
                      "views": views, "vph": views / h,
                      "outlier": views / max(subs.get(v["snippet"]["channelId"], 0), 100)})
    melhor = max(lista, key=lambda x: x["vph"])
    return {"videos_7d": len(lista), "com_10k": sum(1 for x in lista if x["views"] >= 10000),
            "top_vph": round(melhor["vph"]), "outlier": round(max(x["outlier"] for x in lista), 1),
            "melhor": {"titulo": melhor["titulo"], "canal": melhor["canal"], "views": melhor["views"],
                       "url": f"https://youtu.be/{melhor['id']}"}}


# ---------------- 3. REDDIT (conversa em volta do tema) ----------------
REDDIT_OK = True


def valida_reddit(termo):
    global REDDIT_OK
    if not REDDIT_OK:
        return None
    try:
        r = requests.get("https://www.reddit.com/search.json", params={"q": termo, "sort": "top", "t": "week",
                                                                        "limit": 10}, headers=UA, timeout=20)
        if r.status_code != 200:
            REDDIT_OK = False
            print("   reddit bloqueou, seguindo sem ele nesta rodada")
            return None
        posts = [p["data"] for p in r.json()["data"]["children"]]
        if not posts:
            return {"posts": 0, "score": 0, "comentarios": 0, "top": None}
        top = max(posts, key=lambda d: d["score"])
        return {"posts": len(posts), "score": sum(d["score"] for d in posts),
                "comentarios": sum(d["num_comments"] for d in posts),
                "top": {"titulo": top["title"], "url": "https://reddit.com" + top["permalink"],
                        "sub": top["subreddit"]}}
    except Exception as e:
        print("   reddit falhou:", e)
        return None


# ---------------- 4. CLAUDE (opcional: isso vira vídeo de YouTube?) ----------------
def analisa_ia(pais, entradas):
    if not entradas:
        return
    if not AI_KEY:
        print("   claude: ANTHROPIC_API_KEY não configurada")
        return
    linhas = []
    for e in entradas:
        nt = "; ".join(n["titulo"] for n in e.get("noticias", [])[:2])
        linhas.append(f'- "{e["termo"]}" (busca ~{e["trafego"]}+; notícias: {nt})')
    prompt = (f"Você é o V9 ORACLE, analista de tendências para YouTube no mercado {pais['nome']} "
              f"(idioma {pais['idioma']}). Para cada termo em alta no Google abaixo, decida se dá vídeo de "
              f"YouTube com vida útil (não só notícia de 1 dia). Responda SÓ JSON no formato "
              f'{{"<termo exato>": {{"categoria": "...", "vira_video": "sim|talvez|nao", '
              f'"motivo": "até 15 palavras", "angulo": "ângulo diferenciado", '
              f'"titulo": "título pronto no idioma do mercado"}}}}. Categoria em português. Sem travessão.\n'
              + "\n".join(linhas))
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=120, headers={
            "x-api-key": AI_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-5", "max_tokens": 4000,
                  "messages": [{"role": "user", "content": prompt}]})
        txt = r.json()["content"][0]["text"]
        mapa = json.loads(re.sub(r"```json|```", "", txt).strip())
        for e in entradas:
            if e["termo"] in mapa:
                e["ia"] = mapa[e["termo"]]
    except Exception as ex:
        print("   claude falhou:", ex)


# ---------------- 5. SCORE ----------------
def pontua(e, novo):
    t = e.get("trafego", 0)
    demanda = clamp((math.log10(max(t, 100)) - 2) / 4) * 30          # 100 buscas = 0, 1M+ = 30
    persist = clamp(e.get("aparicoes", 1) / 4) * 15                     # ficou em alta várias rodadas
    fresco = 10 if novo else 3                                          # apareceu hoje pela primeira vez
    y = e.get("yt")
    if y:
        brecha = (1 - clamp(y["com_10k"] / 15)) * 20                    # poucos vídeos fortes = brecha
        prova = clamp(y["top_vph"] / 2000) * 15                         # já tem gente assistindo no YT
    else:
        brecha, prova = 10, 0
    rd = e.get("rd") or {}
    conversa = clamp(rd.get("score", 0) / 2000) * 10
    s = demanda + persist + fresco + brecha + prova + conversa
    v = (e.get("ia") or {}).get("vira_video")
    if v == "nao":
        s *= 0.6
    elif v == "sim":
        s = min(100, s * 1.1)
    return round(s)


def fase(s):
    return "pre-pico" if s >= 75 else "aquecendo" if s >= 50 else "frio"


def janela(s):
    return "agora, 3 a 10 dias" if s >= 75 else "1 a 3 semanas" if s >= 50 else "monitorar"


def resumo(e, score):
    return {"termo": e["termo"], "score": score, "fase": fase(score), "janela": janela(score),
            "trafego": e.get("trafego", 0), "aparicoes": e.get("aparicoes", 1),
            "primeira_vez": e.get("primeira_vez"), "noticias": e.get("noticias", [])[:2],
            "yt": e.get("yt"), "rd": e.get("rd"), "ia": e.get("ia")}


# ---------------- 6. RANKINGS ----------------
def dias_de(geo, n):
    pasta = HIST / geo
    limite = (HOJE_D - dt.timedelta(days=n - 1)).isoformat()
    return [(f.stem, ler(f, {})) for f in sorted(pasta.glob("*.json")) if f.stem >= limite]


def agrega(geo, n, peso):
    acc = {}
    for dia, estado in dias_de(geo, n):
        for k, e in estado.get("termos", {}).items():
            a = acc.setdefault(k, {"scores": [], "dias": set(), "meses": set(), "pico": 0, "ultimo": e})
            a["scores"].append(e.get("score", 0))
            a["dias"].add(dia)
            a["meses"].add(dia[:7])
            a["pico"] = max(a["pico"], e.get("trafego", 0))
            a["ultimo"] = e
    out = []
    for k, a in acc.items():
        medio = sum(a["scores"]) / len(a["scores"])
        momentum = a["scores"][-1] - a["scores"][0] if len(a["scores"]) > 1 else 0
        s = peso(medio, len(a["dias"]), len(a["meses"]), momentum)
        r = resumo(a["ultimo"], s)
        r.update({"dias_em_alta": len(a["dias"]), "pico_trafego": a["pico"], "momentum": momentum,
                  "score_medio": round(medio)})
        out.append(r)
    return sorted(out, key=lambda x: -x["score"])[:CFG.get("top", 30)]


def peso_semana(medio, dias, meses, momentum):
    return min(100, round(medio * 0.6 + clamp(dias / 7) * 25 + clamp(momentum / 30) * 15))


def peso_ano(medio, dias, meses, momentum):
    return min(100, round(medio * 0.5 + clamp(dias / 30) * 30 + clamp(meses / 6) * 20))


# ---------------- MAIN ----------------
def main():
    quota = ler(DATA / "quota.json", {})
    if quota.get("data") != HOJE:
        quota = {"data": HOJE, "unidades": 0}
    indice = {"atualizado": AGORA.isoformat() + "Z", "paises": []}

    for pais in CFG["paises"]:
        geo = pais["geo"]
        print(f"==> {pais['nome']} ({geo})")
        arq = HIST / geo / f"{HOJE}.json"
        estado = ler(arq, {"data": HOJE, "rodadas": 0, "termos": {}})
        vistos_antes = set()
        for dia, st in dias_de(geo, 8):
            if dia != HOJE:
                vistos_antes |= set(st.get("termos", {}).keys())

        # 1. Trends
        itens = coleta_trends(geo)
        print(f"   Trends: {len(itens)} termos em alta")
        estado["rodadas"] += 1
        for it in itens:
            k = it["termo"].lower().strip()
            e = estado["termos"].setdefault(k, {"termo": it["termo"], "primeira_vez": AGORA.isoformat() + "Z",
                                                "aparicoes": 0, "trafego": 0, "noticias": []})
            e["aparicoes"] += 1
            e["trafego"] = max(e["trafego"], it["trafego"])
            urls = {n["url"] for n in e["noticias"]}
            e["noticias"] += [n for n in it["noticias"] if n["url"] not in urls]
            e["noticias"] = e["noticias"][:3]
            e["ultima_vez"] = AGORA.isoformat() + "Z"

        fila = sorted(estado["termos"].values(), key=lambda e: -e["trafego"])

        # 2. YouTube (respeitando a quota diária)
        if YT_KEY:
            feitos = 0
            for e in fila:
                if feitos >= CFG.get("yt_por_rodada", 6) or quota["unidades"] + 102 > CFG.get("quota_diaria", 9000):
                    break
                if "yt" in e:
                    continue
                try:
                    e["yt"] = valida_youtube(e["termo"], geo, pais["idioma"])
                    quota["unidades"] += 102
                    feitos += 1
                except Exception as ex:
                    print("   youtube falhou:", ex)
                    break
            print(f"   YouTube: {feitos} termos validados (quota do dia: {quota['unidades']})")

        # 3. Reddit
        feitos = 0
        for e in fila:
            if feitos >= CFG.get("reddit_por_rodada", 8):
                break
            if "rd" in e:
                continue
            e["rd"] = valida_reddit(e["termo"])
            feitos += 1
            time.sleep(2)

        # 4. Claude — roda independente do Reddit
        pendentes = [e for e in fila if "ia" not in e][:CFG.get("ia_por_rodada", 15)]
        if AI_KEY:
            analisa_ia(pais, pendentes)
        else:
            print("   claude: sem ANTHROPIC_API_KEY")

        # 5. Score
        for k, e in estado["termos"].items():
            e["score"] = pontua(e, k not in vistos_antes)
        salvar(arq, estado)

        # 6. Rankings
        ontem = ler(HIST / geo / f"{(HOJE_D - dt.timedelta(days=1)).isoformat()}.json", {}).get("termos", {})
        hoje = []
        for k, e in estado["termos"].items():
            r = resumo(e, e["score"])
            r["novo"] = k not in vistos_antes
            r["variacao"] = e["score"] - ontem[k]["score"] if k in ontem and "score" in ontem[k] else None
            hoje.append(r)
        hoje.sort(key=lambda x: -x["score"])
        serie = []
        for dia, st in dias_de(geo, 30):
            sc = [e.get("score", 0) for e in st.get("termos", {}).values()]
            serie.append({"dia": dia, "termos": len(sc), "pre_pico": sum(1 for s in sc if s >= 75),
                          "aquecendo": sum(1 for s in sc if 50 <= s < 75),
                          "media": round(sum(sc) / len(sc)) if sc else 0})
        saida = {"geo": geo, "nome": pais["nome"], "atualizado": AGORA.isoformat() + "Z", "serie": serie,
                 "dias_de_historico": len(dias_de(geo, 365)),
                 "hoje": hoje[:CFG.get("top", 30)],
                 "semana": agrega(geo, 7, peso_semana),
                 "ano": agrega(geo, 365, peso_ano)}
        salvar(DATA / f"{geo}.json", saida)
        indice["paises"].append({"geo": geo, "nome": pais["nome"], "bandeira": pais.get("bandeira", ""),
                                 "termos_hoje": len(hoje)})

    salvar(DATA / "quota.json", quota)
    salvar(DATA / "index.json", indice)
    print("ok")


if __name__ == "__main__":
    main()
