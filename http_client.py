"""
http_client.py — cliente HTTP do app.

No Streamlit Cloud (IP de datacenter), Transfermarkt e Sofascore bloqueiam
o acesso direto (403). Para contornar, as requisições são roteadas por um
serviço de proxy (ScraperAPI) que usa IPs residenciais limpos.

A chave do ScraperAPI é lida de st.secrets["SCRAPERAPI_KEY"]. Se não houver
chave (ex.: rodando localmente), faz a requisição direta com curl_cffi
imitando o Chrome.
"""
from urllib.parse import quote_plus

from curl_cffi import requests as _creq

try:
    import streamlit as st
except Exception:  # pragma: no cover
    st = None

_IMPERSONATE = "chrome"

# Cache da chave em nível de módulo (lida uma vez na thread principal,
# reutilizada com segurança por threads de paralelização).
_API_KEY_CACHE = "__unset__"


def _get_api_key() -> str | None:
    """Lê a chave do ScraperAPI de st.secrets (ou None se ausente)."""
    global _API_KEY_CACHE
    if _API_KEY_CACHE != "__unset__":
        return _API_KEY_CACHE
    key = None
    if st is not None:
        try:
            key = st.secrets["SCRAPERAPI_KEY"]
        except Exception:
            key = None
    _API_KEY_CACHE = key
    return key


def prime() -> None:
    """Força a leitura da chave na thread principal (antes de paralelizar)."""
    _get_api_key()


def get(url: str, headers: dict | None = None, timeout: int = 45):
    """
    GET com fingerprint de navegador.

    Se houver SCRAPERAPI_KEY, roteia pelo ScraperAPI (IP residencial);
    caso contrário, requisição direta (funciona em IP residencial local).
    Retorna o objeto Response do curl_cffi.
    """
    api_key = _get_api_key()

    if api_key:
        proxied = (
            "https://api.scraperapi.com/"
            f"?api_key={api_key}"
            f"&url={quote_plus(url)}"
        )
        # Pelo proxy não precisamos de impersonate (o ScraperAPI cuida disso)
        return _creq.get(proxied, headers=headers or {}, timeout=timeout)

    # Fallback direto (local / IP residencial).
    # verify=False evita erro de certificado do curl_cffi em alguns hosts (ex.: CBF).
    return _creq.get(
        url,
        headers=headers or {},
        impersonate=_IMPERSONATE,
        timeout=timeout,
        verify=False,
    )
