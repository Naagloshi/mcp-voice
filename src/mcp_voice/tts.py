"""Klient ElevenLabs TTS: jeden požadavek, PCM 24 kHz s16le mono.

Chyby se mapují na kódy; z odpovědi se bere jen stavový kód a detail.status,
nikdy tělo s textem. Žádné raise_for_status() (text výjimky nese URL a hlavičky).
"""
from __future__ import annotations

import json
import re
from typing import Callable

import httpx

from .chyby import HlasChyba

ZAKLAD = "https://api.elevenlabs.io"
POVOLENE_HOSTY = frozenset({"api.elevenlabs.io"})
VZORKOVANI = 24000
MAX_PCM = 24000 * 2 * 600  # 10 minut zvuku
_STATUS = re.compile(r"^[a-z0-9_]{1,64}$")


def detail_status(telo: bytes) -> str | None:
    try:
        d = json.loads(telo[:8192])
    except ValueError:
        return None
    det = d.get("detail") if isinstance(d, dict) else None
    st = det.get("status") if isinstance(det, dict) else None
    return st if isinstance(st, str) and _STATUS.match(st) else None


def mapuj_chybu(kod_http: int, status: str | None) -> HlasChyba:
    s = status or ""
    if "quota" in s:
        return HlasChyba("ELEVENLABS_QUOTA", "Vyčerpaná kvóta klíče nebo účtu ElevenLabs.", http=kod_http)
    if kod_http == 401:
        return HlasChyba("ELEVENLABS_AUTH", "Klíč ElevenLabs je neplatný, nebo nemá oprávnění pro tento endpoint.",
                         http=kod_http)
    if kod_http == 429:
        return HlasChyba("ELEVENLABS_RATE", "Příliš mnoho souběžných požadavků na ElevenLabs. Zkus to za chvíli.",
                         http=kod_http)
    if "voice" in s:
        return HlasChyba("ELEVENLABS_VOICE", "Hlas není dostupný (neexistuje, nebo není pro tento tarif).",
                         http=kod_http, status=s)
    return HlasChyba("ELEVENLABS_ERROR", f"ElevenLabs vrátil chybu HTTP {kod_http}.", http=kod_http,
                     **({"status": s} if s else {}))


async def syntetizuj(klic: str, voice_id: str, text: str, model: str, jazyk: str | None,
                     voice_settings: dict, timeout_s: float,
                     transport: httpx.AsyncBaseTransport | None = None,
                     pri_id: Callable[[str], None] | None = None) -> tuple[bytes, str | None]:
    """Vrátí (PCM, history-item-id). ID je surová hodnota hlavičky (validuje historie.py), None = neuloženo.

    pri_id dostane ID hned po hlavičce, ještě před čtením zvuku: položka historie existuje i tehdy,
    když pak čtení selže (síť, timeout, MAX_PCM, prázdný zvuk, zrušení).
    """
    telo: dict = {"text": text, "model_id": model}
    if jazyk:
        telo["language_code"] = jazyk
    if voice_settings:
        telo["voice_settings"] = voice_settings
    async with httpx.AsyncClient(base_url=ZAKLAD, timeout=timeout_s, follow_redirects=False,
                                 transport=transport) as klient:
        try:
            async with klient.stream(
                "POST", f"/v1/text-to-speech/{voice_id}/stream",
                params={"output_format": f"pcm_{VZORKOVANI}"},
                headers={"xi-api-key": klic, "accept": "audio/pcm"},
                json=telo,
            ) as odp:
                if odp.url.host not in POVOLENE_HOSTY:
                    raise HlasChyba("ELEVENLABS_ERROR", "Neočekávaný host odpovědi.")
                if odp.status_code != 200:
                    chyba = b""
                    async for kus in odp.aiter_bytes():
                        chyba += kus
                        if len(chyba) > 8192:
                            break
                    raise mapuj_chybu(odp.status_code, detail_status(chyba))
                historie = odp.headers.get("history-item-id")
                if historie is not None and pri_id is not None:
                    pri_id(historie)
                pcm = bytearray()
                async for kus in odp.aiter_bytes():
                    pcm += kus
                    if len(pcm) > MAX_PCM:
                        raise HlasChyba("ELEVENLABS_ERROR", "Odpověď ElevenLabs je delší, než je povoleno.")
        except httpx.TimeoutException:
            raise HlasChyba("ELEVENLABS_ERROR", "ElevenLabs neodpověděl včas (timeout).") from None
        except httpx.HTTPError:
            raise HlasChyba("ELEVENLABS_ERROR", "Spojení s ElevenLabs selhalo.") from None
    if len(pcm) < 2:
        raise HlasChyba("ELEVENLABS_ERROR", "ElevenLabs vrátil prázdný zvuk.")
    if len(pcm) % 2:
        pcm = pcm[:-1]
    return bytes(pcm), historie
