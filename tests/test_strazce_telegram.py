"""Strážce a Telegram (kap. 10, S1): odpovez razítkovaný, klíče kontextu, tah z Telegramu, signály z výsledků."""
from __future__ import annotations

import datetime
import json
import os

import pytest

from mcp_voice import puvod, strazce_hook

from . import tg
from .test_strazce_hook import ACME, Prostredi, post, pre, tool_use

IDENT = (os.getpid(), "1")
SIGNAL = tool_use("mcp__acme-db__query", {"q": "x"})


@pytest.fixture
def P(tmp_path, monkeypatch):
    p = Prostredi(tmp_path, monkeypatch)
    monkeypatch.setattr(strazce_hook, "identita_claude", lambda: IDENT)
    return p


def kontext(**kw):
    k = json.loads(json.dumps(ACME))
    k.update(kw)
    return k


def okno(P, sid, tp, **kw):
    tg.stav_okna(P.stav, IDENT, sid=sid, tp=tp, **kw)


def odpovez(sid, tp, tid, **kw):
    return pre(sid, tp, tid, "odpovez", {"text": "Ahoj", "forma": "auto"}, prompt_id="p-1", **kw)


def tah_tg(P, *navic):
    return P.session([SIGNAL, tg.peer()] + list(navic))


def test_bez_signalu_razitko(P):
    sid, tp = P.session([tg.peer()])
    okno(P, sid, tp)
    tid = P.tid()
    assert P.run(odpovez(sid, tp, tid))[0] == "pass" and P.razitko(tid)["nastroj"] == "odpovez"


def test_telegram_nikdy_i_se_souhlasem(P):
    P.nastav([kontext(telegram="nikdy", platnost="session")])
    sid, tp = tah_tg(P)
    okno(P, sid, tp)
    souhlasy = {sid: {"ACME": {"platnost": "session", "den": "x", "cas": 1e12}}}
    (P.stav / "strazce").mkdir(parents=True, exist_ok=True)
    (P.stav / "strazce" / "souhlasy-v2.json").write_text(json.dumps(souhlasy))
    tid = P.tid()
    rozh, duvod = P.run(odpovez(sid, tp, tid))
    assert rozh == "deny" and "nesmí" in duvod and P.razitko(tid) is None
    u = puvod.cti_jsonl(puvod.soubor(P.stav, "udalosti", sid, ".jsonl"))
    assert u[-1]["udalost"] == "odpoved_zamitnuta" and u[-1]["kontext"] == "ACME"


def test_rekni_auto_v_tahu_z_telegramu_jako_telegram(P):
    P.nastav([kontext(telegram="nikdy")])
    sid, tp = tah_tg(P)
    okno(P, sid, tp)
    assert P.run(pre(sid, tp, P.tid(), "rekni", {"text": "x", "cil": "auto"}, prompt_id="p-1"))[0] == "deny"
    sid2, tp2 = P.session([SIGNAL, tg.clovek()])
    assert P.run(pre(sid2, tp2, P.tid(), "rekni", {"text": "x", "cil": "pc"}))[0] == "ask"


def test_tah_povelu_bez_souhlasu_dotaz_a_souhlas(P):
    sid, tp = P.session([SIGNAL, tg.clovek("/telegram start")])
    okno(P, sid, tp, stav="overovani")
    tid = P.tid()
    rozh, duvod = P.run(odpovez(sid, tp, tid))
    assert rozh == "ask" and "Text 4 znaků -> Telegram (cloud, bez koncového šifrování)" in duvod
    assert "hlasem přes ElevenLabs" in duvod  # N6: forma auto může skončit hlasem
    P.run(post(sid, tid, tool_name="mcp__hlas__odpovez"))
    assert P.souhlasy()[sid]["ACME"]["platnost"] == "den"
    assert P.run(odpovez(sid, tp, P.tid()))[0] == "pass"


def test_souhlas_ze_vcerejska(P):
    sid, tp = tah_tg(P)
    okno(P, sid, tp)
    vcera = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    (P.stav / "strazce").mkdir(parents=True, exist_ok=True)
    (P.stav / "strazce" / "souhlasy-v2.json").write_text(json.dumps(
        {sid: {"ACME": {"platnost": "den", "den": vcera, "cas": 1}}}))
    assert P.run(odpovez(sid, tp, P.tid()))[0] == "ask"


def test_tah_z_telegramu_rc_a_cekat_dotaz(P):
    sid, tp = tah_tg(P)
    okno(P, sid, tp)
    assert P.run(odpovez(sid, tp, P.tid()))[0] == "ask"


@pytest.mark.parametrize("kont, okno_kw", [({"telegram_dotaz_v_tahu": "odmitnout"}, {}),
                                           ({}, {"bez_rc": True}),
                                           ({}, {"pri_dotazu": "odmitnout"})])
def test_tah_z_telegramu_bez_dotazu_deny(P, kont, okno_kw):
    P.nastav([kontext(**kont)])
    sid, tp = tah_tg(P)
    okno(P, sid, tp, **okno_kw)
    tid = P.tid()
    rozh, duvod = P.run(odpovez(sid, tp, tid))
    assert rozh == "deny" and "jen v terminálu" in duvod and P.razitko(tid) is None
    assert not list((P.stav / "strazce").glob("cekajici-*"))


def test_platnost_okno(P):
    P.nastav([kontext(telegram_platnost="okno")])
    sid, tp = P.session([SIGNAL, tg.clovek("/telegram start")])
    okno(P, sid, tp, otevreno=1000.0)
    tid = P.tid()
    assert P.run(odpovez(sid, tp, tid))[0] == "ask"
    P.run(post(sid, tid, tool_name="mcp__hlas__odpovez"))
    assert P.souhlasy()[sid]["ACME"]["okno"] == 1000.0
    assert P.run(odpovez(sid, tp, P.tid()))[0] == "pass"
    okno(P, sid, tp, otevreno=2000.0)  # nové okno
    assert P.run(odpovez(sid, tp, P.tid()))[0] == "ask"


def test_tah_z_telegramu_nevytvori_souhlas_bez_cekajiciho(P):
    sid, tp = tah_tg(P)
    okno(P, sid, tp)
    P.run(post(sid, P.tid(), tool_name="mcp__hlas__odpovez"))
    assert P.souhlasy() == {}


def test_signal_z_vysledku_v_tahu_z_telegramu(P):
    P.nastav([kontext(signaly={"texty": ["TAJNY-PROJEKT"]})])
    sid, tp = P.session([tg.peer()])
    okno(P, sid, tp)
    assert P.run(odpovez(sid, tp, P.tid()))[0] == "pass"
    vysledek = {"file": {"content": "… TAJNY-PROJEKT …"}}
    P.run(post(sid, P.tid(), tool_name="Read", tool_response=vysledek, prompt_id="p-1", transcript_path=tp))
    assert P.run(odpovez(sid, tp, P.tid()))[0] == "ask"


def test_signal_z_vysledku_mimo_tah_z_telegramu_nic(P):
    P.nastav([kontext(signaly={"texty": ["TAJNY-PROJEKT"]})])
    sid, tp = P.session([tg.clovek()])
    okno(P, sid, tp)
    P.run(post(sid, P.tid(), tool_name="Read", tool_response="TAJNY-PROJEKT", transcript_path=tp))
    assert not (P.stav / "strazce" / ("sken-%s.json" % sid)).exists() or \
        "ACME" not in json.loads((P.stav / "strazce" / ("sken-%s.json" % sid)).read_text()).get("nalezy", {})


def test_odpovez_ze_subagenta(P):
    sid, tp = P.session([])
    assert P.run(odpovez(sid, tp, P.tid(), agent_id="a1"))[0] == "deny"


@pytest.mark.parametrize("nastroj, vysledek", [
    ("Bash", {"stdout": "… TAJNY-PROJEKT …", "stderr": ""}),
    ("Agent", {"content": [{"type": "text", "text": "TAJNY-PROJEKT"}]}),
    ("WebFetch", {"result": "TAJNY-PROJEKT"}),
])
def test_signal_z_vysledku_bash_agent_webfetch(P, nastroj, vysledek):
    """S2: signály kontextu i ve výsledcích Bash, Agent a WebFetch v tahu z Telegramu."""
    P.nastav([kontext(signaly={"texty": ["TAJNY-PROJEKT"]})])
    sid, tp = P.session([tg.peer()])
    okno(P, sid, tp)
    assert P.run(odpovez(sid, tp, P.tid()))[0] == "pass"
    P.run(post(sid, P.tid(), tool_name=nastroj, tool_response=vysledek, prompt_id="p-1", transcript_path=tp))
    assert P.run(odpovez(sid, tp, P.tid()))[0] == "ask"


def test_signal_z_read_subagenta_tahu_z_telegramu(P):
    """A19/S2: Read subagenta (třída z agenti-<session>.json) označí session kontextem."""
    P.nastav([kontext(signaly={"texty": ["TAJNY-PROJEKT"]})])
    sid, tp = P.session([tg.peer()])
    okno(P, sid, tp)
    puvod.zapis(puvod.soubor(P.stav, "agenti", sid), {"a1": {"trida": "telegram", "start": 1, "stop": None}})
    P.run(post(sid, P.tid(), tool_name="Read", tool_response="TAJNY-PROJEKT", agent_id="a1", transcript_path=tp))
    assert P.run(odpovez(sid, tp, P.tid()))[0] == "ask"


def test_signal_z_read_subagenta_cloveka_nic(P):
    P.nastav([kontext(signaly={"texty": ["TAJNY-PROJEKT"]})])
    sid, tp = P.session([tg.peer()])
    okno(P, sid, tp)
    puvod.zapis(puvod.soubor(P.stav, "agenti", sid), {"a1": {"trida": "clovek", "start": 1, "stop": None}})
    P.run(post(sid, P.tid(), tool_name="Read", tool_response="TAJNY-PROJEKT", agent_id="a1", transcript_path=tp))
    assert P.run(odpovez(sid, tp, P.tid()))[0] == "pass"
