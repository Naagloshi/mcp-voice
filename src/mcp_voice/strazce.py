"""Razítko externího strážce (hook PreToolUse): server pracuje jen s voláním, které hook prověřil.

Protokol (obecný, nezávislý na tom, co strážce kontroluje):
  - Klient (Claude Code) posílá v params._meta pole "claudecode/toolUseId"; hook PreToolUse
    dostane totéž jako tool_use_id.
  - Hook po rozhodnutí „pustit“ nebo „zeptat se“ zapíše <stav>/razitka/<tool_use_id>.json
    (adresář 0700, soubor 0600, O_CREAT|O_EXCL):
        {"v": 1, "nastroj": "<jméno nástroje bez prefixu mcp__server__>",
         "sha256": "<sha256 kanonického JSON argumentů>", "cas": <epoch s>}
    Kanonický JSON = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False), UTF-8.
  - Při zamítnutí razítko nevzniká.
  - Server razítko najde, hned smaže (jednorázové), ověří nástroj, hash a stáří. Jinak GUARD_*.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path

from .chyby import HlasChyba

META_KLIC = "claudecode/toolUseId"
TOOL_USE_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
MAX_RAZITKO = 4096


def kanonicky_hash(argumenty: dict) -> str:
    data = json.dumps(argumenty, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def adresar(stav: Path) -> Path:
    return stav / "razitka"


def _uklid(adr: Path, max_stari_s: float, ted: float) -> None:
    try:
        jmena = os.listdir(adr)
    except OSError:
        return
    for jmeno in jmena:
        p = adr / jmeno
        try:
            if ted - os.lstat(p).st_mtime > max_stari_s:
                os.unlink(p)
        except OSError:
            pass


def over_a_spotrebuj(stav: Path, tool_use_id: object, nastroj: str, argumenty: dict,
                     max_stari_s: float, ted: float | None = None) -> None:
    ted = time.time() if ted is None else ted
    adr = adresar(stav)
    if not isinstance(tool_use_id, str) or not TOOL_USE_ID.match(tool_use_id):
        _uklid(adr, max_stari_s, ted)
        raise HlasChyba("GUARD_MISSING")
    cesta = adr / f"{tool_use_id}.json"
    try:
        fd = os.open(cesta, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError:
        _uklid(adr, max_stari_s, ted)
        raise HlasChyba("GUARD_MISSING") from None
    try:
        st = os.fstat(fd)
        obsah = os.read(fd, MAX_RAZITKO + 1) if stat.S_ISREG(st.st_mode) else b""
        vlastni = st.st_uid == os.getuid() and not (stat.S_IMODE(st.st_mode) & 0o022)
    finally:
        os.close(fd)
        try:
            os.unlink(cesta)  # jednorázové i při neshodě
        except OSError:
            pass
    _uklid(adr, max_stari_s, ted)
    try:
        d = json.loads(obsah) if 0 < len(obsah) <= MAX_RAZITKO else None
    except ValueError:
        d = None
    if not vlastni or not isinstance(d, dict) or d.get("v") != 1:
        raise HlasChyba("GUARD_MISSING")
    cas = d.get("cas")
    if isinstance(cas, bool) or not isinstance(cas, (int, float)) or not (ted - max_stari_s <= cas <= ted + 5):
        raise HlasChyba("GUARD_MISSING")
    if d.get("nastroj") != nastroj or d.get("sha256") != kanonicky_hash(argumenty):
        raise HlasChyba("GUARD_MISMATCH")
