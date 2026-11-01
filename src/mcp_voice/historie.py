"""Mazání položky historie ElevenLabs po TTS: DELETE /v1/history/{id}, krátký timeout, nejvýš jedno opakování.

ElevenLabs ukládá text i audio každého TTS do historie účtu (hlavička history-item-id). Server smaže
jen položku, kterou sám právě vytvořil; na jinou historii účtu nesahá. Klíč potřebuje oprávnění
„History: Write“; bez něj 401/403 -> "nesmazano:opravneni". Položka nemusí být hned po konci streamu
uložená, proto se DELETE při 404/422 jednou zopakuje po PRODLEVA_S. Tělo odpovědi se nečte ani neloguje,
ID ani klíč se nelogují (u neplatného ID jen délka a třídy znaků). Selhání nikdy neovlivní výsledek rekni.
"""
from __future__ import annotations

import asyncio
import logging
import re

import httpx

from .tts import POVOLENE_HOSTY, ZAKLAD

log = logging.getLogger("mcp_voice.historie")

TIMEOUT_S = 5.0
PRODLEVA_S = 3.0                   # čekání před jediným opakováním
OPAKOVAT_PRI = frozenset({404, 422})
# ID jde do cesty URL: jen znaky bezpečné v segmentu cesty, bez tečky (žádné "." ani "..").
ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

NEULOZENO = "neulozeno"                # odpověď TTS nenesla history-item-id
MAZE_SE = "maze_se"                    # DELETE běží na pozadí
SMAZANO = "smazano"
OPRAVNENI = "nesmazano:opravneni"      # klíč bez „History: Write“ (401/403)
NEPLATNE_ID = "nesmazano:neplatne_id"  # hlavička neprošla ID, DELETE se neposlal
SIT = "nesmazano:sit"                  # síť nebo timeout
CHYBA = "nesmazano:chyba"              # neočekávaný host odpovědi, neznámá výjimka
VYPNUTO = "nesmazano:vypnuto"          # elevenlabs.mazat_historii: false
STAVY = (NEULOZENO, MAZE_SE, SMAZANO, OPRAVNENI, NEPLATNE_ID, SIT, CHYBA, VYPNUTO)  # = status.STAVY_HISTORIE
HTTP = re.compile(r"nesmazano:http_[1-5][0-9]{2}")  # jiný HTTP kód; = status.HISTORIE_HTTP (hlídá test)
_ZNAKY = (("-", "'-'"), ("_", "'_'"), (".", "'.'"), (",", "','"), ("/", "'/'"), ("%", "'%'"), (":", "':'"),
          ("=", "'='"), ("+", "'+'"), (" ", "mezera"))


def platne_id(v: object) -> bool:
    return isinstance(v, str) and ID.fullmatch(v) is not None


def platny_stav(v: object) -> bool:
    return isinstance(v, str) and (v in STAVY or HTTP.fullmatch(v) is not None)


def http_stav(kod: int) -> str:
    stav = f"nesmazano:http_{kod}"
    return stav if HTTP.fullmatch(stav) else CHYBA


def popis_id(v: object) -> str:
    """Diagnostika neplatného ID bez hodnoty: typ, délka a třídy znaků."""
    if not isinstance(v, str):
        return f"typ {type(v).__name__}"
    tridy = [n for n, ok in (("písmena", any(c.isascii() and c.isalpha() for c in v)),
                             ("číslice", any(c.isascii() and c.isdigit() for c in v))) if ok]
    tridy += [n for z, n in _ZNAKY if z in v]
    zname = {z for z, _ in _ZNAKY}
    if any(c.isascii() and c.isprintable() and not c.isalnum() and c not in zname for c in v):
        tridy.append("jiné ASCII")
    if any(c.isascii() and not c.isprintable() for c in v):
        tridy.append("řídicí")
    if any(not c.isascii() for c in v):
        tridy.append("ne-ASCII")
    return f"délka {len(v)}, znaky: {', '.join(tridy) or 'žádné'}"


def stav_po_tts(item_id: str | None, mazat: bool) -> str:
    """Stav hned po TTS (bez sítě): MAZE_SE znamená, že se má spustit smaz()."""
    if item_id is None:
        return NEULOZENO
    if not mazat:
        return VYPNUTO
    if not platne_id(item_id):
        log.warning("historie: neplatné ID z hlavičky (%s), DELETE se neposlal", popis_id(item_id))
        return NEPLATNE_ID
    return MAZE_SE


async def _pockej(s: float) -> None:
    await asyncio.sleep(s)


async def smaz(klic: str, item_id: str, transport: httpx.AsyncBaseTransport | None = None,
               timeout_s: float = TIMEOUT_S, prodleva_s: float | None = None) -> str:
    """DELETE, při 404/422 jednou znovu po prodlevě. Vrátí stav ze STAVY nebo http_stav(); výjimku httpx nevyhodí."""
    if not platne_id(item_id):
        log.warning("historie: neplatné ID (%s), DELETE se neposlal", popis_id(item_id))
        return NEPLATNE_ID
    prodleva = PRODLEVA_S if prodleva_s is None else prodleva_s
    try:
        async with httpx.AsyncClient(base_url=ZAKLAD, timeout=timeout_s, follow_redirects=False,
                                     transport=transport) as klient:
            for pokus in (1, 2):
                async with klient.stream("DELETE", f"/v1/history/{item_id}",
                                         headers={"xi-api-key": klic, "accept": "application/json"}) as odp:
                    if odp.url.host not in POVOLENE_HOSTY:
                        log.warning("historie: neočekávaný host odpovědi")
                        return CHYBA
                    kod = odp.status_code
                if kod in (200, 204):
                    if pokus > 1:
                        log.info("historie: smazáno na %d. pokus", pokus)
                    return SMAZANO
                if kod in (401, 403):
                    log.warning("historie: HTTP %d, klíči chybí oprávnění History: Write", kod)
                    return OPRAVNENI
                if kod in OPAKOVAT_PRI and pokus == 1:
                    log.info("historie: HTTP %d, opakování za %.0f s", kod, prodleva)
                    await _pockej(prodleva)
                    continue
                log.warning("historie: HTTP %d (pokus %d), položka zůstala v historii", kod, pokus)
                return http_stav(kod)
    except httpx.HTTPError as e:
        log.warning("historie: síť (%s), položka zůstala v historii", type(e).__name__)
        return SIT
    return CHYBA  # pragma: no cover – smyčka vždy vrátí
