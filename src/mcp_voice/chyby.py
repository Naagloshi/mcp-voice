"""Kódy chyb, maskování tajemství a obal nástrojů, který nikdy nevrátí text výjimky."""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
import sys
import threading
from typing import Any, Awaitable, Callable

log = logging.getLogger("mcp_voice")

# Pevné zprávy pro kódy, které nemají vlastní text z místa vzniku.
ZPRAVY = {
    "INTERNAL_ERROR": "Vnitřní chyba serveru. Podrobnosti (jen typ chyby) jsou v logu serveru.",
    "GUARD_MISSING": "Volání neprověřil strážce (hook). Nic se neodeslalo.",
    "GUARD_MISMATCH": "Razítko strážce nesouhlasí s argumenty volání. Nic se neodeslalo.",
}

_TELEGRAM_TOKEN = re.compile(r"bot\d+:[A-Za-z0-9_-]{20,}")
_OBECNY_TOKEN = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")
_znama_tajemstvi: set[str] = set()
_zamek = threading.Lock()


class HlasChyba(Exception):
    """Chyba s kódem z číselníku a zprávou bez tajemství a bez obsahu textu."""

    def __init__(self, kod: str, zprava: str | None = None, **extra: Any):
        super().__init__(kod)
        self.kod = kod
        self.zprava = zprava or ZPRAVY.get(kod, kod)
        self.extra = extra

    def jako_odpoved(self) -> dict:
        chyba = {"kod": self.kod, "zprava": maskuj(self.zprava)}
        chyba.update({k: v for k, v in self.extra.items() if isinstance(v, (int, float, str, bool))})
        return {"ok": False, "chyba": chyba}


def registruj_tajemstvi(hodnota: str | None) -> None:
    """Hodnotu známou za běhu (klíč, token) bude maskovat log i odpovědi."""
    if hodnota and len(hodnota) >= 8:
        with _zamek:
            _znama_tajemstvi.add(hodnota)


def maskuj(text: Any) -> str:
    s = text if isinstance(text, str) else str(text)
    s = _TELEGRAM_TOKEN.sub("bot[TOKEN]", s)
    s = _OBECNY_TOKEN.sub("[TOKEN]", s)
    with _zamek:
        tajemstvi = sorted(_znama_tajemstvi, key=len, reverse=True)
    for t in tajemstvi:
        s = s.replace(t, "[TAJEMSTVI]")
    return s


class MaskovaciFiltr(logging.Filter):
    """Pojistka: zformátovaný záznam projde maskováním (hlavní ochrana je nelogovat)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            zprava = record.getMessage()
        except Exception:  # noqa: BLE001 – rozbitý formát nesmí shodit log
            zprava = str(record.msg)
        record.msg = maskuj(zprava)
        record.args = None
        if record.exc_info:
            # Traceback nese URL i lokální hodnoty; do logu jen typ výjimky.
            typ = record.exc_info[0].__name__ if record.exc_info[0] else "?"
            record.msg = f"{record.msg} [výjimka {typ}]"
            record.exc_info = None
            record.exc_text = None
        record.stack_info = None
        return True


TICHE_LOGGERY = ("httpx", "httpcore", "hpack", "urllib3", "huggingface_hub", "faster_whisper", "asyncio")


def nastav_logovani(uroven: int = logging.INFO) -> None:
    """Log jen na stderr, s maskováním, hlučné knihovny na WARNING (URL s tokenem)."""
    koren = logging.getLogger()
    for h in list(koren.handlers):
        koren.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(MaskovaciFiltr())
    koren.addHandler(handler)
    koren.setLevel(uroven)
    for jmeno in TICHE_LOGGERY:
        logging.getLogger(jmeno).setLevel(logging.WARNING)

    def _hook(typ, hodnota, tb):  # noqa: ARG001 – traceback se záměrně zahazuje
        sys.stderr.write(f"mcp-voice: neošetřená výjimka {typ.__name__}\n")

    def _vlakno(args):
        sys.stderr.write(f"mcp-voice: neošetřená výjimka ve vlákně {args.exc_type.__name__}\n")

    sys.excepthook = _hook
    threading.excepthook = _vlakno


def bezpecny_nastroj(nazev: str) -> Callable[[Callable[..., Awaitable[dict]]], Callable[..., Awaitable[str]]]:
    """Obal nástroje: HlasChyba -> odpověď s kódem, cokoli jiného -> INTERNAL_ERROR.

    Nikdy str(e), repr(e) ani traceback do odpovědi; do logu jen typ výjimky.
    Výsledek je kompaktní JSON (řetězec), aby se v transkriptu neduplikoval.
    """

    def dekorator(fn):
        @functools.wraps(fn)
        async def obal(*args, **kwargs):
            try:
                vysledek = await fn(*args, **kwargs)
            except HlasChyba as e:
                log.info("nastroj=%s chyba=%s", nazev, e.kod)
                vysledek = e.jako_odpoved()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("nastroj=%s chyba=INTERNAL_ERROR typ=%s", nazev, type(e).__name__)
                vysledek = HlasChyba("INTERNAL_ERROR").jako_odpoved()
            return json.dumps(vysledek, ensure_ascii=False, separators=(",", ":"))

        return obal

    return dekorator
