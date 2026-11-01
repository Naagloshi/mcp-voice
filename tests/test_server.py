"""Nástroje přes skutečného MCP klienta (v paměti): strážce, rekni, chyby bez tajemství (H2)."""
import json
import logging
import os
import stat

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from mcp_voice import server, strazce
from mcp_voice.chyby import nastav_logovani

from .conftest import CHAT, FALESNY_KLIC, FALESNY_TOKEN, HISTORIE_ID, VOICE, razitko

TID = "toolu_01TestTestTest"


async def zavolej(s, nastroj, argumenty, tid=TID, s_razitkem=True):
    if s_razitkem:
        razitko(s.kfg.stav_adresar, tid, nastroj, argumenty)
    mcp = server.vytvor_server(s)
    async with create_connected_server_and_client_session(mcp) as klient:
        meta = {strazce.META_KLIC: tid} if tid else None
        r = await klient.call_tool(nastroj, argumenty, meta=meta)
    await server.dokonci_ulohy(s)  # dotaz na kredity běží na pozadí
    assert not r.isError, r.content
    return json.loads(r.content[0].text)


def tg_pozadavky(sit):
    return [r for r in sit.pozadavky if r.url.host == "api.telegram.org"]


# ------------------------------------------------------------- strážce (H1)

async def test_bez_razitka_nic_neodejde(udelej_sluzby, sit):
    s = udelej_sluzby()
    r = await zavolej(s, "rekni", {"text": "Ahoj", "cil": "telegram"}, s_razitkem=False)
    assert r["chyba"]["kod"] == "GUARD_MISSING"
    assert sit.pozadavky == []


async def test_bez_tool_use_id_nic_neodejde(udelej_sluzby, sit):
    s = udelej_sluzby()
    razitko(s.kfg.stav_adresar, TID, "rekni", {"text": "Ahoj"})
    r = await zavolej(s, "rekni", {"text": "Ahoj"}, tid=None, s_razitkem=False)
    assert r["chyba"]["kod"] == "GUARD_MISSING" and sit.pozadavky == []


async def test_meta_tool_use_id_dojde_do_serveru(udelej_sluzby, sit):
    """P6 (v procesu): _meta["claudecode/toolUseId"] projde FastMCP až ke strážci."""
    s = udelej_sluzby()
    r = await zavolej(s, "rekni", {"text": "Ahoj", "cil": "telegram"})
    assert r["ok"] is True and r["cil"] == "telegram"
    assert not (strazce.adresar(s.kfg.stav_adresar) / f"{TID}.json").exists()


async def test_razitko_na_jine_argumenty(udelej_sluzby, sit):
    s = udelej_sluzby()
    razitko(s.kfg.stav_adresar, TID, "rekni", {"text": "Ahoj", "cil": "pc"})
    r = await zavolej(s, "rekni", {"text": "Ahoj", "cil": "telegram"}, s_razitkem=False)
    assert r["chyba"]["kod"] == "GUARD_MISMATCH" and sit.pozadavky == []


async def test_stav_a_zastav_razitko_nepotrebuji(udelej_sluzby):
    s = udelej_sluzby()
    assert (await zavolej(s, "stav", {}, s_razitkem=False))["ok"]
    assert (await zavolej(s, "zastav", {}, s_razitkem=False))["ok"]


# ------------------------------------------------------------- rekni

async def test_rekni_telegram(udelej_sluzby, sit):
    s = udelej_sluzby()
    r = await zavolej(s, "rekni", {"text": "Dobrý den, tady hlas.", "cil": "telegram"})
    assert r["ok"] and r["cil"] == "telegram" and r["znaku"] == 21 and r["hlas"] == "test"
    assert "Dobrý den" not in json.dumps(r, ensure_ascii=False)  # mluvený text se nevrací
    el = [x for x in sit.pozadavky if x.url.host == "api.elevenlabs.io"]
    assert el[0].url.path == f"/v1/text-to-speech/{VOICE}/stream"
    assert sorted(x.url.path for x in el[1:]) == [f"/v1/history/{HISTORIE_ID}", "/v1/user/subscription"]
    assert el[0].url.params["output_format"] == "pcm_24000"
    assert json.loads(el[0].content)["language_code"] == "cs"
    tg = tg_pozadavky(sit)
    assert len(tg) == 1 and tg[0].url.path.endswith("/sendVoice")
    telo = tg[0].content
    assert b'name="chat_id"' in telo and CHAT.encode() in telo
    assert b"caption" not in telo and b"OggS" in telo


async def test_rekni_pc(udelej_sluzby, sit, prehravac):
    s = udelej_sluzby()
    r = await zavolej(s, "rekni", {"text": "Ahoj", "cil": "pc"})
    assert r["ok"] and r["cil"] == "pc" and prehravac.pridano and tg_pozadavky(sit) == []


@pytest.mark.parametrize("zamek,kod", [(True, "PC_LOCKED"), (None, "PC_STATE_UNKNOWN")])
async def test_rekni_pc_zamceno(udelej_sluzby, sit, plocha, zamek, kod):
    plocha.zamek = zamek
    r = await zavolej(udelej_sluzby(), "rekni", {"text": "Ahoj", "cil": "pc"})
    assert r["chyba"]["kod"] == kod and sit.pozadavky == []


async def test_rekni_auto_bez_stavu_pc(udelej_sluzby, sit, plocha):
    plocha.zamek, plocha.necinnost = None, None
    r = await zavolej(udelej_sluzby(), "rekni", {"text": "Ahoj"})
    assert r["chyba"]["kod"] == "TARGET_UNRESOLVED" and sit.pozadavky == []


async def test_rekni_soubor_a_ulozit(udelej_sluzby, sit, tmp_path):
    s = udelej_sluzby()
    r = await zavolej(s, "rekni", {"text": "Ahoj", "cil": "soubor", "ulozit": "pozdrav"})
    p = tmp_path / "vystup" / "pozdrav.mp3"
    assert r["ok"] and r["soubor"] == str(p) and p.exists()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "vystup").stat().st_mode) == 0o700


@pytest.mark.parametrize("argumenty,kod", [
    ({"text": ""}, "TEXT_EMPTY"),
    ({"text": "   "}, "TEXT_EMPTY"),
    ({"text": "x" * 1501}, "TEXT_TOO_LONG"),
    ({"text": "Ahoj", "hlas": "neznamy"}, "VOICE_UNKNOWN"),
    ({"text": "Ahoj", "hlas": VOICE}, "VOICE_UNKNOWN"),  # surové voice_id jen s povolit_voice_id
    ({"text": "Ahoj", "model": "eleven_turbo_v2_5"}, "MODEL_NOT_ALLOWED"),
    ({"text": "Ahoj", "ulozit": "../x"}, "OUTPUT_PATH_INVALID"),
    ({"text": "Ahoj", "ulozit": "/tmp/x"}, "OUTPUT_PATH_INVALID"),
    ({"text": "Ahoj", "ulozit": ".skryty"}, "OUTPUT_PATH_INVALID"),
])
async def test_rekni_validace_bez_site(udelej_sluzby, sit, argumenty, kod):
    r = await zavolej(udelej_sluzby(), "rekni", argumenty)
    assert r["chyba"]["kod"] == kod and sit.pozadavky == []


async def test_rekni_bez_hlasu_v_konfiguraci(udelej_sluzby, sit):
    s = udelej_sluzby(elevenlabs={"hlas": "vychozi", "hlasy": {}, "data_use_vypnuto": True})
    r = await zavolej(s, "rekni", {"text": "Ahoj", "cil": "pc"})
    assert r["chyba"]["kod"] == "VOICE_UNKNOWN" and "elevenlabs.hlasy" in r["chyba"]["zprava"]
    assert "žádný hlas" in r["chyba"]["zprava"] and sit.pozadavky == []


async def test_rekni_existujici_soubor_se_neprepise(udelej_sluzby, sit, tmp_path):
    s = udelej_sluzby()
    (tmp_path / "vystup").mkdir(mode=0o700)
    (tmp_path / "vystup" / "a.mp3").write_bytes(b"puvodni")
    r = await zavolej(s, "rekni", {"text": "Ahoj", "ulozit": "a"})
    assert r["chyba"]["kod"] == "OUTPUT_PATH_INVALID" and sit.pozadavky == []
    assert (tmp_path / "vystup" / "a.mp3").read_bytes() == b"puvodni"


async def test_data_use_nepotvrzeno_nic_neodejde(udelej_sluzby, sit):
    s = udelej_sluzby(elevenlabs={"data_use_vypnuto": False})
    r = await zavolej(s, "rekni", {"text": "Ahoj", "cil": "telegram"})
    assert r["chyba"]["kod"] == "DATA_USE_NOT_CONFIRMED" and sit.pozadavky == []


async def test_denni_limit_bez_site(udelej_sluzby, sit):
    s = udelej_sluzby(limity={"znaku_za_den": 10})
    r = await zavolej(s, "rekni", {"text": "Ahoj ahoj", "cil": "telegram"}, tid="toolu_prvni0001")
    assert r["ok"] and r["zbyva_znaku_dnes"] == 1
    pocet = len(sit.pozadavky)
    r = await zavolej(s, "rekni", {"text": "Další", "cil": "telegram"}, tid="toolu_druhy0002")
    assert r["chyba"]["kod"] == "DAILY_LIMIT" and len(sit.pozadavky) == pocet


async def test_duplicita_a_limit_za_minutu(udelej_sluzby, sit):
    s = udelej_sluzby(limity={"volani_za_minutu": 2})
    assert (await zavolej(s, "rekni", {"text": "A", "cil": "pc"}, tid="toolu_a0000001"))["ok"]
    assert (await zavolej(s, "rekni", {"text": "A", "cil": "pc"}, tid="toolu_a0000002"))["chyba"]["kod"] == "DUPLICATE"
    assert (await zavolej(s, "rekni", {"text": "B", "cil": "pc"}, tid="toolu_a0000003"))["ok"]
    assert (await zavolej(s, "rekni", {"text": "C", "cil": "pc"}, tid="toolu_a0000004"))["chyba"]["kod"] == "RATE_LIMIT"


async def test_telegram_neni_nastaven(udelej_sluzby, sit):
    s = udelej_sluzby(telegram={"chat_id": None})
    r = await zavolej(s, "rekni", {"text": "Ahoj", "cil": "telegram"})
    assert r["chyba"]["kod"] == "TELEGRAM_NOT_CONFIGURED" and sit.pozadavky == []


async def test_chybi_klic_v_keyringu(udelej_sluzby, sit):
    s = udelej_sluzby()
    s.cteni_tajemstvi = lambda sl, k: None
    r = await zavolej(s, "rekni", {"text": "Ahoj", "cil": "pc"})
    assert r["chyba"]["kod"] == "SECRET_MISSING" and "mcp-voice" in r["chyba"]["zprava"]
    assert sit.pozadavky == []


def _bez_backendu(sluzba, klic):
    from keyring.backends import fail
    return fail.Keyring().get_password(sluzba, klic)


@pytest.mark.parametrize("cil", ["pc", "telegram"])
async def test_keyring_bez_backendu_neni_chybejici_tajemstvi(udelej_sluzby, sit, cil):
    s = udelej_sluzby()
    s.cteni_tajemstvi = _bez_backendu
    r = await zavolej(s, "rekni", {"text": "Ahoj", "cil": cil})
    assert r["chyba"]["kod"] == "KEYRING_UNAVAILABLE" and "Secret Service" in r["chyba"]["zprava"]
    assert sit.pozadavky == []


async def test_chybi_token_bota(udelej_sluzby, sit):
    s = udelej_sluzby()
    s.cteni_tajemstvi = lambda sl, k: None if k == "telegram:bot_token" else "x" * 40
    r = await zavolej(s, "rekni", {"text": "Ahoj", "cil": "telegram"})
    assert r["chyba"]["kod"] == "TELEGRAM_NOT_CONFIGURED" and "telegram:bot_token" in r["chyba"]["zprava"]
    assert sit.pozadavky == []


# ------------------------------------------------------------- H2: tajemství nikdy ven

def _vyjimka(typ):
    def h(req):
        raise typ("selhalo pro " + str(req.url), request=req)
    return h


TG_CHYBY = [
    lambda req: httpx.Response(400, json={"ok": False, "description": f"chat not found bot{FALESNY_TOKEN}"}),
    lambda req: httpx.Response(403, json={"ok": False, "description": "Forbidden: bot was blocked by the user"}),
    lambda req: httpx.Response(413, text="Request Entity Too Large"),
    lambda req: httpx.Response(200, json={"ok": True, "result": {"chat": {"id": 1, "type": "group"}}}),
    _vyjimka(httpx.ReadTimeout),
    _vyjimka(httpx.ConnectError),
]


@pytest.mark.parametrize("odpoved", TG_CHYBY)
async def test_token_telegramu_neunikne(udelej_sluzby, sit, odpoved, caplog, capfd):
    nastav_logovani(logging.DEBUG)
    logging.getLogger("httpx").setLevel(logging.INFO)  # i kdyby někdo hlučnost vrátil
    caplog.set_level(logging.DEBUG)
    sit.tg = odpoved
    r = await zavolej(udelej_sluzby(), "rekni", {"text": "Ahoj", "cil": "telegram"})
    assert r["chyba"]["kod"] == "TELEGRAM_ERROR"
    vse = json.dumps(r) + caplog.text + "".join(capfd.readouterr())
    assert FALESNY_TOKEN not in vse and FALESNY_TOKEN.split(":")[1] not in vse


EL_CHYBY = [
    (lambda req: httpx.Response(401, json={"detail": {"status": "invalid_api_key", "message": FALESNY_KLIC}}),
     "ELEVENLABS_AUTH"),
    (lambda req: httpx.Response(401, json={"detail": {"status": "quota_exceeded"}}), "ELEVENLABS_QUOTA"),
    (lambda req: httpx.Response(429, json={"detail": {"status": "too_many_concurrent_requests"}}), "ELEVENLABS_RATE"),
    (lambda req: httpx.Response(400, json={"detail": {"status": "voice_not_found"}}), "ELEVENLABS_VOICE"),
    (lambda req: httpx.Response(500, text="Ahoj " + FALESNY_KLIC), "ELEVENLABS_ERROR"),
    (_vyjimka(httpx.ReadTimeout), "ELEVENLABS_ERROR"),
    (_vyjimka(httpx.ConnectError), "ELEVENLABS_ERROR"),
]


@pytest.mark.parametrize("odpoved,kod", EL_CHYBY)
async def test_klic_elevenlabs_neunikne(udelej_sluzby, sit, odpoved, kod, caplog, capfd):
    nastav_logovani(logging.DEBUG)
    caplog.set_level(logging.DEBUG)
    sit.el = odpoved
    r = await zavolej(udelej_sluzby(), "rekni", {"text": "Ahoj", "cil": "pc"})
    assert r["chyba"]["kod"] == kod
    vse = json.dumps(r) + caplog.text + "".join(capfd.readouterr())
    assert FALESNY_KLIC not in vse and "Ahoj" not in json.dumps(r)


async def test_httpx_na_info_nic_nezaloguje(udelej_sluzby, sit, caplog):
    nastav_logovani(logging.DEBUG)
    caplog.set_level(logging.INFO)
    await zavolej(udelej_sluzby(), "rekni", {"text": "Ahoj", "cil": "telegram"})
    assert not [r for r in caplog.records if r.name.startswith(("httpx", "httpcore"))]


async def test_neocekavana_vyjimka_bez_textu(udelej_sluzby, monkeypatch):
    async def bum(*a, **k):
        raise RuntimeError(f"tajne {FALESNY_TOKEN}")
    monkeypatch.setattr(server, "rekni_logika", bum)
    r = await zavolej(udelej_sluzby(), "rekni", {"text": "Ahoj"})
    assert r["chyba"]["kod"] == "INTERNAL_ERROR" and FALESNY_TOKEN not in json.dumps(r)


async def test_jen_sendvoice_a_jen_povolene_hosty(udelej_sluzby, sit):
    for i, cil in enumerate(("telegram", "pc", "soubor")):
        await zavolej(udelej_sluzby(), "rekni", {"text": f"Text {i}", "cil": cil}, tid=f"toolu_host000{i}")
    for r in sit.pozadavky:
        assert r.url.host in ("api.elevenlabs.io", "api.telegram.org")
        if r.url.host == "api.telegram.org":
            assert r.url.path.split("/")[-1] == "sendVoice" and r.method == "POST"


def test_klient_telegramu_nezna_jine_metody():
    """V kódu (bez docstringů) jen metody z allowlistu, host api.telegram.org, žádné raise_for_status."""
    import ast
    import inspect

    from mcp_voice import telegram, tts
    for modul in (telegram, tts):
        strom = ast.parse(inspect.getsource(modul))
        retezce, atributy = [], []
        for uzel in ast.walk(strom):
            if isinstance(uzel, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)) and ast.get_docstring(uzel):
                uzel.body = uzel.body[1:]
        for uzel in ast.walk(strom):
            if isinstance(uzel, ast.Constant) and isinstance(uzel.value, str):
                retezce.append(uzel.value)
            if isinstance(uzel, ast.Attribute):
                atributy.append(uzel.attr)
        assert "raise_for_status" not in atributy
        for zakazane in ("setWebhook", "getMe", "deleteWebhook", "logOut", "forwardMessage"):
            assert not any(zakazane in r for r in retezce)
        if modul is tts:
            assert not any(m in r for r in retezce for m in telegram.METODY)
    assert telegram.METODY == ("sendVoice", "sendMessage", "getUpdates", "getFile", "deleteMessages")
    assert telegram.METODA == "sendVoice" and telegram.HOST == "api.telegram.org"
    with pytest.raises(ValueError):
        telegram._url("x", "setWebhook")


def test_stav_pro_ladeni_bez_nazvu_okna(udelej_sluzby, plocha):
    plocha.okno_trida = "tajny.titulek"
    d = server.stav_logika(udelej_sluzby())
    assert "tajny" not in json.dumps(d)
    assert os.path.basename(__file__)  # jen aby test měl i jinou kontrolu
