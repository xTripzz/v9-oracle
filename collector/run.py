"""V9 ORACLE MAXX: orquestrador.

Google Trends descobre os temas de cada pais.
YouTube, Reddit e buscas validam (adapters abaixo).
O core (oracle_core_maxx.py) decide: veredito, janela, temperatura, score. Sem IA na decisao.
A IA (opcional) so le o resultado do core e escreve orientacao de formato, angulo e titulo.
"""
import os, re, sys, json, time, hashlib, dataclasses
import datetime as dt
from pathlib import Path
import xml.etree.ElementTree as ET
import requests, yaml

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import oracle_core_maxx as core
from oracle_core_maxx import (TopicHistory, TopicSnapshot, Temperature, Verdict, Video, RedditPost,
                              analyze_topic, snapshot_from_signal, signal_to_json_ready)

ROOT = Path(os.environ.get("ORACLE_ROOT", BASE.parent))
DATA = ROOT / "docs" / "data"
STATE = ROOT / "state"
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
# Trends vem de leituras do Google a cada rodada no comeco, entao aceita series curtas.
CORE_CFG = dataclasses.replace(core.CFG, trends_min_points=1)

UTC = dt.timezone.utc
NOW = (dt.datetime.fromisoformat(os.environ["ORACLE_NOW"]) if os.environ.get("ORACLE_NOW")
       else dt.datetime.now(UTC))
NOW_ISO = NOW.isoformat(timespec="seconds")
HOJE_D = (NOW - dt.timedelta(hours=3)).date()          # dia no horario de Brasilia
HOJE = HOJE_D.isoformat()

YT_KEY = os.environ.get("YOUTUBE_API_KEY", "")
AI_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")


def provedor_ia():
    """auto: Gemini se tiver GEMINI_API_KEY, senao Claude se tiver ANTHROPIC_API_KEY."""
    p = CFG.get("ia_provedor", "auto")
    if p == "gemini":
        return "gemini" if GEMINI_KEY else None
    if p == "claude":
        return "claude" if AI_KEY else None
    return "gemini" if GEMINI_KEY else ("claude" if AI_KEY else None)


IA_PROV = provedor_ia()
AI_ON = IA_PROV is not None
UA_NAV = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
UA_REDDIT = "v9-oracle/3.0 (by algoritmo-secreto)"
YT_MIN_S = int(CFG.get("yt_min_minutos", 8) * 60)            # so videos longos (nada de Shorts)
YT_DURACOES = CFG.get("yt_duracoes", ["medium", "long"])     # medium = 4 a 20 min, long = mais de 20 min
YT_FILTRO = f"d{YT_MIN_S}"                                   # marca do cache: mudar o filtro invalida o cache
CUSTO_BUSCA = 100 * len(YT_DURACOES) + 4
QUENTES = {"aquecendo", "esquentando", "acelerando", "explosivo"}
RANK_TEMP = {"explosivo": 5, "acelerando": 4, "esquentando": 3, "aquecendo": 2}
ACEITOS = set(CFG.get("encaixe_aceitos", ["sim"]))     # encaixe dark aceito na analise
FORMATOS = {"longo", "serie", "compilacao"}      # canais so com video longo: Shorts fora

def reddit_ativo():
    return bool(os.environ.get("REDDIT_CLIENT_ID") and os.environ.get("REDDIT_CLIENT_SECRET"))


def serpapi_ativo():
    return bool(os.environ.get("SERPAPI_API_KEY"))


TR_SRC = set()   # de onde veio a serie do Trends nesta rodada (pytrends, serpapi)
STATUS = {"google": "ok", "youtube": "ok" if YT_KEY else "sem_chave",
          "reddit": "nao_testado" if reddit_ativo() else "nao_configurado",
          "trends": "so_rss", "ia": "ok" if AI_ON else "sem_chave",
          "serpapi": "nao_testado" if serpapi_ativo() else "nao_configurado",
          "filtro": "ok" if AI_ON else "sem_chave"}


# ============================================================
# UTILIDADES
# ============================================================
def ler(p, padrao):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return padrao


def salvar(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def parse_iso(s):
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def idade_h(iso):
    return (NOW - parse_iso(iso)).total_seconds() / 3600


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def chave(termo):
    """Mesma normalizacao do core, pra o termo casar com titulos e posts."""
    return " ".join(core._filtered(termo) or core._tokens(termo))


# ============================================================
# ADAPTER 1: GOOGLE TRENDS (descoberta)
# ============================================================
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


def google_rss(geo):
    for url in (f"https://trends.google.com/trending/rss?geo={geo}",
                f"https://trends.google.com/trends/trendingsearches/daily/rss?geo={geo}"):
        try:
            r = requests.get(url, headers={"User-Agent": UA_NAV}, timeout=30)
            r.raise_for_status()
            itens = []
            for it in ET.fromstring(r.content).iter("item"):
                titulo = (it.findtext("title") or "").strip()
                if not titulo:
                    continue
                tr = filho(it, "approx_traffic")
                noticias = []
                for ni in it:
                    if ni.tag.split("}")[-1] != "news_item":
                        continue
                    t, u, f = (filho(ni, "news_item_title"), filho(ni, "news_item_url"),
                               filho(ni, "news_item_source"))
                    if t is not None and u is not None:
                        noticias.append({"titulo": (t.text or "").strip(), "url": (u.text or "").strip(),
                                         "fonte": (f.text or "").strip() if f is not None else ""})
                itens.append({"termo": titulo, "trafego": num_trafego(tr.text if tr is not None else ""),
                              "noticias": noticias[:3]})
            if itens:
                return itens
        except Exception as e:
            print(f"   google {geo} falhou em {url}: {str(e)[:100]}")
    return []


# ============================================================
# ADAPTER 2: YOUTUBE (oferta, outliers, novatos)
# ============================================================
def yt_get(path, custo, quota, geo, **p):
    p["key"] = YT_KEY
    quota[geo] = quota.get(geo, 0) + custo
    r = requests.get(f"https://www.googleapis.com/youtube/v3/{path}", params=p, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"youtube {r.status_code}: {r.text[:160]}")
    return r.json()


def dur_s(iso):
    """Duracao ISO 8601 (PT8M30S) em segundos. Live e estreia ('P0D') dao 0 e saem do filtro."""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, sec = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + sec


def yt_buscar(termo, geo, idioma, quota):
    """So videos longos: a busca ja filtra por faixa de duracao e depois corta abaixo do minimo."""
    depois = (NOW - dt.timedelta(days=CFG.get("yt_dias", 90))).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = []
    for faixa in YT_DURACOES:
        r = yt_get("search", 100, quota, geo, part="id", q=termo, type="video", order="viewCount",
                   videoDuration=faixa, publishedAfter=depois, maxResults=50, regionCode=geo,
                   relevanceLanguage=idioma)
        ids += [i["id"]["videoId"] for i in r.get("items", []) if i.get("id", {}).get("videoId")]
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []
    vids = []
    for i in range(0, len(ids), 50):
        vids += yt_get("videos", 1, quota, geo, part="snippet,statistics,contentDetails",
                       id=",".join(ids[i:i + 50])).get("items", [])
    vids = [v for v in vids if dur_s(v.get("contentDetails", {}).get("duration")) >= YT_MIN_S]
    if not vids:
        return []
    cids = list({v["snippet"]["channelId"] for v in vids})
    subs = {}
    for i in range(0, len(cids), 50):
        for c in yt_get("channels", 1, quota, geo, part="statistics",
                        id=",".join(cids[i:i + 50])).get("items", []):
            st = c.get("statistics", {})
            if not st.get("hiddenSubscriberCount"):
                subs[c["id"]] = int(st.get("subscriberCount", 0) or 0)
    out = []
    for v in vids:
        cid = v["snippet"]["channelId"]
        if cid not in subs:                      # inscritos ocultos: sem como medir outlier
            continue
        out.append({"id": v["id"], "t": v["snippet"]["title"], "c": cid,
                    "ch": v["snippet"].get("channelTitle", ""),
                    "v": int(v.get("statistics", {}).get("viewCount", 0) or 0),
                    "p": v["snippet"]["publishedAt"], "s": max(subs[cid], 100),
                    "d": dur_s(v["contentDetails"]["duration"])})
    return sorted(out, key=lambda x: -x["v"])[:50]


def para_videos(lst):
    return [Video(video_id=x["id"], title=x["t"], channel_id=x["c"], views=x["v"],
                  published_at=parse_iso(x["p"]), channel_subs=x["s"]) for x in lst]


def melhor_video(lst, k):
    cand = [x for x in lst if core._matches(k, core._tokenset(x["t"]))]
    if not cand:
        return None
    x = max(cand, key=lambda x: x["v"])
    return {"titulo": x["t"], "url": f"https://youtu.be/{x['id']}", "canal": x["ch"],
            "views": x["v"], "inscritos": x["s"]}


NOVAS = {}   # buscas novas no YouTube por pais nesta rodada (espalha a quota ao longo do dia)
YT_OFF = {"off": False}   # vira True quando o Google responde que a quota acabou


def videos_do_topico(k, termo, geo, idioma, st, quota):
    c = st["yt"].get(k)
    if c and c.get("f") != YT_FILTRO:        # cache feito com outro filtro (ex.: antes de tirar Shorts)
        c = None
    if c and idade_h(c["ts"]) < CFG.get("yt_ttl_horas", 14):
        return c["v"], "cache"
    limite = CFG.get("quota_diaria", 9800) // max(len(CFG["paises"]), 1)
    sem_quota = quota.get(geo, 0) + CUSTO_BUSCA > limite
    na_fila = NOVAS.get(geo, 0) >= CFG.get("yt_novas_por_rodada", 3)
    if YT_KEY and not YT_OFF["off"] and not sem_quota and not na_fila:
        NOVAS[geo] = NOVAS.get(geo, 0) + 1
        try:
            lst = yt_buscar(termo, geo, idioma, quota)
            st["yt"][k] = {"ts": NOW_ISO, "v": lst, "f": YT_FILTRO}
            return lst, "ok"
        except Exception as e:
            print(f"   youtube falhou ({termo}): {str(e)[:120]}")
            if "quota" in str(e).lower():          # quota real do Google acabou: nao insiste nesta rodada
                YT_OFF["off"] = True
                STATUS["youtube"] = "quota"
            else:
                STATUS["youtube"] = "erro"
    elif YT_KEY and sem_quota:
        STATUS["youtube"] = "quota"
    if c:
        return c["v"], "velho"
    return None, "indisponivel"


# ============================================================
# ADAPTER 3: REDDIT (perguntas reais, brechas, velocidade)
# ============================================================
REDDIT = {"token": None, "exp": 0.0, "bloqueado": False}


def reddit_token():
    cid, sec = os.environ.get("REDDIT_CLIENT_ID", ""), os.environ.get("REDDIT_CLIENT_SECRET", "")
    if REDDIT["token"] and time.time() < REDDIT["exp"]:
        return REDDIT["token"]
    r = requests.post("https://www.reddit.com/api/v1/access_token", auth=(cid, sec),
                      data={"grant_type": "client_credentials"}, headers={"User-Agent": UA_REDDIT}, timeout=20)
    r.raise_for_status()
    j = r.json()
    REDDIT["token"] = j["access_token"]
    REDDIT["exp"] = time.time() + int(j.get("expires_in", 3600)) - 60
    return REDDIT["token"]


def reddit_buscar(termo, k):
    """Reddit so entra com credenciais aprovadas pelo Reddit (Responsible Builder Policy).
    Sem elas fica desligado: o core trata como fonte nao verificada e nao penaliza.
    Devolve (posts, top). posts=None quando a fonte esta indisponivel."""
    if REDDIT["bloqueado"] or not reddit_ativo():
        return None, None
    try:
        tok = reddit_token()
        r = requests.get("https://oauth.reddit.com/search",
                         params={"q": termo, "sort": "relevance", "t": "week", "limit": 50, "type": "link"},
                         headers={"Authorization": f"bearer {tok}", "User-Agent": UA_REDDIT}, timeout=20)
        if r.status_code != 200:
            REDDIT["bloqueado"] = True
            STATUS["reddit"] = "bloqueado"
            print(f"   reddit {r.status_code}: seguindo sem Reddit nesta rodada")
            return None, None
        STATUS["reddit"] = "oauth"
        posts, top = [], None
        for ch in r.json()["data"]["children"]:
            d = ch["data"]
            p = RedditPost(title=d.get("title", ""), subreddit=d.get("subreddit", ""),
                           num_comments=int(d.get("num_comments", 0)),
                           created_at=dt.datetime.fromtimestamp(d["created_utc"], UTC),
                           body=d.get("selftext", "") or "", score=int(d.get("score", 0)))
            posts.append(p)
            if core._matches(k, core._tokenset(p.title + " " + p.body)):
                if top is None or p.num_comments > top["comentarios"]:
                    top = {"titulo": p.title, "url": "https://reddit.com" + d.get("permalink", ""),
                           "sub": p.subreddit, "score": p.score, "comentarios": p.num_comments}
        time.sleep(1.0)
        return posts, top
    except Exception as e:
        print(f"   reddit falhou: {str(e)[:100]}")
        REDDIT["bloqueado"] = True
        STATUS["reddit"] = "bloqueado"
        return None, None


# ============================================================
# ADAPTER 4: SERIE DO TRENDS + BUSCAS RELACIONADAS
# ============================================================
PYT = {"bloqueado": False}


def trends_serie(termo, geo):
    """Serie horaria de 7 dias do Google Trends (pytrends). None quando bloqueia."""
    if PYT["bloqueado"]:
        return None, []
    try:
        from pytrends.request import TrendReq
    except Exception:
        PYT["bloqueado"] = True
        return None, []
    try:
        py = TrendReq(hl="en-US", tz=0, timeout=(10, 25))
        py.build_payload([termo], timeframe="now 7-d", geo=geo)
        df = py.interest_over_time()
        serie = None
        if df is not None and not df.empty and termo in df:
            serie = [float(x) for x in df[termo].tolist()]
        rising = []
        try:
            rq = py.related_queries().get(termo, {}).get("rising")
            if rq is not None:
                rising = [str(x) for x in rq["query"].tolist()[:10]]
        except Exception:
            pass
        time.sleep(2)
        return serie, rising
    except Exception as e:
        print(f"   pytrends bloqueado: {str(e)[:90]}")
        PYT["bloqueado"] = True
        return None, []


SERP = {"estado": {}, "run": 0, "geo": {}, "bloqueado": False}


def serpapi_pode(geo):
    """Fallback pago do pytrends. Respeita limite mensal, ritmo diario e teto por rodada."""
    if not serpapi_ativo() or SERP["bloqueado"]:
        return False
    e = SERP["estado"]
    if e.get("mes") != HOJE[:7]:
        e.update({"mes": HOJE[:7], "n": 0})
    if e.get("dia") != HOJE:
        e.update({"dia": HOJE, "n_dia": 0})
    lim_m = CFG.get("serpapi_limite_mensal", 250)
    lim_d = max(1, lim_m // 30)
    por_rodada = min(CFG.get("serpapi_por_rodada", 4), max(1, lim_d // 6))
    if e["n"] >= lim_m or e["n_dia"] >= lim_d:
        STATUS["serpapi"] = "limite"
        return False
    return SERP["run"] < por_rodada and SERP["geo"].get(geo, 0) < CFG.get("serpapi_por_pais", 1)


def serpapi_serie(termo, geo, idioma):
    """Serie horaria de 7 dias via SerpApi (engine google_trends, TIMESERIES). None se falhar."""
    SERP["run"] += 1
    SERP["geo"][geo] = SERP["geo"].get(geo, 0) + 1
    try:
        r = requests.get("https://serpapi.com/search.json", timeout=90, params={
            "engine": "google_trends", "q": termo, "data_type": "TIMESERIES", "geo": geo,
            "date": "now 7-d", "hl": idioma, "tz": "0", "api_key": os.environ["SERPAPI_API_KEY"]})
        try:
            j = r.json()
        except Exception:
            j = {}
        if r.status_code != 200 or j.get("error"):
            print(f"   serpapi {r.status_code}: {str(j.get('error', ''))[:120]}")
            STATUS["serpapi"] = "erro"
            if r.status_code in (401, 403):
                SERP["bloqueado"] = True
            return None
        serie = []
        for pt in j.get("interest_over_time", {}).get("timeline_data", []):
            vals = pt.get("values") or []
            if not vals:
                continue
            v = vals[0].get("extracted_value")
            if v is None:
                v = int(re.sub(r"\D", "", str(vals[0].get("value", "0"))) or 0)
            serie.append(float(v))
        if not serie:
            return None
        SERP["estado"]["n"] = SERP["estado"].get("n", 0) + 1
        SERP["estado"]["n_dia"] = SERP["estado"].get("n_dia", 0) + 1
        STATUS["serpapi"] = "ok"
        TR_SRC.add("serpapi")
        return serie
    except Exception as e:
        print(f"   serpapi falhou: {str(e)[:100]}")
        STATUS["serpapi"] = "erro"
        return None


def sugestoes(termo, idioma):
    """Autocomplete do YouTube: o que as pessoas realmente digitam na busca."""
    try:
        r = requests.get("https://suggestqueries.google.com/complete/search",
                         params={"client": "firefox", "ds": "yt", "q": termo, "hl": idioma},
                         headers={"User-Agent": UA_NAV}, timeout=10)
        return [s for s in r.json()[1] if s.lower().strip() != termo.lower().strip()][:10]
    except Exception:
        return []


def co_trending(k, todos):
    """Outros termos em alta no mesmo pais que dividem palavra com este tema."""
    toks = {t for t in k.split() if len(t) >= 4}
    out = []
    for o in todos:
        ko = chave(o)
        if ko and ko != k and toks & set(ko.split()):
            out.append(o)
    return out[:6]


# ============================================================
# ESTADO (historico por tema, caches)
# ============================================================
SECOES = ("hist", "traf", "yt", "trs", "sug", "meta", "visto", "rdv", "ia", "fit", "desc")


def estado_pais(geo):
    st = ler(STATE / f"{geo}.json", {})
    for s in SECOES:
        st.setdefault(s, {})
    if st.get("dia", {}).get("data") != HOJE:
        st["dia"] = {"data": HOJE, "itens": {}}
    return st


def poda(st):
    for k, ts in list(st["visto"].items()):
        if idade_h(ts) > 24 * 8:
            for s in SECOES:
                st[s].pop(k, None)
    for s in ("yt", "trs", "sug"):
        for k, c in list(st[s].items()):
            if idade_h(c.get("ts", NOW_ISO)) > 48:
                del st[s][k]
    for k, c in list(st["ia"].items()):
        if idade_h(c.get("ts", NOW_ISO)) > 72:
            del st["ia"][k]
    for k, c in list(st["fit"].items()):
        if idade_h(c.get("ts", NOW_ISO)) > 24 * 8:
            del st["fit"][k]
    for k, c in list(st["desc"].items()):
        if idade_h(c.get("ts", NOW_ISO)) > 48:
            del st["desc"][k]
    for k in list(st["dia"]["itens"]):
        if k not in st["visto"]:
            del st["dia"]["itens"][k]


def carrega_hist(lst):
    h = TopicHistory()
    for s in lst:
        try:
            s = dict(s)
            s["timestamp"] = parse_iso(s["timestamp"])
            s["temperature"] = Temperature(s["temperature"])
            h.snapshots.append(TopicSnapshot(**s))
        except Exception:
            continue
    return h


def serializa_snap(s):
    d = dataclasses.asdict(s)
    d["timestamp"] = s.timestamp.isoformat(timespec="seconds")
    d["temperature"] = s.temperature.value
    return d


# ============================================================
# ANALISE POR PAIS
# ============================================================
class IAErro(Exception):
    pass


GEM = {"ultimo": 0.0, "modelo": None}
SEGURANCA = [{"category": c, "threshold": "BLOCK_ONLY_HIGH"} for c in (
    "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")]


def _claude(prompt, max_tokens):
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=120, headers={
        "x-api-key": AI_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": CFG.get("modelo_ia", "claude-sonnet-5-5"), "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": prompt}]})
    if r.status_code != 200:
        raise IAErro(f"claude {r.status_code}: {r.text[:200]}")
    j = r.json()
    txt = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
    return txt, j.get("stop_reason") == "max_tokens"


NIVEIS = ["thinking0", "thinkingmin", "sem_thinking", "sem_seguranca", "basico"]


def _gem_config(nivel, max_tokens):
    """Do pedido mais completo ao mais simples. Se o modelo recusar (400), desce um degrau."""
    gc = {"maxOutputTokens": max_tokens}
    if nivel != "basico":
        gc.update({"responseMimeType": "application/json", "temperature": 0.3})
    if nivel == "thinking0":
        gc["thinkingConfig"] = {"thinkingBudget": 0}
    elif nivel == "thinkingmin":
        gc["thinkingConfig"] = {"thinkingLevel": "minimal"}
    corpo_extra = {"safetySettings": SEGURANCA} if nivel in ("thinking0", "thinkingmin", "sem_thinking") else {}
    return gc, corpo_extra


def _gemini(prompt, max_tokens):
    """Gemini via REST (generateContent). Se o modelo recusar o formato (400) ou nao existir (404),
    desce a escada de formatos e depois a lista de modelos. Guarda o que funcionou."""
    modelos = [m for m in dict.fromkeys([GEM["modelo"], CFG.get("modelo_gemini", "gemini-3.5-flash-lite"),
                                         "gemini-3.6-flash", "gemini-2.5-flash-lite"]) if m]
    ultimo = "sem resposta"
    for modelo in modelos:
        ini = GEM.get("nivel", 0) if GEM["modelo"] == modelo else 0
        for idx in range(ini, len(NIVEIS)):
            gc, extra = _gem_config(NIVEIS[idx], max_tokens)
            r = None
            for _ in range(3):
                espera = 5.0 - (time.time() - GEM["ultimo"])    # respeita o limite por minuto do plano gratis
                if espera > 0:
                    time.sleep(espera)
                GEM["ultimo"] = time.time()
                r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent",
                                  timeout=120, headers={"x-goog-api-key": GEMINI_KEY, "content-type": "application/json"},
                                  json=dict({"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gc}, **extra))
                if r.status_code != 429:
                    break
                ultimo = f"gemini 429: {r.text[:120]}"
                time.sleep(20)                                   # limite de uso: espera e tenta de novo
            if r.status_code == 429:
                raise IAErro(ultimo)
            if r.status_code == 404:                             # modelo nao existe: proximo da lista
                ultimo = f"gemini 404 no modelo {modelo}"
                break
            if r.status_code == 400:                             # formato recusado: formato mais simples
                ultimo = f"gemini 400 ({modelo}, {NIVEIS[idx]}): {r.text[:160]}"
                print(f"   gemini recusou o formato {NIVEIS[idx]}, tentando um mais simples")
                continue
            if r.status_code != 200:
                raise IAErro(f"gemini {r.status_code}: {r.text[:200]}")
            j = r.json()
            cands = j.get("candidates") or []
            if not cands:
                raise IAErro("gemini sem resposta: " + str(j.get("promptFeedback", ""))[:150])
            partes = (cands[0].get("content") or {}).get("parts") or []
            txt = "".join(x.get("text", "") for x in partes if not x.get("thought"))
            if not txt:
                raise IAErro("gemini devolveu vazio (finishReason " + str(cands[0].get("finishReason")) + ")")
            if GEM["modelo"] != modelo or GEM.get("nivel") != idx:
                print(f"   gemini: usando {modelo} no formato {NIVEIS[idx]}")
            GEM["modelo"], GEM["nivel"] = modelo, idx
            return txt, cands[0].get("finishReason") == "MAX_TOKENS"
    raise IAErro(ultimo)


def chama_ia(prompt, max_tokens=4000):
    """Chama o provedor configurado. Devolve (texto, cortou). Levanta IAErro com o motivo."""
    return _gemini(prompt, max_tokens) if IA_PROV == "gemini" else _claude(prompt, max_tokens)


def extrai_json(txt):
    """Le o JSON da resposta da IA mesmo com texto em volta ou resposta cortada no meio."""
    txt = re.sub(r"```json|```", "", txt).strip()
    i = txt.find("{")
    if i < 0:
        raise ValueError("resposta sem JSON: " + txt[:100])
    txt = txt[i:]
    try:
        return json.loads(txt)
    except Exception:
        pass
    j = txt.rfind("}")
    try:
        return json.loads(txt[:j + 1])
    except Exception:
        pass
    k = txt.rfind("},")          # cortou no meio de um item: aproveita os itens completos
    if k > 0:
        return json.loads(txt[:k + 1] + "}")
    raise ValueError("JSON invalido: " + txt[:100])


def classifica_dark(pais, itens, st):
    """Filtro de encaixe. O Google Trends diario e cheio de noticia, esporte e celebridade.
    A IA decide o que serve pra canal dark (sem rosto, narrado, video longo, assunto perene).
    A decisao fica em cache por 72h. Sem ANTHROPIC_API_KEY o filtro fica desligado."""
    if not AI_ON:
        STATUS["filtro"] = "sem_chave"
        return
    vistos, novos = set(), []
    for it in itens:
        k = chave(it["termo"])
        if not k or k in vistos:
            continue
        vistos.add(k)
        c = st["fit"].get(k)
        if not c or idade_h(c["ts"]) > 72:
            novos.append((k, it))
    if not novos:
        return
    lote = novos[:25]
    nichos = "; ".join(CFG.get("nichos_dark", []))
    entrada = [{"termo": it["termo"], "noticia": (it["noticias"][0]["titulo"] if it.get("noticias") else ""),
                "buscas": it.get("trafego", 0)} for _, it in lote]
    prompt = (
        f"Voce filtra temas do Google Trends ({pais['nome']}, idioma {pais['idioma']}) para canais dark do YouTube. "
        "Canal dark e sem rosto, narrado, em video longo (8 minutos ou mais), com assunto perene que continua "
        "rendendo por semanas ou meses. O tema precisa se encaixar como assunto perene em algum dos nichos "
        f"aceitos: {nichos}. "
        "NAO serve: noticia do dia, resultado ou jogo de esporte, politica e eleicao, justica e tribunais do dia, "
        "fofoca e celebridade, cotacao e economia do dia, loteria, clima e previsao do tempo, lancamento de produto, "
        "reality e programa de TV, data comemorativa. "
        "Para cada termo responda encaixe: sim (o proprio assunto e tema perene de canal dark), "
        "talvez (nasceu de noticia mas tem angulo documental forte), nao (e so noticia, esporte, politica, "
        "celebridade ou similar). Exemplos: onca-pintada = sim (animais); STF = nao (politica); "
        "Argentina x Bolivia = nao (esporte); tubarao ataca banhista = sim (animais perigosos). "
        "Responda SO JSON no formato "
        '{"<termo exato>": {"encaixe": "sim|talvez|nao", '
        '"nicho": "nome do nicho aceito, ou noticia|esporte|politica|celebridade|financas|clima|entretenimento|outro", '
        '"motivo": "ate 12 palavras em portugues"}}. Nao use travessao.\n' + json.dumps(entrada, ensure_ascii=False))
    try:
        txt, cortou = chama_ia(prompt, 4000)
        if cortou:
            print("   filtro dark: resposta cortada, aproveitando os itens completos")
        mapa = extrai_json(txt)
        mapa = {str(a).lower().strip(): b for a, b in mapa.items()}
        cont = {"sim": 0, "talvez": 0, "nao": 0}
        for k, it in lote:
            v = mapa.get(it["termo"].lower().strip())
            if not isinstance(v, dict) or v.get("encaixe") not in cont:
                continue
            st["fit"][k] = {"e": v["encaixe"], "n": str(v.get("nicho", ""))[:40],
                            "m": str(v.get("motivo", ""))[:90], "ts": NOW_ISO}
            cont[v["encaixe"]] += 1
        print(f"   filtro dark: {cont['sim']} sim, {cont['talvez']} talvez, {cont['nao']} nao")
        if sum(cont.values()) == 0:
            print("   filtro dark: a resposta nao trouxe nenhum tema classificavel")
            STATUS["filtro"] = "erro"
    except Exception as e:
        print(f"   filtro dark falhou ({IA_PROV}): {str(e)[:200]}")
        STATUS["filtro"] = "erro"


def analisa_pais(pais, quota):
    geo, idioma, nome = pais["geo"], pais["idioma"], pais["nome"]
    print(f"==> {nome} ({geo})")
    st = estado_pais(geo)

    # 1. Google Trends descobre
    rss = google_rss(geo)
    print(f"   Google: {len(rss)} termos em alta")
    if not rss:
        STATUS["google"] = "parcial"
    termos_rss = [it["termo"] for it in rss]

    # 1b. Filtro de encaixe: so entra o que serve pra canal dark (antes de gastar YouTube e Trends)
    legado = [{"termo": st["meta"][k]["termo"], "noticias": st["meta"][k].get("noticias", []),
               "trafego": st["meta"][k].get("atual", 0)} for k in st["visto"] if k in st["meta"]]
    classifica_dark(pais, rss + legado, st)

    def aceito(k):
        return (not AI_ON) or st["fit"].get(k, {}).get("e") in ACEITOS

    if AI_ON:
        for k, c in st["fit"].items():
            if c["e"] not in ACEITOS:
                st["dia"]["itens"].pop(k, None)          # tira do painel o que entrou antes do filtro
        for it in rss:
            k = chave(it["termo"])
            c = st["fit"].get(k) if k else None
            if c and c["e"] not in ACEITOS:
                st["desc"][k] = {"termo": it["termo"], "nicho": c["n"], "motivo": c["m"], "ts": NOW_ISO}
            elif c:
                st["desc"].pop(k, None)
    rss_ok = [it for it in rss if chave(it["termo"]) and aceito(chave(it["termo"]))]
    print(f"   Filtro dark: {len(rss_ok)} de {len(rss)} temas servem pra canal dark")

    for it in rss_ok:
        k = chave(it["termo"])
        m = st["meta"].setdefault(k, {"termo": it["termo"], "primeira": NOW_ISO, "leituras": 0,
                                      "pico": 0, "noticias": []})
        m.update({"termo": it["termo"], "ultima": NOW_ISO, "atual": it["trafego"]})
        m["leituras"] += 1
        m["pico"] = max(m["pico"], it["trafego"])
        urls = {n["url"] for n in m["noticias"]}
        m["noticias"] = (m["noticias"] + [n for n in it["noticias"] if n["url"] not in urls])[:3]
        tr = st["traf"].setdefault(k, [])
        tr.append([NOW_ISO, float(max(it["trafego"], 100))])
        del tr[:-48]
        st["visto"][k] = NOW_ISO

    atuais = {}
    for it in rss_ok:
        k = chave(it["termo"])
        atuais[k] = max(atuais.get(k, 0), it["trafego"])
    cand = [k for k, _ in sorted(atuais.items(), key=lambda x: -x[1])]
    recentes = sorted([k for k, ts in st["visto"].items() if k not in cand and idade_h(ts) <= 24 and aceito(k)],
                      key=lambda k: -st["meta"].get(k, {}).get("pico", 0))
    cand = (cand + recentes)[:CFG.get("topicos_por_pais", 10)]
    if not cand and not rss:
        return None

    n_trends = 0
    for k in cand:
        m = st["meta"][k]
        termo = m["termo"]

        # 2. YouTube
        lst, yt_status = videos_do_topico(k, termo, geo, idioma, st, quota)
        videos = para_videos(lst) if lst is not None else []
        prova = melhor_video(lst, k) if lst else None

        # 3. Reddit
        posts, rd_top = reddit_buscar(termo, k)
        prev_vel = st["rdv"].get(k)
        if posts is not None:
            st["rdv"][k] = core.evaluate_reddit(k, posts, NOW).comment_velocity

        # 4. Serie do Trends: pytrends (gratis) -> SerpApi (so se o pytrends falhar) -> leituras do Google
        serie, rising, fonte_trends = None, [], "pytrends"
        c = st["trs"].get(k)
        if c and idade_h(c["ts"]) < 12:
            serie, rising, fonte_trends = c["s"], c["r"], c.get("src", "pytrends")
            TR_SRC.add(fonte_trends)
        elif n_trends < CFG.get("trends_por_rodada", 5):
            n_trends += 1
            serie, rising = trends_serie(termo, geo)
            if serie:
                fonte_trends = "pytrends"
                TR_SRC.add("pytrends")
            elif serpapi_pode(geo):
                serie, rising = serpapi_serie(termo, geo, idioma), []
                fonte_trends = "serpapi"
            if serie:
                st["trs"][k] = {"ts": NOW_ISO, "s": serie, "r": rising, "src": fonte_trends}
        if not serie or len(serie) < 14:
            serie = [x[1] for x in st["traf"].get(k, [])]
            fonte_trends = "google_rss"

        # 5. Buscas relacionadas
        sc = st["sug"].get(k)
        if sc and idade_h(sc["ts"]) < 24:
            sug = sc["i"]
        else:
            sug = sugestoes(termo, idioma)
            st["sug"][k] = {"ts": NOW_ISO, "i": sug}
        rising_all = list(dict.fromkeys(rising + co_trending(k, termos_rss)))

        # 6. O core decide
        hist = carrega_hist(st["hist"].get(k, []))
        anterior = hist.snapshots[-1] if hist.snapshots else None
        sig = analyze_topic(term=k, videos=videos, reddit_posts=posts, trend_series=serie, now=NOW,
                            rising_queries=rising_all, related_queries=sug, history=hist,
                            previous_reddit_comment_velocity=prev_vel, cfg=CORE_CFG)

        # Ajustes de honestidade sobre dado ausente (nao alteram a regra do core)
        if lst is None:
            sig.verdict = Verdict.UNVERIFIED
            sig.data_gaps.append("YouTube nao consultado nesta rodada (na fila, sem quota ou erro); oferta desconhecida")
        if fonte_trends == "google_rss":
            n = len(serie)
            sig.trend_note = (f"em alta no Google; {n} leitura{'s' if n != 1 else ''} ate agora"
                              if n < 4 else f"{sig.trend_note} (leituras do Google a cada 4h, {n} pontos)")

        if anterior and (NOW - anterior.timestamp).total_seconds() < 1800:
            hist.snapshots.pop()
        snap = snapshot_from_signal(sig, NOW)
        variacao = round(sig.oracle_score - anterior.oracle_score, 1) if anterior else None
        hist.add(snap)
        st["hist"][k] = [serializa_snap(s) for s in hist.snapshots]

        tr = st["traf"].get(k, [])
        tvar = (round((tr[-1][1] / tr[-2][1] - 1) * 100)
                if len(tr) >= 2 and tr[-1][0] == NOW_ISO and tr[-2][1] else None)
        d = signal_to_json_ready(sig)
        d.update({"trafego_var": tvar, "termo": termo, "chave": k, "trafego": m["atual"], "pico_trafego": m["pico"],
                  "leituras": m["leituras"], "primeira_vez": m["primeira"], "noticias": m["noticias"],
                  "prova": prova, "rd_top": rd_top, "fonte_trends": fonte_trends, "yt_status": yt_status,
                  "variacao": variacao, "ia": st["ia"].get(k), "atualizado": NOW_ISO,
                  "encaixe": ({"encaixe": st["fit"][k]["e"], "nicho": st["fit"][k]["n"],
                               "motivo": st["fit"][k]["m"]} if k in st["fit"] else None)})
        st["dia"]["itens"][k] = d
        print(f"   {termo[:28]:<28} score {sig.oracle_score:>5.1f}  {sig.verdict.value:<14} "
              f"{sig.window.value:<12} {sig.temperature.value}")

    poda(st)
    return st


# ============================================================
# IA (opcional): le o resultado do core e orienta formato, angulo e titulo
# ============================================================
def ia_orienta(pais, st):
    itens = sorted(st["dia"]["itens"].values(), key=lambda d: -d["oracle_score"])
    for d in itens:
        d["ia"] = st["ia"].get(d["chave"])
        if d["ia"] and d["ia"].get("formato") not in FORMATOS:      # cache antigo que sugeria Shorts
            d["ia"] = dict(d["ia"], formato=None)
    if not AI_ON:
        return
    novos = [d for d in itens if not d["ia"]][:CFG.get("ia_por_rodada", 12)]
    if not novos:
        return
    entrada = []
    for d in novos:
        entrada.append({
            "termo": d["termo"], "veredito": d["verdict"], "janela": d["window"],
            "temperatura": d["temperature"], "score": d["oracle_score"], "oferta_youtube": d["supply"],
            "fontes_confirmadas": d["sources_confirmed"], "tendencia": d["trend_note"],
            "perguntas_reddit": [q.replace("[pergunta] ", "") for q in d["question_cluster"][:3]],
            "brechas": [q.replace("[brecha] ", "") for q in d["frustration_cluster"][:2]],
            "buscas": d["search_cluster"][:6],
            "noticia": (d["noticias"][0]["titulo"] if d["noticias"] else ""),
            "video_que_pegou": (d["prova"]["titulo"] if d.get("prova") else ""),
            "publico": d["audience"]})
    prompt = (
        f"Voce e o V9 ORACLE MAXX, estrategista de YouTube para o mercado {pais['nome']} (idioma {pais['idioma']}). "
        "O motor deterministico ja decidiu veredito, janela e temperatura de cada tema; NAO reavalie isso. "
        "Para cada tema devolva SO JSON no formato "
        '{"<termo exato>": {"categoria": "1 a 2 palavras em portugues", '
        '"vira_video": "sim|talvez|nao (tem vida util alem de noticia de 1 dia?)", '
        '"formato": "longo|serie|compilacao", "motivo_formato": "ate 15 palavras em portugues", '
        '"angulo": "angulo diferenciado em portugues", "titulo": "titulo pronto no idioma do mercado"}}. '
        "Todos os videos do canal sao longos (8 minutos ou mais): nunca sugira Shorts. "
        "Use as perguntas e brechas e as buscas pra definir angulo e titulo. Nao use travessao.\n"
        + json.dumps(entrada, ensure_ascii=False))
    try:
        txt, _ = chama_ia(prompt, 4000)
        mapa = extrai_json(txt)
        mapa = {str(a).lower().strip(): b for a, b in mapa.items()}
        ok = 0
        for d in novos:
            v = mapa.get(d["termo"].lower().strip())
            if not isinstance(v, dict):
                continue
            if v.get("formato") not in FORMATOS:
                v["formato"] = None
            v["ts"] = NOW_ISO
            st["ia"][d["chave"]] = v
            d["ia"] = v
            ok += 1
        print(f"   ia: {ok}/{len(novos)} temas orientados")
    except Exception as e:
        print(f"   ia falhou ({IA_PROV}): {str(e)[:200]}")
        STATUS["ia"] = "erro"


# ============================================================
# RANKINGS
# ============================================================
def compacto(d):
    return {"score": d["oracle_score"], "trafego": d["trafego"], "veredito": d["verdict"],
            "janela": d["window"], "temp": d["temperature"], "forca": d["strength"], "termo": d["termo"]}


def dias_de(geo, n):
    pasta = STATE / "history" / geo
    limite = (HOJE_D - dt.timedelta(days=n - 1)).isoformat()
    return [(f.stem, ler(f, {})) for f in sorted(pasta.glob("*.json")) if f.stem >= limite]


def agrega(geo, n, hoje_itens):
    acc = {}
    for dia, est in dias_de(geo, n):
        for k, e in est.get("termos", {}).items():
            a = acc.setdefault(k, {"sc": [], "dias": set(), "meses": set(), "pico": 0, "ult": e})
            a["sc"].append(e["score"])
            a["dias"].add(dia)
            a["meses"].add(dia[:7])
            a["pico"] = max(a["pico"], e.get("trafego", 0))
            a["ult"] = e
    out = []
    for k, a in acc.items():
        medio = sum(a["sc"]) / len(a["sc"])
        mom = a["sc"][-1] - a["sc"][0] if len(a["sc"]) > 1 else 0
        if n <= 7:
            s = round(medio * 0.7 + clamp(len(a["dias"]) / 7) * 20 + clamp(mom / 30) * 10)
        else:
            s = round(medio * 0.6 + clamp(len(a["dias"]) / 30) * 25 + clamp(len(a["meses"]) / 6) * 15)
        u = a["ult"]
        item = dict(hoje_itens[k]) if k in hoje_itens else {
            "termo": u["termo"], "chave": k, "oracle_score": u["score"], "verdict": u["veredito"],
            "window": u["janela"], "temperature": u["temp"], "strength": u["forca"],
            "trafego": u.get("trafego", 0), "compacto": True}
        item.update({"score_agregado": min(100, s), "dias_em_alta": len(a["dias"]),
                     "pico_trafego": a["pico"], "score_medio": round(medio), "momentum_dias": round(mom, 1)})
        out.append(item)
    return sorted(out, key=lambda x: -x["score_agregado"])[:CFG.get("top", 30)]


def publica_pais(pais, st):
    geo = pais["geo"]
    itens = sorted(st["dia"]["itens"].values(), key=lambda d: -d["oracle_score"])
    arq = STATE / "history" / geo / f"{HOJE}.json"
    hd = ler(arq, {"data": HOJE, "termos": {}})
    for d in itens:
        hd["termos"][d["chave"]] = compacto(d)
    salvar(arq, hd)

    limiar = core.CFG.meaningful_score_delta
    quentes = []
    for d in itens:
        m = []
        if d["temperature"] in QUENTES:
            m.append(f"temperatura {d['temperature']}")
        if d.get("variacao") is not None and d["variacao"] >= limiar:
            m.append(f"score +{d['variacao']:.0f}")
        if d.get("trafego_var") is not None and d["trafego_var"] >= 50:
            m.append(f"busca no Google +{d['trafego_var']}%")
        d["porque_quente"] = m
        if m:
            quentes.append(d)
    quentes.sort(key=lambda d: (RANK_TEMP.get(d["temperature"], 0), len(d["porque_quente"]),
                                d.get("trafego_var") or 0, d.get("variacao") or 0, d["oracle_score"]),
                 reverse=True)
    serie = []
    for dia, est in dias_de(geo, 30):
        ts = list(est.get("termos", {}).values())
        serie.append({"dia": dia, "termos": len(ts),
                      "oportunidades": sum(1 for e in ts if e.get("veredito") == "oportunidade"),
                      "quentes": sum(1 for e in ts if e.get("temp") in QUENTES)})
    top = CFG.get("top", 30)
    salvar(DATA / f"{geo}.json", {
        "geo": geo, "nome": pais["nome"], "atualizado": NOW_ISO,
        "dias_de_historico": len(dias_de(geo, 365)), "serie": serie,
        "hoje": itens[:top], "esquentando": quentes[:top],
        "descartados": sorted(st["desc"].values(), key=lambda x: x["ts"], reverse=True)[:60],
        "n_descartados": len(st["desc"]),
        "semana": agrega(geo, 7, st["dia"]["itens"]), "ano": agrega(geo, 365, st["dia"]["itens"])})
    return len(itens)


def main():
    quota = ler(STATE / "quota.json", {})
    chave_yt = hashlib.sha1(YT_KEY.encode()).hexdigest()[:8] if YT_KEY else ""
    if (quota.get("data") != HOJE or not isinstance(quota.get("unidades"), dict)
            or quota.get("chave") != chave_yt):         # novo dia ou chave nova: contador volta a zero
        quota = {"data": HOJE, "unidades": {}, "chave": chave_yt}
    indice = {"atualizado": NOW_ISO, "paises": [], "versao": "MAXX", "yt_min_minutos": YT_MIN_S // 60,
              "limiares": {"imediata": core.CFG.immediate_min_score, "entrar": core.CFG.enter_now_min_score,
                           "monitorar": core.CFG.watch_min_score}}
    print(f"IA: {IA_PROV or 'desligada'}" + (f" (modelo {CFG.get('modelo_gemini', 'gemini-3.5-flash-lite')})" if IA_PROV == "gemini" else ""))
    SERP["estado"] = ler(STATE / "serpapi.json", {})
    paises = CFG["paises"]
    r0 = (NOW.hour // 4) % len(paises)          # gira quem vai primeiro (reparte quota entre os paises)
    feitos = {}
    for pais in paises[r0:] + paises[:r0]:
        st = analisa_pais(pais, quota["unidades"])
        if st is None:
            print("   sem temas nesta rodada, mantendo dados anteriores")
            continue
        ia_orienta(pais, st)
        n = publica_pais(pais, st)
        salvar(STATE / f"{pais['geo']}.json", st)
        feitos[pais["geo"]] = {"geo": pais["geo"], "nome": pais["nome"],
                               "bandeira": pais.get("bandeira", ""), "temas": n}
    indice["paises"] = [feitos[p["geo"]] for p in paises if p["geo"] in feitos]
    STATUS["trends"] = "misto" if len(TR_SRC) == 2 else (next(iter(TR_SRC)) if TR_SRC else "so_rss")
    salvar(STATE / "serpapi.json", SERP["estado"])
    indice["fontes"] = STATUS
    salvar(STATE / "quota.json", quota)
    salvar(DATA / "index.json", indice)
    print("fontes:", STATUS, "| quota:", quota["unidades"])


if __name__ == "__main__":
    main()
