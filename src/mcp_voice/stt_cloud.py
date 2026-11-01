"""ElevenLabs Scribe v2: použije se jen při výslovném engine="cloud"."""
from __future__ import annotations

import json

import httpx

from .chyby import HlasChyba
from .tts import POVOLENE_HOSTY, ZAKLAD, detail_status, mapuj_chybu

MODEL = "scribe_v2"


async def prepis(klic: str, data: bytes, jazyk: str | None, timeout_s: float,
                 transport: httpx.AsyncBaseTransport | None = None) -> dict:
    form = {"model_id": MODEL, "tag_audio_events": "false"}
    if jazyk:
        form["language_code"] = jazyk
    async with httpx.AsyncClient(base_url=ZAKLAD, timeout=timeout_s, follow_redirects=False,
                                 transport=transport) as klient:
        try:
            odp = await klient.post("/v1/speech-to-text", headers={"xi-api-key": klic}, data=form,
                                    files={"file": ("audio", data, "application/octet-stream")})
        except httpx.TimeoutException:
            raise HlasChyba("ELEVENLABS_ERROR", "ElevenLabs neodpověděl včas (timeout).") from None
        except httpx.HTTPError:
            raise HlasChyba("ELEVENLABS_ERROR", "Spojení s ElevenLabs selhalo.") from None
    if odp.url.host not in POVOLENE_HOSTY:
        raise HlasChyba("ELEVENLABS_ERROR", "Neočekávaný host odpovědi.")
    if odp.status_code != 200:
        raise mapuj_chybu(odp.status_code, detail_status(odp.content))
    try:
        d = json.loads(odp.content)
    except ValueError:
        d = None
    if not isinstance(d, dict) or not isinstance(d.get("text"), str):
        raise HlasChyba("ELEVENLABS_ERROR", "ElevenLabs vrátil nečitelný přepis.")
    jistota = d.get("language_probability")
    return {
        "text": d["text"].strip(),
        "jazyk": d.get("language_code") if isinstance(d.get("language_code"), str) else None,
        "jistota_jazyka": round(float(jistota), 3) if isinstance(jistota, (int, float)) else None,
    }
