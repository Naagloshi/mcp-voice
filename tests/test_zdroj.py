"""Klasifikace zdroje na syntetických transkriptech a validace vazby (R-ZDR-1..4)."""
import json
import os
import time
import uuid
from pathlib import Path

import pytest

from mcp_voice import zdroj
from mcp_voice.cil import NEZNAMY, REMOTE, TERMINAL

TED = time.time()


def ts(posun_s=0.0):
    import datetime as dt
    return dt.datetime.fromtimestamp(TED + posun_s, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def human(ps="typed", skip=None, posun=-10, **navic):
    d = {"type": "user", "origin": {"kind": "human"}, "timestamp": ts(posun), "message": {"content": "x"}}
    if ps is not None:
        d["promptSource"] = ps
    if skip is not None:
        d["queueSkipSlashCommands"] = skip
    d.update(navic)
    return d


def peer():
    return {"type": "user", "origin": {"kind": "peer"}, "promptSource": "system", "queueSkipSlashCommands": True,
            "isMeta": True, "timestamp": ts(-1)}


def task_notification():
    return {"type": "user", "origin": {"kind": "task-notification"}, "promptSource": "system", "timestamp": ts(-1)}


def assistant():
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__hlas__rekni"}]},
            "timestamp": ts(-1)}


def transkript(tmp_path, zaznamy, sid=None):
    sid = sid or str(uuid.uuid4())
    adr = tmp_path / "projects" / "-x"
    adr.mkdir(parents=True, exist_ok=True)
    p = adr / f"{sid}.jsonl"
    p.write_text("".join(json.dumps(z) + "\n" for z in zaznamy))
    return p


@pytest.mark.parametrize("zaznamy,druh,degradace", [
    ([human("typed")], TERMINAL, False),
    ([human("queued")], TERMINAL, False),  # psal do fronty v terminálu
    ([human("queued", skip=True)], REMOTE, False),
    ([human("suggestion_accepted")], TERMINAL, False),
    ([human("typed"), peer(), task_notification(), assistant()], TERMINAL, False),  # peer se nepočítá
    ([human("queued", skip=True), peer()], REMOTE, False),
    ([human("typed"), human("queued", skip=True), peer()], REMOTE, False),
    ([human(None)], NEZNAMY, True),  # chybí promptSource
    ([human("hlasem")], NEZNAMY, True),  # neznámá hodnota
    ([human("system")], NEZNAMY, True),
    ([peer(), task_notification()], NEZNAMY, True),  # žádný lidský záznam
    ([human("typed", isMeta=True)], NEZNAMY, True),
    ([human("typed", isSidechain=True)], NEZNAMY, True),
])
def test_klasifikace(tmp_path, zaznamy, druh, degradace):
    z = zdroj.klasifikuj(transkript(tmp_path, zaznamy), TED, "hook")
    assert (z.druh, z.degradace) == (druh, degradace)


def test_remote_control_odpojeny_je_neznamy(tmp_path):
    bridge = {"type": "system", "subtype": "bridge_status", "url": None, "timestamp": ts(-1)}
    z = zdroj.klasifikuj(transkript(tmp_path, [human("queued", skip=True), bridge]), TED, "hook")
    assert z.druh == NEZNAMY


def test_remote_control_pripojeny(tmp_path):
    zmena = {"type": "attachment", "attachment": {"type": "remote_session_change", "url": "https://x"},
             "timestamp": ts(-1)}
    z = zdroj.klasifikuj(transkript(tmp_path, [human("queued", skip=True), zmena]), TED, "hook")
    assert z.druh == REMOTE


def test_stary_zaznam_bez_vazby_z_hooku_je_neznamy(tmp_path):
    """Po /clear bez hooku: env ukazuje na starý transkript."""
    p = transkript(tmp_path, [human("typed", posun=-31 * 60)])
    assert zdroj.klasifikuj(p, TED, "env").druh == NEZNAMY
    assert zdroj.klasifikuj(p, TED, "hook").druh == TERMINAL


def test_poskozene_radky_a_velky_soubor(tmp_path):
    p = transkript(tmp_path, [human("queued", skip=True)])
    with p.open("a") as fh:
        fh.write("{nevalidni json\n")
        fh.write(json.dumps({"type": "assistant", "x": "y" * 300_000}) + "\n")
    assert zdroj.klasifikuj(p, TED, "hook").druh == REMOTE


def test_nečitelny_transkript(tmp_path):
    assert zdroj.klasifikuj(tmp_path / "neni.jsonl", TED, "hook").druh == NEZNAMY


# ------------------------------------------------------------- vazba

def test_vazba_z_hooku(tmp_path):
    sid = str(uuid.uuid4())
    tp = transkript(tmp_path, [human()], sid)
    stav = tmp_path / "stav"
    stav.mkdir()
    zdroj.soubor_vazby(stav, 1234).write_text(json.dumps({
        "session_id": sid, "transcript_path": str(tp),
        "snimky": [{"cas": TED - 10, "zamceno": False, "necinnost_ms": 5000, "okno": "com.anthropic.claude"}]}))
    v = zdroj.nacti_vazbu(stav, 1234, env={}, koren=tmp_path / "projects")
    assert v.metoda == "hook" and v.session_id == sid and len(v.snimky) == 1
    sn = zdroj.snimek_ke_zprave(v, TED - 8, 5)
    assert sn.necinnost_s == 5.0 and sn.okno == "com.anthropic.claude"
    assert zdroj.snimek_ke_zprave(v, TED + 60, 5) is None  # mimo toleranci


@pytest.mark.parametrize("sid,cesta", [
    ("neni-uuid", None),
    (None, "/etc/passwd"),
    ("ok", "/tmp/jiny.jsonl"),
])
def test_vazba_validace(tmp_path, sid, cesta):
    real_sid = str(uuid.uuid4())
    tp = transkript(tmp_path, [human()], real_sid)
    stav = tmp_path / "stav"
    stav.mkdir()
    zdroj.soubor_vazby(stav, 1).write_text(json.dumps({
        "session_id": real_sid if sid in (None, "ok") else sid,
        "transcript_path": cesta or str(tp)}))
    if sid is None:
        zdroj.soubor_vazby(stav, 1).write_text(json.dumps({"session_id": real_sid, "transcript_path": "/etc/passwd"}))
    assert zdroj.nacti_vazbu(stav, 1, env={}, koren=tmp_path / "projects") is None


def test_vazba_jine_jmeno_souboru_nez_session(tmp_path):
    tp = transkript(tmp_path, [human()])
    stav = tmp_path / "stav"
    stav.mkdir()
    zdroj.soubor_vazby(stav, 1).write_text(json.dumps({"session_id": str(uuid.uuid4()), "transcript_path": str(tp)}))
    assert zdroj.nacti_vazbu(stav, 1, env={}, koren=tmp_path / "projects") is None


def test_stavovy_soubor_symlink_se_ignoruje(tmp_path):
    sid = str(uuid.uuid4())
    tp = transkript(tmp_path, [human()], sid)
    stav = tmp_path / "stav"
    stav.mkdir()
    jinde = tmp_path / "jinde.json"
    jinde.write_text(json.dumps({"session_id": sid, "transcript_path": str(tp)}))
    os.symlink(jinde, zdroj.soubor_vazby(stav, 7))
    assert zdroj.nacti_vazbu(stav, 7, env={}, koren=tmp_path / "projects") is None


def test_fallback_env(tmp_path):
    sid = str(uuid.uuid4())
    projekt = "/srv/u/projekt.x"
    adr = tmp_path / "projects" / "-srv-u-projekt-x"
    adr.mkdir(parents=True)
    (adr / f"{sid}.jsonl").write_text(json.dumps(human()) + "\n")
    v = zdroj.nacti_vazbu(tmp_path / "stav", None, env={"CLAUDE_CODE_SESSION_ID": sid, "CLAUDE_PROJECT_DIR": projekt},
                          koren=tmp_path / "projects")
    assert v is not None and v.metoda == "env"


def test_najdi_claude(monkeypatch):
    strom = {50: ("bash", 40), 40: ("sh", 30), 30: ("claude", 1)}
    monkeypatch.setattr(zdroj, "_stat", lambda pid: strom.get(pid))
    assert zdroj.najdi_claude(50) == 30
    assert zdroj.najdi_claude(30) == 30
    monkeypatch.setattr(zdroj, "_stat", lambda pid: {50: ("bash", 1)}.get(pid))
    assert zdroj.najdi_claude(50) is None


def test_stat_parsuje_comm_se_zavorkami():
    st = zdroj._stat(os.getpid())
    assert st is not None and isinstance(st[1], int)


def test_uklid_mrtvych(tmp_path, monkeypatch):
    stav = tmp_path
    (stav / "session-111.json").write_text("{}")
    (stav / "session-222.json").write_text("{}")
    (stav / "jiny.json").write_text("{}")
    monkeypatch.setattr(zdroj, "zije", lambda pid: pid == 222)
    assert zdroj.uklid_mrtvych(stav) == 1
    assert sorted(p.name for p in Path(stav).iterdir()) == ["jiny.json", "session-222.json"]
