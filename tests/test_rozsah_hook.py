"""Hook rozsahu: rozhodovací tabulka P0–P7 (7.5), subagenti (R-H1-3), selhání (S5), události."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mcp_voice import puvod, rozsah_hook

from . import tg

IDENT = (os.getpid(), "1")


class Prostredi:
    def __init__(self, tmp_path, monkeypatch):
        self.home = tmp_path / "home"
        (self.home / "proj").mkdir(parents=True)
        (self.home / ".ssh").mkdir()
        self.proj = self.home / ".claude" / "projects" / "-proj"
        self.stav = tmp_path / "stav"
        monkeypatch.setenv("HOME", str(self.home))
        self.tp = tg.zapis_transkript(self.proj, [tg.clovek()])

    def transkript(self, *zaznamy):
        self.tp = tg.zapis_transkript(self.proj, list(zaznamy))

    def okno(self, **kw):
        tg.stav_okna(self.stav, IDENT, tp=self.tp, **kw)
        tg.politika_snimek(self.stav, self.home, IDENT, **{k: v for k, v in kw.items() if k in ("bwrap", "overena_verze", "bash_bez_dotazu")})

    def pre(self, nastroj="Bash", vstup=None, ted=None, **kw):
        d = {"hook_event_name": "PreToolUse", "session_id": tg.SID, "transcript_path": self.tp,
             "cwd": str(self.home / "proj"), "tool_name": nastroj, "tool_input": vstup or {"command": "ls"},
             "tool_use_id": "toolu_x0001", "prompt_id": "p-1", "permission_mode": "auto"}
        d.update(kw)
        v = rozsah_hook.pre(d, str(self.stav), ted, identita=IDENT)
        return None if v is None else v["hookSpecificOutput"]


@pytest.fixture
def p(tmp_path, monkeypatch):
    return Prostredi(tmp_path, monkeypatch)


def test_p0_pojistka_i_bez_okna(p):
    for prikaz in ("ls /run/user/1000/cc-socks/", "echo $CLAUDE_CODE_MESSAGING_SOCKET", "cat ~/.local/state/mcp-voice/x",
                   "cat %s/x" % p.stav):
        assert p.pre(vstup={"command": prikaz})["permissionDecision"] == "deny", prikaz
    assert p.pre(vstup={"command": "ls"}) is None


def test_p0_vypnuta_pojistka(p):
    puvod.zapis(os.path.join(puvod.adresar(p.stav), "pojistka-vypnuta"), {})
    assert p.pre(vstup={"command": "echo $CLAUDE_CODE_MESSAGING_SOCKET"}) is None


def test_p1_bez_okna_nic_necte(p, monkeypatch):
    def zakaz(*a, **k):
        raise AssertionError("mimo okno se nic nečte")
    monkeypatch.setattr(puvod, "cti", zakaz)
    monkeypatch.setattr(puvod, "klasifikuj", zakaz)
    assert p.pre("Write", {"file_path": "/x"}) is None


def test_p1_bez_claude(p):
    v = rozsah_hook.pre({"tool_name": "Read", "tool_input": {}}, str(p.stav), identita=False or None)
    assert v is None or v["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_p2_bez_claude_a_otevrene_okno(p, monkeypatch):
    p.okno()
    monkeypatch.setattr(rozsah_hook, "_identita_claude", lambda: None)
    v = rozsah_hook.pre({"tool_name": "Read", "tool_input": {}}, str(p.stav))
    assert v["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_p2_poskozena_politika_a_vyjimka(p, monkeypatch):
    p.transkript(tg.peer())
    p.okno()
    Path(puvod.soubor(p.stav, "politika", puvod.klic_procesu(*IDENT))).write_text("{rozbité")
    assert p.pre("Read", {"file_path": "x"})["permissionDecision"] == "deny"
    p.okno()
    monkeypatch.setattr(rozsah_hook.politika, "rozhodni", lambda *a, **k: 1 / 0)
    v = p.pre("Read", {"file_path": "x"})
    assert v["permissionDecision"] == "deny" and "chyba hooku rozsahu" in v["permissionDecisionReason"]


def test_n2_vyjimka_pri_zavrenem_okne(p, monkeypatch):
    """N2: výjimka při zavřeném okně = průchod jen pro tah, který prokazatelně není z Telegramu."""
    p.okno(je_otevreno=False)
    monkeypatch.setattr(rozsah_hook, "heartbeat", lambda *a: 1 / 0)
    assert p.pre("Read", {"file_path": "x"}) is None  # tah člověka
    monkeypatch.setattr(rozsah_hook.puvod, "klasifikuj", lambda *a, **k: 1 / 0)
    assert p.pre("Read", {"file_path": "x"})["permissionDecision"] == "deny"  # třída neznámá


def test_p2_poskozene_okno(p):
    Path(puvod.soubor(p.stav, "okno", puvod.klic_procesu(*IDENT))).parent.mkdir(parents=True)
    Path(puvod.soubor(p.stav, "okno", puvod.klic_procesu(*IDENT))).write_text("[")
    assert p.pre("Read", {"file_path": "x"})["permissionDecision"] == "deny"


def test_p3_zavrene_okno_a_tah_z_telegramu(p):
    p.transkript(tg.peer())
    p.okno(je_otevreno=False)
    assert p.pre("Read", {"file_path": "x"})["permissionDecision"] == "deny"
    assert p.pre("mcp__hlas__stav", {}) is None


def test_p3_vyprsene_okno_bez_serveru(p):
    p.transkript(tg.peer())
    ted = time.time()
    p.okno(konec=ted - 1)
    assert p.pre("Read", {"file_path": "x"}, ted=ted)["permissionDecision"] == "deny"
    p.okno(posledni_zprava=ted - 3601, necinnost_min=60)
    assert p.pre("Read", {"file_path": "x"}, ted=ted)["permissionDecision"] == "deny"


def test_p3_clear_stejny_proces_jina_session(p):
    p.transkript(tg.peer())
    p.okno(session_id=tg.SID2)
    assert p.pre("Read", {"file_path": "x"})["permissionDecision"] == "deny"


def test_p4_zavrene_okno_clovek_heartbeat(p):
    p.okno(je_otevreno=False)
    assert p.pre("Write", {"file_path": "x"}) is None
    assert puvod.cti(puvod.soubor(p.stav, "rozsah", tg.SID))["claude_pid"] == IDENT[0]


def test_p4_overovani(p):
    p.okno(stav="overovani")
    assert p.pre("mcp__hlas__odpovez", {"text": "x"}) is None


def test_p5_clovek_v_otevrenem_okne(p):
    p.okno()
    assert p.pre("Bash", {"command": "rm -rf build"}) is None
    v = p.pre("mcp__hlas__odpovez", {"text": "x"})
    assert v["permissionDecision"] == "deny" and "NOT_TELEGRAM_TURN" in v["permissionDecisionReason"]
    assert p.pre("mcp__hlas__odpovez", {"text": "x"}, prompt_id="prompt-povel") is None


def test_p5_uloha(p):
    p.transkript(tg.notifikace_ulohy())
    p.okno()
    assert p.pre("Bash", {"command": "make"}) is None


@pytest.mark.parametrize("zaznam", [tg.peer(identita=tg.CIZI), tg.naplanovany(), tg.navrh(), tg.peer(identita=None)])
def test_p6_omezeny(p, zaznam):
    p.transkript(tg.konec_tahu(), dict(zaznam, turnOrigin="sdk") if zaznam.get("promptSource") == "suggestion_accepted"
                 else zaznam)
    p.okno()
    assert p.pre("Bash", {"command": "ls"})["permissionDecision"] == "deny"
    assert p.pre("Read", {"file_path": "a.txt"})["permissionDecision"] == "allow"
    assert p.pre("mcp__hlas__odpovez", {"text": "x"})["permissionDecision"] == "deny"


def test_p6_naplanovany_bez_omezeni(p):
    p.transkript(tg.naplanovany())
    p.okno()
    pol = puvod.cti(puvod.soubor(p.stav, "politika", puvod.klic_procesu(*IDENT)))
    pol["omezit_naplanovane"] = False
    puvod.zapis(puvod.soubor(p.stav, "politika", puvod.klic_procesu(*IDENT)), pol)
    assert p.pre("Bash", {"command": "ls"}) is None


def test_p7_telegram_rezimy(p):
    p.transkript(tg.peer())
    p.okno(rezim="cteni")
    assert p.pre("Bash", {"command": "ls"})["permissionDecision"] == "deny"
    p.okno(rezim="prace")
    v = p.pre("Bash", {"command": "git pull"})
    assert v["permissionDecision"] == "ask" and "#4821" in v["permissionDecisionReason"]
    assert p.pre("Read", {"file_path": "~/.ssh/id"})["permissionDecision"] == "deny"
    assert p.pre("SendMessage", {"to": "x"})["permissionDecision"] == "deny"
    assert p.pre("mcp__hlas__odpovez", {"text": "x"})["permissionDecision"] == "allow"
    p.okno(rezim="plny", bwrap="/usr/bin/bwrap", overena_verze=True)
    v = p.pre("Bash", {"command": "ls"})
    assert v["permissionDecision"] == "ask" and v["updatedInput"]["command"].startswith("/usr/bin/bwrap ")
    p.okno(rezim="plny", bwrap="/usr/bin/bwrap", overena_verze=True, bash_bez_dotazu=True)
    v = p.pre("Bash", {"command": "ls"})
    assert v["permissionDecision"] == "allow" and v["updatedInput"]["command"].startswith("/usr/bin/bwrap ")
    assert p.pre("Write", {"file_path": "x.txt"}) is None


def post(p, prikaz, tid="toolu_x0001", nastroj="Bash"):
    d = {"hook_event_name": "PostToolUse", "session_id": tg.SID, "transcript_path": p.tp, "cwd": str(p.home / "proj"),
         "tool_name": nastroj, "tool_input": {"command": prikaz}, "tool_use_id": tid, "prompt_id": "p-1"}
    return rozsah_hook.post(d, str(p.stav), time.time(), identita=IDENT)


def zadost_zavrit(p):
    return puvod.cti_tise(puvod.soubor(p.stav, "zadost", tg.SID), {})


@pytest.mark.parametrize("rezim, navic", [("plny", {"bash_bez_dotazu": True}), ("plny", {}), ("prace", {})])
def test_obal_post_bash_bez_obalu_zavre_okno(p, rezim, navic):
    """pre dal Bashi obal; PostToolUse s jiným příkazem (obal zahodil cizí hook) → žádost o zavření."""
    p.transkript(tg.peer())
    p.okno(rezim=rezim, bwrap="/usr/bin/bwrap", overena_verze=True, **navic)
    v = p.pre("Bash", {"command": "ls tajne"})
    assert v["updatedInput"]["command"].startswith("/usr/bin/bwrap ")
    assert post(p, v["updatedInput"]["command"]) is None and zadost_zavrit(p) == {}  # běžel s obalem
    assert post(p, "ls tajne") is None  # záznam už spotřebován
    p.pre("Bash", {"command": "ls tajne"}, tool_use_id="toolu_y0001")
    assert post(p, "ls tajne", tid="toolu_y0001")["duvod"] == rozsah_hook.DUVOD_BEZ_OBALU
    z = zadost_zavrit(p)
    assert z["akce"] == "zavrit" and z["duvod"] == "bash_bez_obalu" and z["claude_pid"] == IDENT[0]


def test_obal_post_bez_zaznamu_nic(p):
    """Bash bez obalu, který pre neobalil (strom nedoběhl, mimo okno, člověk), PostToolUse neřeší."""
    assert post(p, "ls") is None and zadost_zavrit(p) == {}
    p.transkript(tg.peer())
    p.okno(rezim="prace")  # bez bwrap: dotaz bez obalu, nic se nezapíše
    assert p.pre("Bash", {"command": "ls"})["permissionDecision"] == "ask"
    assert post(p, "ls") is None and post(p, "x", nastroj="Read") is None and zadost_zavrit(p) == {}


def _integrita(p, tid="toolu_x0001"):
    return puvod.soubor(p.stav, "integrita", "%s-%s" % (tg.SID, tid))


def test_integrita_beze_zmeny_nic(p):
    """pre uloží snímek zapisovatelných kořenů k tool_use_id, post beze změny nic nezavře a snímek smaže."""
    p.transkript(tg.peer())
    p.okno(rezim="prace", bwrap="/usr/bin/bwrap", overena_verze=True)
    v = p.pre("Bash", {"command": "ls"})
    assert v["permissionDecision"] == "ask" and os.path.exists(_integrita(p))
    assert puvod.cti(_integrita(p), 4 * 1024 * 1024)["koreny"] == [str(p.home / "proj")]
    assert post(p, v["updatedInput"]["command"]) is None and zadost_zavrit(p) == {}
    assert not os.path.exists(_integrita(p))


@pytest.mark.parametrize("zmena", ["mcp", "claude", "vnoreny"])
def test_integrita_nova_konfigurace_zavre_okno(p, zmena):
    """Bash z Telegramu (s obalem) založil konfiguraci agenta → zavřít okno s cestami."""
    p.transkript(tg.peer())
    p.okno(rezim="plny", bwrap="/usr/bin/bwrap", overena_verze=True)
    v = p.pre("Bash", {"command": "ls"})
    cil = {"mcp": p.home / "proj" / ".mcp.json", "claude": p.home / "proj" / ".claude",
           "vnoreny": p.home / "proj" / "nove" / "CLAUDE.md"}[zmena]
    if zmena == "claude":
        cil.mkdir()
    else:
        cil.parent.mkdir(exist_ok=True)
        cil.write_text("x")
    vysledek = post(p, v["updatedInput"]["command"])
    assert vysledek["duvod"] == rozsah_hook.DUVOD_KONFIGURACE and str(cil) in vysledek["cesty"]
    z = zadost_zavrit(p)
    assert z["akce"] == "zavrit" and z["duvod"] == "konfigurace_agenta" and str(cil) in z["cesty"]
    assert not os.path.exists(_integrita(p))
    zprava = json.loads(json.dumps(rozsah_hook.vystup_post(vysledek)))["systemMessage"]
    assert "konfiguraci agenta" in zprava and str(cil) in zprava


@pytest.mark.parametrize("poskodit", ["smazat", "nesmysl"])
def test_integrita_bez_snimku_fail_closed(p, poskodit):
    p.transkript(tg.peer())
    p.okno(rezim="prace", bwrap="/usr/bin/bwrap", overena_verze=True)
    v = p.pre("Bash", {"command": "ls"})
    if poskodit == "smazat":
        os.unlink(_integrita(p))
    else:
        with open(_integrita(p), "w") as fh:
            fh.write("{nesmysl")
    vysledek = post(p, v["updatedInput"]["command"])
    assert vysledek["duvod"] == rozsah_hook.DUVOD_KONFIGURACE and vysledek["nedobehla"] is True
    assert zadost_zavrit(p)["duvod"] == "konfigurace_agenta"


def test_post_vyjimka_zavre_okno(p, monkeypatch):
    """chyba kontroly po běhu Bash z Telegramu zavře okno (fail-closed)."""
    p.transkript(tg.peer())
    p.okno(rezim="prace", bwrap="/usr/bin/bwrap", overena_verze=True)
    v = p.pre("Bash", {"command": "ls"})

    def chyba(*a, **k):
        raise OSError("x")
    monkeypatch.setattr(rozsah_hook.politika, "kontrola_integrity", chyba)
    assert post(p, v["updatedInput"]["command"])["duvod"] == rozsah_hook.DUVOD_KONFIGURACE
    assert zadost_zavrit(p)["akce"] == "zavrit"


def test_cekajici_zavreni_zamitne_tah_z_telegramu(p):
    """dokud server nezavřel okno podle žádosti, pre tahu z Telegramu nic nepustí; člověk ano."""
    p.transkript(tg.peer())
    p.okno(rezim="plny", bwrap="/usr/bin/bwrap", overena_verze=True, bash_bez_dotazu=True)
    assert p.pre("Bash", {"command": "ls"})["permissionDecision"] == "allow"
    rozsah_hook.zavrit({"session_id": tg.SID}, str(p.stav), time.time(), IDENT, "konfigurace_agenta")
    for nastroj, vstup in (("Bash", {"command": "ls"}), ("Read", {"file_path": "x"}), ("Write", {"file_path": "x"})):
        v = p.pre(nastroj, vstup)
        assert v["permissionDecision"] == "deny" and "zavírá" in v["permissionDecisionReason"], nastroj
    p.transkript(tg.clovek())
    assert p.pre("Bash", {"command": "ls"}) is None
    # žádost jiného procesu claude (server ji nezpracuje) tah z Telegramu neblokuje
    p.transkript(tg.peer())
    puvod.zapis(puvod.soubor(p.stav, "zadost", tg.SID), {"akce": "zavrit", "duvod": "clear", "claude_pid": 1,
                                                          "claude_start": "9", "session_id": tg.SID})
    assert p.pre("Bash", {"command": "ls"})["permissionDecision"] == "allow"


def test_obal_pre_bez_tool_use_id_zamitne(p):
    p.transkript(tg.peer())
    p.okno(rezim="prace", bwrap="/usr/bin/bwrap")
    v = p.pre("Bash", {"command": "ls"}, tool_use_id=None)
    assert v["permissionDecision"] == "deny"


def test_p7_znacka_prompt_id(p):
    p.okno()
    puvod.zapis(puvod.soubor(p.stav, "znacky", tg.SID), {"p-1": {"znacka": "77", "cas": time.time()}})
    v = p.pre("Bash", {"command": "ls"})
    assert v["permissionDecision"] == "ask" and "#77" in v["permissionDecisionReason"]


# ---------------------------------------------------------------- subagenti

def subagent(p, udalost, **kw):
    d = dict(tg.fixture_hook("subagent-start" if udalost == "start" else "subagent-stop"),
             transcript_path=p.tp, prompt_id="p-1")
    d.update(kw)
    if udalost == "start":
        rozsah_hook.subagent_start(d, str(p.stav), time.time(), IDENT)
    else:
        rozsah_hook.subagent_stop(d, str(p.stav), time.time())


def test_s5_znacka_cloveka_a_posledni_vlozeni(p, monkeypatch):
    """S5: dlouhý tah člověka (začátek mimo rozpočet) se značkou prompt_id je člověk, dokud server
    po značce nevložil zprávu z Telegramu."""
    monkeypatch.setattr(puvod, "MAX_OTEVRENO", 4096)
    vypln = [tg.asistent_nastroj(tid="toolu_%05d" % i) for i in range(400)]
    p.transkript(tg.clovek(), *vypln)
    ted = time.time()
    puvod.zapis(puvod.soubor(p.stav, "znacky", tg.SID), {"p-1": {"trida": "clovek", "cas": ted - 50}})
    p.okno(posledni_vlozeni=ted - 100)
    assert p.pre("Bash", {"command": "make"}) is None
    p.okno(posledni_vlozeni=ted - 10)
    v = p.pre("Bash", {"command": "make"})
    assert v["permissionDecision"] == "deny" and "nezname" in v["permissionDecisionReason"]


def test_s5_znacku_tahu_cloveka_pise_pretooluse(p, monkeypatch):
    """S5: značku prompt_id → člověk zapíše hook rozsahu (v UserPromptSubmit záznam promptu ještě není),
    s časem začátku tahu. Dlouhý tah pak zůstane člověk i se začátkem mimo rozpočet."""
    ted = time.time()
    p.transkript(tg.clovek("oprav test", t=ted - 5, prompt_id="p-1"))
    p.okno()
    assert p.pre("Bash", {"command": "make"}) is None
    z = puvod.cti(puvod.soubor(p.stav, "znacky", tg.SID))["p-1"]
    assert z["trida"] == "clovek" and abs(z["cas"] - (ted - 5)) < 0.01
    monkeypatch.setattr(puvod, "MAX_OTEVRENO", 4096)
    tg.pripis(p.tp, *[tg.asistent_nastroj(tid="toolu_%05d" % i) for i in range(400)])
    assert p.pre("Bash", {"command": "make"}) is None
    p.okno(posledni_vlozeni=ted)  # server po začátku tahu vložil zprávu: značka neplatí
    assert p.pre("Bash", {"command": "make"})["permissionDecision"] == "deny"


@pytest.mark.parametrize("zaznam", [
    tg.clovek("x", prompt_id="p-jiny"),
    tg.clovek("x"),
    dict(tg.navrh(), promptId="p-1"),
    tg.peer("x", identita=tg.CIZI, prompt_id="p-1"),
    tg.peer(prompt_id="p-1"),
])
def test_s5_znacka_jen_lidsky_zacatek_tohoto_promptu(p, zaznam):
    p.transkript(zaznam)
    p.okno()
    p.pre("Read", {"file_path": str(p.home / "proj" / "a.txt")})
    assert "p-1" not in puvod.cti_tise(puvod.soubor(p.stav, "znacky", tg.SID), {})


def test_s5_bez_okna_nic(p):
    p.transkript(tg.clovek("x", prompt_id="p-1"))
    assert p.pre("Bash", {"command": "make"}) is None
    assert not os.path.exists(puvod.soubor(p.stav, "znacky", tg.SID))


def test_subagent_tahu_z_telegramu_omezeny_i_po_stop_hlavniho_tahu(p):
    p.transkript(tg.peer())
    p.okno()
    subagent(p, "start")
    tg.pripis(p.tp, tg.konec_tahu(), tg.clovek("další tah člověka"))
    v = p.pre("Bash", {"command": "ls"}, agent_id="a308a0941b3849e8c", prompt_id="p-2")
    assert v["permissionDecision"] == "ask"
    assert p.pre("Bash", {"command": "ls"}, prompt_id="p-2") is None  # hlavní tah člověka
    subagent(p, "stop")
    agenti = puvod.cti(puvod.soubor(p.stav, "agenti", tg.SID))
    assert agenti["a308a0941b3849e8c"]["stop"] is not None


def test_neznamy_agent_v_okne_omezeny(p):
    p.okno()
    v = p.pre("Bash", {"command": "ls"}, agent_id="neznamy")
    assert v["permissionDecision"] == "deny" and "Omezený" in v["permissionDecisionReason"]


def test_agent_mimo_okno_pruchod(p):
    p.okno(je_otevreno=False)
    assert p.pre("Bash", {"command": "ls"}, agent_id="neznamy") is None


def test_handback_agenta_z_telegramu(p):
    p.transkript(tg.peer())
    p.okno()
    subagent(p, "start")
    p.transkript(tg.handback())
    assert p.pre("Bash", {"command": "ls"})["permissionDecision"] == "ask"


# ---------------------------------------------------------------- události

def test_udalosti_a_zadost_o_zavreni(p):
    p.okno()
    d = tg.fixture_hook("notification")
    rozsah_hook.udalost(d, str(p.stav), 1.0, IDENT, "notifikace")
    rozsah_hook.udalost(dict(d, notification_type="idle_prompt"), str(p.stav), 2.0, IDENT, "notifikace")
    rozsah_hook.udalost(tg.fixture_hook("stop"), str(p.stav), 3.0, IDENT, "stop")
    u = puvod.cti_jsonl(puvod.soubor(p.stav, "udalosti", tg.SID, ".jsonl"))
    assert [x["udalost"] for x in u] == ["notifikace", "stop"] and "message" not in u[0]
    rozsah_hook.zavrit(tg.fixture_hook("session-start-clear"), str(p.stav), 4.0, IDENT, "clear")
    z = puvod.cti(puvod.soubor(p.stav, "zadost", tg.SID))
    assert z["akce"] == "zavrit" and z["duvod"] == "clear" and z["claude_pid"] == IDENT[0]


def test_udalosti_bez_nastroju_obnovi_heartbeat(p):
    """V2: heartbeat i z událostí bez nástrojů (Stop, Notification, SubagentStart/Stop)."""
    p.okno()
    cesta = puvod.soubor(p.stav, "rozsah", tg.SID)
    rozsah_hook.udalost(tg.fixture_hook("stop"), str(p.stav), 100.0, IDENT, "stop")
    assert puvod.cti(cesta)["claude_pid"] == IDENT[0]
    os.unlink(cesta)
    rozsah_hook.subagent_stop({"session_id": tg.SID, "agent_id": "a1"}, str(p.stav), 200.0, IDENT)
    assert puvod.cti(cesta)["claude_pid"] == IDENT[0]


def test_p0_pojistka_v_okne_obnovi_heartbeat(p):
    p.okno()
    assert p.pre(vstup={"command": "ls /run/user/1000/cc-socks/"})["permissionDecision"] == "deny"
    assert puvod.cti(puvod.soubor(p.stav, "rozsah", tg.SID))["claude_pid"] == IDENT[0]


def test_konstanta_heartbeatu_shodna_se_serverem():
    from mcp_voice import prijem
    assert prijem.HEARTBEAT_ZAPIS_S == rozsah_hook.HEARTBEAT_S


def test_udalosti_bez_okna_nic(p):
    rozsah_hook.udalost(tg.fixture_hook("stop"), str(p.stav), 3.0, IDENT, "stop")
    assert not os.path.exists(puvod.adresar(p.stav))


# ---------------------------------------------------------------- samostatný proces (python3 -I)

def spust(p, udalost, data):
    env = {"HOME": str(p.home), "PATH": os.environ.get("PATH", "")}
    cesta = Path(rozsah_hook.__file__)
    return subprocess.run([sys.executable, "-I", str(cesta), "--udalost", udalost, "--stav", str(p.stav)],
                          input=json.dumps(data).encode(), capture_output=True, env=env, timeout=20)


def test_proces_bez_okna_exit_0_bez_vystupu(p):
    r = spust(p, "pre", tg.fixture_hook("pre-tool-use"))
    assert r.returncode == 0 and r.stdout == b""


def test_proces_neexistujici_stav_varuje_a_neblokuje(p):
    assert not p.stav.exists()
    for udalost, jmeno in (("pre", "pre-tool-use"), ("stop", "stop")):
        r = spust(p, udalost, tg.fixture_hook(jmeno))
        assert r.returncode == 0 and r.stdout == b"" and b"neexistuje" in r.stderr and b"cesty.stav" in r.stderr
    p.stav.mkdir()
    r = spust(p, "pre", tg.fixture_hook("pre-tool-use"))
    assert r.returncode == 0 and r.stdout == b"" and r.stderr == b""


def test_proces_pojistka(p):
    d = dict(tg.fixture_hook("pre-tool-use"), tool_input={"command": "ls /tmp/cc-socks-1000"})
    r = spust(p, "pre", d)
    assert r.returncode == 0 and json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_proces_necitelny_vstup_exit_2(p):
    env = {"HOME": str(p.home), "PATH": os.environ.get("PATH", "")}
    r = subprocess.run([sys.executable, "-I", rozsah_hook.__file__, "--udalost", "pre", "--stav", str(p.stav)],
                       input=b"nejson", capture_output=True, env=env, timeout=20)
    assert r.returncode == 2


@pytest.mark.parametrize("udalost, jmeno", [("stop", "stop"), ("notifikace", "notification"),
                                            ("subagent-start", "subagent-start"), ("konec", "session-end")])
def test_proces_ostatni_udalosti_exit_0(p, udalost, jmeno):
    r = spust(p, udalost, tg.fixture_hook(jmeno))
    assert r.returncode == 0 and r.stdout == b""


def test_adresar_stavu():
    assert rozsah_hook.adresar_stavu("/x", {}) == "/x"
    assert rozsah_hook.adresar_stavu(None, {"MCP_VOICE_STATE_DIR": "/y"}) == "/y"
    assert rozsah_hook.adresar_stavu(None, {}).endswith(".local/state/mcp-voice")
