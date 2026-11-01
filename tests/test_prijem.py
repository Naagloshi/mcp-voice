"""prijem.py: okno (5.3), příjem (8.2), sanitizace (8.3), hlasovky (8.4), fronta a doručení (8.6–8.8),
odpovědi (9), retence (9.4), zakázané příkazy. Bez sítě: falešný klient Telegramu a falešný inbox."""
from __future__ import annotations

import asyncio
import os
import subprocess
import time

import pytest

from mcp_voice import config, okno as oknomod, prijem, puvod
from mcp_voice.chyby import HlasChyba
from mcp_voice.inbox import InboxChyba

from . import tg

CHAT = int("4242" + "42")
IDENT = (os.getpid(), "1")


class Klient:
    def __init__(self):
        self.zpravy_volani: list = []
        self.odpovedi: list = []
        self.texty: list = []
        self.hlasy: list = []
        self.smazano: list = []
        self.soubory: dict = {}
        self.soubor_volani: list = []
        self.chyba = None
        self.mid = 9000

    async def zpravy(self, offset, timeout_s):
        self.zpravy_volani.append((offset, timeout_s))
        if self.chyba:
            raise self.chyba
        return self.odpovedi.pop(0) if self.odpovedi else []

    async def text(self, text, reply_to=None):
        self.mid += 1
        self.texty.append((text, reply_to))
        return self.mid

    async def hlas(self, ogg, delka_s, reply_to=None):
        self.mid += 1
        self.hlasy.append((len(ogg), reply_to))
        return self.mid

    async def soubor(self, file_id, max_bajtu):
        self.soubor_volani.append(file_id)
        return self.soubory[file_id]

    async def smaz(self, ids):
        self.smazano.append(list(ids))
        return True


class Inbox:
    def __init__(self):
        self.odeslano: list = []
        self.chyba = None

    def posli(self, text):
        if self.chyba:
            raise InboxChyba(self.chyba)
        self.odeslano.append(text)


class P:
    def __init__(self, tmp_path, monkeypatch, **prijem_kw):
        self.home = tmp_path / "home"
        self.proj = self.home / ".claude" / "projects" / "-p"
        self.proj.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(self.home))
        self.stav = tmp_path / "stav"
        self.T = [time.time()]
        pr = {"povoleno": True, "povolene_user_id": [CHAT]}
        pr.update(prijem_kw)
        self.kfg = config.z_dat({"telegram": {"chat_id": str(CHAT), "prijem": pr}, "cesty": {"stav": str(self.stav)}})
        assert self.kfg.telegram.prijem.chyba is None, self.kfg.telegram.prijem.chyba
        prijem.potvrd_allowlist(self.stav, self.kfg.telegram.prijem.povolene_user_id, self.kfg.telegram.chat_id)
        self.klient, self.inbox = Klient(), Inbox()
        self.prepisy: list = []
        self.prepis_vysledek = {"text": "kolik je hodin"}
        self.tp = tg.zapis_transkript(self.proj, [tg.bridge(t=self.T[0] - 5), tg.clovek("/telegram start 1h", t=self.T[0], prompt_id="p-povel")])
        self.p = prijem.Prijem(self.kfg, IDENT[0], IDENT[1], self.klient, self.inbox, self.prepis,
                               hodiny=lambda: self.T[0], server_pid=tg.SERVER[0], server_start=tg.SERVER[1],
                               polling=False)
        self.p.start()

    async def prepis(self, f32):
        self.prepisy.append(len(f32))
        if isinstance(self.prepis_vysledek, Exception):
            raise self.prepis_vysledek
        return self.prepis_vysledek

    def zadost(self, akce="start", minut=60, rezim="prace", prompt="/telegram start 1h", **kw):
        d = {"akce": akce, "minut": minut, "rezim": rezim, "prompt_id": "p-povel", "sha": puvod.otisk(prompt),
             "cas": self.T[0], "claude_pid": IDENT[0], "claude_start": IDENT[1], "transcript_path": self.tp,
             "cwd": str(self.home), "session_id": tg.SID}
        d.update(kw)
        puvod.zapis(puvod.soubor(self.stav, "zadost", tg.SID), d)

    def heartbeat(self, cas=None):
        puvod.zapis(puvod.soubor(self.stav, "rozsah", tg.SID), {"verze": 1, "cas": self.T[0] if cas is None else cas,
                                                                  "claude_pid": IDENT[0]})

    async def otevri(self, **kw):
        self.zadost(**kw)
        await self.p.krok()
        assert self.p.okno["stav"] == "overovani", self.odpoved()
        self.heartbeat()
        r = await self.p.potvrd_otevreni()
        assert r["ok"] and self.p.okno["stav"] == "otevreno"
        tg.pripis(self.tp, tg.konec_tahu(t=self.T[0]))
        self.klient.texty.clear()

    def odpoved(self, prompt_id="p-povel"):
        return puvod.cti_tise(str(oknomod.cesta(self.stav, "odpoved", prompt_id)), {})

    def zprava(self, text="ahoj", mid=None, **kw):
        self.klient.mid += 1
        m = {"message_id": mid or self.klient.mid, "date": int(self.T[0]) + 1,
             "from": {"id": CHAT, "is_bot": False}, "chat": {"id": CHAT, "type": "private"}}
        if text is not None:
            m["text"] = text
        m.update(kw)
        return {"update_id": m["message_id"] + 100000, "message": m}

    def sablony(self):
        return [t for t, _ in self.klient.texty]

    def doruc_v_transkriptu(self, text, identita=tg.SERVER):
        tg.pripis(self.tp, tg.peer(text, identita=identita, t=self.T[0]))


@pytest.fixture
def p(tmp_path, monkeypatch):
    return P(tmp_path, monkeypatch)


# ---------------------------------------------------------------- okno

async def test_mimo_okno_zadne_getupdates(p):
    for _ in range(3):
        await p.p.krok()
    assert p.klient.zpravy_volani == []


async def test_otevreni_zahodi_backlog_a_potvrdi_offset(p):
    p.klient.odpovedi = [[{"update_id": 50}, {"update_id": 51}], []]
    await p.otevri()
    assert p.klient.zpravy_volani[:2] == [(-1, 0), (52, 0)]
    assert p.p.offset == 52
    assert puvod.cti(str(oknomod.adresar(p.stav) / "aktivni.json"))["claude_pid"] == IDENT[0]
    assert puvod.cti(puvod.soubor(p.stav, "politika", puvod.klic_procesu(*IDENT)))["registrace"] == "hlas"


async def test_sablona_otevreni_a_odpoved_hooku(p):
    p.zadost()
    await p.p.krok()
    assert p.odpoved()["ok"] and p.odpoved()["kod"] == "overuje_se" and p.odpoved()["uprava"] is False
    p.heartbeat()
    await p.p.potvrd_otevreni()
    assert p.sablony()[0].startswith("Okno otevřeno do ")


async def test_bez_heartbeatu_se_okno_neotevre(p):
    p.zadost()
    await p.p.krok()
    p.T[0] += 121
    await p.p.krok()
    assert p.p.okno["stav"] == "zavreno" and p.p.okno["duvod_zavreni"] == "hook_rozsahu_nebezi"
    assert p.klient.zpravy_volani == []


async def test_server_otevre_sam_po_120_s_s_heartbeatem(p):
    p.zadost()
    await p.p.krok()
    p.heartbeat()
    p.T[0] += 121
    await p.p.krok()
    assert p.p.okno["stav"] == "otevreno"


async def test_potvrzeni_jen_v_tahu_cloveka(p):
    p.zadost()
    await p.p.krok()
    p.heartbeat()
    tg.pripis(p.tp, tg.peer(identita=tg.CIZI, t=p.T[0]))
    with pytest.raises(HlasChyba) as e:
        await p.p.potvrd_otevreni()
    assert e.value.kod == "NOT_TELEGRAM_TURN"


@pytest.mark.parametrize("uprava, kod", [
    ({"minut": 300}, "zadost_neplatna"),
    ({"rezim": "vse"}, "zadost_neplatna"),
    ({"transcript_path": "/tmp/jinde.jsonl"}, "zadost_neplatna"),
    ({"prompt_id": "p povel"}, "zadost_neplatna"),
    ({"sha": None}, "zadost_neplatna"),
])
async def test_zadost_neplatneho_tvaru_hned(p, uprava, kod):
    p.zadost(**uprava)
    await p.p.krok()
    assert p.p.okno["stav"] == "zavreno" and p.p.povel_ceka is None
    assert p.odpoved(uprava.get("prompt_id", "p-povel")).get("kod", kod) == kod


@pytest.mark.parametrize("uprava, kod", [
    ({"prompt": "/telegram start 2h"}, "povel_neoveren"),
    ({"rezim": "plny"}, "povel_nesouhlasi"),  # N1: parametry ze žádosti musí sedět s povelem v transkriptu
    ({"minut": 240}, "povel_nesouhlasi"),
])
async def test_overeni_povelu_odmitne(p, uprava, kod, caplog):
    p.zadost(**uprava)
    await p.p.krok()
    assert p.odpoved()["kod"] == "overuje_se"  # hook dostane jen převzetí
    assert p.p.okno["stav"] == "zavreno" and p.p.povel_ceka is None
    assert p.sablony() == ["Okno se neotevřelo: povel z terminálu nešel ověřit (%s)." % kod]
    assert "povel okna neovereny kod=%s" % kod in caplog.text


async def test_povel_ceka_na_zaznam_promptu(p):
    """Záznam promptu vzniká až po hooku UserPromptSubmit: server čeká na záznam s prompt_id žádosti."""
    p.zadost(prompt_id="p-novy", prompt="/telegram start 30m cteni", minut=30, rezim="cteni")
    await p.p.krok()
    assert p.odpoved("p-novy")["kod"] == "overuje_se" and p.p.povel_ceka is not None
    assert os.path.exists(oknomod.cesta(p.stav, "okno", puvod.klic_procesu(*IDENT)))  # heartbeat P4 v tahu povelu
    p.T[0] += 1
    await p.p.krok()
    assert p.p.okno["stav"] == "zavreno" and p.sablony() == []
    tg.pripis(p.tp, tg.slash("/telegram", "start 30m cteni", t=p.T[0] - 0.5, prompt_id="p-novy"))
    await p.p.krok()
    assert p.p.okno["stav"] == "overovani" and p.p.okno["otevreno_prompt_id"] == "p-novy"


async def test_povel_bez_zaznamu_po_limitu_neotevre(p, caplog):
    p.zadost(prompt_id="p-novy")
    await p.p.krok()
    p.T[0] += prijem.OVERENI_POVELU_S - 1
    await p.p.krok()
    assert p.p.povel_ceka is not None
    p.T[0] += 2
    await p.p.krok()
    assert p.p.okno["stav"] == "zavreno" and p.p.povel_ceka is None
    assert p.sablony() == ["Okno se neotevřelo: povel z terminálu nešel ověřit (povel_neoveren)."]
    assert "povel okna neovereny kod=povel_neoveren" in caplog.text


async def test_stary_shodny_povel_neplati(p):
    """Záznam se stejným textem, ale jiným prompt_id (předchozí pokus) nový povel nepotvrdí."""
    p.zadost(prompt_id="p-druhy")
    await p.p.krok()
    p.T[0] += prijem.OVERENI_POVELU_S + 1
    await p.p.krok()
    assert p.p.okno["stav"] == "zavreno"
    assert p.sablony() == ["Okno se neotevřelo: povel z terminálu nešel ověřit (povel_neoveren)."]


async def test_zaznam_povelu_starsi_nez_prevzeti(p):
    p.T[0] += prijem.STARI_POVELU_S + 1
    p.zadost()
    await p.p.krok()
    assert p.p.okno["stav"] == "zavreno"
    assert p.sablony() == ["Okno se neotevřelo: povel z terminálu nešel ověřit (povel_stary)."]


async def test_stop_zrusi_cekajici_povel(p):
    p.zadost(prompt_id="p-novy")
    await p.p.krok()
    p.zadost(akce="stop", prompt_id="p-stop")
    await p.p.krok()
    assert p.p.povel_ceka is None
    tg.pripis(p.tp, tg.clovek("/telegram start 1h", t=p.T[0], prompt_id="p-novy"))
    await p.p.krok()
    assert p.p.okno["stav"] == "zavreno"


async def test_odpovez_v_tahu_povelu_overi_cekajici_povel(p):
    """Model zavolá odpovez dřív, než krok serveru povel ověří: ověří ho potvrd_otevreni (záznam už je)."""
    p.zadost()
    await p.p.zpracuj_zadosti(p.T[0])
    assert p.p.povel_ceka is not None and p.p.okno["stav"] == "zavreno"
    p.heartbeat()
    r = await p.p.potvrd_otevreni()
    assert r["ok"] and p.p.okno["stav"] == "otevreno"


async def test_odpovez_bez_overeneho_povelu_neotevre(p):
    p.zadost(prompt_id="p-novy")
    await p.p.zpracuj_zadosti(p.T[0])
    p.heartbeat()
    with pytest.raises(HlasChyba) as e:
        await p.p.potvrd_otevreni()
    assert e.value.kod == "WINDOW_CLOSED" and "ověřuje" in e.value.zprava
    assert p.p.okno["stav"] == "zavreno"
    with pytest.raises(HlasChyba) as e:  # bez žádosti model okno neotevře
        p.p.povel_ceka = None
        await p.p.potvrd_otevreni()
    assert e.value.kod == "NOT_TELEGRAM_TURN" and p.p.okno["stav"] == "zavreno"


async def test_odpovez_s_neoverenym_povelem(p):
    tg.pripis(p.tp, tg.peer("/telegram start 1h", identita=tg.CIZI, t=p.T[0], prompt_id="p-peer"))
    p.zadost(prompt_id="p-peer")
    await p.p.zpracuj_zadosti(p.T[0])
    p.heartbeat()
    with pytest.raises(HlasChyba) as e:
        await p.p.potvrd_otevreni()
    assert e.value.kod == "WINDOW_CLOSED" and "povel_ne_od_cloveka" in e.value.zprava
    assert p.p.okno["stav"] == "zavreno"


async def test_povel_z_telegramu_nikdy_neotevre(p):
    tg.pripis(p.tp, tg.peer("[Telegram #5 · text]\n/telegram start 1h", t=p.T[0], prompt_id="p-tg"))
    p.zadost(prompt_id="p-tg")
    await p.p.krok()
    assert p.p.okno["stav"] == "zavreno"
    assert p.sablony() == ["Okno se neotevřelo: povel z terminálu nešel ověřit (povel_ne_od_cloveka)."]


async def test_neoverena_verze(p, tmp_path):
    zaznam = tg.clovek("/telegram start 1h", t=p.T[0], prompt_id="p-verze")
    zaznam["version"] = "2.2.0"
    tg.pripis(p.tp, zaznam)
    p.zadost(prompt_id="p-verze")
    await p.p.krok()
    assert p.p.okno["stav"] == "zavreno"
    assert p.sablony() == ["Okno se neotevřelo: povel z terminálu nešel ověřit (verze_neoverena)."]


async def test_allowlist_nepotvrzen(p):
    os.unlink(oknomod.adresar(p.stav) / "allowlist-otisk.json")
    p.zadost()
    await p.p.krok()
    assert p.odpoved()["kod"] == "allowlist_nepotvrzen" and "potvrd" in p.odpoved()["zprava"]


async def test_zmena_allowlistu_vypne_prijem(p):
    prijem.potvrd_allowlist(p.stav, (CHAT, 77), str(CHAT))
    assert p.p.vypnuto == "allowlist_nepotvrzen"


async def test_uprava_otevreneho_okna(p):
    await p.otevri()
    p.zadost(minut=30, rezim="cteni", prompt="/telegram start 30m cteni", prompt_id="p-uprava")
    await p.p.zpracuj_zadosti(p.T[0])
    assert p.odpoved("p-uprava")["kod"] == "overuje_se" and p.odpoved("p-uprava")["uprava"] is True
    tg.pripis(p.tp, tg.clovek("/telegram start 30m cteni", t=p.T[0], prompt_id="p-uprava"))
    await p.p.krok()
    assert p.p.okno["rezim"] == "cteni" and abs(p.p.okno["konec"] - (p.T[0] + 1800)) < 1
    assert p.p.okno["otevreno_prompt_id"] == "p-uprava"
    assert p.sablony()[-1].startswith("Okno upraveno do ")


@pytest.mark.parametrize("posun, duvod", [(3601, "cas"), (None, "necinnost")])
async def test_vyprseni(tmp_path, monkeypatch, posun, duvod):
    p = P(tmp_path, monkeypatch, okno={"vychozi_min": 120, "necinnost_min": 30, "max_min": 240})
    if posun:
        await p.otevri(minut=60)
    else:
        tg.pripis(p.tp, tg.clovek("/telegram start 2h", t=p.T[0], prompt_id="p-2h"))
        await p.otevri(minut=120, prompt="/telegram start 2h", prompt_id="p-2h")
    p.T[0] += posun or 1801
    await p.p.krok()
    assert p.p.okno["stav"] == "zavreno" and p.p.okno["duvod_zavreni"] == duvod
    assert p.sablony()[-1] == "Okno zavřeno (%s)." % duvod
    assert p.klient.zpravy_volani[-1] == (p.p.offset, 0) or p.p.offset is None


async def test_stop_z_terminalu_a_clear(p):
    await p.otevri()
    p.zadost(akce="stop")
    await p.p.krok()
    assert p.p.okno["duvod_zavreni"] == "stop_terminal"
    await p.otevri()
    puvod.zapis(puvod.soubor(p.stav, "zadost", tg.SID2), {"akce": "zavrit", "duvod": "clear", "claude_pid": IDENT[0],
                                                           "claude_start": IDENT[1], "session_id": tg.SID2})
    await p.p.krok()
    assert p.p.okno["duvod_zavreni"] == "clear"
    assert not os.path.exists(puvod.soubor(p.stav, "politika", puvod.klic_procesu(*IDENT)))


async def test_bash_bez_obalu_zavre_okno(p):
    """hook v PostToolUse zjistil Bash z Telegramu bez obalu → okno zavřít, chyba, šablona."""
    await p.otevri()
    puvod.zapis(puvod.soubor(p.stav, "zadost", tg.SID), {"akce": "zavrit", "duvod": "bash_bez_obalu",
                                                          "claude_pid": IDENT[0], "claude_start": IDENT[1],
                                                          "session_id": tg.SID})
    await p.p.krok()
    assert p.p.okno["stav"] == "zavreno" and p.p.okno["duvod_zavreni"] == "bash_bez_obalu"
    assert p.p.chyba == "bash_bez_obalu" and p.sablony()[-1] == "Okno zavřeno (bash_bez_obalu)."
    assert not os.path.exists(puvod.soubor(p.stav, "politika", puvod.klic_procesu(*IDENT)))


async def test_zmena_konfigurace_agenta_zavre_okno(p, caplog):
    """hook po Bash z Telegramu našel novou konfiguraci agenta → okno zavřít, chyba, šablona, cesty do logu."""
    await p.otevri()
    puvod.zapis(puvod.soubor(p.stav, "zadost", tg.SID), {"akce": "zavrit", "duvod": "konfigurace_agenta",
                                                          "cesty": ["/x/proj/.mcp.json", 7, "/x/a\nb"],
                                                          "claude_pid": IDENT[0], "claude_start": IDENT[1],
                                                          "session_id": tg.SID})
    with caplog.at_level("WARNING"):
        await p.p.krok()
    assert p.p.okno["stav"] == "zavreno" and p.p.okno["duvod_zavreni"] == "konfigurace_agenta"
    assert p.p.chyba == "konfigurace_agenta"
    assert "konfiguraci agenta" in p.sablony()[-1] and "/x/proj" not in p.sablony()[-1]
    assert "/x/proj/.mcp.json" in caplog.text and "a\nb" not in caplog.text
    assert not os.path.exists(puvod.soubor(p.stav, "zadost", tg.SID))


async def test_zadost_zavrit_smazana_az_po_zavreni(p, monkeypatch):
    """žádost o zavření zmizí až po zavření okna (hook ji mezitím vidí a tah z Telegramu zamítá)."""
    await p.otevri()
    puvod.zapis(puvod.soubor(p.stav, "zadost", tg.SID), {"akce": "zavrit", "duvod": "clear", "claude_pid": IDENT[0],
                                                          "claude_start": IDENT[1], "session_id": tg.SID})
    videno = []
    puvodni = p.p.zavri

    async def zavri(*a, **k):
        videno.append(os.path.exists(puvod.soubor(p.stav, "zadost", tg.SID)))
        await puvodni(*a, **k)
    monkeypatch.setattr(p.p, "zavri", zavri)
    await p.p.krok()
    assert videno == [True] and not os.path.exists(puvod.soubor(p.stav, "zadost", tg.SID))


async def test_zadost_jineho_procesu_a_znovupouziteho_pid(p):
    p.zadost(claude_start="999")
    await p.p.krok()
    assert p.p.okno["stav"] == "zavreno"
    assert os.path.exists(puvod.soubor(p.stav, "zadost", tg.SID))  # cizí žádost zůstane


async def test_presun_do_jineho_procesu(p):
    await p.otevri()
    puvod.zapis(str(oknomod.adresar(p.stav) / "aktivni.json"), {"claude_pid": 1, "claude_start": "5"})
    p.p.fronta.append(prijem.Zprava(1, "text", "x", "text", p.T[0]))
    await p.p.krok()
    assert p.p.okno["duvod_zavreni"] == "presun" and p.p.fronta == []
    assert p.sablony()[-1] == "Okno přepnuto do jiné session." and p.p.zamek.fd is None


async def test_mrtvy_proces_okno_zavre_uklid(p):
    puvod.zapis(str(oknomod.cesta(p.stav, "okno", "999999999-1")), {"stav": "otevreno"})
    oknomod.uklid(p.stav, p.T[0], p.p.klic)
    assert puvod.cti(str(oknomod.cesta(p.stav, "okno", "999999999-1")))["stav"] == "zavreno"


async def test_identita_serveru(p):
    d = puvod.cti(puvod.soubor(p.stav, "server", puvod.klic_procesu(*IDENT)))
    assert d["identity"][-1]["pid"] == tg.SERVER[0]
    for i in range(7):
        prijem.zapis_identitu(p.stav, p.p.klic, 100 + i, "1", p.T[0])
    assert len(puvod.cti(puvod.soubor(p.stav, "server", p.p.klic))["identity"]) == 5


# ---------------------------------------------------------------- příjem a filtr

async def test_filtr_tiche_zahozeni(p):
    await p.otevri()
    for u in (p.zprava(**{"from": {"id": 1, "is_bot": False}}),
              p.zprava(**{"from": {"id": CHAT, "is_bot": True}}),
              p.zprava(chat={"id": -100, "type": "group"}),
              p.zprava(chat={"id": CHAT, "type": "channel"}),
              p.zprava(date=int(p.T[0]) - 100)):
        await p.p.prijmi(u)
    assert p.p.fronta == [] and p.klient.texty == []


@pytest.mark.parametrize("klic, hodnota", [("forward_origin", {"type": "user"}), ("is_automatic_forward", True),
                                           ("story", {}), ("via_bot", {}), ("sender_chat", {}),
                                           ("business_connection_id", "x"), ("paid_media", {})])
async def test_preposlane_a_specialni(p, klic, hodnota):
    await p.otevri()
    await p.p.prijmi(p.zprava(**{klic: hodnota}))
    assert p.p.fronta == [] and p.sablony() == ["Přeposlané a speciální zprávy nepřijímám."]


@pytest.mark.parametrize("navic", [{"photo": [{}], "caption": "x"}, {"document": {}}, {"audio": {}},
                                   {"video_note": {}}, {"sticker": {}}])
async def test_nepodporovany_typ(p, navic):
    await p.otevri()
    await p.p.prijmi(p.zprava(None, **navic))
    assert p.p.fronta == [] and "nepřijímám" in p.sablony()[0]


async def test_reply_a_entity_se_nepouziji(p):
    await p.otevri()
    await p.p.prijmi(p.zprava("můj text", reply_to_message={"text": "TAJNE-CITACE"}, quote={"text": "Q"},
                              entities=[{"type": "text_link", "url": "https://zle.invalid"}]))
    assert p.p.fronta[0].text == "můj text"


async def test_stop_a_stav_z_telegramu(p):
    await p.otevri()
    await p.p.prijmi(p.zprava("/stav"))
    assert p.sablony()[-1].startswith("Okno do ")
    await p.p.prijmi(p.zprava("/stop@MujBot"))
    assert p.p.okno["duvod_zavreni"] == "stop_telegram"


async def test_jine_prikazy_nahrazeny_znackou(p):
    await p.otevri()
    await p.p.prijmi(p.zprava("/loop 5m x"))
    z = p.p.fronta[-1]
    assert z.text == "[zakázaný příkaz] 5m x" and z.odstraneno == 1
    assert p.sablony()[-1] == "Příkazy ve zprávě jsem odstranil (1)."


async def test_limity_a_zavreni_pri_druhem_prekroceni(p):
    await p.otevri()
    for i in range(6):
        await p.p.prijmi(p.zprava("z%d" % i))
    p.p.fronta.clear()
    await p.p.prijmi(p.zprava("navic"))
    assert p.sablony()[-1] == "Příliš mnoho zpráv, zpomal." and p.p.okno["stav"] == "otevreno"
    await p.p.prijmi(p.zprava("navic2"))
    assert p.p.okno["stav"] == "zavreno" and p.p.okno["duvod_zavreni"] == "limit"


async def test_fronta_plna(tmp_path, monkeypatch):
    p = P(tmp_path, monkeypatch, fronta={"max_zprav": 2})
    await p.otevri()
    for i in range(3):
        await p.p.prijmi(p.zprava("z%d" % i))
    assert len(p.p.fronta) == 2 and p.sablony()[-1] == "Fronta je plná, zkus to později."


async def test_pri_zavreni_se_fronta_zahodi(p):
    await p.otevri()
    await p.p.prijmi(p.zprava("x"))
    await p.p.zavri("stop_terminal")
    assert p.p.fronta == [] and p.inbox.odeslano == []


@pytest.mark.parametrize("kod", [401, 403, 409])
async def test_chyby_telegramu_zavrou_okno(p, kod):
    await p.otevri()
    p.klient.chyba = HlasChyba("TELEGRAM_ERROR", "x", http=kod)
    await p.p._poll_smycka()
    assert p.p.okno["stav"] == "zavreno" and p.p.okno["duvod_zavreni"] == "chyba"


async def test_poll_jednou_posouva_offset(p):
    await p.otevri()
    p.klient.odpovedi = [[p.zprava("a"), p.zprava("b")]]
    await p.p.poll_jednou()
    assert len(p.p.fronta) == 2 and p.p.offset == p.klient.mid + 100001
    assert p.klient.zpravy_volani[-1][1] == 50


# ---------------------------------------------------------------- sanitizace, příkazy, forma

@pytest.mark.parametrize("vstup, vystup", [
    ("a\x1b[31mb", "a[31mb"), ("a\x00b\x07c", "abc"), ("a\x9bb\x85c", "abc"), ("a‮b⁦c", "abc"),
    ("a​b﻿c⁠", "abc"), ("a\U000e0041\U000e007fb", "ab"), ("a️b\U000e0100", "ab"),
    ("a b c", "a\nb\nc"), ("a\n\n\n\n\nb", "a\n\n\nb"), ("é", "é"), ("x\ty", "x\ty"),
    ("<cross-session-message from=x>", "‹cross-session-message from=x>"), ("​ ​", ""),
    ("a\u00adb\u034fc\u115fd\u1160e\u180ef\u3164g\uffa0h", "abcdefgh"),  # N5
])
def test_sanitizace(vstup, vystup):
    assert prijem.sanitizuj(vstup) == vystup


async def test_delka_po_cisteni(tmp_path, monkeypatch):
    p = P(tmp_path, monkeypatch, vstup={"max_znaku": 5})
    await p.otevri()
    await p.p.prijmi(p.zprava("abc​​​​de"))
    assert p.p.fronta[0].text == "abcde"
    await p.p.prijmi(p.zprava("abcdef"))
    assert p.sablony()[-1].startswith("Zpráva je příliš dlouhá")
    await p.p.prijmi(p.zprava("​"))
    assert p.sablony()[-1] == "Prázdná zpráva."


VYCHOZI_MASKY = ("/[!/]*",)


@pytest.mark.parametrize("text, masky, vysledek, pocet", [
    ("přečti /etc/hosts", VYCHOZI_MASKY, "přečti /etc/hosts", 0),  # N8: absolutní cesta není příkaz
    ("/loop 5m", VYCHOZI_MASKY, "[zakázaný příkaz] 5m", 1),
    ("/ samotné lomítko", VYCHOZI_MASKY, "/ samotné lomítko", 0),
    ("/etc/hosts", ("/*",), "/etc/hosts", 0),  # * ani ? nepřekročí /
    ("/loop 5m x", VYCHOZI_MASKY, "[zakázaný příkaz] 5m x", 1),
    ("/compact", VYCHOZI_MASKY, "[zakázaný příkaz]", 1),
    ("ahoj /sta", VYCHOZI_MASKY, "ahoj [zakázaný příkaz]", 1),
    ("viz https://example.com/stav a cesta/x", VYCHOZI_MASKY, "viz https://example.com/stav a cesta/x", 0),
    ("/loop 5m x", (), "/loop 5m x", 0),
    ("ahoj /stav /start", ("/sta*",), "ahoj [zakázaný příkaz] [zakázaný příkaz]", 2),
    ("ahoj /loop", ("/sta*",), "ahoj /loop", 0),
    ("řádek\n/clear", VYCHOZI_MASKY, "řádek\n[zakázaný příkaz]", 1),
])
def test_zakazane_prikazy(text, masky, vysledek, pocet):
    from mcp_voice import config
    assert config.ZAKAZANE_PRIKAZY_VYCHOZI == VYCHOZI_MASKY
    t, n, trefene = prijem.odstran_prikazy(text, masky)
    assert (t, n) == (vysledek, pocet)
    assert "/loop" not in t or not masky or masky == ("/sta*",)


async def test_zakazane_prikazy_v_prepisu_hlasovky(p, monkeypatch):
    await p.otevri()
    p.prepis_vysledek = {"text": "lomítko /compact prosím"}

    async def nic(*a, **k):
        return prijem.vstup_audio.InfoAudia("ogg", "ogg", 2.0)

    async def dek(*a, **k):
        return b"\x00" * 64000
    monkeypatch.setattr(prijem.vstup_audio, "over_format", nic)
    monkeypatch.setattr(prijem.vstup_audio, "dekoduj_f32_16k", dek)
    p.klient.soubory["f1"] = b"OggS"
    await p.p.prijmi(p.zprava(None, voice={"file_id": "f1", "mime_type": "audio/ogg", "duration": 2, "file_size": 4}))
    assert p.p.fronta[0].text == "lomítko [zakázaný příkaz] prosím" and p.p.fronta[0].forma == "hlas"


@pytest.mark.parametrize("text, typ, forma", [
    ("kolik je hodin", "text", "text"), ("kolik je hodin", "hlas", "hlas"),
    ("Řekni mi, co je nového", "text", "hlas"), ("přečti mi to", "text", "hlas"),
    ("přečti si soubor", "text", "text"), ("přečti soubor", "text", "text"),
    ("pošli textem stav", "hlas", "text"), ("odpověz na PC", "text", "pc"),
    ("řekni to na pc", "text", "pc"), ("nahlas, ale pak pošli textem", "text", "text"),
    ("pošli textem, ne, řekni", "text", "hlas"), ("zahlasuj", "text", "text"),
])
def test_forma(text, typ, forma):
    assert prijem.urci_formu(text, typ, config.FORMA_HLAS, config.FORMA_TEXT, config.FORMA_PC) == forma


def test_forma_prazdny_seznam_vypne_vyjimku():
    assert prijem.urci_formu("řekni mi", "text", (), config.FORMA_TEXT, config.FORMA_PC) == "text"


def test_obalka():
    o = prijem.obalka(4821, "hlas", 23, "hlas", 0, "prace", "cs", 2)
    assert o.startswith("[Telegram #4821 · hlasovka 0:23 · odpověď: hlas · okno do ") and o.endswith(
        " · práce · odstraněno příkazů: 2]")
    assert "\n" not in o


# ---------------------------------------------------------------- hlasovky

async def test_hlasovka_kontroly_pred_stazenim(p):
    await p.otevri()
    for v in ({"file_id": "f", "mime_type": "audio/mpeg", "duration": 2, "file_size": 10},
              {"file_id": "f", "mime_type": "audio/ogg", "duration": 301, "file_size": 10},
              {"file_id": "f", "mime_type": "audio/ogg", "duration": 2, "file_size": 6 * 1024 * 1024},
              {"file_id": "f", "mime_type": "audio/ogg", "duration": 2},
              {"file_id": "../f", "mime_type": "audio/ogg", "duration": 2, "file_size": 10}):
        await p.p.prijmi(p.zprava(None, voice=v))
    assert p.klient.soubor_volani == [] and p.p.fronta == []


def _audio(tmp_path, kodek):
    cesta = tmp_path / ("a-%s.ogg" % kodek)
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=1",
                        "-c:a", kodek, str(cesta)], capture_output=True, timeout=30)
    if r.returncode != 0:
        pytest.skip("ffmpeg bez kodeku %s" % kodek)
    return cesta.read_bytes()


async def test_hlasovka_opus_lokalni_prepis(p, tmp_path):
    await p.otevri()
    p.klient.soubory["f1"] = _audio(tmp_path, "libopus")
    await p.p.prijmi(p.zprava(None, voice={"file_id": "f1", "mime_type": "audio/ogg", "duration": 1, "file_size": 9}))
    assert p.prepisy and p.p.fronta[0].text == "kolik je hodin" and p.p.fronta[0].typ == "hlas"


async def test_hlasovka_jiny_kodek_odmitnuta(p, tmp_path):
    await p.otevri()
    p.klient.soubory["f1"] = _audio(tmp_path, "libvorbis")
    await p.p.prijmi(p.zprava(None, voice={"file_id": "f1", "mime_type": "audio/ogg", "duration": 1, "file_size": 9}))
    assert p.prepisy == [] and p.p.fronta == [] and "nepřijímám" in p.sablony()[-1]


@pytest.mark.parametrize("vysledek, sablona", [(HlasChyba("STT_LOCAL_FAILED"), "Přepis hlasovky není dostupný."),
                                               (RuntimeError("x"), "Přepis hlasovky není dostupný."),
                                               ({"text": "  "}, "Hlasovce jsem nerozuměl.")])
async def test_hlasovka_selhani_prepisu(p, tmp_path, vysledek, sablona):
    await p.otevri()
    p.klient.soubory["f1"] = _audio(tmp_path, "libopus")
    p.prepis_vysledek = vysledek
    await p.p.prijmi(p.zprava(None, voice={"file_id": "f1", "mime_type": "audio/ogg", "duration": 1, "file_size": 9}))
    assert p.p.fronta == [] and p.sablony()[-1] == sablona


# ---------------------------------------------------------------- doručení, nečinnost, konec tahu

async def test_doruceni_a_potvrzeni(p):
    await p.otevri()
    await p.p.prijmi(p.zprava("kolik je hodin", mid=4821))
    p.heartbeat()
    await p.p.krok()
    assert len(p.inbox.odeslano) == 1
    text = p.inbox.odeslano[0]
    assert text.startswith("[Telegram #4821 · text · odpověď: text · okno do ") and text.endswith("\nkolik je hodin")
    led = p.p.ledger()["4821"]
    assert led["stav"] == "odeslano" and "kolik" not in str(led)
    p.doruc_v_transkriptu(text)
    await p.p.krok()
    assert p.p.ledger()["4821"]["stav"] == "doruceno" and p.p.okno["tah"] == "4821"


async def test_doruceni_nepotvrzeno(p):
    await p.otevri()
    await p.p.prijmi(p.zprava("x", mid=10))
    p.heartbeat()
    await p.p.krok()
    p.T[0] += 11
    p.heartbeat()
    await p.p.krok()
    assert p.p.ledger()["10"]["stav"] == "nepotvrzeno" and p.sablony()[-1].startswith("Doručení nepotvrzeno")
    assert len(p.inbox.odeslano) == 1  # žádný nový pokus


async def test_posledni_vlozeni_pri_potvrzeni(p):
    """posledni_vlozeni se posune i při potvrzení doručení, i opožděného po vypršení potvrzení."""
    await p.otevri()
    await p.p.prijmi(p.zprava("x", mid=12))
    p.heartbeat()
    await p.p.krok()
    odeslano = p.p.okno["posledni_vlozeni"]
    p.T[0] += 5
    p.heartbeat()
    p.doruc_v_transkriptu(p.inbox.odeslano[0])
    await p.p.krok()
    assert p.p.okno["posledni_vlozeni"] == p.T[0] > odeslano
    # opožděné doručení: potvrzení vyprší, zpráva se objeví později
    await p.p.prijmi(p.zprava("y", mid=13))
    p.T[0] += 100
    p.heartbeat()
    await p.p.krok()
    p.T[0] += 11
    p.heartbeat()
    await p.p.krok()
    assert p.p.ledger()["13"]["stav"] == "nepotvrzeno" and "13" in p.p.pozde
    p.T[0] += 300
    p.heartbeat()
    p.doruc_v_transkriptu(p.inbox.odeslano[-1])
    await p.p.krok()
    assert p.p.okno["posledni_vlozeni"] == p.T[0] and "13" not in p.p.pozde


async def test_doruceni_bez_verified_pid_zavre_okno(p):
    await p.otevri()
    await p.p.prijmi(p.zprava("x", mid=11))
    p.heartbeat()
    await p.p.krok()
    p.doruc_v_transkriptu(p.inbox.odeslano[0], identita=None)
    await p.p.krok()
    assert p.p.okno["stav"] == "zavreno" and p.p.chyba == "neznamy_tvar_transkriptu"


async def test_chyba_socketu(p):
    await p.otevri()
    p.inbox.chyba = "socket_cizi"
    await p.p.prijmi(p.zprava("x", mid=12))
    p.heartbeat()
    await p.p.krok()
    assert p.p.chyba == "socket_cizi" and p.p.ledger()["12"]["stav"] == "nepotvrzeno"


async def test_klidna_session_20_min_bez_nastroju_doruci(p):
    """A17 (V2): klid bez nástrojů neznamená, že hook neběží; okno zůstane a zpráva se doručí."""
    await p.otevri()
    p.T[0] += 20 * 60
    await p.p.krok()
    await p.p.prijmi(p.zprava("x", mid=13))
    await p.p.krok()
    assert p.p.okno["stav"] == "otevreno" and len(p.inbox.odeslano) == 1


async def test_nastroj_po_heartbeatu_bez_noveho_heartbeatu_zavre(p):
    await p.otevri()
    p.T[0] += 10 * 60
    tg.pripis(p.tp, tg.asistent_nastroj(t=p.T[0] - 120), tg.vysledek_nastroje(t=p.T[0] - 119),
              tg.konec_tahu(t=p.T[0] - 118))
    await p.p.prijmi(p.zprava("x"))
    await p.p.krok()
    assert p.p.okno["stav"] == "zavreno" and p.p.okno["duvod_zavreni"] == "chyba"
    assert p.p.chyba == "hook_rozsahu_nebezi" and p.inbox.odeslano == []


async def test_nastroj_po_heartbeatu_hlidani_i_bez_zpravy(p):
    await p.otevri()
    p.T[0] += 10 * 60
    tg.pripis(p.tp, tg.asistent_nastroj(t=p.T[0] - 120))
    await p.p.krok()
    assert p.p.okno["stav"] == "zavreno" and p.p.chyba == "hook_rozsahu_nebezi"


async def test_cerstvy_nastroj_bez_heartbeatu_ceka(p):
    """Záznam tool_use vzniká před hookem: krátce po něm se nezavírá, jen čeká."""
    await p.otevri()
    p.T[0] += 10 * 60
    tg.pripis(p.tp, tg.asistent_nastroj(t=p.T[0] - 5))
    await p.p.prijmi(p.zprava("x"))
    await p.p.krok()
    assert p.p.okno["stav"] == "otevreno" and p.inbox.odeslano == []


async def test_nastroj_s_heartbeatem_doruci(p):
    await p.otevri()
    p.T[0] += 10 * 60
    tg.pripis(p.tp, tg.asistent_nastroj(t=p.T[0] - 120), tg.vysledek_nastroje(t=p.T[0] - 119),
              tg.konec_tahu(t=p.T[0] - 118))
    p.heartbeat(cas=p.T[0] - 119)
    await p.p.prijmi(p.zprava("x"))
    await p.p.krok()
    assert p.p.okno["stav"] == "otevreno" and len(p.inbox.odeslano) == 1


@pytest.mark.parametrize("uprava", ["smazat", "cizi_pid"])
async def test_bez_heartbeatu_tohoto_procesu_pri_doruceni_zavre(p, uprava):
    await p.otevri()
    cesta = puvod.soubor(p.stav, "rozsah", tg.SID)
    if uprava == "smazat":
        os.unlink(cesta)
    else:
        puvod.zapis(cesta, {"verze": 1, "cas": p.T[0], "claude_pid": IDENT[0] + 1})
    await p.p.prijmi(p.zprava("x"))
    await p.p.krok()
    assert p.p.okno["stav"] == "zavreno" and p.inbox.odeslano == []


async def test_pracujici_session_fronta_a_max_cekani(p):
    await p.otevri()
    tg.pripis(p.tp, tg.clovek("dlouhá práce", t=p.T[0]), tg.asistent_nastroj(t=p.T[0]))
    await p.p.prijmi(p.zprava("x", mid=20))
    p.heartbeat()
    await p.p.krok()
    assert p.inbox.odeslano == []
    p.T[0] += 121
    p.heartbeat()
    await p.p.krok()
    assert p.sablony()[-1] == "Session pracuje, zpráva je ve frontě (1)."
    p.T[0] += 15 * 60
    p.heartbeat()
    p.p.okno["posledni_zprava"] = p.T[0]
    await p.p.krok()
    assert len(p.inbox.odeslano) == 1


async def test_konec_tahu_bez_odpovedi(p):
    await p.otevri()
    await p.p.prijmi(p.zprava("x", mid=30))
    p.heartbeat()
    await p.p.krok()
    p.doruc_v_transkriptu(p.inbox.odeslano[0])
    await p.p.krok()
    tg.pripis(p.tp, tg.konec_tahu(t=p.T[0]))
    await p.p.krok()
    assert p.sablony()[-1] == "Hotovo, odpověď je v terminálu." and p.p.ledger()["30"]["stav"] == "bez_odpovedi"


async def test_esc_bez_odpovedi(p):
    await p.otevri()
    await p.p.prijmi(p.zprava("x", mid=31))
    p.heartbeat()
    await p.p.krok()
    p.doruc_v_transkriptu(p.inbox.odeslano[0])
    tg.pripis(p.tp, tg.asistent_nastroj(t=p.T[0]), tg.vysledek_nastroje(t=p.T[0]))
    await p.p.krok()
    assert p.sablony() == []
    p.T[0] += 91
    p.heartbeat()
    await p.p.krok()
    assert p.sablony()[-1] == "Přerušeno, odpověď je v terminálu."


async def test_notifikace_dialogu(p):
    await p.otevri()
    await p.p.prijmi(p.zprava("x", mid=32))
    p.heartbeat()
    await p.p.krok()
    p.doruc_v_transkriptu(p.inbox.odeslano[0])
    tg.pripis(p.tp, tg.asistent_nastroj(t=p.T[0]))
    await p.p.krok()
    for _ in range(2):
        puvod.pripis_jsonl(puvod.soubor(p.stav, "udalosti", tg.SID, ".jsonl"),
                           {"udalost": "notifikace", "prompt_id": "p-9", "cas": p.T[0]})
        await p.p.krok()
    assert p.sablony().count("Čeká potvrzení v terminálu nebo Remote Control.") == 1


# ---------------------------------------------------------------- odpovědi

async def test_cil_odpovedi(p):
    await p.otevri()
    with pytest.raises(HlasChyba) as e:
        p.p.cil_odpovedi()
    assert e.value.kod == "NOT_TELEGRAM_TURN"
    await p.p.prijmi(p.zprava("řekni mi čas", mid=40))
    p.heartbeat()
    await p.p.krok()
    p.doruc_v_transkriptu(p.inbox.odeslano[0])
    await p.p.krok()
    c = p.p.cil_odpovedi()
    assert c == {"znacka": "40", "message_id": 40, "forma": "hlas"}
    await p.p.zavri("stop_terminal")
    with pytest.raises(HlasChyba) as e:
        p.p.cil_odpovedi()
    assert e.value.kod == "WINDOW_CLOSED"


async def test_odpoved_od_ciziho_peer(p):
    await p.otevri()
    tg.pripis(p.tp, tg.peer(identita=tg.CIZI, t=p.T[0]))
    with pytest.raises(HlasChyba):
        p.p.cil_odpovedi()


def test_rozdel():
    assert prijem.rozdel("a\n\nb", 3, 4000) == ["a\n\nb"]
    casti = prijem.rozdel("x" * 9000, 3, 4000)
    assert [len(c) for c in casti] == [4000, 4000, 1000]
    casti = prijem.rozdel("y" * 20000, 3, 4000)
    assert len(casti) == 3 and casti[-1].endswith("(zbytek v terminálu)") and len(casti[-1]) == 4000
    casti = prijem.rozdel("a" * 3000 + "\n\n" + "b" * 3000, 3, 4000)
    assert casti == ["a" * 3000, "b" * 3000]


@pytest.mark.parametrize("text", ["-----BEGIN OPENSSH PRIVATE KEY-----\nabc", "token 1234567:AA" + "b" * 33,
                                  "sk-ant-" + "a" * 30, "ghp_" + "a" * 36, "AKIA" + "A" * 16,
                                  "token: " + "eyJ" + "hbGciOiJSUzI1NiJ9" + "." + "eyJ" + "zdWIiOiIxMjMifQ" + ".c2ln",
                                  "eyJ" + "a" * 12 + ".eyJ" + "b" * 12 + ".", "ya29." + "A" * 40,
                                  "refresh 1//0" + "g" * 40])
def test_tajemstvi(text):
    assert prijem.tajemstvi_v_textu(text)


def test_bez_tajemstvi():
    assert not prijem.tajemstvi_v_textu("Je 16:30, máš 3 otevřené úkoly.")
    assert not prijem.tajemstvi_v_textu("Soubor eyJ.txt a cesta 1//0 v textu.")


# ---------------------------------------------------------------- retence

async def test_otevreni_backlog_z_allowlistu_do_retence(p):
    """N7: zpráva zahozená při otevření (offset -1) se smaže retencí, cizí ne."""
    p.klient.odpovedi = [[{"update_id": 50, "message": {"message_id": 77, "from": {"id": CHAT}}},
                          {"update_id": 51, "message": {"message_id": 78, "from": {"id": 5}}}], []]
    await p.otevri()
    ids = [z["message_id"] for z in puvod.cti_jsonl(str(oknomod.adresar(p.stav) / "retence.jsonl"))]
    assert 77 in ids and 78 not in ids


async def test_ceka_potvrzeni_ulozeno_hned(p):
    """N9: stav „čeká potvrzení“ se zapíše hned (status bar)."""
    await p.otevri()
    await p.p.prijmi(p.zprava("x", mid=33))
    await p.p.krok()
    p.doruc_v_transkriptu(p.inbox.odeslano[0])
    tg.pripis(p.tp, tg.asistent_nastroj(t=p.T[0]))
    await p.p.krok()
    puvod.pripis_jsonl(puvod.soubor(p.stav, "udalosti", tg.SID, ".jsonl"),
                       {"udalost": "notifikace", "prompt_id": "p-9", "cas": p.T[0]})
    await p.p.krok()
    assert oknomod.nacti_okno(p.stav, puvod.klic_procesu(*IDENT))["ceka_potvrzeni"] is True


async def test_odpojeni_remote_control_behem_okna(p):
    """N9: odpojení RC během okna přepne dotazy na odmítnutí (i ve snímku politiky)."""
    await p.otevri()
    assert p.p.okno["bez_rc"] is False
    tg.pripis(p.tp, tg.bridge(url=None, t=p.T[0]))
    p.T[0] += 31
    await p.p.krok()
    pol = puvod.cti(puvod.soubor(p.stav, "politika", puvod.klic_procesu(*IDENT)))
    assert p.p.okno["bez_rc"] is True and p.p.okno["pri_dotazu"] == "odmitnout" and pol["bez_rc"] is True


async def test_retence(p):
    ted = p.T[0]
    cesta = str(oknomod.adresar(p.stav) / "retence.jsonl")
    for i, stari_h in enumerate([1, 25, 30, 47.995, 50]):
        puvod.pripis_jsonl(cesta, {"message_id": i, "cas": ted - stari_h * 3600})
    for i in range(150):
        puvod.pripis_jsonl(cesta, {"message_id": 1000 + i, "cas": ted - 26 * 3600})
    await p.p.retence(ted)
    smazane = [x for d in p.klient.smazano for x in d]
    assert all(len(d) <= 100 for d in p.klient.smazano)
    assert {1, 2}.issubset(smazane) and 0 not in smazane and 3 not in smazane and 4 not in smazane
    assert [z["message_id"] for z in puvod.cti_jsonl(cesta)] == [0]


async def test_retence_vypnuta(tmp_path, monkeypatch):
    p = P(tmp_path, monkeypatch)
    object.__setattr__(p.kfg.telegram, "smazat_po_h", 0)
    await p.p.retence(p.T[0])
    assert p.klient.smazano == []


async def test_prichozi_a_odchozi_do_retence(p):
    await p.otevri()
    await p.p.prijmi(p.zprava("/stav", mid=77))
    ids = [z["message_id"] for z in puvod.cti_jsonl(str(oknomod.adresar(p.stav) / "retence.jsonl"))]
    assert 77 in ids and p.klient.mid in ids


def test_povely():
    r = lambda t: oknomod.rozeber_povel(t, "/telegram", 60, 240, "prace")  # noqa: E731
    assert r("/telegram start") == oknomod.Povel("start", 60, "prace")
    assert r("/telegram start 2h") == oknomod.Povel("start", 120, "prace")
    assert r("/telegram start 90min čtení") == oknomod.Povel("start", 90, "cteni")
    assert r("/Telegram  START 30m PLNÝ") == oknomod.Povel("start", 30, "plny")
    assert r("/telegram start práce") == oknomod.Povel("start", 60, "prace")
    assert r("/telegram stop") == oknomod.Povel("stop") and r("/telegram stav") == oknomod.Povel("stav")
    # G3b: Remote Control posílá víc mezer a slovní jednotky
    assert r("/telegram  start 30 minut cteni") == oknomod.Povel("start", 30, "cteni")
    assert r("/telegram start 2 hodiny") == oknomod.Povel("start", 120, "prace")
    assert r("/telegram start 1 hodinu plný") == oknomod.Povel("start", 60, "plny")
    assert r("/telegram start 45 minutes") == oknomod.Povel("start", 45, "prace")
    for spatne in ("/telegram start prace 2h", "/telegram start 2", "/telegram start 1.5h", "/telegram", "telegram start",
                   "/telegram start 2h navic", "/telegram zapni", "/telegramstart"):
        with pytest.raises(oknomod.ChybaPovelu):
            r(spatne)
    with pytest.raises(oknomod.ChybaPovelu) as e:
        r("/telegram start 5h")
    assert e.value.kod == "povel_nad_strop" and "4 h" in e.value.zprava
    assert oknomod.je_povel("/telegram start", "/telegram") and not oknomod.je_povel("/telegramx", "/telegram")


async def test_snimek_politiky_nese_volby_bash(tmp_path, monkeypatch):
    """Volby obalu a konfigurace agenta dojdou do snímku politiky pro hook."""
    (tmp_path / "home" / "data").mkdir(parents=True)
    q = P(tmp_path, monkeypatch, konfigurace_agenta={"pridat": ["COPILOT.md"]},
          rezimy={"plny": {"bash_bez_dotazu": True, "bash_skryt": ["/nfs2"],
                           "bash_koreny": [{"cesta": "~/data", "pristup": "ro"}]}})
    await q.otevri()
    pol = puvod.cti(puvod.soubor(q.stav, "politika", puvod.klic_procesu(*IDENT)))
    assert pol["v"] == 3 and pol["bash_bez_dotazu"] is True and "COPILOT.md" in pol["agent"]
    assert "/nfs2" in pol["bash_skryt"] and pol["bash_koreny"] == {"plny": [[str(tmp_path / "home" / "data"), "ro"]]}
    assert str(tmp_path / "home" / ".local" / "bin") in pol["bash_domov"]
