"""inbox.py (N-k7-1, S4): falešný socketový server v tmp, žádná skutečná session."""
from __future__ import annotations

import json
import os
import socket
import threading

import pytest

from mcp_voice.inbox import Inbox, InboxChyba, Proces, radek

PID = os.getpid()


class FalesnySocket:
    def __init__(self, adr):
        self.cesta = str(adr / ("%d.sock" % PID))
        self.data = b""
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.bind(self.cesta)
        self.s.listen(1)
        self.vlakno = threading.Thread(target=self._prijmi, daemon=True)
        self.vlakno.start()

    def _prijmi(self):
        self.s.settimeout(5)
        try:
            k, _ = self.s.accept()
        except OSError:
            return
        with k:
            while True:
                kus = k.recv(65536)
                if not kus:
                    break
                self.data += kus

    def zavri(self):
        try:
            self.s.shutdown(socket.SHUT_RDWR)  # probudí accept ve vlákně
        except OSError:
            pass
        self.s.close()
        self.vlakno.join(2)


@pytest.fixture
def prostredi(tmp_path):
    run = tmp_path / "r"
    adr = run / "cc-socks"
    adr.mkdir(parents=True, mode=0o700)
    os.chmod(adr, 0o700)
    srv = FalesnySocket(adr)
    yield run, adr, srv
    srv.zavri()


def proces(comm="claude", start="1", rodic=PID, uid=None):
    return Proces(getppid=lambda: rodic, stat=lambda pid: (comm, 1, start), getuid=lambda: os.getuid() if uid is None else uid)


def inbox(run, cesta, claude_pid=PID, **kw):
    return Inbox(claude_pid, "1", env={"XDG_RUNTIME_DIR": str(run), "CLAUDE_CODE_MESSAGING_SOCKET": cesta},
                 proces=proces(**kw))


def test_presne_jeden_radek(prostredi):
    run, adr, srv = prostredi
    inbox(run, srv.cesta).posli("[Telegram #1 · text]\nahoj")
    srv.vlakno.join(2)
    radky = srv.data.split(b"\n")
    assert len(radky) == 2 and radky[1] == b""
    d = json.loads(radky[0])
    assert d == {"type": "user", "message": {"role": "user", "content": "[Telegram #1 · text]\nahoj"}}
    assert set(d) == {"type", "message"} and isinstance(d["message"]["content"], str)
    for zakazane in (b"priority", b"file_attachments", b"auth", b"cross-session-message", b"token"):
        assert zakazane not in srv.data


def test_peercred_jineho_pid_neodesle(prostredi):
    run, adr, srv = prostredi
    i = inbox(run, srv.cesta, claude_pid=PID)
    i.claude_pid = PID  # cesta i rodič sedí, ale protistrana socketu je jiný proces
    i.p = proces()
    i.over()
    i.claude_pid = PID + 1
    i.p = proces(rodic=PID + 1)
    with pytest.raises(InboxChyba) as e:
        i.posli("x")
    assert e.value.kod == "socket_rodic"


def test_peercred_kontrola(prostredi, monkeypatch):
    run, adr, srv = prostredi
    import mcp_voice.inbox as modul
    puvodni = socket.socket.getsockopt

    class S(socket.socket):
        def getsockopt(self, *a):
            import struct
            return struct.pack("3i", PID + 7, os.getuid(), os.getgid())

    monkeypatch.setattr(modul.socket, "socket", S)
    with pytest.raises(InboxChyba) as e:
        inbox(run, srv.cesta).posli("x")
    assert e.value.kod == "socket_cizi"
    assert puvodni


@pytest.mark.parametrize("kw, kod", [
    ({"comm": "bash"}, "socket_rodic"),
    ({"start": "2"}, "socket_rodic"),
    ({"rodic": PID + 1}, "socket_rodic"),
    ({"uid": 0}, "socket_prava"),
])
def test_kontroly_rodice_a_vlastnika(prostredi, kw, kod):
    run, adr, srv = prostredi
    with pytest.raises(InboxChyba) as e:
        inbox(run, srv.cesta, **kw).posli("x")
    assert e.value.kod == kod


def test_spatna_cesta_a_symlink(prostredi, tmp_path):
    run, adr, srv = prostredi
    with pytest.raises(InboxChyba) as e:
        inbox(run, str(tmp_path / ("%d.sock" % PID))).posli("x")
    assert e.value.kod == "socket_cesta"
    jiny = adr / "1.sock"
    with pytest.raises(InboxChyba):
        inbox(run, str(jiny)).posli("x")
    os.rename(srv.cesta, str(adr / "puvodni.sock"))
    os.symlink(adr / "puvodni.sock", srv.cesta)
    with pytest.raises(InboxChyba) as e:
        inbox(run, srv.cesta).posli("x")
    assert e.value.kod == "socket_prava"


def test_adresar_s_jinymi_pravy(prostredi):
    run, adr, srv = prostredi
    os.chmod(adr, 0o755)
    with pytest.raises(InboxChyba) as e:
        inbox(run, srv.cesta).posli("x")
    assert e.value.kod == "socket_prava"


def test_bez_promenne(prostredi):
    run, adr, srv = prostredi
    with pytest.raises(InboxChyba) as e:
        Inbox(PID, "1", env={}, proces=proces()).posli("x")
    assert e.value.kod == "socket_chybi"


def test_radek_odmitne_prazdny_text():
    with pytest.raises(InboxChyba):
        radek("")
