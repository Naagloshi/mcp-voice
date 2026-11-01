"""Společné fixtures. Žádná síť: HTTP jen přes httpx.MockTransport, tajemství jsou falešná."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from mcp_voice import config, server, strazce
from mcp_voice.cil import StavPc

FALESNY_TOKEN = "123456789:AAFakeTokenFakeTokenFakeToken_xyz-12"
FALESNY_KLIC = "sk_fakeelevenlabskey0123456789abcdef"
VOICE = "AbCdEfGhIjKlMnOpQrSt"
CHAT = "424242"
HISTORIE_ID = "Hx9FakeHistoryItem01"  # tvar ID položky historie (fiktivní)
# Tvar odpovědi GET /v1/user/subscription (zkrácený, fiktivní hodnoty).
PREDPLATNE = {"tier": "starter", "character_count": 5388, "character_limit": 30000,
              "next_character_count_reset_unix": 1767225600, "status": "active", "currency": "usd",
              "voice_limit": 10}


@dataclass
class FalesnaPlocha:
    zamek: bool | None = False
    necinnost: float | None = 1.0
    okno_trida: str | None = None
    volani: list = field(default_factory=list)

    def zamceno(self):
        return self.zamek

    def stav(self, s_oknem=False):
        return StavPc(self.zamek, self.necinnost, self.okno_trida if s_oknem else None)


@dataclass
class FalesnyPrehravac:
    pridano: list = field(default_factory=list)
    je: bool = True

    def dostupny(self):
        return self.je

    def pridej(self, pcm):
        self.pridano.append(len(pcm))
        return len(self.pridano)

    def zastav(self):
        n = len(self.pridano)
        self.pridano.clear()
        return n


class FalesnyLokalni:
    def __init__(self, vysledek=None, chyba=None):
        self.vysledek = vysledek or {"text": "ahoj", "jazyk": "cs", "jistota_jazyka": 0.99, "trvani_ms": 5,
                                     "zarizeni": "cpu"}
        self.chyba = chyba
        self.volano = 0

    async def prepis(self, f32, jazyk):
        self.volano += 1
        if self.chyba:
            raise self.chyba
        return self.vysledek


class Sit:
    """MockTransport, který zaznamenává požadavky a vrací odpovědi podle hostu."""

    def __init__(self):
        self.pozadavky: list[httpx.Request] = []
        self.el = lambda req: httpx.Response(200, content=b"\x01\x00" * 24000,
                                             headers={"history-item-id": HISTORIE_ID})
        self.tg = lambda req: httpx.Response(200, json={"ok": True, "result": {
            "chat": {"id": int(CHAT), "type": "private"}}})
        self.sub = lambda req: httpx.Response(200, json=PREDPLATNE)
        self.hist = lambda req: httpx.Response(200, json={"status": "ok"})

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.pozadavky.append(req)
        if req.url.host == "api.elevenlabs.io" and req.url.path == "/v1/user/subscription":
            return self.sub(req)
        if req.url.host == "api.elevenlabs.io" and req.url.path.startswith("/v1/history/"):
            return self.hist(req)
        if req.url.host == "api.elevenlabs.io":
            return self.el(req)
        if req.url.host == "api.telegram.org":
            return self.tg(req)
        return httpx.Response(599)

    @property
    def transport(self):
        return httpx.MockTransport(self)


def zakladni_data(tmp_path: Path, **zmeny) -> dict:
    (tmp_path / "uploads").mkdir(exist_ok=True)
    d = {
        "elevenlabs": {"hlas": "test", "hlasy": {"test": VOICE}, "data_use_vypnuto": True},
        "telegram": {"chat_id": CHAT},
        "limity": {"znaku_za_den": 10000},
        "stt": {"povolene_adresare": [str(tmp_path / "uploads")], "model_cesta": str(tmp_path / "model")},
        "cesty": {"stav": str(tmp_path / "stav"), "vystup_adresar": str(tmp_path / "vystup")},
    }
    for sekce, hodnoty in zmeny.items():
        if isinstance(hodnoty, dict):
            d.setdefault(sekce, {}).update(hodnoty)
        else:
            d[sekce] = hodnoty
    return d


def tajemstvi(sluzba, klic):
    assert sluzba == "mcp-voice"
    return {"elevenlabs:api_key": FALESNY_KLIC, "telegram:bot_token": FALESNY_TOKEN}.get(klic)


@pytest.fixture
def sit():
    return Sit()


@pytest.fixture
def plocha():
    return FalesnaPlocha()


@pytest.fixture
def prehravac():
    return FalesnyPrehravac()


@pytest.fixture
def udelej_sluzby(tmp_path, sit, plocha, prehravac):
    def _udelej(lokalni=None, **zmeny):
        kfg = config.z_dat(zakladni_data(tmp_path, **zmeny))
        return server.Sluzby.z_konfigurace(kfg, plocha=plocha, prehravac=prehravac,
                                           lokalni=lokalni or FalesnyLokalni(), claude_pid=None,
                                           cteni_tajemstvi=tajemstvi, transport=sit.transport)
    return _udelej


def razitko(stav: Path, tool_use_id: str, nastroj: str, argumenty: dict, cas: float | None = None) -> Path:
    """Razítko tak, jak ho zapisuje hook strážce."""
    adr = strazce.adresar(stav)
    adr.mkdir(parents=True, exist_ok=True, mode=0o700)
    p = adr / f"{tool_use_id}.json"
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"v": 1, "nastroj": nastroj, "sha256": strazce.kanonicky_hash(argumenty),
                   "cas": time.time() if cas is None else cas}, fh)
    return p
