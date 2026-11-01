"""Hook strážce `mcp-voice-guard`: subagenti, razítko, citlivé kontexty, souhlasy, sken transkriptu.

Fiktivní kontext ACME. Hook běží v procesu (rychlost) a v několika testech i jako samostatný proces.
"""
from __future__ import annotations

import datetime
import io
import json
import os
import subprocess
import sys
import time

import pytest
import yaml

from mcp_voice import config, strazce_hook
from mcp_voice.config import ChybaKonfigurace

# Stejný vektor ověřuje strazce.py: hash musí sedět na obou stranách.
VEKTOR = ({"text": "Dobrý den, čtu zprávu.", "cil": "auto", "ulozit": False, "hlas": None},
          "99a52086c7831e14a7693de02946c837369f17f72362e3ee1edc0f3652609d16")

ACME = {
    "nazev": "ACME",
    "upozorneni": "Session pracuje s daty ACME.",
    "platnost": "den",
    "signaly": {
        "mcp": ["acme-db", "acme-*", "tickets-*", "ide", "timesheet", "mail_365"],
        "agenti": ["mail-assistant"],
        "skilly": ["acme", "acme-report"],
        "cesty": ["~/work/acme", "~/notes/kb/acme"],
        "texty": [".agents/skills/acme/"],
        "vstupy": [
            {"nastroj": "mcp__notes__*", "pole": "area", "hodnoty": ["acme"]},
            {"nastroj": "mcp__notes__*", "pole": ["rel_path", "path"], "predpony": ["kb/acme/", "acme/"]},
        ],
    },
}


def tool_use(jmeno, vstup, cwd="/tmp"):
    return {"type": "assistant", "cwd": cwd,
            "message": {"content": [{"type": "tool_use", "id": "x", "name": jmeno, "input": vstup}]}}


class Prostredi:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        self.stav = tmp_path / "stav"
        self.proj = self.home / ".claude" / "projects" / "-p"
        self.proj.mkdir(parents=True)
        (self.home / "work" / "acme" / "app").mkdir(parents=True)
        self.kfg = tmp_path / "config.yaml"
        self.n = 0
        self.mp = monkeypatch
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv("MCP_VOICE_CONFIG", str(self.kfg))
        self.nastav()

    def nastav(self, kontexty=(ACME,), **strazce):
        d = {"cesty": {"stav": str(self.stav)}, "strazce": dict(strazce, citlive_kontexty=list(kontexty))}
        self.kfg.write_text(yaml.safe_dump(d, allow_unicode=True))

    def session(self, zaznamy=(), subagenti=None):
        sid = "%08x-0000-4000-8000-%012x" % (os.getpid() & 0xffffffff, self.n)
        self.n += 1
        tp = self.proj / f"{sid}.jsonl"
        tp.write_text("".join(json.dumps(z) + "\n" for z in zaznamy))
        for jmeno, zaz in (subagenti or {}).items():
            d = self.proj / sid / "subagents"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"agent-{jmeno}.jsonl").write_text("".join(json.dumps(z) + "\n" for z in zaz))
        return sid, str(tp)

    def tid(self):
        self.n += 1
        return "toolu_test%08d" % self.n

    def run(self, data, surovy=None):
        self.mp.setattr(sys, "stdin", io.StringIO(surovy if surovy is not None else json.dumps(data)))
        out, puvodni = io.StringIO(), sys.stdout
        sys.stdout = out
        stary = os.umask(0o022)
        try:
            strazce_hook.main()
        finally:
            os.umask(stary)
            sys.stdout = puvodni
        text = out.getvalue().strip()
        d = json.loads(text)["hookSpecificOutput"] if text else None
        return (d["permissionDecision"] if d else "pass"), (d or {}).get("permissionDecisionReason", "")

    def razitko(self, tid):
        p = self.stav / "razitka" / f"{tid}.json"
        return json.loads(p.read_text()) if p.exists() else None

    def souhlasy(self):
        p = self.stav / "strazce" / "souhlasy-v2.json"
        return json.loads(p.read_text()) if p.exists() else {}


@pytest.fixture
def P(tmp_path, monkeypatch):
    return Prostredi(tmp_path, monkeypatch)


def pre(sid, tp, tid, nastroj="rekni", vstup=None, **navic):
    d = {"hook_event_name": "PreToolUse", "session_id": sid, "transcript_path": tp, "cwd": "/tmp",
         "tool_name": "mcp__hlas__" + nastroj, "tool_input": vstup if vstup is not None else {"text": "Ahoj"},
         "tool_use_id": tid, "permission_mode": "default"}
    d.update(navic)
    return d


def post(sid, tid, **navic):
    d = {"hook_event_name": "PostToolUse", "session_id": sid, "tool_name": "mcp__hlas__rekni", "tool_use_id": tid}
    d.update(navic)
    return d


# ------------------------------------------------------------- základ

@pytest.mark.parametrize("pole_agenta", ["agent_type", "agent_id"])
def test_subagent_deny_bez_razitka(P, pole_agenta):
    sid, tp = P.session([tool_use("Bash", {"command": "ls"})])
    tid = P.tid()
    assert P.run(pre(sid, tp, tid, **{pole_agenta: "pomocnik"}))[0] == "deny"
    assert P.razitko(tid) is None


@pytest.mark.parametrize("nastroj", ["stav", "zastav"])
def test_stav_a_zastav_bez_zasahu(P, nastroj):
    sid, tp = P.session([])
    tid = P.tid()
    assert P.run(pre(sid, tp, tid, nastroj, {}))[0] == "pass"
    assert P.razitko(tid) is None


def test_ne_mcp_nastroj_bez_zasahu(P):
    sid, tp = P.session([])
    d = pre(sid, tp, P.tid())
    d["tool_name"] = "Bash"
    assert P.run(d)[0] == "pass"


def test_prepis_lokalne_razitko_bez_dotazu(P):
    sid, tp = P.session([tool_use("mcp__acme-db__query", {})])
    tid = P.tid()
    assert P.run(pre(sid, tp, tid, "prepis", {"cesta": "/x.ogg"}))[0] == "pass"
    assert P.razitko(tid) is not None


def test_prepis_cloud_vzdy_dotaz(P):
    sid, tp = P.session([tool_use("Bash", {"command": "ls"})])
    for _ in range(2):
        tid = P.tid()
        rozh, duvod = P.run(pre(sid, tp, tid, "prepis", {"cesta": "/x.ogg", "engine": "cloud"}))
        assert rozh == "ask" and "Scribe" in duvod and P.razitko(tid) is not None
        P.run(post(sid, tid, tool_name="mcp__hlas__prepis"))
    assert P.souhlasy() == {}  # souhlas s cloudem bez kontextu nezakládá souhlas pro session


def test_prepis_cloud_v_bypass_deny(P):
    sid, tp = P.session([])
    tid = P.tid()
    rozh, duvod = P.run(pre(sid, tp, tid, "prepis", {"cesta": "/x.ogg", "engine": "cloud"},
                            permission_mode="bypassPermissions"))
    assert rozh == "deny" and "bypassPermissions" in duvod and P.razitko(tid) is None


def test_prepis_cloud_povolit(P):
    P.nastav(prepis_cloud="povolit")
    sid, tp = P.session([])
    tid = P.tid()
    assert P.run(pre(sid, tp, tid, "prepis", {"cesta": "/x.ogg", "engine": "cloud"}))[0] == "pass"
    assert P.razitko(tid) is not None
    sid, tp = P.session([tool_use("mcp__acme-db__query", {})])
    rozh, duvod = P.run(pre(sid, tp, P.tid(), "prepis", {"cesta": "/x.ogg", "engine": "cloud"}))
    assert rozh == "ask" and "ACME" in duvod and "Cloudový přepis" not in duvod


def test_cloud_v_citlive_session_jmenuje_kontext(P):
    sid, tp = P.session([tool_use("mcp__acme-db__query", {})])
    rozh, duvod = P.run(pre(sid, tp, P.tid(), "prepis", {"cesta": "/x.ogg", "engine": "cloud"}))
    assert rozh == "ask" and duvod.startswith("Cloudový přepis") and "ACME" in duvod


def test_rekni_bez_kontextu_razitko_protokol(P):
    sid, tp = P.session([tool_use("Bash", {"command": "ls"})])
    tid = P.tid()
    vstup, ocekavany = VEKTOR
    assert P.run(pre(sid, tp, tid, "rekni", vstup))[0] == "pass"
    r = P.razitko(tid)
    assert r["v"] == 1 and r["nastroj"] == "rekni" and r["sha256"] == ocekavany
    assert oct(os.stat(P.stav / "razitka" / f"{tid}.json").st_mode & 0o777) == "0o600"
    assert oct(os.stat(P.stav / "razitka").st_mode & 0o777) == "0o700"
    assert strazce_hook.kanonicky_hash(VEKTOR[0]) == VEKTOR[1]


# ------------------------------------------------------------- signály

SIGNALY = [
    ("MCP v subagentu", [], {"a1": [tool_use("mcp__mail_365__search", {"q": "x"})]}),
    ("agent", [tool_use("Agent", {"subagent_type": "mail-assistant", "prompt": "pošta"})], None),
    ("agent přes Task", [tool_use("Task", {"subagent_type": "mail-assistant", "prompt": "x"})], None),
    ("MCP db v subagentu", [], {"k": [tool_use("mcp__acme-db__query", {"sql": "x"})]}),
    ("MCP glob 1", [tool_use("mcp__tickets-acme__get", {})], None),
    ("MCP glob 2", [tool_use("mcp__tickets-acme-dev__get", {})], None),
    ("MCP glob 3", [tool_use("mcp__acme-wiki__query", {})], None),
    ("MCP timesheet", [tool_use("mcp__timesheet__add", {})], None),
    ("MCP ide", [tool_use("mcp__ide__get_file", {})], None),
    ("text v zadání", [tool_use("Agent", {"subagent_type": "dev",
                                          "prompt": "Čti /x/.agents/skills/acme/SKILL.md"})], None),
    ("cesta v zadání", [tool_use("Agent", {"subagent_type": "dev", "prompt": "Repo `~/work/acme`, oprav"})], None),
    ("skill", [tool_use("Skill", {"skill": "acme"})], None),
    ("skill 2", [tool_use("Skill", {"skill": "acme-report"})], None),
    ("skill s pluginem", [tool_use("Skill", {"skill": "acme:sub"})], None),
    ("Read v cestě", [tool_use("Read", {"file_path": "~/work/acme/app/a.php"})], None),
    ("Grep v cestě", [tool_use("Grep", {"path": "~/work/acme"})], None),
    ("relativní cesta vůči cwd", [tool_use("Read", {"file_path": "acme/app/a.php"}, cwd="__HOME__/work")], None),
    ("Bash s cestou", [tool_use("Bash", {"command": "cd ~/work/acme && git status"})], None),
    ("Bash s $HOME", [tool_use("Bash", {"command": "ls \"$HOME/work/acme/app\""})], None),
    ("Bash s absolutní cestou", [tool_use("Bash", {"command": "ls __HOME__/work/acme"})], None),
    ("vstup hodnota", [tool_use("mcp__notes__search", {"query": "x", "area": "acme"})], None),
    ("vstup předpona", [tool_use("mcp__notes__get_note", {"rel_path": "kb/acme/x.md"})], None),
    ("Read poznámek", [tool_use("Read", {"file_path": "~/notes/kb/acme/x.md"})], None),
    ("cwd řádku transkriptu", [tool_use("Glob", {"pattern": "*.php"}, cwd="__HOME__/work/acme/app")], None),
    ("slash příkaz", [{"type": "user", "message": {"role": "user",
                                                    "content": "<command-name>/acme</command-name>"}}], None),
    ("slash příkaz v seznamu", [{"type": "user", "message": {"role": "user", "content": [
        {"type": "text", "text": "<command-message>acme</command-message>\n<command-name>acme</command-name>"}]}}],
     None),
]


def _dosad_home(P, zaznamy):
    return json.loads(json.dumps(zaznamy).replace("__HOME__", str(P.home)))


@pytest.mark.parametrize("nazev,zaznamy,sub", SIGNALY, ids=[s[0] for s in SIGNALY])
def test_signal(P, nazev, zaznamy, sub):
    sid, tp = P.session(_dosad_home(P, zaznamy), _dosad_home(P, sub) if sub else None)
    tid = P.tid()
    rozh, duvod = P.run(pre(sid, tp, tid))
    assert rozh == "ask" and P.razitko(tid) is not None, duvod
    assert "Session pracuje s daty ACME." in duvod and "(ACME: " in duvod
    assert "Ahoj" not in duvod


BEZ_SIGNALU = [
    tool_use("Read", {"file_path": "/etc/hosts"}),
    tool_use("Read", {"file_path": "~/work/acme-other/a.txt"}),
    tool_use("Bash", {"command": "ls ~/work/acmex; echo acme"}),
    tool_use("mcp__notes__search", {"area": "linux"}),
    tool_use("mcp__notes__get_note", {"rel_path": "kb/acmex/x.md"}),
    tool_use("mcp__other__search", {"area": "acme"}),
    tool_use("Agent", {"subagent_type": "dev", "prompt": "web"}),
    tool_use("Skill", {"skill": "acmex"}),
    tool_use("mcp__hlas__rekni", {"text": "acme ~/work/acme"}),
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "x", "content": "<command-name>/acme</command-name>"}]}},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "<command-name>/acme</command-name>"}]}},
]


def test_bez_signalu_bez_dotazu(P):
    sid, tp = P.session(BEZ_SIGNALU)
    tid = P.tid()
    assert P.run(pre(sid, tp, tid)) == ("pass", "")
    assert P.razitko(tid) is not None


def test_cwd_hooku(P):
    sid, tp = P.session([])
    rozh, duvod = P.run(pre(sid, tp, P.tid(), cwd=str(P.home / "work" / "acme" / "app")))
    assert rozh == "ask" and "pracovní adresář" in duvod


def test_symlink_do_citlive_cesty(P):
    os.symlink(P.home / "work" / "acme", P.home / "zkratka")
    sid, tp = P.session([tool_use("Read", {"file_path": str(P.home / "zkratka" / "app" / "a.php")})])
    assert P.run(pre(sid, tp, P.tid()))[0] == "ask"


def test_bez_kontextu_jen_razitko(P):
    P.nastav(kontexty=())
    sid, tp = P.session([tool_use("mcp__acme-db__query", {})])
    tid = P.tid()
    assert P.run(pre(sid, tp, tid))[0] == "pass" and P.razitko(tid) is not None


def test_bez_sekce_strazce(P):
    P.kfg.write_text(yaml.safe_dump({"cesty": {"stav": str(P.stav)}}))
    sid, tp = P.session([tool_use("mcp__acme-db__query", {})])
    tid = P.tid()
    assert P.run(pre(sid, tp, tid))[0] == "pass" and P.razitko(tid) is not None


# ------------------------------------------------------------- souhlasy a platnost

def test_souhlas_na_den(P):
    sid, tp = P.session([tool_use("mcp__acme-db__query", {})])
    t1 = P.tid()
    rozh, duvod = P.run(pre(sid, tp, t1, vstup={"text": "Ahoj", "cil": "telegram"}))
    assert rozh == "ask" and "ElevenLabs -> Telegram" in duvod and "4 znaků" in duvod and "do konce dne" in duvod
    assert P.run(pre(sid, tp, P.tid()))[0] == "ask", "zamítnuto (bez PostToolUse) -> znovu dotaz"
    P.run(post(sid, t1, agent_id="x"))
    assert P.run(pre(sid, tp, P.tid()))[0] == "ask", "PostToolUse ze subagenta souhlas nedá"
    t2 = P.tid()
    P.run(pre(sid, tp, t2))
    P.run(post("00000000-0000-4000-8000-000000000000", t2))
    assert P.run(pre(sid, tp, P.tid()))[0] == "ask", "PostToolUse jiné session souhlas nedá"
    t5 = P.tid()
    P.run(pre(sid, tp, t5))
    P.run(post(sid, t5, hook_event_name="PostToolUseFailure"))
    assert P.run(pre(sid, tp, P.tid()))[0] == "ask", "PostToolUseFailure souhlas nedá"
    t3 = P.tid()
    P.run(pre(sid, tp, t3))
    P.run(post(sid, t3))
    t4 = P.tid()
    assert P.run(pre(sid, tp, t4))[0] == "pass" and P.razitko(t4) is not None
    sid2, tp2 = P.session([tool_use("mcp__acme-db__query", {})])
    assert P.run(pre(sid2, tp2, P.tid()))[0] == "ask", "jiná session znovu dotaz"
    vcera = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    s = P.souhlasy()
    s[sid]["ACME"]["den"] = vcera
    (P.stav / "strazce" / "souhlasy-v2.json").write_text(json.dumps(s))
    assert P.run(pre(sid, tp, P.tid()))[0] == "ask", "další den znovu dotaz"
    assert P.run(pre(sid, tp, P.tid(), permission_mode="bypassPermissions"))[0] == "deny"
    stat = json.loads((P.stav / "strazce" / "statistika.json").read_text())
    assert set(stat[datetime.date.today().isoformat()]) == {"dotazy", "souhlasy"}


def _souhlas(P, sid, tp):
    t = P.tid()
    rozh, duvod = P.run(pre(sid, tp, t))
    assert rozh == "ask"
    P.run(post(sid, t))
    return duvod


def test_platnost_session(P):
    P.nastav(kontexty=[dict(ACME, platnost="session")])
    sid, tp = P.session([tool_use("mcp__acme-db__query", {})])
    assert "do konce této session" in _souhlas(P, sid, tp)
    assert P.run(pre(sid, tp, P.tid()))[0] == "pass"
    s = P.souhlasy()
    s[sid]["ACME"]["den"] = "2000-01-01"  # u platnosti session na dni nezáleží
    (P.stav / "strazce" / "souhlasy-v2.json").write_text(json.dumps(s))
    assert P.run(pre(sid, tp, P.tid()))[0] == "pass"
    sid2, tp2 = P.session([tool_use("mcp__acme-db__query", {})])
    assert P.run(pre(sid2, tp2, P.tid()))[0] == "ask"


def test_platnost_volani(P):
    P.nastav(kontexty=[dict(ACME, platnost="volani")])
    sid, tp = P.session([tool_use("mcp__acme-db__query", {})])
    assert "jen pro toto volání" in _souhlas(P, sid, tp)
    assert P.run(pre(sid, tp, P.tid()))[0] == "ask"
    assert P.souhlasy() == {}


def test_zmena_platnosti_zrusi_souhlas(P):
    sid, tp = P.session([tool_use("mcp__acme-db__query", {})])
    _souhlas(P, sid, tp)
    assert P.run(pre(sid, tp, P.tid()))[0] == "pass"
    P.nastav(kontexty=[dict(ACME, platnost="session")])
    assert P.run(pre(sid, tp, P.tid()))[0] == "ask"


EXAMPLE = {"nazev": "Example Corp", "upozorneni": "Session pracuje s daty Example Corp.", "platnost": "session",
           "signaly": {"mcp": ["example-*"], "cesty": ["~/work/example"]}}


def test_vic_kontextu(P):
    P.nastav(kontexty=[ACME, EXAMPLE])
    sid, tp = P.session([tool_use("mcp__example-crm__get", {})])
    rozh, duvod = P.run(pre(sid, tp, P.tid()))
    assert rozh == "ask" and "Example Corp" in duvod and "ACME" not in duvod
    sid, tp = P.session([tool_use("mcp__example-crm__get", {}), tool_use("mcp__acme-db__query", {})])
    t = P.tid()
    rozh, duvod = P.run(pre(sid, tp, t))
    assert rozh == "ask" and "(ACME: MCP acme-db)" in duvod and "(Example Corp: MCP example-crm)" in duvod
    assert "ACME do konce dne" in duvod and "Example Corp do konce této session" in duvod
    P.run(post(sid, t))
    assert set(P.souhlasy()[sid]) == {"ACME", "Example Corp"}
    assert P.run(pre(sid, tp, P.tid()))[0] == "pass"
    s = P.souhlasy()
    del s[sid]["ACME"]
    (P.stav / "strazce" / "souhlasy-v2.json").write_text(json.dumps(s))
    rozh, duvod = P.run(pre(sid, tp, P.tid()))
    assert rozh == "ask" and "ACME" in duvod and "Example Corp" not in duvod, "dotaz jen na kontext bez souhlasu"


def test_novy_kontext_v_session_po_souhlasu(P):
    P.nastav(kontexty=[ACME, EXAMPLE])
    sid, tp = P.session([tool_use("mcp__acme-db__query", {})])
    _souhlas(P, sid, tp)
    with open(tp, "a") as fh:
        fh.write(json.dumps(tool_use("mcp__example-crm__get", {})) + "\n")
    rozh, duvod = P.run(pre(sid, tp, P.tid()))
    assert rozh == "ask" and "Example Corp" in duvod and "(ACME" not in duvod


def test_stary_format_souhlasu_se_ignoruje(P):
    sid, tp = P.session([tool_use("mcp__acme-db__query", {})])
    (P.stav / "strazce").mkdir(parents=True)
    (P.stav / "strazce" / "souhlasy-v2.json").write_text(json.dumps({sid: datetime.date.today().isoformat()}))
    assert P.run(pre(sid, tp, P.tid()))[0] == "ask"


# ------------------------------------------------------------- sken transkriptu

def test_prirustkove_cteni_a_trvaly_priznak(P):
    sid, tp = P.session([tool_use("Bash", {"command": "ls"})])
    assert P.run(pre(sid, tp, P.tid()))[0] == "pass"
    with open(tp, "a") as fh:
        fh.write(json.dumps(tool_use("mcp__acme-db__query", {})) + "\n")
    assert P.run(pre(sid, tp, P.tid()))[0] == "ask"
    with open(tp, "w") as fh:
        fh.write("{}\n")
    assert P.run(pre(sid, tp, P.tid()))[0] == "ask", "příznak je trvalý i po zkrácení transkriptu"


def test_neuplny_radek_docte_pristi_beh(P):
    sid, tp = P.session([tool_use("Bash", {"command": "ls"})])
    radek = json.dumps(tool_use("mcp__acme-db__query", {}))
    with open(tp, "a") as fh:
        fh.write(radek[:20])
    assert P.run(pre(sid, tp, P.tid()))[0] == "pass"
    with open(tp, "a") as fh:
        fh.write(radek[20:] + "\n")
    assert P.run(pre(sid, tp, P.tid()))[0] == "ask"


def test_zmena_signalu_znovu_projde_transkript(P):
    sid, tp = P.session([tool_use("mcp__crm__get", {})])
    assert P.run(pre(sid, tp, P.tid()))[0] == "pass"
    acme = json.loads(json.dumps(ACME))
    acme["signaly"]["mcp"].append("crm")
    P.nastav(kontexty=[acme])
    assert P.run(pre(sid, tp, P.tid()))[0] == "ask"


def test_stary_format_skenu_se_zahodi(P):
    sid, tp = P.session([tool_use("mcp__acme-db__query", {})])
    (P.stav / "strazce").mkdir(parents=True)
    velikost = os.path.getsize(tp)
    (P.stav / "strazce" / f"sken-{sid}.json").write_text(json.dumps({"citlivy": False, "offsety": {tp: velikost}}))
    assert P.run(pre(sid, tp, P.tid()))[0] == "ask"


def test_vykon_40_mb(P):
    radek = json.dumps(tool_use("Bash", {"command": "echo " + "x" * 4000})) + "\n"
    sid, tp = P.session([])
    with open(tp, "w") as fh:
        for _ in range(10000):
            fh.write(radek)
    t = time.monotonic()
    assert P.run(pre(sid, tp, P.tid()))[0] == "pass"
    prvni = time.monotonic() - t
    t = time.monotonic()
    P.run(pre(sid, tp, P.tid()))
    druhy = time.monotonic() - t
    assert prvni < 5 and druhy < 1, (prvni, druhy)


# ------------------------------------------------------------- selhání

def test_vnitrni_chyba_ask_s_razitkem(P):
    tid = P.tid()
    rozh, duvod = P.run(pre("neni-uuid", "/x.jsonl", tid))
    assert rozh == "ask" and "nešlo provést" in duvod and P.razitko(tid) is not None


def test_vnitrni_chyba_v_bypass_deny_bez_razitka(P):
    tid = P.tid()
    assert P.run(pre("neni-uuid", "/x.jsonl", tid, permission_mode="bypassPermissions"))[0] == "deny"
    assert P.razitko(tid) is None


def test_spatne_tool_use_id(P):
    rozh, _ = P.run(pre("neni-uuid", "/x.jsonl", "../spatne-id"))
    assert rozh == "ask"
    assert not (P.stav / "spatne-id.json").exists() and not (P.tmp / "spatne-id.json").exists()


def test_cizi_transkript(P):
    sid, _ = P.session([])
    assert P.run(pre(sid, "/etc/passwd", P.tid()))[0] == "ask"


def test_bez_kontextu_neni_treba_transkript(P):
    P.nastav(kontexty=())
    tid = P.tid()
    assert P.run(pre("neni-uuid", "/x.jsonl", tid))[0] == "pass" and P.razitko(tid) is not None


def test_rozbita_konfigurace_deny(P):
    P.kfg.write_text("strazce: {citlive_kontexty: [{nazev: X}]}")
    sid, tp = P.session([])
    tid = P.tid()
    rozh, duvod = P.run(pre(sid, tp, tid))
    assert rozh == "deny" and "Konfiguraci" in duvod and P.razitko(tid) is None
    assert P.run(pre(sid, tp, P.tid(), "stav", {}))[0] == "pass"


def test_hlidaci_casovac(P, monkeypatch):
    sid, tp = P.session([tool_use("mcp__acme-db__query", {})])
    monkeypatch.setattr(strazce_hook, "HLIDAC_S", 1)

    def pomalu(data, kfg):
        time.sleep(3)
        return {}
    monkeypatch.setattr(strazce_hook, "detekuj", pomalu)
    tid = P.tid()
    rozh, duvod = P.run(pre(sid, tp, tid))
    assert rozh == "ask" and "StrazceChyba" in duvod and P.razitko(tid) is not None


def test_post_bez_cekajiciho_nic(P):
    sid, _ = P.session([])
    P.run(post(sid, P.tid()))
    assert P.souhlasy() == {}


def test_uklid(P):
    adr = P.stav / "strazce"
    adr.mkdir(parents=True)
    stary = time.time() - 40 * 86400
    for jmeno in ("cekajici-toolu_starystary.json", "sken-x.json", "souhlasy-v2.json"):
        (adr / jmeno).write_text("{}")
        os.utime(adr / jmeno, (stary, stary))
    strazce_hook.uklid(P.stav)
    assert sorted(os.listdir(adr)) == ["souhlasy-v2.json"]


def test_prorez_souhlasu():
    den, ted = "2026-01-02", 1_000_000_000.0
    s = {"a": {"X": {"platnost": "den", "den": "2026-01-01", "cas": ted},
               "Y": {"platnost": "session", "den": "2025-01-01", "cas": ted - 10}},
         "b": {"X": {"platnost": "session", "cas": ted - 31 * 86400}}, "c": "2026-01-02"}
    assert strazce_hook._prorez_souhlasy(s, den, ted) == {"a": {"Y": s["a"]["Y"]}}


def _proces(P, vstup):
    env = dict(os.environ, HOME=str(P.home), MCP_VOICE_CONFIG=str(P.kfg))
    return subprocess.run([sys.executable, "-m", "mcp_voice.strazce_hook"], input=vstup, capture_output=True,
                          env=env, timeout=20)


@pytest.mark.parametrize("vstup", [b"nejson", b"[1]", b""])
def test_proces_necitelny_vstup_exit_2(P, vstup):
    r = _proces(P, vstup)
    assert r.returncode == 2 and r.stdout == b""


def test_proces_ask_a_post(P):
    sid, tp = P.session([tool_use("mcp__acme-db__query", {})])
    tid = P.tid()
    r = _proces(P, json.dumps(pre(sid, tp, tid)).encode())
    d = json.loads(r.stdout)["hookSpecificOutput"]
    assert r.returncode == 0 and d["permissionDecision"] == "ask" and d["hookEventName"] == "PreToolUse"
    r = _proces(P, json.dumps(post(sid, tid)).encode())
    assert r.returncode == 0 and r.stdout == b""
    assert "ACME" in P.souhlasy()[sid]


# ------------------------------------------------------------- schéma

def _kfg(kontexty, **strazce):
    return config.z_dat({"strazce": dict(strazce, citlive_kontexty=kontexty)})


def test_schema_platne():
    k = _kfg([ACME, EXAMPLE]).strazce.citlive_kontexty
    assert [x.nazev for x in k] == ["ACME", "Example Corp"]
    assert k[0].vstupy[0].pole == ("area",) and k[0].vstupy[1].predpony == ("kb/acme/", "acme/")
    assert k[1].platnost == "session" and k[1].agenti == ()
    assert _kfg(None).strazce.citlive_kontexty == () and _kfg([]).strazce.prepis_cloud == "dotaz"
    assert config.z_dat({}).strazce.citlive_kontexty == ()
    assert _kfg([{"nazev": "A", "upozorneni": "x", "signaly": {"mcp": "a-*"}}]).strazce.citlive_kontexty[0].mcp \
        == ("a-*",)


def _s(**zmeny):
    k = json.loads(json.dumps(ACME))
    for klic, v in zmeny.items():
        if v is None:
            k.pop(klic, None)
        else:
            k[klic] = v
    return k


def _sig(**zmeny):
    return _s(signaly=dict(json.loads(json.dumps(ACME["signaly"])), **zmeny))


@pytest.mark.parametrize("kontexty,strazce", [
    ("ACME", {}),
    ([ACME, ACME], {}),
    ([ACME, _s(nazev="acme")], {}),
    ([ACME] * 21, {}),
    (["x"], {}),
    ([_s(nazev=None)], {}),
    ([_s(nazev="")], {}),
    ([_s(nazev="a/b")], {}),
    ([_s(nazev="x" * 41)], {}),
    ([_s(upozorneni=None)], {}),
    ([_s(upozorneni="   ")], {}),
    ([_s(upozorneni="a\nb")], {}),
    ([_s(upozorneni="x" * 301)], {}),
    ([_s(platnost="vzdy")], {}),
    ([_s(platnost=True)], {}),
    ([_s(signaly=None)], {}),
    ([_s(signaly=[])], {}),
    ([_s(signaly={})], {}),
    ([_s(signaly={"mcp": []})], {}),
    ([_s(neznamy=1)], {}),
    ([_sig(mpc=["x"])], {}),
    ([_sig(mcp=["a b"])], {}),
    ([_sig(mcp=[""])], {}),
    ([_sig(mcp=[1])], {}),
    ([_sig(mcp=["x"] * 101)], {}),
    ([_sig(agenti=["a/b"])], {}),
    ([_sig(cesty=["relativni/cesta"])], {}),
    ([_sig(cesty=["/"])], {}),
    ([_sig(cesty=["~"])], {}),
    ([_sig(cesty=["~/"])], {}),
    ([_sig(cesty=["/x\x00y"])], {}),
    ([_sig(texty=["ab"])], {}),
    ([_sig(texty=["a\nbc"])], {}),
    ([_sig(vstupy={"nastroj": "x"})], {}),
    ([_sig(vstupy=["x"])], {}),
    ([_sig(vstupy=[{"nastroj": "x", "pole": "a"}])], {}),
    ([_sig(vstupy=[{"pole": "a", "hodnoty": ["x"]}])], {}),
    ([_sig(vstupy=[{"nastroj": "x", "hodnoty": ["x"]}])], {}),
    ([_sig(vstupy=[{"nastroj": "x", "pole": "a-b", "hodnoty": ["x"]}])], {}),
    ([_sig(vstupy=[{"nastroj": "x y", "pole": "a", "hodnoty": ["x"]}])], {}),
    ([_sig(vstupy=[{"nastroj": "x", "pole": "a", "hodnoty": ["x"], "pole2": 1}])], {}),
    ([_sig(vstupy=[{"nastroj": "x", "pole": "a", "predpony": [""]}])], {}),
    ([], {"prepis_cloud": "vzdy"}),
    ([], {"prepis_cloud": False}),
])
def test_schema_neplatne(kontexty, strazce):
    with pytest.raises(ChybaKonfigurace):
        _kfg(kontexty, **strazce)


def test_chyba_schematu_neprozradi_hodnotu():
    with pytest.raises(ChybaKonfigurace) as e:
        _kfg([_sig(mcp=["TAJNY server"])])
    assert "TAJNY" not in str(e.value) and "citlive_kontexty[0].signaly.mcp" in str(e.value)


def test_example_konfigurace_je_platna():
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "config.yaml.example"
    text = p.read_text(encoding="utf-8")
    assert config.z_dat(yaml.safe_load(text)).strazce.citlive_kontexty == ()
    # Zakomentovaný příklad kontextů musí po odkomentování projít validací.
    radky, priklad = [], False
    for r in text.splitlines():
        if r.startswith("  citlive_kontexty: []"):
            continue
        if r.startswith("  # citlive_kontexty:"):
            priklad = True
        if priklad and r.startswith("  # "):
            r = "  " + r[4:]
        elif priklad:
            priklad = False
        radky.append(r)
    k = config.z_dat(yaml.safe_load("\n".join(radky)))
    assert [x.nazev for x in k.strazce.citlive_kontexty] == ["ACME", "Example Corp"]
    assert k.strazce.citlive_kontexty[0].vstupy[1].pole == ("path", "rel_path")


# ------------------------------------------------------------- neznámé a duplicitní klíče, režimy

@pytest.mark.parametrize("data", [
    {"strazce": {"citlive_kontexy": [ACME]}},
    {"strazec": {"citlive_kontexty": [ACME]}},
    {"instrukce": "x"},
    {"elevenlabs": {"modle": "eleven_v4"}},
    {"telegram": {"chatid": "1"}},
    {"cil": {"tiche_hodnoty": [22, 7]}},
    {"prehravani": {"prikazy": "paplay"}},
    {"limity": {"znaku_denne": 10}},
    {"stt": {"cloud_povoleno": False}},
    {"cesty": {"stavy": "/tmp/x"}},
])
def test_neznamy_klic_sekce_i_korene(data):
    with pytest.raises(ChybaKonfigurace) as e:
        config.z_dat(data)
    assert "neznámý klíč" in str(e.value)


def test_stt_engine_ma_vlastni_hlasku():
    with pytest.raises(ChybaKonfigurace) as e:
        config.z_dat({"stt": {"engine": "cloud"}})
    assert "stt.engine" in str(e.value)


@pytest.mark.parametrize("text", [
    "strazce:\n  citlive_kontexy: []\n",
    "strazec:\n  vyzadovat: true\n",
    "strazce:\n  citlive_kontexty: [{nazev: A, upozorneni: x, signaly: {mcp: [a]}}]\n  citlive_kontexty: []\n",
    "strazce: {vyzadovat: true}\nstrazce: {vyzadovat: false}\n",
])
def test_preklep_nebo_duplicita_v_konfiguraci_deny(P, text):
    P.kfg.write_text("cesty: {stav: %s}\n%s" % (json.dumps(str(P.stav)), text))
    with pytest.raises(ChybaKonfigurace):
        config.nacti()
    sid, tp = P.session([tool_use("mcp__acme-db__query", {})])
    tid = P.tid()
    rozh, duvod = P.run(pre(sid, tp, tid))
    assert rozh == "deny" and "Konfiguraci" in duvod and P.razitko(tid) is None


def test_duplicitni_klic_hlaska_bez_hodnoty(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("telegram:\n  chat_id: '111'\n  chat_id: '222'\n")
    with pytest.raises(ChybaKonfigurace) as e:
        config.nacti(str(p))
    assert "duplicitní klíč" in str(e.value) and "řádek 3" in str(e.value)
    assert "111" not in str(e.value) and "222" not in str(e.value)


def test_loader_jinak_jako_safe_load(tmp_path):
    text = "a: &x {b: 1}\nc: *x\nd: {\"<<\": 2}\ne: [1, {f: g}]\n"
    assert config.nacti_yaml(text) == yaml.safe_load(text)
    with pytest.raises(ChybaKonfigurace):
        config.nacti_yaml("a: !!python/object:os.system x\n")


@pytest.mark.parametrize("text", [
    "s:\n  <<: {k: [111]}\n  <<: {k: []}\n",
    "s:\n  <<: [{k: []}, {k: [222]}]\n",
    "a: &x {k: [111]}\ns:\n  <<: *x\n  m: 1\n",
    "s: {<<: {k: 222}}\n",
])
def test_loader_odmita_merge(text):
    with pytest.raises(ChybaKonfigurace) as e:
        config.nacti_yaml(text)
    assert "merge" in str(e.value) and "111" not in str(e.value) and "222" not in str(e.value)


def test_merge_v_souboru_konfigurace(P):
    P.kfg.write_text("strazce:\n  <<: {citlive_kontexty: []}\n")
    with pytest.raises(ChybaKonfigurace):
        config.nacti()
    with pytest.raises(ChybaKonfigurace):
        config.nacti_yaml("a: [\n")


@pytest.mark.parametrize("rezim", ["default", "acceptEdits", "plan", "auto"])
def test_rezimy_s_dotazem(P, rezim):
    sid, tp = P.session([tool_use("mcp__acme-db__query", {})])
    tid = P.tid()
    assert P.run(pre(sid, tp, tid, permission_mode=rezim))[0] == "ask" and P.razitko(tid) is not None


@pytest.mark.parametrize("rezim", ["bypassPermissions", "dontAsk", "novyRezim", None, 1, "x" * 100, [], {}])
def test_rezimy_bez_dotazu_deny(P, rezim):
    sid, tp = P.session([tool_use("mcp__acme-db__query", {})])
    tid = P.tid()
    data = pre(sid, tp, tid, permission_mode=rezim)
    if rezim is None:
        del data["permission_mode"]
    rozh, duvod = P.run(data)
    assert rozh == "deny" and "nejde zeptat" in duvod and P.razitko(tid) is None
    assert not (P.stav / "strazce" / f"cekajici-{tid}.json").exists()
    # Vnitřní chyba v témže režimu: taky deny bez razítka.
    tid = P.tid()
    data = pre("neni-uuid", "/x.jsonl", tid, permission_mode=rezim)
    if rezim is None:
        del data["permission_mode"]
    assert P.run(data)[0] == "deny" and P.razitko(tid) is None


def test_rezimy_bez_dotazu_neblokuji_volani_bez_kontextu(P):
    sid, tp = P.session([tool_use("Bash", {"command": "ls"})])
    tid = P.tid()
    assert P.run(pre(sid, tp, tid, permission_mode="dontAsk"))[0] == "pass" and P.razitko(tid) is not None


def test_poskozena_statistika_nezmeni_dotaz(P):
    adr = P.stav / "strazce"
    adr.mkdir(parents=True)
    (adr / "statistika.json").write_text(json.dumps({datetime.date.today().isoformat(): {"dotazy": "x"}}))
    sid, tp = P.session([tool_use("mcp__acme-db__query", {})])
    tid = P.tid()
    rozh, duvod = P.run(pre(sid, tp, tid))
    assert rozh == "ask" and "Session pracuje s daty ACME." in duvod
    P.run(post(sid, tid))
    assert "ACME" in P.souhlasy()[sid]


def test_selhani_razitka_neprinese_souhlas_s_kontextem(P, monkeypatch):
    sid, tp = P.session([tool_use("mcp__acme-db__query", {})])
    tid = P.tid()
    puvodni = strazce_hook.zapis_razitko
    pokusy = []

    def jednou_selze(*a):
        pokusy.append(1)
        if len(pokusy) == 1:
            raise OSError("disk")
        return puvodni(*a)
    monkeypatch.setattr(strazce_hook, "zapis_razitko", jednou_selze)
    rozh, duvod = P.run(pre(sid, tp, tid))
    assert rozh == "ask" and "nešlo provést" in duvod
    assert not (P.stav / "strazce" / f"cekajici-{tid}.json").exists()
    P.run(post(sid, tid))
    assert P.souhlasy() == {}


def test_selhani_po_zapisu_cekajiciho_ho_uklidi(P, monkeypatch):
    # Výjimka (např. hlídací časovač) po zápisu cekajici, ale před razítkem: cekajici nesmí zůstat.
    sid, tp = P.session([tool_use("mcp__acme-db__query", {})])
    tid = P.tid()
    puvodni = strazce_hook.uloz_cekajici

    def zapise_a_selze(*a):
        puvodni(*a)
        raise strazce_hook.StrazceChyba("hlídací časovač")
    monkeypatch.setattr(strazce_hook, "uloz_cekajici", zapise_a_selze)
    rozh, duvod = P.run(pre(sid, tp, tid))
    assert rozh == "ask" and "nešlo provést" in duvod
    assert not (P.stav / "strazce" / f"cekajici-{tid}.json").exists()
    P.run(post(sid, tid))
    assert P.souhlasy() == {}
