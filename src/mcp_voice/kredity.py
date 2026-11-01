"""Zůstatek kreditů ElevenLabs: jeden GET /v1/user/subscription, krátký timeout, bez opakování.

Klíč potřebuje oprávnění „User: Access“ (jen čtení účtu a předplatného); bez něj 401/403 -> chyba
"opravneni" a kredity null. Z odpovědi se berou jen tři čísla (čerpání, limit, čas obnovy),
tělo se neloguje a nikam neukládá. Selhání nikdy neovlivní výsledek rekni.
"""
from __future__ import annotations

import datetime as dt
import json
import logging

import httpx

from .status import MAX_CISLO
from .tts import POVOLENE_HOSTY, ZAKLAD

log = logging.getLogger("mcp_voice.kredity")

CESTA = "/v1/user/subscription"
TIMEOUT_S = 5.0
MAX_TELO = 64 * 1024


def _cislo(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < MAX_CISLO else None


def rozber(telo: bytes) -> dict:
    """{"kredity_zbyva", "kredity_limit", "obnova"}; neplatný tvar = ValueError."""
    d = json.loads(telo)
    if not isinstance(d, dict):
        raise ValueError("odpověď není objekt")
    pouzito, limit = _cislo(d.get("character_count")), _cislo(d.get("character_limit"))
    if pouzito is None or limit is None:
        raise ValueError("chybí character_count nebo character_limit")
    obnova = _cislo(d.get("next_character_count_reset_unix"))
    try:
        obnova_iso = (dt.datetime.fromtimestamp(obnova).astimezone().isoformat(timespec="seconds")
                      if obnova else None)
    except (OverflowError, OSError, ValueError):
        obnova_iso = None
    return {"kredity_zbyva": max(0, limit - pouzito), "kredity_limit": limit, "obnova": obnova_iso}


async def zjisti(klic: str, transport: httpx.AsyncBaseTransport | None = None,
                 timeout_s: float = TIMEOUT_S) -> dict:
    """Pole pro status.json. Úspěch: kredity_* + kredity_chyba None. 401/403: kredity null + "opravneni".
    Jiná chyba: jen kredity_chyba (poslední známé kredity v souboru zůstanou)."""
    try:
        async with httpx.AsyncClient(base_url=ZAKLAD, timeout=timeout_s, follow_redirects=False,
                                     transport=transport) as klient:
            async with klient.stream("GET", CESTA, headers={"xi-api-key": klic, "accept": "application/json"}) as odp:
                if odp.url.host not in POVOLENE_HOSTY:
                    return {"kredity_chyba": "http"}
                if odp.status_code in (401, 403):
                    return {"kredity_zbyva": None, "kredity_limit": None, "obnova": None,
                            "kredity_chyba": "opravneni"}
                if odp.status_code != 200:
                    log.info("kredity: HTTP %d", odp.status_code)
                    return {"kredity_chyba": "http"}
                telo = b""
                async for kus in odp.aiter_bytes():
                    telo += kus
                    if len(telo) > MAX_TELO:
                        return {"kredity_chyba": "format"}
    except httpx.HTTPError:
        return {"kredity_chyba": "sit"}
    try:
        return rozber(telo) | {"kredity_chyba": None}
    except ValueError:
        return {"kredity_chyba": "format"}
