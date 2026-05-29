"""
http_client.py — cliente HTTP que imita o fingerprint TLS do Chrome.
Usa curl_cffi (binário embutido no pacote → funciona no Streamlit Cloud,
ao contrário de wrapper-tls-requests, que baixa o binário em runtime).
"""
from curl_cffi import requests as _creq

_IMPERSONATE = "chrome"


def get(url: str, headers: dict | None = None, timeout: int = 20):
    """GET com fingerprint de navegador. Retorna o objeto Response do curl_cffi."""
    return _creq.get(
        url,
        headers=headers or {},
        impersonate=_IMPERSONATE,
        timeout=timeout,
    )
