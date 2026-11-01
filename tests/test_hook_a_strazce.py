"""Hook UserPromptSubmit (vazba + snímek) a razítko strážce (R-H1-2, R-H1-6, N6)."""
import json
import os
import stat
import subprocess
import sys
import time
import uuid

import pytest

from mcp_voice import hook, strazce, zdroj
from mcp_voice.chyby import HlasChyba

from .conftest import FalesnaPlocha, razitko


def _transkript(tmp_path, sid):
    adr = tmp_path / "projects" / "-p"
    adr.mkdir(parents=True, exist_ok=True)
    p = adr / f"{sid}.jsonl"
    p.write_text("{}\n")
    return p


@pytest.fixture
def koren(tmp_path, monkeypatch):
    monkeypatch.setattr(zdroj, "projekty_claude", lambda: tmp_path / "projects")
    return tmp_path


def test_hook_zapise_vazbu_a_snimek_bez_textu_promptu(koren):
    sid = str(uuid.uuid4())
    tp = _transkript(koren, sid)
    stav = koren / "stav"
    data = {"hook_event_name": "UserPromptSubmit", "session_id": sid, "transcript_path": str(tp),
            "prompt": "TAJNY_TEXT_PROMPTU"}
    assert hook.zpracuj(data, stav, FalesnaPlocha(False, 3.5, "com.anthropic.claude"), 999)
    p = zdroj.soubor_vazby(stav, 999)
    obsah = p.read_text()
    assert "TAJNY_TEXT_PROMPTU" not in obsah
    d = json.loads(obsah)
    assert d["session_id"] == sid and d["snimky"][0]["necinnost_ms"] == 3500
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(stav.stat().st_mode) == 0o700


def test_hook_kruhovy_seznam_a_nova_session(koren):
    stav = koren / "stav"
    sid = str(uuid.uuid4())
    tp = _transkript(koren, sid)
    for i in range(25):
        hook.zpracuj({"session_id": sid, "transcript_path": str(tp)}, stav, FalesnaPlocha(), 5, ted=1000 + i)
    d = json.loads(zdroj.soubor_vazby(stav, 5).read_text())
    assert len(d["snimky"]) == zdroj.MAX_SNIMKU and d["snimky"][-1]["cas"] == 1024
    sid2 = str(uuid.uuid4())  # /clear: nová session v témže procesu
    hook.zpracuj({"session_id": sid2, "transcript_path": str(_transkript(koren, sid2))}, stav, FalesnaPlocha(), 5)
    d = json.loads(zdroj.soubor_vazby(stav, 5).read_text())
    assert d["session_id"] == sid2 and len(d["snimky"]) == 1


def test_hook_soubeh_dvou_session(koren):
    stav = koren / "stav"
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    hook.zpracuj({"session_id": a, "transcript_path": str(_transkript(koren, a))}, stav, FalesnaPlocha(), 10)
    hook.zpracuj({"session_id": b, "transcript_path": str(_transkript(koren, b))}, stav, FalesnaPlocha(), 20)
    assert json.loads(zdroj.soubor_vazby(stav, 10).read_text())["session_id"] == a
    assert json.loads(zdroj.soubor_vazby(stav, 20).read_text())["session_id"] == b


@pytest.mark.parametrize("data", [
    {"session_id": "x", "transcript_path": "/etc/passwd"},
    {"session_id": str(uuid.uuid4()), "transcript_path": "/etc/passwd"},
    {"hook_event_name": "PreToolUse"},
])
def test_hook_neplatny_vstup_nic_nezapise(koren, data):
    assert not hook.zpracuj(data, koren / "stav", FalesnaPlocha(), 1)
    assert not (koren / "stav").exists()


def test_hook_bez_procesu_claude_nic_nezapise(koren):
    sid = str(uuid.uuid4())
    assert not hook.zpracuj({"session_id": sid, "transcript_path": str(_transkript(koren, sid))},
                            koren / "stav", FalesnaPlocha(), None)


@pytest.mark.parametrize("vstup", [b"", b"nejson", b"[1,2]", b'{"session_id": 1}'])
def test_hook_main_vzdy_exit_0_a_nic_na_stdout(tmp_path, vstup):
    env = dict(os.environ, MCP_VOICE_CONFIG=str(tmp_path / "neni.yaml"), HOME=str(tmp_path))
    r = subprocess.run([sys.executable, "-m", "mcp_voice.hook"], input=vstup, capture_output=True, env=env, timeout=20)
    assert r.returncode == 0 and r.stdout == b""


def test_hook_main_rozbita_konfigurace_exit_0(tmp_path):
    kfg = tmp_path / "c.yaml"
    kfg.write_text("cil: [rozbite")
    env = dict(os.environ, MCP_VOICE_CONFIG=str(kfg), HOME=str(tmp_path))
    r = subprocess.run([sys.executable, "-m", "mcp_voice.hook"], input=b"{}", capture_output=True, env=env, timeout=20)
    assert r.returncode == 0 and r.stdout == b""


# ------------------------------------------------------------- razítko

ARG = {"text": "Ahoj", "cil": "auto"}


def test_razitko_platne_a_jednorazove(tmp_path):
    razitko(tmp_path, "toolu_abcdefgh", "rekni", ARG)
    strazce.over_a_spotrebuj(tmp_path, "toolu_abcdefgh", "rekni", dict(reversed(list(ARG.items()))), 600)
    with pytest.raises(HlasChyba) as e:
        strazce.over_a_spotrebuj(tmp_path, "toolu_abcdefgh", "rekni", ARG, 600)
    assert e.value.kod == "GUARD_MISSING"


@pytest.mark.parametrize("uprava,kod", [
    (lambda p: None, None),
    (lambda p: p.unlink(), "GUARD_MISSING"),
])
def test_razitko_chybi(tmp_path, uprava, kod):
    p = razitko(tmp_path, "toolu_12345678", "rekni", ARG)
    uprava(p)
    if kod is None:
        strazce.over_a_spotrebuj(tmp_path, "toolu_12345678", "rekni", ARG, 600)
    else:
        with pytest.raises(HlasChyba) as e:
            strazce.over_a_spotrebuj(tmp_path, "toolu_12345678", "rekni", ARG, 600)
        assert e.value.kod == kod


def test_razitko_jine_argumenty(tmp_path):
    razitko(tmp_path, "toolu_12345678", "rekni", ARG)
    with pytest.raises(HlasChyba) as e:
        strazce.over_a_spotrebuj(tmp_path, "toolu_12345678", "rekni", {"text": "Jiný", "cil": "auto"}, 600)
    assert e.value.kod == "GUARD_MISMATCH"
    assert not (strazce.adresar(tmp_path) / "toolu_12345678.json").exists()


def test_razitko_jiny_nastroj(tmp_path):
    razitko(tmp_path, "toolu_12345678", "prepis", ARG)
    with pytest.raises(HlasChyba) as e:
        strazce.over_a_spotrebuj(tmp_path, "toolu_12345678", "rekni", ARG, 600)
    assert e.value.kod == "GUARD_MISMATCH"


def test_razitko_stare(tmp_path):
    razitko(tmp_path, "toolu_12345678", "rekni", ARG, cas=time.time() - 601)
    with pytest.raises(HlasChyba) as e:
        strazce.over_a_spotrebuj(tmp_path, "toolu_12345678", "rekni", ARG, 600)
    assert e.value.kod == "GUARD_MISSING"


@pytest.mark.parametrize("tid", [None, "", "../x", "a/b", "x" * 200, 123])
def test_razitko_neplatne_id(tmp_path, tid):
    with pytest.raises(HlasChyba) as e:
        strazce.over_a_spotrebuj(tmp_path, tid, "rekni", ARG, 600)
    assert e.value.kod == "GUARD_MISSING"


def test_razitko_symlink_a_cizi_prava(tmp_path):
    jinde = razitko(tmp_path / "jinde", "toolu_12345678", "rekni", ARG)
    adr = strazce.adresar(tmp_path)
    adr.mkdir(parents=True)
    os.symlink(jinde, adr / "toolu_12345678.json")
    with pytest.raises(HlasChyba):
        strazce.over_a_spotrebuj(tmp_path, "toolu_12345678", "rekni", ARG, 600)
    p = razitko(tmp_path, "toolu_87654321", "rekni", ARG)
    os.chmod(p, 0o666)
    with pytest.raises(HlasChyba) as e:
        strazce.over_a_spotrebuj(tmp_path, "toolu_87654321", "rekni", ARG, 600)
    assert e.value.kod == "GUARD_MISSING"


def test_kanonicky_hash_nezavisi_na_poradi_klicu():
    assert strazce.kanonicky_hash({"a": 1, "b": "č"}) == strazce.kanonicky_hash({"b": "č", "a": 1})


def test_testovaci_vektor_hashe():
    """Stejný vektor ověřuje externí strážce: hash musí sedět na obou stranách."""
    vstup = {"text": "Dobrý den, čtu zprávu.", "cil": "auto", "ulozit": False, "hlas": None}
    assert strazce.kanonicky_hash(vstup) == "99a52086c7831e14a7693de02946c837369f17f72362e3ee1edc0f3652609d16"
