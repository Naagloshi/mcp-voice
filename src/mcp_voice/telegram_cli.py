"""`mcp-voice-telegram`: ruční správa příjmu z Telegramu v terminálu (bez sítě).

  potvrd  potvrdí allowlist (povolene_user_id, chat_id) – jen s TTY a mimo proces Claude Code
  stav    stav oken a allowlistu
  zavri   žádost o zavření aktivního okna (funguje i bez TTY)
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from . import okno as oknomod
from . import prijem, puvod


ZNAKY_SESSION = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "CLAUDE_CODE_ENTRYPOINT")


def ze_session() -> bool:
    """S4: běh z nástroje session (předek claude nebo jeho proměnné). TTY si Bash vyrobí přes script/pty."""
    return puvod.najdi_claude(os.getppid()) is not None or any(os.environ.get(k) for k in ZNAKY_SESSION)


def potvrd(kfg, vstup=input, tty: bool | None = None, session: bool | None = None) -> int:
    tty = sys.stdin.isatty() if tty is None else tty
    if not tty:
        sys.stderr.write("mcp-voice-telegram potvrd: jen v terminálu (stdin není TTY), nic se nezměnilo.\n")
        return 2
    if ze_session() if session is None else session:
        sys.stderr.write("mcp-voice-telegram potvrd: spusť v samostatném terminálu mimo Claude Code "
                         "(ne přes ! ani nástroj Bash), nic se nezměnilo.\n")
        return 2
    p = kfg.telegram.prijem
    if p.chyba:
        sys.stderr.write("mcp-voice-telegram: chybná konfigurace příjmu: %s\n" % p.chyba)
        return 2
    print("Povolené user_id: %s" % ", ".join(str(x) for x in p.povolene_user_id))
    print("chat_id: %s" % kfg.telegram.chat_id)
    try:
        odpoved = vstup("Potvrdit tento allowlist pro příjem zpráv? [y/N] ")
    except EOFError:
        odpoved = ""
    if odpoved.strip().lower() not in ("y", "a", "ano", "yes"):
        print("Nepotvrzeno.")
        return 1
    prijem.potvrd_allowlist(kfg.stav_adresar, p.povolene_user_id, kfg.telegram.chat_id)
    print("Allowlist potvrzen. Platí po restartu serveru mcp-voice (nová session).")
    return 0


def stav(kfg) -> int:
    p = kfg.telegram.prijem
    ted = time.time()
    print("Příjem: %s" % ("zapnutý" if p.povoleno else "vypnutý") + (" (chyba konfigurace: %s)" % p.chyba if p.chyba else ""))
    print("Allowlist potvrzen: %s" % ("ano" if prijem.allowlist_potvrzen(kfg.stav_adresar, p.povolene_user_id,
                                                                          kfg.telegram.chat_id) else "ne"))
    adr = oknomod.adresar(kfg.stav_adresar)
    try:
        jmena = sorted(j for j in os.listdir(adr) if j.startswith("okno-") and j.endswith(".json"))
    except OSError:
        jmena = []
    for j in jmena:
        o = puvod.cti_tise(str(adr / j), {})
        print("%s: %s" % (j[5:-5], oknomod.popis(o, ted)))
    if not jmena:
        print("Žádné okno.")
    return 0


def zavri(kfg) -> int:
    a = puvod.cti_tise(str(oknomod.adresar(kfg.stav_adresar) / "aktivni.json"), {})
    sid = a.get("session_id")
    if not isinstance(sid, str) or not puvod.UUID.match(sid) or not isinstance(a.get("claude_pid"), int):
        print("Žádné aktivní okno.")
        return 0
    puvod.zapis(puvod.soubor(kfg.stav_adresar, "zadost", sid),
                {"akce": "zavrit", "duvod": "stop_terminal", "cas": time.time(), "claude_pid": a["claude_pid"],
                 "claude_start": str(a.get("claude_start")), "session_id": sid})
    print("Žádost o zavření okna zapsána.")
    return 0


def main(argv: list[str] | None = None) -> None:
    os.umask(0o077)
    ap = argparse.ArgumentParser(prog="mcp-voice-telegram", description="Příjem z Telegramu: allowlist a okno.")
    ap.add_argument("prikaz", choices=("potvrd", "stav", "zavri"))
    a = ap.parse_args(argv)
    from .config import ChybaKonfigurace, nacti
    try:
        kfg = nacti()
    except ChybaKonfigurace as e:
        sys.stderr.write("mcp-voice-telegram: chybná konfigurace: %s\n" % e)
        sys.exit(2)
    sys.exit({"potvrd": potvrd, "stav": stav, "zavri": zavri}[a.prikaz](kfg))


if __name__ == "__main__":
    main()
