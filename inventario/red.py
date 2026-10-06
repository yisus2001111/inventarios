"""Datos de la conexión: IP real del visitante y si llegó por el túnel de Cloudflare.

Con Cloudflare Tunnel, el programa `cloudflared` corre en la misma computadora y reenvía
las visitas desde 127.0.0.1. Solo en ese caso se confía en los encabezados que agrega
Cloudflare (nadie de afuera puede hacerse pasar por 127.0.0.1).
"""

from flask import request

LOCALES = ("127.0.0.1", "::1")


def por_tunel():
    return request.remote_addr in LOCALES and bool(request.headers.get("CF-Connecting-IP"))


def ip_cliente():
    if por_tunel():
        return request.headers["CF-Connecting-IP"].strip()
    return request.remote_addr or ""


def direccion_actual():
    """Dirección base con la que se está viendo la página (https si llegó por el túnel)."""
    https_por_proxy = (request.remote_addr in LOCALES
                       and request.headers.get("X-Forwarded-Proto") == "https")
    esquema = "https" if por_tunel() or https_por_proxy else request.scheme
    return f"{esquema}://{request.host}"
