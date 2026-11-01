"""Nástroj odpovez (9.1–9.3), rekni auto v tahu z Telegramu, stav příjmu. Bez sítě (MockTransport)."""
from __future__ import annotations

import json
import os
import time

import pytest

from mcp_voice import config, prijem, puvod, server
from mcp_voice.chyby import HlasChyba

from . import tg
from .conftest import FalesnyLokalni, tajemstvi, zakladni_data
from .test_prijem import CHAT, IDENT, Inbox, Klient
from .test_server import zavolej


class S:
    def __init__(self, tmp_path, monkeypatch, sit, plocha, prehravac):
        self.home = tmp_path / "home"
        self.proj = self.home / ".claude" / "projects" / "-p"
        self.proj.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(self.home))
        data = zakladni_data(tmp_path, telegram={"chat_id": str(CHAT),
                                                 "prijem": {"povoleno": True, "povolene_user_id": [CHAT]}},
                             strazce={"vyzadovat": False})
        self.kfg = config.z_dat(data)
        self.s = server.Sluzby.z_konfigurace(self.kfg, plocha=plocha, prehravac=prehravac, lokalni=FalesnyLokalni(),
                                             claude_pid=None, cteni_tajemstvi=tajemstvi, transport=sit.transport)
        prijem.potvrd_allowlist(self.kfg.stav_adresar, (CHAT,), str(CHAT))
        self.klient, self.inbox = Klient(), Inbox()
        self.T = [time.time()]
        self.tp = tg.zapis_transkript(self.proj, [tg.bridge(), tg.clovek("/telegram start 1h", t=self.T[0], prompt_id="p-povel")])
        self.pr = prijem.Prijem(self.kfg, IDENT[0], IDENT[1], self.klient, self.inbox, None, hodiny=lambda: self.T[0],
                                server_pid=tg.SERVER[0], server_start=tg.SERVER[1], polling=False)
        self.pr.start()
        self.s.prijem = self.pr
        self.stav = self.kfg.stav_adresar

    def zadost(self, prompt_id="p-povel"):
        puvod.zapis(puvod.soubor(self.stav, "zadost", tg.SID), {
            "akce": "start", "minut": 60, "rezim": "prace", "prompt_id": prompt_id,
            "sha": puvod.otisk("/telegram start 1h"), "claude_pid": IDENT[0], "claude_start": IDENT[1],
            "transcript_path": self.tp, "cwd": str(self.home), "session_id": tg.SID})

    async def otevri(self):
        self.zadost()
        await self.pr.krok()
        puvod.zapis(puvod.soubor(self.stav, "rozsah", tg.SID), {"cas": self.T[0], "claude_pid": IDENT[0]})
        r = await server.odpovez_logika(self.s, "okno")
        assert r["sablona"] == "okno_otevreno" and self.pr.okno["stav"] == "otevreno"
        tg.pripis(self.tp, tg.konec_tahu())

    async def zprava(self, text, mid):
        await self.pr.prijmi({"update_id": mid, "message": {
            "message_id": mid, "date": int(self.T[0]) + 1, "text": text,
            "from": {"id": CHAT, "is_bot": False}, "chat": {"id": CHAT, "type": "private"}}})
        puvod.zapis(puvod.soubor(self.stav, "rozsah", tg.SID), {"cas": self.T[0], "claude_pid": IDENT[0]})
        await self.pr.krok()
        tg.pripis(self.tp, tg.peer(self.inbox.odeslano[-1], t=self.T[0]))
        await self.pr.krok()


@pytest.fixture
def S_(tmp_path, monkeypatch, sit, plocha, prehravac):
    return S(tmp_path, monkeypatch, sit, plocha, prehravac)


async def test_odpovez_v_tahu_povelu_pred_overenim(S_):
    """odpovez v tahu povelu dřív, než krok serveru povel ověřil: ověří se při volání (záznam už je)."""
    S_.zadost()
    await S_.pr.zpracuj_zadosti(S_.T[0])
    assert S_.pr.povel_ceka is not None and S_.pr.okno["stav"] == "zavreno"
    puvod.zapis(puvod.soubor(S_.stav, "rozsah", tg.SID), {"cas": S_.T[0], "claude_pid": IDENT[0]})
    r = await server.odpovez_logika(S_.s, "okno")
    assert r["sablona"] == "okno_otevreno" and S_.pr.okno["stav"] == "otevreno"


async def test_odpovez_bez_zaznamu_povelu_okno_neotevre(S_):
    S_.zadost(prompt_id="p-jiny")
    await S_.pr.zpracuj_zadosti(S_.T[0])
    puvod.zapis(puvod.soubor(S_.stav, "rozsah", tg.SID), {"cas": S_.T[0], "claude_pid": IDENT[0]})
    with pytest.raises(HlasChyba) as e:
        await server.odpovez_logika(S_.s, "okno")
    assert e.value.kod == "WINDOW_CLOSED" and S_.pr.okno["stav"] == "zavreno"
    assert S_.klient.texty == []


async def test_odpovez_bez_razitka(udelej_sluzby):
    s = udelej_sluzby()
    r = await zavolej(s, "odpovez", {"text": "x"}, s_razitkem=False)
    assert r["chyba"]["kod"] == "GUARD_MISSING"


async def test_odpovez_bez_prijmu(udelej_sluzby):
    r = await zavolej(udelej_sluzby(), "odpovez", {"text": "x"})
    assert r["chyba"]["kod"] == "WINDOW_CLOSED"


async def test_odpovez_v_tahu_cloveka(S_):
    await S_.otevri()
    tg.pripis(S_.tp, tg.clovek("ahoj"))
    with pytest.raises(HlasChyba) as e:
        await server.odpovez_logika(S_.s, "x")
    assert e.value.kod == "NOT_TELEGRAM_TURN"


async def test_odpovez_text_jako_reply(S_):
    await S_.otevri()
    await S_.zprava("kolik je hodin", 4821)
    S_.klient.texty.clear()
    r = await server.odpovez_logika(S_.s, "Je 16:30.")
    assert r == {"ok": True, "odeslano": "text", "casti": 1}
    assert S_.klient.texty == [("Je 16:30.", 4821)]
    assert S_.pr.ledger()["4821"]["stav"] == "odpovezeno"


async def test_odpovez_hlas_sendvoice_s_reply(S_, sit):
    await S_.otevri()
    await S_.zprava("řekni mi čas", 4822)
    r = await server.odpovez_logika(S_.s, "Je půl páté.")
    assert r["odeslano"] == "hlas"
    voice = [q for q in sit.pozadavky if q.url.path.endswith("/sendVoice")]
    assert voice and b'"message_id": 4822' in voice[-1].content


async def test_odpovez_pc(S_, prehravac):
    await S_.otevri()
    await S_.zprava("odpověz na PC", 4823)
    S_.klient.texty.clear()
    r = await server.odpovez_logika(S_.s, "Hotovo.")
    assert r["odeslano"] == "pc" and prehravac.pridano and S_.klient.texty == [("Odpověď je na PC.", 4823)]


async def test_odpovez_tajemstvi_a_delka(S_):
    await S_.otevri()
    await S_.zprava("x", 4824)
    with pytest.raises(HlasChyba) as e:
        await server.odpovez_logika(S_.s, "-----BEGIN RSA PRIVATE KEY-----\nabc")
    assert e.value.kod == "SECRET_LIKE"
    with pytest.raises(HlasChyba) as e:
        await server.odpovez_logika(S_.s, "x" * 20001)
    assert e.value.kod == "TEXT_TOO_LONG"
    with pytest.raises(HlasChyba) as e:
        await server.odpovez_logika(S_.s, "x", "fax")
    assert e.value.kod == "ARGUMENT_INVALID"


async def test_odpovez_zavrene_okno(S_):
    await S_.otevri()
    await S_.pr.zavri("stop_terminal")
    with pytest.raises(HlasChyba) as e:
        await server.odpovez_logika(S_.s, "x")
    assert e.value.kod == "WINDOW_CLOSED"


async def test_rekni_auto_v_tahu_z_telegramu(S_, plocha):
    await S_.otevri()
    await S_.zprava("x", 4825)
    plocha.zamek = False  # uživatel u PC, přesto tah z Telegramu → Telegram
    r = await server._rekni(S_.s, "Ahoj", "auto")
    assert r["cil"] == "telegram" and r["duvod"] == "tah_z_telegramu" and r["zdroj"] == "telegram"


async def test_stav_prijmu(S_):
    await S_.otevri()
    d = server.stav_logika(S_.s)["telegram_prijem"]
    assert d["okno"] == "otevreno" and d["rezim"] == "prace" and d["allowlist_potvrzen"] is True
    assert d["vypnuto"] is None and "kolik" not in json.dumps(d)


def test_stav_bez_prijmu(udelej_sluzby):
    d = server.stav_logika(udelej_sluzby())["telegram_prijem"]
    assert d["stav"] == "neběží" and d["vypnuto"] == "vypnuto"


async def test_retence_odchozi_hlasovky(S_, sit):
    await S_.otevri()
    await S_.zprava("řekni mi to", 4826)
    await server.odpovez_logika(S_.s, "Ahoj.")
    ids = [z["message_id"] for z in puvod.cti_jsonl(os.path.join(puvod.adresar(S_.stav), "retence.jsonl"))]
    assert 4826 in ids
