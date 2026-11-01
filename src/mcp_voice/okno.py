"""Okno příjmu z Telegramu: povely (5.1), soubory okna, aktivní proces, zámek pollingu, úklid.

Stavový automat řídí server (prijem.Prijem); tady jsou čisté funkce a práce se soubory, které
sdílí i hook UserPromptSubmit, status bar a CLI. Stav: zavreno | overovani | otevreno.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import puvod

STAVY = ("zavreno", "overovani", "otevreno")
DUVODY = ("cas", "necinnost", "stop_terminal", "stop_telegram", "clear", "presun", "chyba", "allowlist", "limit",
          "bash_bez_obalu", "konfigurace_agenta",
          "hook_rozsahu_nebezi", "konec_serveru")
REZIM_ALIAS = {"cteni": "cteni", "prace": "prace", "plny": "plny"}
STARI_S = 48 * 3600
POPIS_REZIMU = {"cs": {"cteni": "čtení", "prace": "práce", "plny": "plný"},
                "en": {"cteni": "read", "prace": "work", "plny": "full"}}


class ChybaPovelu(ValueError):
    def __init__(self, kod: str, zprava: str):
        super().__init__(kod)
        self.kod = kod
        self.zprava = zprava


@dataclass(frozen=True)
class Povel:
    akce: str  # start | stop | stav
    minut: int | None = None
    rezim: str | None = None


def je_povel(prompt: object, prefix: str) -> bool:
    if not isinstance(prompt, str):
        return False
    n = puvod.normalizuj(prompt)
    p = puvod.normalizuj(prefix)
    return n == p or n.startswith(p + " ")


# Jednotky času v povelu (po normalizaci bez diakritiky): m… = minuty, h… = hodiny.
JEDNOTKY = r"m|min|minut[auy]?|minutes?|h|hod|hodin[auy]?|hours?"


def rozeber_povel(prompt: str, prefix: str, vychozi_min: int, max_min: int, vychozi_rezim: str) -> Povel:
    """Gramatika: <prefix> start [ČAS] [REŽIM] | stop | stav. Jiný tvar = ChybaPovelu s nápovědou."""
    n = puvod.normalizuj(prompt)
    p = re.escape(puvod.normalizuj(prefix))
    m = re.fullmatch(p + r"\s+(start(?:\s+(\d{1,3})\s*(%s))?(?:\s+(cteni|prace|plny))?|stop|stav)\s*" % JEDNOTKY, n)
    napoveda = "%s start [30m|90min|2h] [cteni|prace|plny], %s stop, %s stav" % (prefix, prefix, prefix)
    if not m:
        raise ChybaPovelu("povel_neplatny", "Neplatný povel. Tvar: " + napoveda)
    akce = m.group(1).split()[0]
    if akce != "start":
        return Povel(akce)
    minut = vychozi_min
    if m.group(2):
        minut = int(m.group(2)) * (60 if m.group(3).startswith("h") else 1)
        if minut > max_min:
            raise ChybaPovelu("povel_nad_strop", "Okno nejvýš %d min (max %d h)." % (max_min, max_min // 60))
        if minut < 5:
            raise ChybaPovelu("povel_pod_minimum", "Okno nejméně 5 min.")
    return Povel("start", minut, m.group(4) or vychozi_rezim)


# ---------------------------------------------------------------- soubory

def adresar(stav: Path) -> Path:
    return Path(puvod.adresar(stav))


def cesta(stav: Path, druh: str, klic: str, pripona: str = ".json") -> Path:
    return Path(puvod.soubor(stav, druh, klic, pripona))


def nacti_okno(stav: Path, klic: str) -> dict:
    return puvod.cti_tise(str(cesta(stav, "okno", klic)), {})


def uloz_okno(stav: Path, klic: str, okno: dict) -> None:
    puvod.zapis(str(cesta(stav, "okno", klic)), okno)


def hhmm(t: float | None) -> str:
    return "--:--" if t is None else dt.datetime.fromtimestamp(t).strftime("%H:%M")


def popis(okno: dict, ted: float, jazyk: str = "cs") -> str:
    """Jednořádkový stav okna pro systemMessage, status a nástroj stav (bez kontextů a souhlasů)."""
    rezim = POPIS_REZIMU.get(jazyk, POPIS_REZIMU["cs"]).get(okno.get("rezim"), "?")
    if okno.get("stav") == "otevreno" and puvod.okno_plati(okno, okno.get("session_id"), ted):
        if jazyk == "en":
            return "Telegram window open until %s, mode %s." % (hhmm(okno.get("konec")), rezim)
        return "Okno Telegramu otevřené do %s, režim %s." % (hhmm(okno.get("konec")), rezim)
    if okno.get("stav") == "overovani":
        return "Telegram window is being verified." if jazyk == "en" else "Okno Telegramu se ověřuje."
    duvod = okno.get("duvod_zavreni")
    if jazyk == "en":
        return "Telegram window closed" + (" (%s)." % duvod if duvod else ".")
    return "Okno Telegramu zavřené" + (" (%s)." % duvod if duvod else ".")


def zadosti(stav: Path, claude_pid: int, claude_start: str) -> list[tuple[Path, dict]]:
    """Žádosti (zadost-<session>.json) tohoto procesu claude; cizí se nechají být."""
    adr = adresar(stav)
    try:
        jmena = sorted(os.listdir(adr))
    except OSError:
        return []
    vysledek = []
    for j in jmena:
        if not (j.startswith("zadost-") and j.endswith(".json")):
            continue
        p = adr / j
        d = puvod.cti_tise(str(p), {})
        if d.get("claude_pid") == claude_pid and str(d.get("claude_start")) == str(claude_start):
            vysledek.append((p, d))
    return vysledek


class PollZamek:
    """flock na telegram/poll.lock: polluje jen proces z aktivni.json."""

    def __init__(self, stav: Path):
        self.cesta = adresar(stav) / "poll.lock"
        self.fd: int | None = None

    def zkus(self) -> bool:
        if self.fd is not None:
            return True
        puvod.zajisti_adresar(str(self.cesta.parent))
        fd = os.open(self.cesta, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        self.fd = fd
        return True

    def uvolni(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def uklid(stav: Path, ted: float, vlastni_klic: str | None = None) -> None:
    """Okna mrtvých procesů zavřít (za ně), soubory starší 48 h smazat (kromě trvalých)."""
    adr = adresar(stav)
    try:
        jmena = os.listdir(adr)
    except OSError:
        return
    for j in jmena:
        p = adr / j
        m = re.match(r"^okno-(\d+)-(\d+)\.json$", j)
        if m and m.group(0) != "okno-%s.json" % vlastni_klic:
            okno = puvod.cti_tise(str(p), {})
            if okno.get("stav") != "zavreno" and not puvod.zije(int(m.group(1)), m.group(2)):
                okno.update(stav="zavreno", duvod_zavreni="konec_serveru", zavreno_v=ted)
                try:
                    puvod.zapis(str(p), okno)
                except OSError:
                    pass
                continue
        trvale = j in ("aktivni.json", "allowlist-otisk.json", "retence.jsonl", "poll.lock", "pojistka-vypnuta")
        if trvale or j == "prichozi" or (vlastni_klic and vlastni_klic in j):
            continue
        try:
            if ted - os.lstat(p).st_mtime > STARI_S:
                os.unlink(p)
        except OSError:
            pass
