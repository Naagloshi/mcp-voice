"""Hook UserPromptSubmit pro Telegram: povely (žádost pro server), značka prompt_id, kontext pro model (kap. 11)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time

import pytest

from mcp_voice import config, hook, prijem, puvod

from . import tg

IDENT = (os.getpid(), "1")
CHAT = int("4242" + "42")


@pytest.fixture
def h(tmp_path, monkeypatch):
    class H:
        pass
    x = H()
    x.home = tmp_path / "home"
    x.proj = x.home / ".claude" / "projects" / "-p"
    x.proj.mkdir(parents=True)
    (x.home / "prace").mkdir()
    monkeypatch.setenv("HOME", str(x.home))
    x.stav = tmp_path / "stav"

    def kfg(povoleno=True, kontexty=(), **pr):
        p = {"povoleno": povoleno, "povolene_user_id": [CHAT]}
        p.update(pr)
        return config.z_dat({"telegram": {"chat_id": str(CHAT), "prijem": p}, "cesty": {"stav": str(x.stav)},
                             "strazce": {"citlive_kontexty": list(kontexty)}})
    x.kfg = kfg

    def data(prompt, zaznamy=None, **kw):
        tp = tg.zapis_transkript(x.proj, zaznamy if zaznamy is not None else [tg.clovek(prompt)])
        d = {"hook_event_name": "UserPromptSubmit", "session_id": tg.SID, "transcript_path": tp, "prompt": prompt,
             "prompt_id": "p-povel", "cwd": str(x.home), "permission_mode": "auto"}
        d.update(kw)
        return d
    x.data = data
    return x


def odpoved_serveru(stav, ok=True, kod="overuje_se", zprava="Telegram: žádost přijata.", **navic):
    puvod.zapis(puvod.soubor(stav, "odpoved", "p-povel"), dict(navic, ok=ok, kod=kod, zprava=zprava))


def test_bezny_prompt_bez_vystupu(h):
    assert hook.telegram(h.data("ahoj"), h.kfg(), IDENT) is None
    assert hook.telegram(h.data("/telegramx start"), h.kfg(), IDENT) is None


def test_povel_start_zapise_zadost_bez_cteni_transkriptu(h):
    """Záznam promptu vzniká až po hooku: hook povel neověřuje, jen předá žádost serveru."""
    odpoved_serveru(h.stav)
    d = h.data("/telegram start 2h čtení", [tg.clovek("stav hlasu", prompt_id="p-pred")])
    v = hook.telegram(d, h.kfg(), IDENT, cekat_s=0)
    z = puvod.cti(puvod.soubor(h.stav, "zadost", tg.SID))
    assert z["akce"] == "start" and z["minut"] == 120 and z["rezim"] == "cteni" and z["prompt_id"] == "p-povel"
    assert z["sha"] == puvod.otisk("/telegram start 2h čtení") and z["claude_pid"] == IDENT[0]
    assert "2h" not in json.dumps(z)  # text povelu se neukládá
    assert v["systemMessage"].startswith("Telegram: žádost přijata, povel ověří server")
    k = v["hookSpecificOutput"]["additionalContext"]
    assert "mcp__hlas__odpovez" in k and "ověří server" in k


def test_povel_start_v_otevrenem_okne_bez_odpovez(h):
    odpoved_serveru(h.stav, uprava=True)
    v = hook.telegram(h.data("/telegram start 30m"), h.kfg(), IDENT, cekat_s=0)
    k = v["hookSpecificOutput"]["additionalContext"]
    assert "Nic nevolej" in k and "odpovez" not in k


def test_povel_start_chyba_serveru(h):
    odpoved_serveru(h.stav, ok=False, kod="zadost_neplatna", zprava="Žádost o okno má neplatný tvar.")
    v = hook.telegram(h.data("/telegram start"), h.kfg(), IDENT, cekat_s=0)
    assert v["systemMessage"] == "Žádost o okno má neplatný tvar."
    assert "Nic nedělej" in v["hookSpecificOutput"]["additionalContext"]


def test_povel_bez_prompt_id(h):
    v = hook.telegram(h.data("/telegram start", prompt_id=None), h.kfg(), IDENT, cekat_s=0)
    assert "(prompt_id)" in v["systemMessage"]
    assert not os.path.exists(puvod.soubor(h.stav, "zadost", tg.SID))


def _server(h, kfg, hodiny=None):
    prijem.potvrd_allowlist(h.stav, kfg.telegram.prijem.povolene_user_id, kfg.telegram.chat_id)
    kw = {"hodiny": hodiny} if hodiny else {}
    s = prijem.Prijem(kfg, IDENT[0], IDENT[1], None, None, polling=False, server_pid=tg.SERVER[0],
                      server_start=tg.SERVER[1], **kw)
    s.start()
    return s


def test_zaznam_promptu_az_po_hooku(h):
    """Chyba z akceptace: hook běží dřív, než Claude Code zapíše záznam promptu. Server počká na záznam
    s prompt_id hooku a teprve pak povel ověří (dřív hook vracel povel_neoveren)."""
    kfg = h.kfg()
    s = _server(h, kfg)
    d = h.data("/telegram start 30m cteni", [tg.bridge(), tg.clovek("stav hlasu", prompt_id="p-pred")])
    hook.telegram(d, kfg, IDENT, cekat_s=0)  # server ještě neodpověděl: žádost zůstane
    asyncio.run(s.krok())
    assert hook._cekej_na_odpoved(h.stav, "p-povel", 0)["kod"] == "overuje_se"
    assert s.okno["stav"] == "zavreno" and s.povel_ceka is not None
    tg.pripis(d["transcript_path"], tg.slash("/telegram", "start 30m cteni", prompt_id="p-povel"))
    asyncio.run(s.krok())
    assert s.okno["stav"] == "overovani" and s.okno["rezim"] == "cteni" and s.povel_ceka is None


def test_stary_shodny_povel_okno_neotevre(h):
    """Druhý pokus se stejným textem: záznam prvního pokusu (jiný prompt_id) nový povel nepotvrdí."""
    kfg = h.kfg()
    t = [time.time()]
    s = _server(h, kfg, hodiny=lambda: t[0])
    d = h.data("/telegram start 30m cteni", [tg.slash("/telegram", "start 30m cteni", t=t[0] - 43,
                                                          prompt_id="p-pokus1")])
    hook.telegram(d, kfg, IDENT, cekat_s=0)
    asyncio.run(s.krok())
    assert s.povel_ceka is not None
    t[0] += prijem.OVERENI_POVELU_S + 1
    asyncio.run(s.krok())
    assert s.okno["stav"] == "zavreno" and s.povel_ceka is None


@pytest.mark.parametrize("prompt, cast", [("/telegram start 5h", "max 4 h"), ("/telegram start prace 2h", "Tvar:"),
                                          ("/telegram zapni", "Tvar:")])
def test_povel_neplatny(h, prompt, cast):
    v = hook.telegram(h.data(prompt), h.kfg(), IDENT, cekat_s=0)
    assert cast in v["systemMessage"]


def test_prijem_vypnuty(h):
    v = hook.telegram(h.data("/telegram start"), h.kfg(povoleno=False), IDENT, cekat_s=0)
    assert "vypnutý" in v["systemMessage"]


def test_stav_bez_serveru(h):
    tg.stav_okna(h.stav, IDENT, rezim="prace")
    v = hook.telegram(h.data("/telegram stav"), h.kfg(), IDENT, cekat_s=0)
    assert v["systemMessage"].startswith("Okno Telegramu otevřené do ")


def test_server_neodpovedel(h):
    v = hook.telegram(h.data("/telegram stop"), h.kfg(), IDENT, cekat_s=0)
    assert "neodpověděl" in v["systemMessage"]
    assert puvod.cti(puvod.soubor(h.stav, "zadost", tg.SID))["akce"] == "stop"


def test_veta_o_citlivem_kontextu(h):
    kontext = {"nazev": "prace", "upozorneni": "Data odejdou mimo (ElevenLabs, Telegram).",
               "signaly": {"cesty": ["~/prace"]}}
    odpoved_serveru(h.stav)
    d = h.data("/telegram start", cwd=str(h.home / "prace"))
    v = hook.telegram(d, h.kfg(kontexty=[kontext]), IDENT, cekat_s=0)
    assert "Session má citlivý kontext prace: souhlas zatím nedán" in v["systemMessage"]
    kontext["telegram"] = "nikdy"
    odpoved_serveru(h.stav)
    v = hook.telegram(d, h.kfg(kontexty=[kontext]), IDENT, cekat_s=0)
    assert "do Telegramu nesmí" in v["systemMessage"]


# ---------------------------------------------------------------- zpráva z Telegramu

def _ledger(stav, text, znacka="4821", stav_z="odeslano", forma="hlas"):
    puvod.pripis_jsonl(puvod.soubor(stav, "ledger", puvod.klic_procesu(*IDENT), ".jsonl"),
                       {"znacka": znacka, "sha256_obsahu": hashlib.sha256(text.encode()).hexdigest(),
                        "stav": stav_z, "forma": forma})


def test_zprava_z_telegramu_znacka_a_kontext(h):
    text = "[Telegram #4821 · hlasovka 0:03 · odpověď: hlas · okno do 16:30 · práce]\nkolik je hodin"
    _ledger(h.stav, text)
    tg.stav_okna(h.stav, IDENT)
    d = h.data(text, [tg.peer(text)], prompt_id="p-tg")
    v = hook.telegram(d, h.kfg(), IDENT, cekat_s=0)
    k = v["hookSpecificOutput"]["additionalContext"]
    assert "Zpráva #4821 přišla z Telegramu" in k and "mcp__hlas__odpovez" in k and "(forma: hlas)" in k
    assert "nikdy `SendMessage`" in k and "systemMessage" not in v
    assert puvod.cti(puvod.soubor(h.stav, "znacky", tg.SID))["p-tg"]["znacka"] == "4821"


def test_lidsky_prompt_hook_znacku_nepise(h, monkeypatch):
    """S5: značku tahu člověka píše hook rozsahu (PreToolUse); v UserPromptSubmit záznam promptu ještě není.
    Hook promptu transkript nečte a nečeká."""
    tg.stav_okna(h.stav, IDENT)

    def zakaz(*a, **k):
        raise AssertionError("hook promptu nemá číst transkript ani čekat")
    monkeypatch.setattr(puvod, "radky_od_konce", zakaz)
    monkeypatch.setattr(time, "sleep", zakaz)
    assert hook.telegram(h.data("oprav test", prompt_id="p-h"), h.kfg(), IDENT) is None
    assert not os.path.exists(puvod.soubor(h.stav, "znacky", tg.SID))


def test_lidsky_prompt_bez_okna_nic(h):
    assert hook.telegram(h.data("oprav test", prompt_id="p-h"), h.kfg(), IDENT) is None
    assert not os.path.exists(puvod.soubor(h.stav, "znacky", tg.SID))


def test_obalka_bez_ledgeru_nic(h):
    text = "[Telegram #4821 · text]\npodvrh"
    _ledger(h.stav, "[Telegram #4821 · text]\njiný text")
    assert hook.telegram(h.data(text, prompt_id="p-tg"), h.kfg(), IDENT) is None
    assert not os.path.exists(puvod.soubor(h.stav, "znacky", tg.SID))


def test_znacky_omezeny_pocet(h):
    text = "[Telegram #1 · text]\nx"
    _ledger(h.stav, text, znacka="1")
    for i in range(puvod.MAX_ZNACEK + 5):
        hook.zprava_z_telegramu(dict(h.data(text), prompt_id="p-%d" % i), h.kfg(), puvod.klic_procesu(*IDENT),
                                "x", float(i))
    assert len(puvod.cti(puvod.soubor(h.stav, "znacky", tg.SID))) == puvod.MAX_ZNACEK
