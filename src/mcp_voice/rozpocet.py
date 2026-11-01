"""Denní rozpočet znaků TTS (soubor + flock, sdílený všemi procesy) a pojistky proti smyčce.

Znaky se počítají pesimisticky před odesláním: selhání po odeslání je nevrací.
Limit volání za minutu a deduplikace jsou jen v paměti procesu (bez hashe textu na disku).
"""
from __future__ import annotations

import collections
import datetime as dt
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path

from .chyby import HlasChyba
from .stavy import zajisti_adresar


class Rozpocet:
    def __init__(self, stav: Path, znaku_za_den: int, volani_za_minutu: int, duplicita_s: float):
        self.soubor = stav / "rozpocet.json"
        self.stav = stav
        self.znaku_za_den = znaku_za_den
        self.volani_za_minutu = volani_za_minutu
        self.duplicita_s = duplicita_s
        self._volani: collections.deque[float] = collections.deque()
        self._posledni: dict[str, float] = {}

    @staticmethod
    def _den(ted: float) -> str:
        return dt.datetime.fromtimestamp(ted).strftime("%Y-%m-%d")

    def _s_zamkem(self, fn):
        zajisti_adresar(self.stav)
        fd = os.open(self.soubor, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            raw = os.pread(fd, 4096, 0)
            try:
                d = json.loads(raw) if raw else {}
            except ValueError:
                d = {}
            if not isinstance(d, dict):
                d = {}
            vysledek, novy = fn(d)
            if novy is not None:
                data = json.dumps(novy).encode()
                os.ftruncate(fd, 0)
                os.pwrite(fd, data, 0)
                os.fsync(fd)
            return vysledek
        finally:
            os.close(fd)

    def zbyva(self, ted: float | None = None) -> int:
        ted = time.time() if ted is None else ted
        den = self._den(ted)

        def fn(d):
            pouzito = d.get("znaku", 0) if d.get("den") == den else 0
            return max(0, self.znaku_za_den - int(pouzito)), None

        return self._s_zamkem(fn)

    def zkontroluj_smycku(self, text: str, ted: float | None = None) -> str:
        """Limit volání za minutu a stejný text v krátké době. Vrací otisk textu (jen v paměti)."""
        ted = time.time() if ted is None else ted
        while self._volani and ted - self._volani[0] > 60:
            self._volani.popleft()
        if len(self._volani) >= self.volani_za_minutu:
            raise HlasChyba("RATE_LIMIT", f"Víc než {self.volani_za_minutu} hlasových odpovědí za minutu. Počkej.")
        otisk = hashlib.sha256(text.encode("utf-8")).hexdigest()
        for k, cas in list(self._posledni.items()):
            if ted - cas > self.duplicita_s:
                del self._posledni[k]
        if otisk in self._posledni:
            raise HlasChyba("DUPLICATE", "Stejný text už zazněl před chvílí. Neopakuj ho; při chybě se zeptej uživatele.")
        return otisk

    def rezervuj(self, znaku: int, otisk: str, ted: float | None = None) -> int:
        """Odečte znaky z denního rozpočtu, nebo vyhodí DAILY_LIMIT. Vrací zbytek."""
        ted = time.time() if ted is None else ted
        den = self._den(ted)

        def fn(d):
            pouzito = int(d.get("znaku", 0)) if d.get("den") == den else 0
            if pouzito + znaku > self.znaku_za_den:
                raise HlasChyba("DAILY_LIMIT",
                                f"Denní limit {self.znaku_za_den} znaků by se překročil "
                                f"(zbývá {max(0, self.znaku_za_den - pouzito)}, text má {znaku}). Nic se neodeslalo.")
            pouzito += znaku
            return self.znaku_za_den - pouzito, {"den": den, "znaku": pouzito}

        zbyva = self._s_zamkem(fn)
        self._volani.append(ted)
        self._posledni[otisk] = ted
        return zbyva
