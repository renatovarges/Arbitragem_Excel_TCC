"""
arbitragem_scraper.py
Busca escala de árbitros no Transfermarkt e estatísticas da temporada.
Faltas médias complementadas via Sofascore (busca por nome).
"""
import json
import re
import unicodedata
from pathlib import Path

import http_client
from bs4 import BeautifulSoup
import streamlit as st

BASE_DIR = Path(__file__).resolve().parent

TM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.transfermarkt.com.br/",
}
SF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/",
}

SF_TOURNAMENT_ID = 325   # Brasileirão Série A no Sofascore


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    """Lowercase sem acentos, só letras/números/espaço."""
    n = unicodedata.normalize("NFKD", str(s)).encode("ASCII", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", "", n.lower()).strip()


def _safe_int(val, default: int = 0) -> int:
    try:
        return int(str(val).replace("-", "0").strip() or 0)
    except (ValueError, TypeError):
        return default


def _team_match(team_json: str, tm_title: str) -> bool:
    """Casa um nome de time do JSON com o título do Transfermarkt."""
    tj = _norm(team_json)
    tt = _norm(tm_title)
    if not tj or not tt:
        return False
    if tj[:5] in tt or tt[:5] in tj:
        return True
    # Qualquer palavra significativa em comum
    return any(w in tt for w in tj.split() if len(w) > 3)


def _name_match(nome_a: str, nome_b: str) -> bool:
    """Casa dois nomes de árbitro (normalizado)."""
    a, b = _norm(nome_a), _norm(nome_b)
    if not a or not b:
        return False
    return a == b or a[:12] in b or b[:12] in a


def _load_confrontos(rodada_num: int) -> list:
    with open(BASE_DIR / "rodadas.json", encoding="utf-8") as f:
        data = json.load(f)
    return data.get(str(rodada_num), [])


# ---------------------------------------------------------------------------
# FUNÇÃO PRINCIPAL
# ---------------------------------------------------------------------------

@st.cache_data(ttl=1800, show_spinner=False)
def get_rodada_completa(rodada_num: int, buscar_faltas: bool = True) -> list[dict]:
    """
    Retorna lista de dicts (um por jogo):
    {mandante, visitante, arbitro, jogos, penaltis,
     amarelos_media, vermelhos_total, faltas_media}
    """
    confrontos = _load_confrontos(rodada_num)

    escala    = _get_escala_tm(rodada_num, confrontos)      # {(mand,vis): arbitro}
    stats_map = _get_all_stats_tm(rodada_num)               # {norm_nome: stats}

    # Faltas (Sofascore) — buscadas em paralelo para os árbitros confirmados
    faltas_map = {}
    if buscar_faltas:
        nomes = sorted({
            escala.get((_norm(j["mandante"]), _norm(j["visitante"])), "")
            for j in confrontos
        } - {"", "A confirmar", "—"})
        if nomes:
            faltas_map = _faltas_em_lote(tuple(nomes))

    resultado = []
    for jogo in confrontos:
        mand = jogo["mandante"]
        vis  = jogo["visitante"]
        key  = (_norm(mand), _norm(vis))

        arb_nome = escala.get(key, "A confirmar")

        # Stats do árbitro (Transfermarkt)
        stats = {}
        if arb_nome not in ("A confirmar", "", "—"):
            for k, v in stats_map.items():
                if _name_match(arb_nome, k):
                    stats = v
                    break

        jogos    = stats.get("jogos", "—")
        am_total = stats.get("amarelos_total", 0)
        am_media = (
            round(am_total / jogos, 1)
            if isinstance(jogos, int) and jogos > 0
            else "—"
        )

        resultado.append({
            "mandante":        mand,
            "visitante":       vis,
            "arbitro":         arb_nome,
            "jogos":           jogos,
            "penaltis":        stats.get("penaltis", "—"),
            "amarelos_media":  am_media,
            "vermelhos_total": stats.get("vermelhos_total", "—"),
            "faltas_media":    faltas_map.get(arb_nome, "—"),
        })

    return resultado


# ---------------------------------------------------------------------------
# TRANSFERMARKT — escala por rodada (baseado em div.box)
# ---------------------------------------------------------------------------

def _get_escala_tm(rodada_num: int, confrontos: list) -> dict:
    """
    Para cada div.box que contém um link de árbitro, extrai
    (mandante, visitante, árbitro) e casa com os confrontos do JSON.
    Retorna {(norm_mand, norm_vis): nome_arbitro}.
    """
    url = (
        "https://www.transfermarkt.com.br/campeonato-brasileiro-serie-a/"
        f"spieltag/wettbewerb/BRA1/plus/?saison_id=2025&spieltag={rodada_num}"
    )
    games = []
    try:
        r    = http_client.get(url, headers=TM_HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")

        refs = soup.find_all(
            "a", href=lambda h: h and "/profil/schiedsrichter/" in h
        )
        for ref in refs:
            # Subir até o div.box do jogo
            box = ref
            while box is not None and not (
                box.get("class") and "box" in box.get("class")
            ):
                box = box.parent
            if box is None:
                continue

            # Times = títulos únicos dos links de clube, em ordem
            seen = []
            for a in box.find_all(
                "a", href=lambda h: h and "/verein/" in h
            ):
                t = a.get("title")
                if t and t not in seen:
                    seen.append(t)

            arb = ref.get_text(strip=True)
            if len(seen) >= 2 and arb:
                games.append({"home": seen[0], "away": seen[1], "arb": arb})
    except Exception:
        games = []

    # Casar cada confronto do JSON com os jogos do TM
    result = {}
    for jogo in confrontos:
        key = (_norm(jogo["mandante"]), _norm(jogo["visitante"]))
        arb = "A confirmar"
        for g in games:
            if (_team_match(jogo["mandante"], g["home"]) and
                    _team_match(jogo["visitante"], g["away"])):
                arb = g["arb"]
                break
        result[key] = arb

    return result


# ---------------------------------------------------------------------------
# TRANSFERMARKT — stats de todos os árbitros da temporada
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def _get_all_stats_tm(rodada_num: int) -> dict:
    """
    Retorna {norm_nome: {jogos, amarelos_total, vermelhos_total, penaltis}}.
    Colunas TM: vals[2]=nome, vals[5]=jogos, vals[6]=amarelos,
                vals[7]=duplo_amarelo, vals[8]=verm_direto, vals[9]=penaltis
    """
    url = (
        "https://www.transfermarkt.com.br/campeonato-brasileiro-serie-a/"
        f"schiedsrichter/wettbewerb/BRA1/saison_id/2025/spieltag/{rodada_num}"
    )
    result = {}
    try:
        r      = http_client.get(url, headers=TM_HEADERS, timeout=20)
        soup   = BeautifulSoup(r.text, "html.parser")
        tables = soup.find_all("table")
        if len(tables) < 2:
            return {}
        table = tables[1]

        for row in table.find_all("tr"):
            vals = [c.get_text(strip=True) for c in row.find_all("td")]
            if len(vals) < 10 or not vals[2]:
                continue
            if not any(v.isdigit() for v in "".join(vals[5:])):
                continue

            jogos = _safe_int(vals[5])
            if jogos == 0:
                continue

            duplo_am = _safe_int(vals[7])
            verm_dir = _safe_int(vals[8])
            result[_norm(vals[2])] = {
                "jogos":           jogos,
                "amarelos_total":  _safe_int(vals[6]),
                "vermelhos_total": duplo_am + verm_dir,
                "penaltis":        _safe_int(vals[9]),
            }
    except Exception:
        return result

    return result


# ---------------------------------------------------------------------------
# SOFASCORE — média de faltas por árbitro (busca por nome)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=86400, show_spinner=False)
def _faltas_em_lote(nomes: tuple) -> dict:
    """
    Calcula a média de faltas de vários árbitros em paralelo.
    Retorna {nome_arbitro: "26.0" | "—"}.
    Cacheado pelo conjunto de nomes (24h).
    """
    from concurrent.futures import ThreadPoolExecutor

    out = {}
    if not nomes:
        return out

    # Garante que a chave do proxy foi lida na thread principal antes
    # de disparar as threads (st.secrets pode falhar dentro de threads).
    http_client.prime()

    with ThreadPoolExecutor(max_workers=min(6, len(nomes))) as ex:
        futures = {ex.submit(_faltas_um, nome): nome for nome in nomes}
        for fut in futures:
            nome = futures[fut]
            try:
                out[nome] = fut.result()
            except Exception:
                out[nome] = "—"
    return out


def _faltas_um(arbitro_nome: str, n_jogos: int = 4) -> str:
    """
    Busca o árbitro pelo nome no Sofascore e calcula a média de faltas
    (home + away) dos últimos n_jogos no Brasileirão. Função "pura"
    (sem chamadas Streamlit) — segura para rodar em thread.
    """
    try:
        # 1. ID do árbitro
        q = arbitro_nome.replace(" ", "%20")
        r = http_client.get(
            f"https://api.sofascore.com/api/v1/search/all?q={q}&page=0",
            headers=SF_HEADERS, timeout=20
        )
        results = json.loads(r.text).get("results", [])
        ref_id = None
        for item in results:
            if item.get("type") == "referee":
                ref_id = item.get("entity", {}).get("id")
                break
        if not ref_id:
            return "—"

        # 2. Últimos jogos
        r2 = http_client.get(
            f"https://api.sofascore.com/api/v1/referee/{ref_id}/events/last/0",
            headers=SF_HEADERS, timeout=20
        )
        events = json.loads(r2.text).get("events", [])

        # 3. Faltas dos últimos jogos do Brasileirão
        fouls = []
        for ev in events[:12]:
            tid = (ev.get("tournament", {})
                     .get("uniqueTournament", {})
                     .get("id"))
            if tid != SF_TOURNAMENT_ID:
                continue
            eid = ev.get("id")
            if not eid:
                continue
            try:
                r3 = http_client.get(
                    f"https://api.sofascore.com/api/v1/event/{eid}/statistics",
                    headers=SF_HEADERS, timeout=20
                )
                stats = json.loads(r3.text).get("statistics", [])
                for period in stats:
                    if period.get("period") != "ALL":
                        continue
                    for group in period.get("groups", []):
                        for item in group.get("statisticsItems", []):
                            if item.get("name", "").lower() == "fouls":
                                h = _safe_int(item.get("home", 0))
                                a = _safe_int(item.get("away", 0))
                                fouls.append(h + a)
            except Exception:
                continue
            if len(fouls) >= n_jogos:
                break

        if not fouls:
            return "—"
        return f"{sum(fouls) / len(fouls):.1f}"

    except Exception:
        return "—"
