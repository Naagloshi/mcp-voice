"""Stav hlasu pro status bar: <cesty.stav>/status.json a příkaz `mcp-voice-status`.

status.json (0600, zápis atomicky pod zámkem status.lock) nese jen čísla a časy:
  v, cas            verze formátu, čas posledního zápisu (ISO 8601)
  den, znaku_dnes, limit_den
                    denní rozpočet znaků (zapisuje rekni a stav, bez sítě)
  kredity_zbyva, kredity_limit, obnova, kredity_cas, kredity_chyba
                    poslední známý zůstatek kreditů ElevenLabs (zapisuje rekni, viz kredity.py);
                    kredity_cas = čas odpovědi dotazu, výsledek staršího dotazu novější nepřepíše;
                    kredity_chyba: null | opravneni | vypnuto | sit | http | format
  historie, historie_cas
                    položka historie ElevenLabs z posledního rekni (viz historie.py) a čas zápisu;
                    historie: neulozeno | maze_se | smazano | nesmazano:opravneni | nesmazano:neplatne_id
                    | nesmazano:sit | nesmazano:http_<kód> | nesmazano:chyba | nesmazano:vypnuto
Žádné ID účtu, e-mail ani část klíče. Příkaz `mcp-voice-status` nikdy nesahá na síť.
Segment okna Telegramu (telegram/okno-<pid>-<start>.json procesu claude, ze kterého status běží):
  TG do 16:30 · práce | TG tah #4821 | TG čeká potvrzení | TG fronta 2 | TG chyba: hook | TG ověřuji
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import sys
import time
from pathlib import Path

from .stavy import zajisti_adresar, zapis_json

SOUBOR = "status.json"
ZAMEK = "status.lock"
VERZE = 1
MAX_BAJTU = 16 * 1024
CHYBY_KREDITU = ("opravneni", "vypnuto", "sit", "http", "format")
# Stejné hodnoty jako konstanty v historie.py (ten modul načítá httpx, status bar ne).
STAVY_HISTORIE = ("neulozeno", "maze_se", "smazano", "nesmazano:opravneni", "nesmazano:neplatne_id",
                  "nesmazano:sit", "nesmazano:chyba", "nesmazano:vypnuto")
HISTORIE_HTTP = re.compile(r"nesmazano:http_[1-5][0-9]{2}")
MAX_CISLO = 10**12  # podvržené obří číslo nesmí roztáhnout status bar
# Časy a den (ISO 8601) a kód chyby: jen ASCII bez řídicích a bidi znaků, které by --json propustil.
RETEZEC = re.compile(r"[0-9A-Za-z:+.-]{1,40}")
ZAMEK_POKUSY, ZAMEK_PRODLEVA_S = 5, 0.01
# Klíč -> povolené typy hodnot; nic jiného se do souboru nezapíše.
KREDITY = ("kredity_zbyva", "kredity_limit", "obnova", "kredity_cas", "kredity_chyba")
POLE: dict[str, tuple[type, ...]] = {
    "cas": (str,), "den": (str,), "znaku_dnes": (int,), "limit_den": (int,),
    "kredity_zbyva": (int, type(None)), "kredity_limit": (int, type(None)), "obnova": (str, type(None)),
    "kredity_cas": (str, type(None)), "kredity_chyba": (str, type(None)),
    "historie": (str, type(None)), "historie_cas": (str, type(None)),
}


def _cas(ted: float | None) -> dt.datetime:
    return dt.datetime.now() if ted is None else dt.datetime.fromtimestamp(ted)


def ted_iso(ted: float | None = None) -> str:
    return _cas(ted).astimezone().isoformat(timespec="seconds")


def den(ted: float | None = None) -> str:
    """Stejný den jako rozpočet znaků (místní čas)."""
    return _cas(ted).strftime("%Y-%m-%d")


def nacti(stav: Path) -> dict:
    """Obsah status.json, nebo {} (chybí, symlink, poškozený, příliš velký)."""
    try:
        fd = os.open(stav / SOUBOR, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError:
        return {}
    try:
        with os.fdopen(fd, "rb") as fh:
            raw = fh.read(MAX_BAJTU + 1)
        d = json.loads(raw) if 0 < len(raw) <= MAX_BAJTU else None
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items() if k in POLE and _platna(k, v)}


def _platna(klic: str, v: object) -> bool:
    if isinstance(v, bool) or not isinstance(v, POLE[klic]):
        return False
    if isinstance(v, int) and not 0 <= v < MAX_CISLO:
        return False
    if klic == "historie":
        return v is None or v in STAVY_HISTORIE or HISTORIE_HTTP.fullmatch(v) is not None
    if isinstance(v, str) and not RETEZEC.fullmatch(v):
        return False
    return not (klic == "kredity_chyba" and v is not None and v not in CHYBY_KREDITU)


def _zamkni(fd: int) -> None:
    """Zámek bez trvalého čekání: status je best-effort, cizí držitel zámku nesmí zdržet server (nejvýš ~50 ms)."""
    for pokus in range(ZAMEK_POKUSY):
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if pokus == ZAMEK_POKUSY - 1:
                raise
            time.sleep(ZAMEK_PRODLEVA_S)


def _iso(v: object) -> dt.datetime | None:
    try:
        d = dt.datetime.fromisoformat(v) if isinstance(v, str) else None
    except ValueError:
        return None
    return d if d is not None and d.tzinfo is not None else None


def _kredity_zastarale(d: dict, kredity_k: str | None, ted: float | None) -> bool:
    """Uložený kredity_cas je novější než odpověď tohoto dotazu (a není v budoucnosti po posunu hodin)."""
    ulozeny, tento = _iso(d.get("kredity_cas")), _iso(kredity_k)
    if ulozeny is None or tento is None:
        return False
    return tento < ulozeny <= _cas(ted).astimezone()


def aktualizuj(stav: Path, zmeny: dict, ted: float | None = None, kredity_k: str | None = None) -> dict:
    """Sloučí změny do status.json pod zámkem (víc procesů serveru).

    kredity_k = čas odpovědi dotazu na kredity (ISO 8601): je-li uložený kredity_cas novější,
    pole KREDITY se ze změn vynechají (starší dotaz doběhl později, třeba za opakovaným mazáním historie).
    Neplatné pole = ValueError, zámek držený jinde = BlockingIOError (zápis se vynechá).
    """
    for k, v in zmeny.items():
        if k not in POLE or k == "cas" or not _platna(k, v):
            raise ValueError(f"status.json: neplatné pole {k}")
    zajisti_adresar(stav)
    fd = os.open(stav / ZAMEK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        _zamkni(fd)
        d = nacti(stav)
        if _kredity_zastarale(d, kredity_k, ted):
            zmeny = {k: v for k, v in zmeny.items() if k not in KREDITY}
        d.update(zmeny)
        d["v"] = VERZE
        d["cas"] = ted_iso(ted)
        zapis_json(stav / SOUBOR, d)
        return d
    finally:
        os.close(fd)


def denni_cast(znaku_za_den: int, zbyva: int, ted: float | None = None) -> dict:
    return {"den": den(ted), "znaku_dnes": max(0, znaku_za_den - zbyva), "limit_den": znaku_za_den}


# ---------------------------------------------------------------- výpis pro status bar

def platny_dnes(d: dict, dnes: str) -> dict:
    """Nový den: denní počítadlo od nuly (rozpočet se taky nuluje)."""
    d = dict(d)
    if d.get("den") != dnes:
        d["den"], d["znaku_dnes"] = dnes, 0
    return d


def zkratka(n: int) -> str:
    """24 612 -> 24,6k; zaokrouhluje dolů, aby zůstatek nevypadal větší, než je."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return "%d,%dk" % (n // 1000, n % 1000 // 100)
    return "%d,%dM" % (n // 1_000_000, n % 1_000_000 // 100_000)


def segment(d: dict, dnes: str) -> str:
    if not d:
        return ""
    d = platny_dnes(d, dnes)
    casti = []
    if isinstance(d.get("limit_den"), int):
        casti.append("%d/%d" % (d.get("znaku_dnes", 0), d["limit_den"]))
    if isinstance(d.get("kredity_zbyva"), int):
        casti.append(zkratka(d["kredity_zbyva"]) + " kr")
    return ("🔊 " + " · ".join(casti)) if casti else ""


def segment_telegramu(stav: Path, identita: tuple[int, str] | None, ted: float | None = None) -> str:
    from . import puvod
    if identita is None:
        return ""
    ted = time.time() if ted is None else ted
    o = puvod.cti_tise(puvod.soubor(stav, "okno", puvod.klic_procesu(*identita)), {})
    if o.get("stav") == "overovani":
        return "TG ověřuji"
    if not puvod.okno_plati(o, o.get("session_id"), ted):
        return ""
    if o.get("chyba") == "hook_rozsahu_nebezi":
        return "TG chyba: hook"
    if o.get("ceka_potvrzeni"):
        return "TG čeká potvrzení"
    if isinstance(o.get("fronta"), int) and not isinstance(o.get("fronta"), bool) and 0 < o["fronta"] < 1000:
        return "TG fronta %d" % o["fronta"]
    tah = o.get("tah")
    if isinstance(tah, str) and tah.isdigit() and len(tah) <= 15:
        return "TG tah #" + tah
    rezim = {"cteni": "čtení", "prace": "práce", "plny": "plný"}.get(o.get("rezim"), "?")
    konec = o.get("konec")
    return "TG do %s · %s" % (dt.datetime.fromtimestamp(konec).strftime("%H:%M"), rezim)


def _identita_claude() -> tuple[int, str] | None:
    from . import puvod
    pid = puvod.najdi_claude(os.getppid())
    start = puvod.start_procesu(pid) if pid else None
    return (pid, start) if pid and start else None


def _adresar_stavu(arg: str | None) -> Path:
    if arg:
        return Path(os.path.expanduser(arg))
    from .config import nacti as nacti_konfiguraci
    return nacti_konfiguraci().stav_adresar


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="mcp-voice-status",
                                description="Segment pro status bar ze status.json (bez sítě).")
    p.add_argument("--json", action="store_true", help="vypsat stav jako JSON")
    p.add_argument("--stav", help="adresář stavu (výchozí cesty.stav z konfigurace)")
    a = p.parse_args(argv)
    try:
        stav = _adresar_stavu(a.stav)
        d = nacti(stav)
        dnes = den()
        if a.json:
            vystup = json.dumps(platny_dnes(d, dnes) if d else {}, ensure_ascii=False)
        else:
            try:
                tg = segment_telegramu(stav, _identita_claude())
            except Exception:  # noqa: BLE001
                tg = ""
            vystup = " ".join(x for x in (segment(d, dnes), tg) if x)
    except Exception:  # noqa: BLE001 – status bar nesmí rozbít ani chybná konfigurace
        vystup = ""
    if vystup:
        sys.stdout.write(vystup + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
