"""puvod.py: třída tahu z transkriptu (H1), ověření povelu (S2), doručení (N6), nečinnost session."""
from __future__ import annotations

import ast
import os
import time
from pathlib import Path

import pytest

from mcp_voice import puvod

from . import tg

ID = {tg.SERVER}


def tr(tmp_path, zaznamy):
    return tg.zapis_transkript(tmp_path / "proj", zaznamy)


def tr_fix(tmp_path, jmeno, *navic):
    return tr(tmp_path, tg.fixture_transkript(jmeno) + list(navic))


@pytest.mark.parametrize("jmeno, trida", [
    ("human-typed", puvod.CLOVEK),
    ("human-queued-rc", puvod.CLOVEK),
    ("slash-bez-promptsource", puvod.CLOVEK),
    ("vykricnik", puvod.CLOVEK),
    ("peer-server", puvod.TELEGRAM),
    ("peer-cizi", puvod.CIZI),
    ("peer-bez-pid", puvod.CIZI),
    ("queued-command", puvod.TELEGRAM),
    ("task-notification", puvod.ULOHA),
    ("scheduled", puvod.NAPLANOVANY),
    ("handback", puvod.CIZI),
    ("esc-turnindex", puvod.TELEGRAM),
])
def test_tridy_z_fixtures(tmp_path, jmeno, trida):
    assert puvod.klasifikuj(tr_fix(tmp_path, jmeno), identity=ID)["trida"] == trida


def test_peer_ze_serveru_nese_znacku(tmp_path):
    t = puvod.klasifikuj(tr_fix(tmp_path, "peer-server"), identity=ID)
    assert t["znacky"] == ["4821"]


def test_obalka_telegramu_od_ciziho_pid_je_cizi(tmp_path):
    t = puvod.klasifikuj(tr(tmp_path, [tg.peer(identita=tg.CIZI)]), identity=ID)
    assert t["trida"] == puvod.CIZI and t["znacky"] == []


def test_queued_command_uprostred_tahu_cloveka_prepne_zbytek_tahu(tmp_path):
    cesta = tr(tmp_path, [tg.clovek("práce"), tg.asistent_nastroj()])
    assert puvod.klasifikuj(cesta, identity=ID)["trida"] == puvod.CLOVEK
    tg.pripis(cesta, tg.fronta())
    t = puvod.klasifikuj(cesta, identity=ID)
    assert t["trida"] == puvod.TELEGRAM and t["znacky"] == ["4822"]


def test_dve_zpravy_v_jednom_tahu(tmp_path):
    t = puvod.klasifikuj(tr(tmp_path, [tg.peer(), tg.fronta()]), identity=ID)
    assert t["trida"] == puvod.TELEGRAM and t["znacky"] == ["4822", "4821"]


def test_peer_serveru_ma_prednost_pred_cizim(tmp_path):
    t = puvod.klasifikuj(tr(tmp_path, [tg.peer(), tg.fronta(identita=tg.CIZI)]), identity=ID)
    assert t["trida"] == puvod.TELEGRAM


def test_znacka_prompt_id_bez_zaznamu_v_transkriptu(tmp_path):
    cesta = tr(tmp_path, [tg.clovek()])
    t = puvod.klasifikuj(cesta, "p-1", identity=ID, znacky={"p-1": {"znacka": "77", "cas": 1}})
    assert t["trida"] == puvod.TELEGRAM and t["znacky"] == ["77"]


def test_bez_znacky_rozhodne_transkript(tmp_path):
    assert puvod.klasifikuj(tr_fix(tmp_path, "peer-server"), "jiny", identity=ID, znacky={})["trida"] == puvod.TELEGRAM


def test_esc_a_opakovany_turn_index_hranice_podle_turn_origin(tmp_path):
    cesta = tr(tmp_path, [tg.peer(turn=10), tg.asistent_nastroj(), tg.clovek("nový", turn=10)])
    assert puvod.klasifikuj(cesta, identity=ID)["trida"] == puvod.CLOVEK


def test_zacatek_mimo_rozpocet_je_nezname(tmp_path):
    vypln = [tg.asistent_nastroj(tid="toolu_%05d" % i) for i in range(400)]
    cesta = tr(tmp_path, [tg.clovek()] + vypln)
    assert puvod.klasifikuj(cesta, identity=ID, max_bajtu=4096)["trida"] == puvod.NEZNAME


def test_dlouhy_tah_cloveka_podle_znacky_prompt_id(tmp_path):
    """S5/A18: začátek tahu člověka mimo rozpočet, prompt_id ověřený hookem jako lidský → člověk."""
    vypln = [tg.asistent_nastroj(tid="toolu_%05d" % i) for i in range(400)]
    cesta = tr(tmp_path, [tg.clovek()] + vypln)
    znacky = {"p-1": {"trida": "clovek", "cas": 1.0}}
    t = puvod.klasifikuj(cesta, "p-1", identity=ID, znacky=znacky, max_bajtu=4096)
    assert t["trida"] == puvod.CLOVEK and t["znacky"] == []
    assert puvod.klasifikuj(cesta, "p-2", identity=ID, znacky=znacky, max_bajtu=4096)["trida"] == puvod.NEZNAME
    # peer záznam v přečteném úseku vyhrává
    cesta = tr(tmp_path, [tg.clovek()] + vypln + [tg.fronta()])
    assert puvod.klasifikuj(cesta, "p-1", identity=ID, znacky=znacky, max_bajtu=4096)["trida"] == puvod.TELEGRAM


def test_klasifikuj_vraci_prompt_id_a_lidsky_zacatek(tmp_path):
    """S5: hook rozsahu značí prompt_id tahu člověka podle přečteného začátku tahu."""
    t = puvod.klasifikuj(tr(tmp_path, [tg.clovek("Ahoj", t=100.0, prompt_id="p-1")]), "p-1", identity=ID)
    assert (t["trida"], t["prompt_id"], t["lidsky"], t["cas_zacatku"]) == (puvod.CLOVEK, "p-1", True, 100.0)
    assert puvod.klasifikuj(tr(tmp_path, [tg.slash("/review", "x", prompt_id="p-2")]), "p-2")["lidsky"]
    assert not puvod.klasifikuj(tr(tmp_path, [dict(tg.navrh(), promptId="p-3")]), "p-3")["lidsky"]
    assert not puvod.klasifikuj(tr(tmp_path, [tg.peer("ahoj", prompt_id="p-4")]), "p-4", identity=ID)["lidsky"]
    assert "prompt_id" not in puvod.klasifikuj(tr(tmp_path, [tg.asistent_nastroj()]), "p-5")


def test_shrnuti_komprimace_neni_zacatek_tahu(tmp_path):
    shrnuti = dict(tg.clovek("Shrnutí předchozí konverzace"), isCompactSummary=True)
    cesta = tr(tmp_path, [tg.peer(), tg.asistent_nastroj(), shrnuti, tg.asistent_nastroj(tid="toolu_b")])
    assert puvod.klasifikuj(cesta, identity=ID)["trida"] == puvod.TELEGRAM


def test_identita_predchoziho_serveru(tmp_path):
    cesta = tr(tmp_path, [tg.peer(identita=(1111, "222"))])
    assert puvod.klasifikuj(cesta, identity={tg.SERVER, (1111, "222")})["trida"] == puvod.TELEGRAM


def test_handback_agenta_podle_tridy_agenta(tmp_path):
    cesta = tr_fix(tmp_path, "handback")
    agenti = {"a308a0941b3849e8c": {"trida": "telegram"}}
    assert puvod.klasifikuj(cesta, identity=ID, agenti=agenti)["trida"] == puvod.TELEGRAM
    agenti = {"a308a0941b3849e8c": {"trida": "clovek"}}
    assert puvod.klasifikuj(cesta, identity=ID, agenti=agenti)["trida"] == puvod.CLOVEK


def test_agent_podle_zaznamu_a_neznamy_agent(tmp_path):
    cesta = tr(tmp_path, [tg.clovek()])
    assert puvod.klasifikuj(cesta, agent_id="a1", agenti={"a1": {"trida": "telegram"}})["trida"] == puvod.TELEGRAM
    assert puvod.klasifikuj(cesta, agent_id="a2", agenti={})["trida"] == puvod.NEZNAME


def test_necitelny_transkript_je_nezname(tmp_path):
    assert puvod.klasifikuj(str(tmp_path / "chybi.jsonl"), identity=ID)["trida"] == puvod.NEZNAME


# ---------------------------------------------------------------- povel

def over(cesta, prompt, prompt_id="p-povel", od=None, do=None):
    ted = time.time()
    return puvod.over_povel(cesta, prompt_id, puvod.otisk(prompt), "/telegram", ted - 30 if od is None else od,
                            ted + 65 if do is None else do)


@pytest.mark.parametrize("zaznam, prompt", [
    (tg.clovek("/telegram start 2h", prompt_id="p-povel"), "/telegram start 2h"),
    (tg.clovek("/telegram start 2h", "queued", prompt_id="p-povel"), "/telegram start 2h"),
    (tg.slash("/telegram", "start 1h", prompt_id="p-povel"), "/telegram start 1h"),
    (tg.clovek("/telegram start 30m čtení", prompt_id="p-povel"), "/telegram  start 30m CTENI"),
])
def test_povel_prijat(tmp_path, zaznam, prompt):
    v = over(tr(tmp_path, [tg.konec_tahu(), zaznam]), prompt)
    assert v["ok"], v


def test_povel_podle_prompt_id_i_s_pozdejsim_tahem(tmp_path):
    """Server ověřuje po hooku: za záznamem povelu už může být další záznam i začátek dalšího tahu."""
    cesta = tr(tmp_path, [tg.slash("/telegram", "start 1h", prompt_id="p-povel"), tg.asistent_nastroj(),
                          tg.clovek("další", prompt_id="p-dalsi")])
    assert over(cesta, "/telegram start 1h")["ok"]


@pytest.mark.parametrize("zaznam, kod", [
    (tg.peer("/telegram start 1h", prompt_id="p-povel"), "povel_ne_od_cloveka"),
    (dict(tg.naplanovany(), promptId="p-povel"), "povel_ne_od_cloveka"),
    (tg.clovek("/telegram start 1h", "suggestion_accepted", prompt_id="p-povel"), "povel_ne_od_cloveka"),
    (tg.clovek("/telegram start 1h", "system", prompt_id="p-povel"), "povel_ne_od_cloveka"),
    (tg.clovek("/telegram start 1h", "neco_noveho", prompt_id="p-povel"), "povel_ne_od_cloveka"),
    (tg.clovek("/telegram start 1h", t=time.time() - 60, prompt_id="p-povel"), "povel_stary"),
    (tg.clovek("/telegram start 9h", prompt_id="p-povel"), "povel_neoveren"),
    (tg.clovek("jiný text", None, prompt_id="p-povel"), "povel_ne_od_cloveka"),
    (tg.clovek("/telegram start 1h"), "povel_nenalezen"),  # záznam bez promptId
])
def test_povel_odmitnut(tmp_path, zaznam, kod):
    v = over(tr(tmp_path, [zaznam]), "/telegram start 1h")
    assert not v["ok"] and v["kod"] == kod


def test_povel_zaznam_jeste_neni(tmp_path):
    """Hook UserPromptSubmit běží dřív, než Claude Code zapíše záznam promptu (ověřeno na 2.1.314–2.1.316)."""
    cesta = tr(tmp_path, [tg.clovek("stav hlasu", prompt_id="p-pred")])
    assert over(cesta, "/telegram start 1h")["kod"] == "povel_nenalezen"
    tg.pripis(cesta, tg.slash("/telegram", "start 1h", prompt_id="p-povel"))
    assert over(cesta, "/telegram start 1h")["ok"]


def test_stary_shodny_povel_jineho_promptu_neplati(tmp_path):
    """Předchozí pokus se stejným textem (jiný prompt_id) nový povel nepotvrdí."""
    cesta = tr(tmp_path, [tg.slash("/telegram", "start 30m cteni", prompt_id="p-pokus1")])
    assert over(cesta, "/telegram start 30m cteni", prompt_id="p-pokus2")["kod"] == "povel_nenalezen"


@pytest.mark.parametrize("prompt_id, sha", [(None, "x"), ("p povel", "x"), ("p-povel", None)])
def test_povel_neplatna_zadost(tmp_path, prompt_id, sha):
    cesta = tr(tmp_path, [tg.clovek("/telegram start 1h", prompt_id="p-povel")])
    assert puvod.over_povel(cesta, prompt_id, sha, "/telegram", 0, 2e9)["kod"] == "povel_neplatny"


def test_povel_necitelny_transkript(tmp_path):
    assert over(str(tmp_path / "chybi.jsonl"), "/telegram start 1h")["kod"] == "transkript_necitelny"


def test_povel_jako_zprava_z_telegramu_nikdy(tmp_path):
    cesta = tr(tmp_path, [tg.peer("[Telegram #1 · text]\n/telegram start 1h", prompt_id="p-povel")])
    assert not over(cesta, "/telegram start 1h")["ok"]


def test_verze():
    assert puvod.verze_ok("2.1.314") and puvod.verze_ok("2.1.400")
    assert not puvod.verze_ok("2.1.313") and not puvod.verze_ok("2.2.0") and not puvod.verze_ok("3.0.0")
    assert not puvod.verze_ok(None) and not puvod.verze_ok("2.1")


# ---------------------------------------------------------------- doručení, nečinnost

def test_doruceni(tmp_path):
    cesta = tr(tmp_path, [tg.peer()])
    assert puvod.najdi_doruceni(cesta, "4821", ID) == "doruceno"
    assert puvod.najdi_doruceni(cesta, "999", ID) is None
    cesta = tr(tmp_path, [tg.peer(identita=None)])
    assert puvod.najdi_doruceni(cesta, "4821", ID) == "chyba_tvaru"
    cesta = tr(tmp_path, [tg.fronta()])
    assert puvod.najdi_doruceni(cesta, "4822", ID) == "doruceno"


def test_necinnost(tmp_path):
    ted = time.time()
    cesta = tr(tmp_path, [tg.clovek(), tg.asistent_nastroj(), tg.vysledek_nastroje(), tg.konec_tahu()])
    r = puvod.stav_session(cesta, ted, 90)
    assert r["stav"] == "necinna" and r["jak"] == "konec"
    cesta = tr(tmp_path, [tg.clovek(), tg.asistent_nastroj()])
    assert puvod.stav_session(cesta, ted, 90)["stav"] == "pracuje"
    assert puvod.stav_session(cesta, ted + 1000, 90)["stav"] == "pracuje"  # běžící nástroj
    cesta = tr(tmp_path, [tg.clovek(), tg.asistent_nastroj(), tg.vysledek_nastroje()])
    assert puvod.stav_session(cesta, ted, 90)["stav"] == "pracuje"
    r = puvod.stav_session(cesta, ted + 91, 90)
    assert r["stav"] == "necinna" and r["jak"] == "preruseno"
    assert puvod.stav_session(str(tmp_path / "nic.jsonl"), ted, 90)["stav"] == "necinna"


def test_remote_control(tmp_path):
    assert puvod.ma_remote_control(tr_fix(tmp_path, "human-typed"))
    assert not puvod.ma_remote_control(tr_fix(tmp_path, "vykricnik"))
    assert not puvod.ma_remote_control(tr(tmp_path, [tg.bridge(url=None)]))


def test_okno_plati():
    ted = 1000.0
    o = {"stav": "otevreno", "session_id": tg.SID, "konec": 2000, "posledni_zprava": 900, "necinnost_min": 60}
    assert puvod.okno_plati(o, tg.SID, ted)
    assert not puvod.okno_plati(o, tg.SID2, ted)
    assert not puvod.okno_plati(dict(o, stav="overovani"), tg.SID, ted)
    assert not puvod.okno_plati(o, tg.SID, 2000)
    assert not puvod.okno_plati(dict(o, posledni_zprava=0, necinnost_min=10), tg.SID, ted)
    assert not puvod.okno_plati(dict(o, konec=True), tg.SID, ted)


def test_identita_vlastniho_procesu():
    st = puvod.stat_procesu(os.getpid())
    assert st is not None and st[2].isdigit()
    assert puvod.zije(os.getpid(), st[2]) and not puvod.zije(os.getpid(), "1")


# ---------------------------------------------------------------- jen stdlib (S5)

STDLIB = {"__future__", "argparse", "datetime", "fcntl", "hashlib", "importlib", "json", "os", "re", "shlex",
          "stat", "sys", "tempfile", "time", "unicodedata", "fnmatch", "urllib"}


@pytest.mark.parametrize("modul", ["rozsah_hook.py", "puvod.py", "politika.py"])
def test_jen_standardni_knihovna(modul):
    zdroj = Path(puvod.__file__).with_name(modul).read_text(encoding="utf-8")
    for uzel in ast.walk(ast.parse(zdroj)):
        if isinstance(uzel, ast.Import):
            for a in uzel.names:
                assert a.name.split(".")[0] in STDLIB, a.name
        elif isinstance(uzel, ast.ImportFrom):
            if uzel.level:
                assert {a.name for a in uzel.names} <= {"politika", "puvod"}, ast.dump(uzel)
            else:
                assert uzel.module.split(".")[0] in STDLIB, uzel.module
