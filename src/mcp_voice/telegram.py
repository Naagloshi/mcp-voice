"""Telegram Bot API: jen metody z METODY, jen host api.telegram.org, bez přesměrování.

Token je součástí cesty URL, proto: URL se skládá jen v _url() a _url_souboru(), odpověď se čte
ze status_code a JSON (žádné raise_for_status), výjimky httpx se nikdy nepřevádí na text.
Žádné setWebhook, deleteWebhook ani getMe; text bez parse_mode, hlasovka bez caption.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable

import httpx

from .chyby import HlasChyba

HOST = "api.telegram.org"
METODY = ("sendVoice", "sendMessage", "getUpdates", "getFile", "deleteMessages")
METODA = METODY[0]
CESTA_SOUBORU = re.compile(r"^[A-Za-z0-9_./-]{1,256}$")
MAX_ODPOVED = 4 * 1024 * 1024
log = logging.getLogger("mcp_voice.telegram")

_ZPRAVY = {
    400: "Telegram odmítl požadavek (např. chat nenalezen; uživatel musí botovi nejdřív napsat /start).",
    401: "Token bota je neplatný.",
    403: "Bot nemá do chatu přístup (zablokovaný nebo odebraný).",
    404: "Token bota je neplatný.",
    409: "Bot má nastavený webhook nebo jiný příjem (getUpdates je obsazené).",
    413: "Hlasovka je pro Telegram příliš velká.",
    429: "Telegram omezuje četnost zpráv. Zkus to za chvíli.",
}


def _url(token: str, metoda: str = METODA) -> str:
    if metoda not in METODY:
        raise ValueError("metoda Telegramu není povolená")
    return f"https://{HOST}/bot{token}/{metoda}"


def over_cestu_souboru(cesta: object) -> str:
    if (not isinstance(cesta, str) or not CESTA_SOUBORU.match(cesta) or ".." in cesta.split("/")
            or cesta.startswith("/")):
        raise HlasChyba("TELEGRAM_ERROR", "Telegram vrátil neplatnou cestu souboru.")
    return cesta


def _url_souboru(token: str, cesta: str) -> str:
    return f"https://{HOST}/file/bot{token}/{over_cestu_souboru(cesta)}"


def _chyba_http(kod: int) -> HlasChyba:
    log.info("telegram http=%d", kod)
    return HlasChyba("TELEGRAM_ERROR", _ZPRAVY.get(kod, f"Telegram vrátil HTTP {kod}."), http=kod)


def _vysledek(odp: httpx.Response):
    if odp.status_code != 200:
        raise _chyba_http(odp.status_code)
    try:
        d = json.loads(odp.content[:MAX_ODPOVED])
    except ValueError:
        d = None
    if not isinstance(d, dict) or d.get("ok") is not True:
        raise HlasChyba("TELEGRAM_ERROR", "Telegram vrátil neplatnou odpověď.")
    return d.get("result")


def _over_chat(vysledek, chat_id: str) -> int | None:
    """R-CFG-3: zpráva musí skončit v soukromém chatu z konfigurace. Vrací message_id."""
    chat = vysledek.get("chat") if isinstance(vysledek, dict) else None
    if not isinstance(chat, dict) or str(chat.get("id")) != str(chat_id) or chat.get("type") != "private":
        log.warning("telegram: odpověď nepotvrdila soukromý chat z konfigurace")
        raise HlasChyba("TELEGRAM_ERROR", "Telegram nepotvrdil doručení do soukromého chatu z konfigurace.")
    mid = vysledek.get("message_id")
    return mid if isinstance(mid, int) and not isinstance(mid, bool) else None


async def _post(url: str, timeout_s: float, transport, **kw) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=False, transport=transport) as klient:
        try:
            return await klient.post(url, **kw)
        except httpx.TimeoutException:
            raise HlasChyba("TELEGRAM_ERROR", "Telegram neodpověděl včas (timeout).", sit=True) from None
        except httpx.HTTPError:
            raise HlasChyba("TELEGRAM_ERROR", "Spojení s Telegramem selhalo.", sit=True) from None


def _odpoved_na(data: dict, reply_to: int | None) -> dict:
    if reply_to is not None:
        data["reply_parameters"] = json.dumps({"message_id": int(reply_to), "allow_sending_without_reply": True})
    return data


async def posli_hlasovku(token: str, chat_id: str, ogg: bytes, delka_s: float, timeout_s: float,
                         protect_content: bool = False,
                         transport: httpx.AsyncBaseTransport | None = None, reply_to: int | None = None) -> int | None:
    data = {"chat_id": chat_id, "duration": str(max(1, round(delka_s)))}
    if protect_content:
        data["protect_content"] = "true"
    soubory = {"voice": ("hlas.ogg", ogg, "audio/ogg")}
    odp = await _post(_url(token, "sendVoice"), timeout_s, transport, data=_odpoved_na(data, reply_to), files=soubory)
    return _over_chat(_vysledek(odp), chat_id)


async def posli_text(token: str, chat_id: str, text: str, timeout_s: float, protect_content: bool = False,
                     transport: httpx.AsyncBaseTransport | None = None, reply_to: int | None = None) -> int | None:
    """sendMessage bez parse_mode, bez náhledu odkazů."""
    data = {"chat_id": chat_id, "text": text, "link_preview_options": json.dumps({"is_disabled": True})}
    if protect_content:
        data["protect_content"] = "true"
    odp = await _post(_url(token, "sendMessage"), timeout_s, transport, data=_odpoved_na(data, reply_to))
    return _over_chat(_vysledek(odp), chat_id)


async def nacti_zpravy(token: str, offset: int | None, timeout_s: int, http_timeout_s: float,
                       transport: httpx.AsyncBaseTransport | None = None) -> list:
    data = {"timeout": str(int(timeout_s)), "allowed_updates": json.dumps(["message"])}
    if offset is not None:
        data["offset"] = str(int(offset))
    odp = await _post(_url(token, "getUpdates"), http_timeout_s, transport, data=data)
    vysledek = _vysledek(odp)
    if not isinstance(vysledek, list):
        raise HlasChyba("TELEGRAM_ERROR", "Telegram vrátil neplatný seznam zpráv.")
    return vysledek


async def cesta_souboru(token: str, file_id: str, timeout_s: float,
                        transport: httpx.AsyncBaseTransport | None = None) -> str:
    odp = await _post(_url(token, "getFile"), timeout_s, transport, data={"file_id": file_id})
    v = _vysledek(odp)
    return over_cestu_souboru(v.get("file_path") if isinstance(v, dict) else None)


async def stahni(token: str, cesta: str, max_bajtu: int, timeout_s: float,
                 transport: httpx.AsyncBaseTransport | None = None) -> bytes:
    """Stažení souboru s limitem velikosti i při čtení streamu, bez přesměrování."""
    url = _url_souboru(token, cesta)
    async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=False, transport=transport) as klient:
        try:
            async with klient.stream("GET", url) as odp:
                if odp.status_code != 200:
                    raise _chyba_http(odp.status_code)
                data = bytearray()
                async for kus in odp.aiter_bytes():
                    data.extend(kus)
                    if len(data) > max_bajtu:
                        raise HlasChyba("FILE_TOO_LARGE", "Hlasovka je větší než limit.")
                return bytes(data)
        except httpx.TimeoutException:
            raise HlasChyba("TELEGRAM_ERROR", "Telegram neodpověděl včas (timeout).", sit=True) from None
        except httpx.HTTPError:
            raise HlasChyba("TELEGRAM_ERROR", "Spojení s Telegramem selhalo.", sit=True) from None


async def smaz_zpravy(token: str, chat_id: str, ids: list[int], timeout_s: float,
                      transport: httpx.AsyncBaseTransport | None = None) -> bool:
    if not 1 <= len(ids) <= 100:
        raise ValueError("deleteMessages: 1–100 ID")
    odp = await _post(_url(token, "deleteMessages"), timeout_s, transport,
                      data={"chat_id": chat_id, "message_ids": json.dumps([int(x) for x in ids])})
    return _vysledek(odp) is True


class Klient:
    """Telegram pro příjem: token se čte při každém volání (keyring), nedrží se v atributu."""

    def __init__(self, token: Callable[[], str], chat_id: str, timeout_s: float, protect_content: bool = False,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._token = token
        self.chat_id = chat_id
        self.timeout_s = timeout_s
        self.protect_content = protect_content
        self.transport = transport

    async def zpravy(self, offset: int | None, timeout_s: int) -> list:
        return await nacti_zpravy(self._token(), offset, timeout_s, timeout_s + 10, self.transport)

    async def text(self, text: str, reply_to: int | None = None) -> int | None:
        return await posli_text(self._token(), self.chat_id, text, self.timeout_s, self.protect_content,
                                self.transport, reply_to)

    async def hlas(self, ogg: bytes, delka_s: float, reply_to: int | None = None) -> int | None:
        return await posli_hlasovku(self._token(), self.chat_id, ogg, delka_s, self.timeout_s, self.protect_content,
                                    self.transport, reply_to)

    async def soubor(self, file_id: str, max_bajtu: int) -> bytes:
        token = self._token()
        cesta = await cesta_souboru(token, file_id, self.timeout_s, self.transport)
        return await stahni(token, cesta, max_bajtu, self.timeout_s, self.transport)

    async def smaz(self, ids: list[int]) -> bool:
        return await smaz_zpravy(self._token(), self.chat_id, ids, self.timeout_s, self.transport)
