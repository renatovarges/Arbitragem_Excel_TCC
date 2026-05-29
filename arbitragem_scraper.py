"""
arbitragem_scraper.py
Coleta de dados de arbitragem do Brasileirão.

- Escala (árbitro de cada jogo): Transfermarkt, quando já publicada.
  Quando ainda não saiu no TM, o usuário escolhe o árbitro manualmente
  (a escala da CBF costuma sair antes do TM atualizar).
- Estatísticas da temporada (jogos, pênaltis, amarelos, vermelhos):
  tabela de árbitros do Transfermarkt — disponível para qualquer árbitro
  independentemente da rodada.
- Faltas médias: Sofascore (média das últimas partidas no Brasileirão).
"""
import json
import re
import unicodedata
from pathlib import Path

import http_client
from bs4 import BeautifulSoup
import streamlit as st

BASE_DIR = Path(__file__).resolve().parent

TM_HEADERS = {"Accept-Language": "pt-BR,pt;q=0.9"}
SF_HEADERS = {"Accept": "application/json"}

SF_TOURNAMENT_ID = 325   # Brasileirão Série A no Sofascore


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    n = unicodedata.normalize("NFKD", str(s)).encode("ASCII", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", "", n.lower()).strip()


def _safe_int(val, default: int = 0) -> int:
    try:
        return int(str(val).replace("-", "0").strip() or 0)
    except (ValueError, TypeError):
        return default


def _team_match(team_json: str, tm_title: str) -> bool:
    tj, tt = _norm(team_json), _norm(tm_title)
    if not tj or not tt:
        return False
    if tj[:5] in tt or tt[:5] in tj:
        return True
    return any(w in tt for w in tj.split() if len(w) > 3)


def _name_match(nome_a: str, nome_b: str) -> bool:
    a, b = _norm(nome_a), _norm(nome_b)
    if not a or not b:
        return False
    return a == b or a[:12] in b or b[:12] in a


def load_confrontos(rodada_num: int) -> list:
    with open(BASE_DIR / "rodadas.json", encoding="utf-8") as f:
        data = json.load(f)
    return data.get(str(rodada_num), [])


# ---------------------------------------------------------------------------
# ESTATÍSTICAS DA TEMPORADA (tabela de árbitros do Transfermarkt)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=43200, show_spinner=False)   # 12h
def get_stats_arbitros() -> dict:
    """
    {norm_nome: {nome, jogos, amarelos_total, vermelhos_total, penaltis}}
    para todos os árbitros da temporada.
    """
    url = (
        "https://www.transfermarkt.com.br/campeonato-brasileiro-serie-a/"
        "schiedsrichter/wettbewerb/BRA1/saison_id/2025/spieltag/1"
    )
    result = {}
    try:
        r      = http_client.get(url, headers=TM_HEADERS, timeout=60)
        soup   = BeautifulSoup(r.text, "html.parser")
        tables = soup.find_all("table")
        if len(tables) < 2:
            return {}
        for row in tables[1].find_all("tr"):
            vals = [c.get_text(strip=True) for c in row.find_all("td")]
            if len(vals) < 10 or not vals[2]:
                continue
            if not any(v.isdigit() for v in "".join(vals[5:])):
                continue
            jogos = _safe_int(vals[5])
            if jogos == 0:
                continue
            result[_norm(vals[2])] = {
                "nome":            vals[2],
                "jogos":           jogos,
                "amarelos_total":  _safe_int(vals[6]),
                "vermelhos_total": _safe_int(vals[7]) + _safe_int(vals[8]),
                "penaltis":        _safe_int(vals[9]),
            }
    except Exception:
        return result
    return result


def get_lista_arbitros() -> list:
    """Lista ordenada dos nomes (display) dos árbitros da temporada."""
    stats = get_stats_arbitros()
    return sorted(v["nome"] for v in stats.values())


# ---------------------------------------------------------------------------
# ESCALA AUTOMÁTICA (Transfermarkt) — quando já publicada
# ---------------------------------------------------------------------------

@st.cache_data(ttl=21600, show_spinner=False)   # 6h
def get_escala_auto(rodada_num: int) -> dict:
    """
    {(norm_mand, norm_vis): nome_arbitro} para a rodada, lendo o TM.
    Jogos sem árbitro publicado ficam de fora do dicionário.
    """
    confrontos = load_confrontos(rodada_num)
    url = (
        "https://www.transfermarkt.com.br/campeonato-brasileiro-serie-a/"
        f"spieltag/wettbewerb/BRA1/plus/?saison_id=2025&spieltag={rodada_num}"
    )
    games = []
    try:
        r    = http_client.get(url, headers=TM_HEADERS, timeout=60)
        soup = BeautifulSoup(r.text, "html.parser")
        for ref in soup.find_all(
            "a", href=lambda h: h and "/profil/schiedsrichter/" in h
        ):
            box = ref
            while box is not None and not (
                box.get("class") and "box" in box.get("class")
            ):
                box = box.parent
            if box is None:
                continue
            seen = []
            for a in box.find_all("a", href=lambda h: h and "/verein/" in h):
                t = a.get("title")
                if t and t not in seen:
                    seen.append(t)
            arb = ref.get_text(strip=True)
            if len(seen) >= 2 and arb:
                games.append({"home": seen[0], "away": seen[1], "arb": arb})
    except Exception:
        games = []

    result = {}
    for jogo in confrontos:
        for g in games:
            if (_team_match(jogo["mandante"], g["home"]) and
                    _team_match(jogo["visitante"], g["away"])):
                result[(_norm(jogo["mandante"]), _norm(jogo["visitante"]))] = g["arb"]
                break
    return result


# ---------------------------------------------------------------------------
# MONTAGEM DOS DADOS FINAIS (a partir da escala escolhida)
# ---------------------------------------------------------------------------

def montar_dados(rodada_num: int, escala_por_indice: dict) -> list[dict]:
    """
    escala_por_indice: {indice_do_jogo: nome_arbitro ("" ou "A confirmar" se vazio)}
    Retorna a lista final de dicts (um por jogo) com stats + faltas.
    """
    confrontos = load_confrontos(rodada_num)
    stats_map  = get_stats_arbitros()

    # Faltas em paralelo para os árbitros escolhidos
    nomes = sorted({
        n for n in escala_por_indice.values()
        if n and n not in ("A confirmar", "—")
    })
    faltas_map = _faltas_em_lote(tuple(nomes)) if nomes else {}

    resultado = []
    for i, jogo in enumerate(confrontos):
        arb = escala_por_indice.get(i, "") or "A confirmar"

        stats = {}
        if arb not in ("A confirmar", "", "—"):
            for k, v in stats_map.items():
                if _name_match(arb, k):
                    stats = v
                    break

        jogos    = stats.get("jogos", "—")
        am_total = stats.get("amarelos_total", 0)
        am_media = (
            round(am_total / jogos, 1)
            if isinstance(jogos, int) and jogos > 0 else "—"
        )

        resultado.append({
            "mandante":        jogo["mandante"],
            "visitante":       jogo["visitante"],
            "arbitro":         arb,
            "jogos":           jogos,
            "penaltis":        stats.get("penaltis", "—"),
            "amarelos_media":  am_media,
            "vermelhos_total": stats.get("vermelhos_total", "—"),
            "faltas_media":    faltas_map.get(arb, "—"),
        })
    return resultado


# ---------------------------------------------------------------------------
# SOFASCORE — média de faltas (paralelo)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=604800, show_spinner=False)   # 7 dias
def _faltas_em_lote(nomes: tuple) -> dict:
    from concurrent.futures import ThreadPoolExecutor

    out = {}
    if not nomes:
        return out
    http_client.prime()   # lê a chave do proxy na thread principal

    # max_workers=4 para respeitar o limite de 5 conexões simultâneas do proxy
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(_faltas_um, n): n for n in nomes}
        for fut in futures:
            nome = futures[fut]
            try:
                out[nome] = fut.result()
            except Exception:
                out[nome] = "—"
    return out


def _faltas_um(arbitro_nome: str, n_jogos: int = 3) -> str:
    """Média de faltas (home+away) das últimas partidas no Brasileirão."""
    try:
        q = arbitro_nome.replace(" ", "%20")
        r = http_client.get(
            f"https://api.sofascore.com/api/v1/search/all?q={q}&page=0",
            headers=SF_HEADERS, timeout=60
        )
        results = json.loads(r.text).get("results", [])
        ref_id = next(
            (it.get("entity", {}).get("id")
             for it in results if it.get("type") == "referee"),
            None,
        )
        if not ref_id:
            return "—"

        r2 = http_client.get(
            f"https://api.sofascore.com/api/v1/referee/{ref_id}/events/last/0",
            headers=SF_HEADERS, timeout=60
        )
        events = json.loads(r2.text).get("events", [])

        fouls = []
        for ev in events[:20]:
            tid = (ev.get("tournament", {})
                     .get("uniqueTournament", {}).get("id"))
            if tid != SF_TOURNAMENT_ID:
                continue
            eid = ev.get("id")
            if not eid:
                continue
            try:
                r3 = http_client.get(
                    f"https://api.sofascore.com/api/v1/event/{eid}/statistics",
                    headers=SF_HEADERS, timeout=60
                )
                stats = json.loads(r3.text).get("statistics", [])
                for period in stats:
                    if period.get("period") != "ALL":
                        continue
                    for group in period.get("groups", []):
                        for item in group.get("statisticsItems", []):
                            if item.get("name", "").lower() == "fouls":
                                fouls.append(
                                    _safe_int(item.get("home", 0)) +
                                    _safe_int(item.get("away", 0))
                                )
            except Exception:
                continue
            if len(fouls) >= n_jogos:
                break

        if not fouls:
            return "—"
        return f"{sum(fouls) / len(fouls):.1f}"
    except Exception:
        return "—"
