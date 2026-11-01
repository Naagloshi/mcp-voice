"""Zdroj poslední zprávy uživatele (terminál, nebo Remote Control) a vazba MCP ↔ session.

Vazbu zapisuje hook UserPromptSubmit (hook.py) do <stav>/session-<pid claude>.json.
Pole transkriptu (promptSource, queueSkipSlashCommands, origin.kind) jsou
nedokumentovaná: když chybí nebo mají neznámou hodnotu, zdroj je „neznamy“.
Z transkriptu se čtou jen metadata záznamů, nikdy obsah zprávy do výstupu ani logu.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from .cil import NEZNAMY, REMOTE, TERMINAL, StavPc

log = logging.getLogger("mcp_voice.zdroj")

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MAX_CTENI = 2 * 1024 * 1024
BLOK = 256 * 1024
MAX_STAVOVY = 64 * 1024
MAX_SNIMKU = 20
STARY_ZAZNAM_S = 30 * 60
ZNAME_ZDROJE = {"typed", "queued", "suggestion_accepted", "system"}
_varovano: set[str] = set()


def projekty_claude() -> Path:
    return Path(os.path.expanduser("~/.claude/projects"))


# ---------------------------------------------------------------- proces claude

def _stat(pid: int) -> tuple[str, int] | None:
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    # comm je v závorkách a smí obsahovat mezery i závorky -> poslední ')'
    l, r = data.find("("), data.rfind(")")
    if l < 0 or r < 0:
        return None
    comm = data[l + 1:r]
    zbytek = data[r + 2:].split()
    try:
        return comm, int(zbytek[1])
    except (IndexError, ValueError):
        return None


def najdi_claude(start_pid: int, max_kroku: int = 16) -> int | None:
    """PID nejbližšího předka (včetně start_pid) s comm == "claude"."""
    pid = start_pid
    for _ in range(max_kroku):
        if pid <= 1:
            return None
        st = _stat(pid)
        if st is None:
            return None
        if st[0] == "claude":
            return pid
        pid = st[1]
    return None


def zije(pid: int) -> bool:
    st = _stat(pid)
    return st is not None and st[0] == "claude"


# ---------------------------------------------------------------- stavový soubor

def soubor_vazby(stav: Path, pid: int) -> Path:
    return stav / f"session-{int(pid)}.json"


def over_transkript(session_id: object, transcript_path: object, koren: Path | None = None) -> Path | None:
    """R-ZDR-1: UUID, realpath pod ~/.claude/projects, jméno <session_id>.jsonl."""
    if not isinstance(session_id, str) or not UUID.match(session_id):
        return None
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    koren = Path(os.path.realpath(koren or projekty_claude()))
    real = Path(os.path.realpath(transcript_path))
    if os.path.commonpath([str(real), str(koren)]) != str(koren) or real.name != session_id + ".jsonl":
        return None
    return real


def cti_bez_symlinku(cesta: Path, max_bajtu: int = MAX_STAVOVY) -> bytes | None:
    try:
        fd = os.open(cesta, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > max_bajtu:
            return None
        return os.read(fd, max_bajtu + 1)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class Vazba:
    session_id: str
    transkript: Path
    snimky: tuple[tuple[float, StavPc], ...]  # (epoch, stav)
    metoda: str  # hook | env


def _snimek(d: object) -> tuple[float, StavPc] | None:
    if not isinstance(d, dict):
        return None
    cas = d.get("cas")
    if isinstance(cas, bool) or not isinstance(cas, (int, float)):
        return None
    z = d.get("zamceno")
    n = d.get("necinnost_ms")
    o = d.get("okno")
    return float(cas), StavPc(
        zamceno=z if isinstance(z, bool) else None,
        necinnost_s=n / 1000.0 if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else None,
        okno=o if isinstance(o, str) and re.match(r"^[A-Za-z0-9._-]{1,128}$", o) else None,
    )


def nacti_vazbu(stav: Path, pid: int | None, env: dict | None = None, koren: Path | None = None) -> Vazba | None:
    if pid is not None:
        raw = cti_bez_symlinku(soubor_vazby(stav, pid))
        if raw is not None:
            try:
                d = json.loads(raw)
            except ValueError:
                d = None
            if isinstance(d, dict):
                tp = over_transkript(d.get("session_id"), d.get("transcript_path"), koren)
                if tp is not None:
                    seznam = d.get("snimky") if isinstance(d.get("snimky"), list) else []
                    snimky = tuple(s for s in (_snimek(x) for x in seznam[-MAX_SNIMKU:]) if s)
                    return Vazba(d["session_id"], tp, snimky, "hook")
    env = os.environ if env is None else env
    sid, proj = env.get("CLAUDE_CODE_SESSION_ID"), env.get("CLAUDE_PROJECT_DIR")
    if sid and proj:
        slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(proj))
        tp = over_transkript(sid, str((koren or projekty_claude()) / slug / f"{sid}.jsonl"), koren)
        if tp is not None:
            return Vazba(sid, tp, (), "env")
    return None


# ---------------------------------------------------------------- transkript

def _radky_od_konce(cesta: Path, max_bajtu: int = MAX_CTENI):
    """Úplné řádky souboru od konce, nejvýš max_bajtu."""
    fd = os.open(cesta, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        konec = os.fstat(fd).st_size
        precteno = 0
        zbytek = b""
        pozice = konec
        while pozice > 0 and precteno < max_bajtu:
            krok = min(BLOK, pozice, max_bajtu - precteno)
            pozice -= krok
            data = os.pread(fd, krok, pozice) + zbytek
            precteno += krok
            radky = data.split(b"\n")
            zbytek = radky[0]
            for r in reversed(radky[1:]):
                if r.strip():
                    yield r
        if pozice == 0 and zbytek.strip():
            yield zbytek
    finally:
        os.close(fd)


def _cas(ts: object) -> float | None:
    if not isinstance(ts, str):
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass(frozen=True)
class Zdroj:
    druh: str  # terminal | remote_control | neznamy
    metoda: str  # transkript | zadna
    cas_zpravy: float | None
    degradace: bool


def _varuj_jednou(hodnota: str) -> None:
    if hodnota not in _varovano:
        _varovano.add(hodnota)
        log.warning("neznámá hodnota promptSource (délka %d), zdroj = neznamy", len(hodnota))


def klasifikuj(cesta: Path, ted: float, metoda_vazby: str) -> Zdroj:
    try:
        bridge: bool | None = None
        for radek in _radky_od_konce(cesta):
            if b'"bridge_status"' in radek or b'"remote_session_change"' in radek:
                if bridge is None:
                    try:
                        d = json.loads(radek)
                    except ValueError:
                        continue
                    url = d.get("url") if d.get("subtype") == "bridge_status" else (d.get("attachment") or {}).get("url")
                    bridge = url is not None
                continue
            if b'"human"' not in radek:
                continue
            try:
                d = json.loads(radek)
            except ValueError:
                continue
            if (d.get("type") != "user" or not isinstance(d.get("origin"), dict)
                    or d["origin"].get("kind") != "human" or d.get("isMeta") or d.get("isSidechain")):
                continue
            return _zdroj_ze_zaznamu(d, ted, metoda_vazby, bridge)
    except OSError:
        pass
    return Zdroj(NEZNAMY, "zadna", None, True)


def _zdroj_ze_zaznamu(d: dict, ted: float, metoda_vazby: str, bridge: bool | None) -> Zdroj:
    cas = _cas(d.get("timestamp"))
    if metoda_vazby == "env" and (cas is None or ted - cas > STARY_ZAZNAM_S):
        return Zdroj(NEZNAMY, "transkript", cas, True)
    ps = d.get("promptSource")
    if not isinstance(ps, str):
        return Zdroj(NEZNAMY, "transkript", cas, True)
    if ps not in ZNAME_ZDROJE or ps == "system":
        _varuj_jednou(ps)
        return Zdroj(NEZNAMY, "transkript", cas, True)
    if ps == "queued" and d.get("queueSkipSlashCommands") is True:
        if bridge is False:  # transkript tvrdí, že Remote Control je odpojený
            return Zdroj(NEZNAMY, "transkript", cas, True)
        return Zdroj(REMOTE, "transkript", cas, False)
    return Zdroj(TERMINAL, "transkript", cas, False)


def snimek_ke_zprave(vazba: Vazba | None, cas_zpravy: float | None, tolerance_s: float) -> StavPc | None:
    if vazba is None or cas_zpravy is None or not vazba.snimky:
        return None
    nejlepsi = min(vazba.snimky, key=lambda s: abs(s[0] - cas_zpravy))
    return nejlepsi[1] if abs(nejlepsi[0] - cas_zpravy) <= tolerance_s else None


def uklid_mrtvych(stav: Path) -> int:
    smazano = 0
    try:
        jmena = os.listdir(stav)
    except OSError:
        return 0
    for jmeno in jmena:
        m = re.match(r"^session-(\d+)\.json$", jmeno)
        if m and not zije(int(m.group(1))):
            try:
                os.unlink(stav / jmeno)
                smazano += 1
            except OSError:
                pass
    return smazano
