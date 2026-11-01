"""mcp-voice-telegram (S8): potvrzení allowlistu jen s TTY, stav, zavři."""
from __future__ import annotations

import subprocess
import sys

import yaml

from mcp_voice import config, prijem, puvod, telegram_cli

from . import tg

CHAT = int("4242" + "42")


def kfg(tmp_path, ids=(CHAT,)):
    return config.z_dat({"telegram": {"chat_id": str(CHAT), "prijem": {"povoleno": True, "povolene_user_id": list(ids)}},
                         "cesty": {"stav": str(tmp_path / "stav")}})


def test_potvrd_bez_tty_odmitne(tmp_path):
    k = kfg(tmp_path)
    assert telegram_cli.potvrd(k, vstup=lambda _: "y", tty=False) == 2
    assert not prijem.allowlist_potvrzen(k.stav_adresar, (CHAT,), str(CHAT))


def test_potvrd_s_tty(tmp_path, capsys):
    k = kfg(tmp_path)
    assert telegram_cli.potvrd(k, vstup=lambda _: "n", tty=True, session=False) == 1
    assert telegram_cli.potvrd(k, vstup=lambda _: "y", tty=True, session=False) == 0
    assert prijem.allowlist_potvrzen(k.stav_adresar, (CHAT,), str(CHAT))
    assert not prijem.allowlist_potvrzen(k.stav_adresar, (CHAT, 5), str(CHAT))  # změna = znovu potvrdit


def test_potvrd_proces_bez_tty(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(yaml.safe_dump({"telegram": {"chat_id": str(CHAT), "prijem": {
        "povoleno": True, "povolene_user_id": [CHAT]}}, "cesty": {"stav": str(tmp_path / "stav")}}))
    r = subprocess.run([sys.executable, "-m", "mcp_voice.telegram_cli", "potvrd"], input=b"y\n",
                       capture_output=True, env={"MCP_VOICE_CONFIG": str(cfg), "HOME": str(tmp_path)}, timeout=20)
    assert r.returncode == 2 and not (tmp_path / "stav" / "telegram" / "allowlist-otisk.json").exists()


def test_potvrd_z_procesu_session_odmitne(tmp_path, monkeypatch):
    """S4: TTY si nástroj Bash vyrobí (script, pty); potomek procesu claude potvrdit nesmí."""
    k = kfg(tmp_path)
    monkeypatch.setattr(telegram_cli.puvod, "najdi_claude", lambda pid: 4242)
    assert telegram_cli.potvrd(k, vstup=lambda _: "y", tty=True) == 2
    monkeypatch.setattr(telegram_cli.puvod, "najdi_claude", lambda pid: None)
    for promenna in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID"):
        monkeypatch.setenv(promenna, "1")
        assert telegram_cli.potvrd(k, vstup=lambda _: "y", tty=True) == 2
        monkeypatch.delenv(promenna)
    assert not prijem.allowlist_potvrzen(k.stav_adresar, (CHAT,), str(CHAT))


def test_zavri_a_stav(tmp_path, capsys):
    k = kfg(tmp_path)
    assert telegram_cli.zavri(k) == 0 and "Žádné aktivní" in capsys.readouterr().out
    puvod.zapis(str(k.stav_adresar / "telegram" / "aktivni.json"), {"claude_pid": 7, "claude_start": "9",
                                                                     "session_id": tg.SID})
    telegram_cli.zavri(k)
    z = puvod.cti(puvod.soubor(k.stav_adresar, "zadost", tg.SID))
    assert z["akce"] == "zavrit" and z["claude_pid"] == 7 and z["claude_start"] == "9"
    tg.stav_okna(k.stav_adresar, (7, "9"))
    telegram_cli.stav(k)
    out = capsys.readouterr().out
    assert "Allowlist potvrzen: ne" in out and "7-9: Okno Telegramu otevřené do" in out
