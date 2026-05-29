"""
arbitragem_scraper.py
Busca escala de árbitros no Transfermarkt e estatísticas da temporada.
Fouls complementados via Sofascore.
"""
import json
import re
import time
import unicodedata
from pathlib import Path

import tls_requests
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

# Sofascore: Brasileirão Serie A
SF_TOURNAMENT_ID = 325
SF_SEASON_ID     = 87678   # 2026


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    """Lowercase sem acentos."""
    n = unicodedata.normalize("NFKD", str(s)).encode("ASCII", "ignore").decode()
    return re.sub(r"\s+", " ", n).lower().strip()


def _safe_int(val, default: int = 0) -> int:
    try:
        return int(str(val).replace("-", "0").strip() or 0)
    except (ValueError, TypeError):
        return default


def _load_confrontos(rodada_num: int) -> list:
    with open(BASE_DIR / "rodadas.json", encoding="utf-8") as f:
        data = json.load(f)
    return data.get(str(rodada_num), [])


# ---------------------------------------------------------------------------
# FUNÇÃO PRINCIPAL
# ---------------------------------------------------------------------------

@st.cache_data(ttl=1800, show_spinner=False)
def get_rodada_completa(rodada_num: int) -> list[dict]:
    """
    Retorna lista de 10 dicts, um por jogo:
    {mandante, visitante, arbitro, jogos, penaltis,
     amarelos_media, vermelhos_total, faltas_media}
    """
    confrontos = _load_confrontos(rodada_num)

    # 1. Escala (árbitro por jogo) ─ Transfermarkt spieltag
    escala = _get_escala_tm(rodada_num, confrontos)

    # 2. Stats de todos os árbitros na temporada ─ TM schiedsrichter
    stats_map = _get_all_stats_tm(rodada_num)

    # 3. Mapa de IDs Sofascore (para fouls)
    sf_id_map = _build_sf_id_map()

    resultado = []
    for jogo in confrontos:
        mand = jogo["mandante"]
        vis  = jogo["visitante"]
        key  = (_norm(mand), _norm(vis))

        arb_nome = escala.get(key, "A confirmar")

        # Stats do árbitro no Transfermarkt
        stats = {}
        arb_key = _norm(arb_nome)
        # Busca fuzzy no mapa
        for k, v in stats_map.items():
            if arb_key[:8] and arb_key[:8] in k:
                stats = v
                break

        jogos    = stats.get("jogos", "—")
        am_total = stats.get("amarelos_total", 0)
        am_media = (
            round(am_total / jogos, 1)
            if isinstance(jogos, int) and jogos > 0
            else "—"
        )

        # Fouls via Sofascore
        faltas = "—"
        if arb_nome not in ("A confirmar", "—", ""):
            sf_id = sf_id_map.get(_norm(arb_nome))
            if sf_id:
                faltas = _get_faltas_media(sf_id)

        resultado.append({
            "mandante":        mand,
            "visitante":       vis,
            "arbitro":         arb_nome,
            "jogos":           jogos,
            "penaltis":        stats.get("penaltis", "—"),
            "amarelos_media":  am_media,
            "vermelhos_total": stats.get("vermelhos_total", "—"),
            "faltas_media":    faltas,
        })

    return resultado


# ---------------------------------------------------------------------------
# TRANSFERMARKT — escala por rodada
# ---------------------------------------------------------------------------

def _get_escala_tm(rodada_num: int, confrontos: list) -> dict:
    """
    Scrape a página de spieltag do TM e associa árbitro a cada confronto.
    Retorna dict {(norm_mand, norm_vis): nome_arbitro}.
    """
    url = (
        "https://www.transfermarkt.com.br/campeonato-brasileiro-serie-a/"
        f"spieltag/wettbewerb/BRA1/plus/?saison_id=2025&spieltag={rodada_num}"
    )
    try:
        r    = tls_requests.get(url, headers=TM_HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(separator="\n")
        lines = [l.strip() for l in text.split("\n") if l.strip()]
    except Exception:
        return {}

    result   = {}
    assigned = set()

    for i, line in enumerate(lines):
        # Detectar "Árbitro:" (com ou sem acentos)
        if not re.search(r"[Áa]rbitro\s*:", line, re.IGNORECASE):
            continue

        raw_arb = re.split(r"[Áa]rbitro\s*:", line, flags=re.IGNORECASE)[-1].strip()
        if not raw_arb or "desconhecido" in raw_arb.lower():
            arb_nome = "A confirmar"
        else:
            arb_nome = raw_arb

        # Contexto das linhas anteriores para identificar os times
        context = " ".join(lines[max(0, i - 30):i]).lower()

        best       = None
        best_score = 0
        for jogo in confrontos:
            key = (_norm(jogo["mandante"]), _norm(jogo["visitante"]))
            if key in assigned:
                continue
            score = 0
            for team in (jogo["mandante"], jogo["visitante"]):
                t_n = _norm(team)
                # Testar variações do nome (primeiras letras, primeira palavra)
                first_word = t_n.split()[0][:5]
                if t_n[:6] in context:
                    score += 2
                elif first_word in context:
                    score += 1
            if score > best_score:
                best_score = score
                best = jogo

        key = (_norm(best["mandante"]), _norm(best["visitante"])) if best else None
        if key and best_score >= 1:
            result[key] = arb_nome
            assigned.add(key)
        elif arb_nome != "A confirmar":
            # Sem match claro: associa ao próximo confronto sem árbitro (ordem TM)
            for jogo in confrontos:
                k = (_norm(jogo["mandante"]), _norm(jogo["visitante"]))
                if k not in result:
                    result[k] = arb_nome
                    assigned.add(k)
                    break

    # Completar não encontrados
    for jogo in confrontos:
        key = (_norm(jogo["mandante"]), _norm(jogo["visitante"]))
        if key not in result:
            result[key] = "A confirmar"

    return result


# ---------------------------------------------------------------------------
# TRANSFERMARKT — stats de todos os árbitros
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def _get_all_stats_tm(rodada_num: int) -> dict:
    """
    Retorna dict {norm_nome: {jogos, amarelos_total, vermelhos_total, penaltis, ...}}.
    Colunas TM: nome | país | estreia | jogos | amarelos | duplo_am | verm_dir | penaltis
    """
    url = (
        "https://www.transfermarkt.com.br/campeonato-brasileiro-serie-a/"
        f"schiedsrichter/wettbewerb/BRA1/saison_id/2025/spieltag/{rodada_num}"
    )
    try:
        r    = tls_requests.get(url, headers=TM_HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        tables = soup.find_all("table")
        if len(tables) < 2:
            return {}
        table = tables[1]
    except Exception:
        return {}

    result = {}
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        vals  = [c.get_text(strip=True) for c in cells]

        # Precisamos de pelo menos 10 células; nome em vals[2], números em vals[5..9]
        if len(vals) < 10 or not vals[2]:
            continue
        if not any(v.isdigit() for v in "".join(vals[5:])):
            continue

        nome     = vals[2]
        jogos    = _safe_int(vals[5])
        amarelos = _safe_int(vals[6])
        duplo_am = _safe_int(vals[7])
        verm_dir = _safe_int(vals[8])
        penaltis = _safe_int(vals[9])

        if jogos == 0:
            continue

        result[_norm(nome)] = {
            "jogos":           jogos,
            "amarelos_total":  amarelos,
            "duplo_amarelo":   duplo_am,
            "vermelho_direto": verm_dir,
            "vermelhos_total": duplo_am + verm_dir,
            "penaltis":        penaltis,
        }

    return result


# ---------------------------------------------------------------------------
# SOFASCORE — mapa nome → ID do árbitro
# ---------------------------------------------------------------------------

@st.cache_data(ttl=86400, show_spinner=False)
def _build_sf_id_map() -> dict:
    """
    Constrói {norm_nome_arbitro: sofascore_id} a partir das últimas rodadas
    completas do Brasileirão no Sofascore.
    """
    result = {}
    try:
        for round_num in range(1, 20):
            url = (
                f"https://api.sofascore.com/api/v1/unique-tournament/"
                f"{SF_TOURNAMENT_ID}/season/{SF_SEASON_ID}/events/round/{round_num}"
            )
            r      = tls_requests.get(url, headers=SF_HEADERS, timeout=12)
            events = json.loads(r.text).get("events", [])
            if not events:
                break

            for ev in events:
                ev_id  = ev.get("id")
                status = ev.get("status", {}).get("type", "")
                if status != "finished" or not ev_id:
                    continue
                try:
                    r2         = tls_requests.get(
                        f"https://api.sofascore.com/api/v1/event/{ev_id}",
                        headers=SF_HEADERS, timeout=10
                    )
                    event_data = json.loads(r2.text).get("event", {})
                    ref        = event_data.get("referee") or {}
                    if ref.get("name") and ref.get("id"):
                        result[_norm(ref["name"])] = ref["id"]
                    time.sleep(0.15)
                except Exception:
                    continue
            time.sleep(0.3)
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# SOFASCORE — média de faltas
# ---------------------------------------------------------------------------

@st.cache_data(ttl=86400, show_spinner=False)
def _get_faltas_media(sf_ref_id: int, n_jogos: int = 10) -> str:
    """Média de faltas dos últimos n_jogos deste árbitro no Brasileirão."""
    try:
        url    = f"https://api.sofascore.com/api/v1/referee/{sf_ref_id}/events/last/0"
        r      = tls_requests.get(url, headers=SF_HEADERS, timeout=12)
        events = json.loads(r.text).get("events", [])

        fouls_list = []
        for ev in events[:25]:
            # Filtrar só Brasileirão
            tour_id = (
                ev.get("tournament", {})
                  .get("uniqueTournament", {})
                  .get("id")
            )
            if tour_id != SF_TOURNAMENT_ID:
                continue

            ev_id = ev.get("id")
            if not ev_id:
                continue

            try:
                r2    = tls_requests.get(
                    f"https://api.sofascore.com/api/v1/event/{ev_id}/statistics",
                    headers=SF_HEADERS, timeout=10
                )
                stats = json.loads(r2.text).get("statistics", [])
                for period_data in stats:
                    if period_data.get("period") != "ALL":
                        continue
                    for group in period_data.get("groups", []):
                        for item in group.get("statisticsItems", []):
                            if item.get("name", "").lower() == "fouls":
                                h = _safe_int(item.get("home", 0))
                                a = _safe_int(item.get("away", 0))
                                fouls_list.append(h + a)
                time.sleep(0.15)
            except Exception:
                continue

            if len(fouls_list) >= n_jogos:
                break

        if not fouls_list:
            return "—"
        return f"{sum(fouls_list) / len(fouls_list):.1f}"

    except Exception:
        return "—"
