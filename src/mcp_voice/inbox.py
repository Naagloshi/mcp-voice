"""Doručení zprávy do inbox socketu vlastní session Claude Code (R-SOCK-1, N-k7-1).

Socket není bezpečnostní hranice (S4): píše do něj každý proces uživatele. Proto přesně jeden řádek
`{"type":"user","message":{"role":"user","content":<str>}}` ze serveru samotného (verifiedPeerPid musí
sedět), cesta jen z CLAUDE_CODE_MESSAGING_SOCKET vlastního procesu a kontrola protistrany SO_PEERCRED.
Token socketu se nečte, neloguje ani neposílá. Žádné opakování se stejným textem.
"""
from __future__ import annotations

import json
import os
import socket
import stat
import struct
import time
from dataclasses import dataclass
from typing import Callable

from . import puvod

PROMENNA = "CLAUDE_CODE_MESSAGING_SOCKET"
TIMEOUT_CONNECT_S = 5.0
TIMEOUT_CELKEM_S = 30.0


class InboxChyba(Exception):
    def __init__(self, kod: str):
        super().__init__(kod)
        self.kod = kod


@dataclass
class Proces:
    """Odkud se ověřuje rodič (v testech podvrženo)."""
    getppid: Callable[[], int] = os.getppid
    stat: Callable[[int], tuple | None] = puvod.stat_procesu
    getuid: Callable[[], int] = os.getuid


def radek(text: str) -> bytes:
    if not isinstance(text, str) or not text:
        raise InboxChyba("text_neplatny")
    return (json.dumps({"type": "user", "message": {"role": "user", "content": text}}, ensure_ascii=False)
            + "\n").encode("utf-8")


class Inbox:
    def __init__(self, claude_pid: int, claude_start: str, env: dict | None = None, proces: Proces | None = None):
        self.claude_pid = claude_pid
        self.claude_start = str(claude_start)
        self.env = os.environ if env is None else env
        self.p = proces or Proces()

    def povolene_adresare(self) -> tuple[str, ...]:
        uid = self.p.getuid()
        runtime = self.env.get("XDG_RUNTIME_DIR") or "/run/user/%d" % uid
        return (os.path.join(runtime, "cc-socks"), "/tmp/cc-socks-%d" % uid)

    def over(self) -> str:
        """Kontroly před každým odesláním (znovupoužití PID). Vrací cestu socketu."""
        cesta = self.env.get(PROMENNA)
        if not isinstance(cesta, str) or not cesta or "\x00" in cesta:
            raise InboxChyba("socket_chybi")
        adr, jmeno = os.path.split(os.path.normpath(cesta))
        if adr not in self.povolene_adresare():
            raise InboxChyba("socket_cesta")
        rodic = self.p.getppid()
        st = self.p.stat(rodic)
        if (rodic != self.claude_pid or jmeno != "%d.sock" % rodic or st is None or st[0] != "claude"
                or st[2] != self.claude_start):
            raise InboxChyba("socket_rodic")
        uid = self.p.getuid()
        try:
            st_adr, st_sock = os.lstat(adr), os.lstat(cesta)
        except OSError:
            raise InboxChyba("socket_chybi") from None
        if (not stat.S_ISDIR(st_adr.st_mode) or st_adr.st_uid != uid or stat.S_IMODE(st_adr.st_mode) != 0o700
                or not stat.S_ISSOCK(st_sock.st_mode) or st_sock.st_uid != uid):
            raise InboxChyba("socket_prava")
        return cesta

    def posli(self, text: str) -> None:
        data = radek(text)
        cesta = self.over()
        zacatek = time.monotonic()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(TIMEOUT_CONNECT_S)
            try:
                s.connect(cesta)
            except OSError:
                raise InboxChyba("socket_spojeni") from None
            pid, uid, _ = struct.unpack("3i", s.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                            struct.calcsize("3i")))
            if pid != self.claude_pid or uid != self.p.getuid():
                raise InboxChyba("socket_cizi")
            s.settimeout(max(0.1, TIMEOUT_CELKEM_S - (time.monotonic() - zacatek)))
            try:
                s.sendall(data)
                s.shutdown(socket.SHUT_WR)
            except OSError:
                raise InboxChyba("socket_zapis") from None
        finally:
            s.close()
