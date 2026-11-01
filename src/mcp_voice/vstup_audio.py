"""Nedůvěryhodné audio pro přepis: bezpečné otevření, kontrola formátu, dekódování.

- Cesta: realpath + commonpath proti allowlistu, otevření O_NOFOLLOW, znovu kontrola
  skutečné cesty deskriptoru, jen běžný soubor, velikost z fstat. Čte se jednou do paměti.
- ffprobe a ffmpeg čtou tytéž bajty z memfd přes protokol fd (jiné protokoly zakázané,
  takže playlist HLS ani concat nesáhne na jiný soubor nebo síť), s limity CPU a paměti.
- Dekóduje systémový ffmpeg s pevným demuxerem na f32le 16 kHz mono; PyAV se vstupu nedotkne.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from .chyby import HlasChyba

# format_name z ffprobe -> demuxer pro dekódování
FORMATY = {
    "ogg": "ogg",
    "mp3": "mp3",
    "wav": "wav",
    "flac": "flac",
    "mov,mp4,m4a,3gp,3g2,mj2": "mov",
    "matroska,webm": "matroska",
}
PROBESIZE = "5000000"
ANALYZEDURATION = "10000000"
LIMIT_PAMETI = str(4 * 1024 ** 3)


def normalizuj_povolene(adresare: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(os.path.realpath(os.path.expanduser(a)) for a in adresare)


def _pod(cesta: str, povolene: tuple[str, ...]) -> bool:
    for p in povolene:
        try:
            if os.path.commonpath([cesta, p]) == p:
                return True
        except ValueError:
            continue
    return False


def nacti_soubor(cesta: object, povolene: tuple[str, ...], max_bajtu: int) -> bytes:
    if not isinstance(cesta, str) or not cesta or "\x00" in cesta or not os.path.isabs(cesta):
        raise HlasChyba("FILE_NOT_ALLOWED", "Cesta musí být absolutní.")
    real = os.path.realpath(cesta)
    if not _pod(real, povolene):
        raise HlasChyba("FILE_NOT_ALLOWED", "Soubor neleží v povoleném adresáři.")
    try:
        fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:
        raise HlasChyba("FILE_NOT_FOUND", "Soubor neexistuje.") from None
    except OSError:
        raise HlasChyba("FILE_NOT_ALLOWED", "Soubor nejde bezpečně otevřít.") from None
    try:
        skutecna = os.readlink(f"/proc/self/fd/{fd}")
        if not _pod(skutecna, povolene):
            raise HlasChyba("FILE_NOT_ALLOWED", "Soubor neleží v povoleném adresáři.")
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise HlasChyba("FILE_NOT_ALLOWED", "Nejde o běžný soubor.")
        if st.st_size > max_bajtu:
            raise HlasChyba("FILE_TOO_LARGE", f"Soubor má {st.st_size} B, limit je {max_bajtu} B.")
        if st.st_size == 0:
            raise HlasChyba("AUDIO_UNREADABLE", "Soubor je prázdný.")
        kusy = []
        zbyva = st.st_size
        while zbyva > 0:
            kus = os.read(fd, min(zbyva, 1024 * 1024))
            if not kus:
                break
            kusy.append(kus)
            zbyva -= len(kus)
        data = b"".join(kusy)
        if len(data) > max_bajtu:
            raise HlasChyba("FILE_TOO_LARGE", f"Soubor je větší než limit {max_bajtu} B.")
        return data
    finally:
        os.close(fd)


def _omez(argv: list[str], cpu_s: int) -> list[str]:
    if shutil.which("prlimit"):
        return ["prlimit", f"--cpu={cpu_s}", f"--as={LIMIT_PAMETI}", "--"] + argv
    return argv


async def _spust_nad_bajty(argv_bez_fd, data: bytes, timeout_s: float, cpu_s: int,
                           max_vystup: int, obal: list[str] | None = None) -> tuple[int, bytes]:
    fd = os.memfd_create("mcp-voice-audio", os.MFD_CLOEXEC)
    try:
        os.write(fd, data)
        os.lseek(fd, 0, os.SEEK_SET)
        argv = _omez(list(obal or []) + argv_bez_fd(fd), cpu_s)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, pass_fds=(fd,))
        except OSError:
            raise HlasChyba("AUDIO_UNREADABLE", "ffmpeg/ffprobe nejde spustit.") from None
        try:
            out = bytearray()

            async def cti():
                while True:
                    kus = await proc.stdout.read(1024 * 1024)
                    if not kus:
                        return
                    out.extend(kus)
                    if len(out) > max_vystup:
                        proc.kill()
                        return

            await asyncio.wait_for(cti(), timeout_s)
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise HlasChyba("AUDIO_UNREADABLE", "Zpracování audia nedoběhlo včas.") from None
        if len(out) > max_vystup:
            raise HlasChyba("AUDIO_TOO_LONG", "Dekódované audio je delší, než je povoleno.")
        return proc.returncode, bytes(out)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class InfoAudia:
    format_name: str
    demuxer: str
    delka_s: float | None


async def over_format(data: bytes, max_s: float, timeout_s: float = 15.0, obal: list[str] | None = None,
                      kodek: str | None = None) -> InfoAudia:
    """kodek = požadovaný kodek všech zvukových stop (např. opus pro hlasovku z Telegramu)."""
    def argv(fd):
        return ["ffprobe", "-v", "error", "-protocol_whitelist", "fd", "-probesize", PROBESIZE,
                "-analyzeduration", ANALYZEDURATION, "-fd", str(fd),
                "-show_entries", "format=format_name,duration:stream=codec_type,codec_name", "-of", "json", "-i", "fd:"]

    kod, out = await _spust_nad_bajty(argv, data, timeout_s, cpu_s=10, max_vystup=256 * 1024, obal=obal)
    try:
        d = json.loads(out) if kod == 0 else None
    except ValueError:
        d = None
    fmt = (d or {}).get("format") or {}
    jmeno = fmt.get("format_name")
    if not isinstance(jmeno, str) or jmeno not in FORMATY:
        raise HlasChyba("AUDIO_UNREADABLE", "Soubor není audio v podporovaném formátu (ogg, mp3, wav, flac, m4a, webm).")
    streamy = (d or {}).get("streams") or []
    if not any(isinstance(s, dict) and s.get("codec_type") == "audio" for s in streamy):
        raise HlasChyba("AUDIO_UNREADABLE", "Soubor neobsahuje zvukovou stopu.")
    if kodek is not None and not all(isinstance(s, dict) and s.get("codec_type") == "audio"
                                     and s.get("codec_name") == kodek for s in streamy):
        raise HlasChyba("AUDIO_UNREADABLE", "Audio nemá očekávaný kodek.")
    try:
        delka = float(fmt.get("duration")) if fmt.get("duration") not in (None, "N/A") else None
    except (TypeError, ValueError):
        delka = None
    if delka is not None and delka > max_s:
        raise HlasChyba("AUDIO_TOO_LONG", f"Nahrávka má {delka:.0f} s, limit je {max_s:.0f} s.")
    return InfoAudia(jmeno, FORMATY[jmeno], delka)


async def dekoduj_f32_16k(data: bytes, info: InfoAudia, max_s: float, timeout_s: float = 60.0,
                          obal: list[str] | None = None, dekoder: str | None = None) -> bytes:
    """f32le 16 kHz mono (vstup pro Whisper). dekoder = pevný dekodér (např. libopus)."""
    def argv(fd):
        return ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error", "-nostdin",
                "-protocol_whitelist", "fd", "-probesize", PROBESIZE, "-analyzeduration", ANALYZEDURATION,
                "-fd", str(fd), "-f", info.demuxer] + (["-c:a", dekoder] if dekoder else []) + ["-i", "fd:", "-vn", "-sn", "-dn",
                "-t", str(int(max_s) + 1), "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"]

    max_vystup = int((max_s + 2) * 16000 * 4)
    kod, out = await _spust_nad_bajty(argv, data, timeout_s, cpu_s=int(timeout_s), max_vystup=max_vystup, obal=obal)
    if kod != 0 or len(out) < 4 * 1600:  # méně než 0,1 s
        raise HlasChyba("AUDIO_UNREADABLE", "Audio nejde dekódovat.")
    if len(out) / (16000 * 4) > max_s + 0.5:  # ffprobe délku neznal, ffmpeg ji ořízl na max_s + 1
        raise HlasChyba("AUDIO_TOO_LONG", f"Nahrávka je delší než limit {max_s:.0f} s.")
    return out[: len(out) - len(out) % 4]


def povolene_cesty(adresare: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(p for p in normalizuj_povolene(adresare) if Path(p).is_absolute())
