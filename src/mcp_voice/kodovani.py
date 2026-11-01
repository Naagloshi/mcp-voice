"""Kódování PCM (s16le 24 kHz mono) přes systémový ffmpeg, vše v paměti přes roury."""
from __future__ import annotations

import asyncio
import shutil

from .chyby import HlasChyba

VSTUP_PCM = ["-f", "s16le", "-ar", "24000", "-ac", "1", "-i", "pipe:0"]
SPOLECNE = ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error", "-protocol_whitelist", "pipe"]
OGG_OPUS = ["-c:a", "libopus", "-b:a", "32k", "-ar", "48000", "-application", "voip", "-f", "ogg", "pipe:1"]
MP3 = ["-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3", "pipe:1"]
MAX_VYSTUP = 64 * 1024 * 1024


def delka_s(pcm: bytes) -> float:
    return len(pcm) / (24000 * 2)


async def _ffmpeg(argv: list[str], vstup: bytes, timeout_s: float) -> bytes:
    if shutil.which("ffmpeg") is None:
        raise HlasChyba("ENCODER_ERROR", "Chybí program ffmpeg.")
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
    except OSError:
        raise HlasChyba("ENCODER_ERROR", "ffmpeg nejde spustit.") from None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(vstup), timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise HlasChyba("ENCODER_ERROR", "ffmpeg nedoběhl včas.") from None
    if proc.returncode != 0 or not out or len(out) > MAX_VYSTUP:
        raise HlasChyba("ENCODER_ERROR", f"ffmpeg selhal (kód {proc.returncode}).")
    return out


async def na_ogg_opus(pcm: bytes, timeout_s: float = 30.0) -> bytes:
    return await _ffmpeg(SPOLECNE + VSTUP_PCM + OGG_OPUS, pcm, timeout_s)


async def na_mp3(pcm: bytes, timeout_s: float = 30.0) -> bytes:
    return await _ffmpeg(SPOLECNE + VSTUP_PCM + MP3, pcm, timeout_s)
