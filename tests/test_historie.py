"""Mazání položky historie ElevenLabs po TTS (historie.py). Síť jen přes MockTransport."""
from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from mcp_voice import config, historie, server, status, strazce, tts
from mcp_voice.chyby import nastav_logovani

from .conftest import FALESNY_KLIC, HISTORIE_ID, PREDPLATNE, razitko
from .test_server import zavolej


def _transport(odpoved):
    zaznam = []

    def h(req):
        zaznam.append(req)
        return odpoved(req)
    return httpx.MockTransport(h), zaznam


def _vyjimka(typ):
    def h(req):
        raise typ("selhalo", request=req)
    return h


def _status(s):
    p = s.kfg.stav_adresar / "status.json"
    return json.loads(p.read_text()) if p.exists() else None


def _mazani(sit):
    return [x for x in sit.pozadavky if x.url.path.startswith("/v1/history/")]


@pytest.fixture(autouse=True)
def cekani(monkeypatch):
    """Prodleva před opakováním DELETE bez skutečného spánku; zaznamená délky."""
    zaznam = []

    async def pockej(s):
        zaznam.append(s)
    monkeypatch.setattr(historie, "_pockej", pockej)
    return zaznam


def _postupne(*odpovedi):
    """Handler, který vrací odpovědi v pořadí (poslední se opakuje)."""
    fronta = list(odpovedi)

    def h(req):
        o = fronta.pop(0) if len(fronta) > 1 else fronta[0]
        return o(req)
    return h


class _Prerusovany(httpx.AsyncByteStream):
    """Tělo TTS: kus zvuku a pak akce (výjimka nebo čekání navždy)."""

    def __init__(self, akce):
        self.akce = akce

    async def __aiter__(self):
        yield b"\x01\x00" * 100
        await self.akce()


# ------------------------------------------------------------- historie.smaz

@pytest.mark.parametrize("kod", [200, 204])
async def test_smaz_uspech(kod):
    t, zaznam = _transport(lambda req: httpx.Response(kod, json={"status": "ok"} if kod == 200 else None))
    assert await historie.smaz(FALESNY_KLIC, HISTORIE_ID, t) == historie.SMAZANO
    (req,) = zaznam
    assert req.method == "DELETE" and req.url.host == "api.elevenlabs.io"
    assert req.url.path == f"/v1/history/{HISTORIE_ID}" and not req.url.query
    assert req.headers["xi-api-key"] == FALESNY_KLIC and not req.content


@pytest.mark.parametrize("kod", [401, 403])
async def test_smaz_bez_opravneni(kod):
    t, zaznam = _transport(lambda req: httpx.Response(kod, json={"detail": {"status": "missing_permissions"}}))
    assert await historie.smaz(FALESNY_KLIC, HISTORIE_ID, t) == historie.OPRAVNENI
    assert len(zaznam) == 1


@pytest.mark.parametrize("odpoved,vysledek", [
    (lambda req: httpx.Response(500, text="x"), "nesmazano:http_500"),
    (lambda req: httpx.Response(429, text="x"), "nesmazano:http_429"),
    (lambda req: httpx.Response(400, text="x"), "nesmazano:http_400"),
    (lambda req: httpx.Response(302, headers={"location": "https://example.com/"}), "nesmazano:http_302"),
    (_vyjimka(httpx.ReadTimeout), historie.SIT),
    (_vyjimka(httpx.ConnectTimeout), historie.SIT),
    (_vyjimka(httpx.ConnectError), historie.SIT),
], ids=["500", "429", "400", "redirect", "read-timeout", "connect-timeout", "connect-error"])
async def test_smaz_chyby_bez_opakovani(odpoved, vysledek, cekani):
    t, zaznam = _transport(odpoved)
    assert await historie.smaz(FALESNY_KLIC, HISTORIE_ID, t) == vysledek
    assert len(zaznam) == 1 and cekani == []  # jedno volání, žádné opakování ani přesměrování


@pytest.mark.parametrize("kod", [404, 422])
@pytest.mark.parametrize("druha,vysledek", [
    (lambda req: httpx.Response(200, json={"status": "ok"}), historie.SMAZANO),
    (lambda req: httpx.Response(204), historie.SMAZANO),
    (None, None),  # stejný kód podruhé -> nesmazano:http_<kód>
    (lambda req: httpx.Response(500, text="x"), "nesmazano:http_500"),
    (lambda req: httpx.Response(401, text="x"), historie.OPRAVNENI),
    (_vyjimka(httpx.ConnectError), historie.SIT),
], ids=["200", "204", "znovu", "500", "401", "sit"])
async def test_smaz_jedno_opakovani_po_prodleve(kod, druha, vysledek, cekani):
    """Položka nemusí být hned po konci streamu uložená: 404/422 -> jednou znovu po PRODLEVA_S."""
    prvni = lambda req: httpx.Response(kod, text="x")  # noqa: E731
    t, zaznam = _transport(_postupne(prvni, druha or prvni))
    assert await historie.smaz(FALESNY_KLIC, HISTORIE_ID, t) == (vysledek or f"nesmazano:http_{kod}")
    assert len(zaznam) == 2 and cekani == [historie.PRODLEVA_S]
    assert {r.url.path for r in zaznam} == {f"/v1/history/{HISTORIE_ID}"}
    assert 2 <= historie.PRODLEVA_S <= 5


async def test_smaz_cizi_host(monkeypatch):
    monkeypatch.setattr(historie, "POVOLENE_HOSTY", frozenset({"jiny.example"}))
    t, zaznam = _transport(lambda req: httpx.Response(200))
    assert await historie.smaz(FALESNY_KLIC, HISTORIE_ID, t) == historie.CHYBA and len(zaznam) == 1


def test_http_stav():
    assert historie.http_stav(404) == "nesmazano:http_404"
    for spatne in (99, 600, 1000, -1):
        assert historie.http_stav(spatne) == historie.CHYBA
    assert all(historie.platny_stav(s) for s in historie.STAVY)
    assert historie.platny_stav("nesmazano:http_500") and not historie.platny_stav("nesmazano:http_5000")
    assert not historie.platny_stav(None) and not historie.platny_stav("nesmazano:http_")


@pytest.mark.parametrize("item_id", [None, "", ".", "..", "a/b", "a.b", "a b", "a?b=1", "a#b", "%2e%2e", "é",
                                     "a\nb", "x" * 65, 123, b"abc"])
async def test_smaz_neplatne_id_nic_neposle(item_id):
    t, zaznam = _transport(lambda req: httpx.Response(200))
    assert await historie.smaz(FALESNY_KLIC, item_id, t) == historie.NEPLATNE_ID
    assert zaznam == []


@pytest.mark.parametrize("item_id,popis", [
    (HISTORIE_ID + ", " + HISTORIE_ID, "délka 42, znaky: písmena, číslice, ',', mezera"),
    ("abc.def/ghi", "délka 11, znaky: písmena, '.', '/'"),
    ("x" * 65, "délka 65, znaky: písmena"),
    ("", "délka 0, znaky: žádné"),
    ("é\n#", "délka 3, znaky: jiné ASCII, řídicí, ne-ASCII"),
    (123, "typ int"),
])
def test_popis_id_bez_hodnoty(item_id, popis, caplog):
    caplog.set_level(logging.WARNING)
    assert historie.popis_id(item_id) == popis
    assert historie.stav_po_tts(item_id, True) == historie.NEPLATNE_ID
    assert popis in caplog.text and HISTORIE_ID not in caplog.text


def test_stav_po_tts():
    assert historie.stav_po_tts(None, True) == historie.NEULOZENO
    assert historie.stav_po_tts(HISTORIE_ID, False) == historie.VYPNUTO
    assert historie.stav_po_tts("../x", True) == historie.NEPLATNE_ID
    assert historie.stav_po_tts(HISTORIE_ID, True) == historie.MAZE_SE


def test_stavy_shodne_se_status():
    assert historie.STAVY == status.STAVY_HISTORIE
    assert historie.HTTP.pattern == status.HISTORIE_HTTP.pattern


# ------------------------------------------------------------- rekni -> mazání na pozadí

@pytest.mark.parametrize("argumenty", [
    {"text": "Ahoj", "cil": "pc"},
    {"text": "Ahoj", "cil": "telegram"},
    {"text": "Ahoj", "cil": "soubor"},
    {"text": "Ahoj", "cil": "pc", "model": "eleven_v4"},
], ids=["pc", "telegram", "soubor", "eleven_v4"])
async def test_rekni_smaze_polozku_vsechny_cesty(udelej_sluzby, sit, argumenty):
    s = udelej_sluzby()
    r = await zavolej(s, "rekni", argumenty)
    assert r["ok"] and r["historie_elevenlabs"] == historie.MAZE_SE
    (req,) = _mazani(sit)
    assert req.method == "DELETE" and req.url.path == f"/v1/history/{HISTORIE_ID}"
    d = _status(s)
    assert d["historie"] == historie.SMAZANO and d["historie_cas"] and d["kredity_zbyva"] == 24612
    st = await zavolej(s, "stav", {}, s_razitkem=False)
    assert st["historie"] == {"stav": "smazano", "cas": d["historie_cas"]} and st["mazat_historii"] is True


@pytest.mark.parametrize("odpoved,vysledek", [
    (lambda req: httpx.Response(401, json={"detail": {"status": "missing_permissions"}}), historie.OPRAVNENI),
    (lambda req: httpx.Response(403, text="x"), historie.OPRAVNENI),
    (lambda req: httpx.Response(422, text="x"), "nesmazano:http_422"),
    (lambda req: httpx.Response(404, text="x"), "nesmazano:http_404"),
    (lambda req: httpx.Response(500, text="x"), "nesmazano:http_500"),
    (_vyjimka(httpx.ReadTimeout), historie.SIT),
], ids=["401", "403", "422", "404", "500", "timeout"])
async def test_rekni_neuspesne_mazani_neselze(udelej_sluzby, sit, odpoved, vysledek):
    sit.hist = odpoved
    s = udelej_sluzby()
    r = await zavolej(s, "rekni", {"text": "Ahoj", "cil": "pc"})
    assert r["ok"] and r["historie_elevenlabs"] == historie.MAZE_SE
    assert len(_mazani(sit)) == (2 if vysledek in ("nesmazano:http_404", "nesmazano:http_422") else 1)
    d = _status(s)
    assert d["historie"] == vysledek and d["znaku_dnes"] == 4 and d["kredity_zbyva"] == 24612


async def test_rekni_vyjimka_pri_mazani_neselze(udelej_sluzby, sit, monkeypatch):
    async def bum(*a, **kw):
        raise RuntimeError("bum")
    monkeypatch.setattr(historie, "smaz", bum)
    s = udelej_sluzby()
    assert (await zavolej(s, "rekni", {"text": "Ahoj", "cil": "pc"}))["ok"]
    d = _status(s)
    assert d["historie"] == historie.CHYBA and d["kredity_zbyva"] == 24612


async def test_rekni_bez_hlavicky_nemaze(udelej_sluzby, sit):
    sit.el = lambda req: httpx.Response(200, content=b"\x01\x00" * 24000)
    s = udelej_sluzby()
    r = await zavolej(s, "rekni", {"text": "Ahoj", "cil": "pc"})
    assert r["ok"] and r["historie_elevenlabs"] == historie.NEULOZENO and _mazani(sit) == []
    assert _status(s)["historie"] == historie.NEULOZENO


@pytest.mark.parametrize("item_id", ["../user/subscription", "a.b", "x" * 65, "", "a%2Fb"])
async def test_rekni_neplatne_id_nemaze(udelej_sluzby, sit, item_id):
    sit.el = lambda req: httpx.Response(200, content=b"\x01\x00" * 24000, headers={"history-item-id": item_id})
    s = udelej_sluzby()
    r = await zavolej(s, "rekni", {"text": "Ahoj", "cil": "pc"})
    assert r["ok"] and r["historie_elevenlabs"] == historie.NEPLATNE_ID and _mazani(sit) == []
    assert [x.method for x in sit.pozadavky].count("DELETE") == 0
    assert _status(s)["historie"] == historie.NEPLATNE_ID


async def test_mazani_vypnute(udelej_sluzby, sit):
    s = udelej_sluzby(elevenlabs={"mazat_historii": False})
    r = await zavolej(s, "rekni", {"text": "Ahoj", "cil": "pc"})
    assert r["ok"] and r["historie_elevenlabs"] == historie.VYPNUTO and _mazani(sit) == []
    d = _status(s)
    assert d["historie"] == historie.VYPNUTO and d["kredity_zbyva"] == 24612
    assert (await zavolej(s, "stav", {}, s_razitkem=False))["mazat_historii"] is False


async def test_mazani_bez_kreditu(udelej_sluzby, sit):
    s = udelej_sluzby(elevenlabs={"kredity": False})
    assert (await zavolej(s, "rekni", {"text": "Ahoj", "cil": "pc"}))["ok"]
    assert len(_mazani(sit)) == 1 and not [x for x in sit.pozadavky if x.url.path == "/v1/user/subscription"]
    d = _status(s)
    assert d["historie"] == historie.SMAZANO and d["kredity_chyba"] == "vypnuto"


async def test_selhane_doruceni_po_tts_polozku_smaze(udelej_sluzby, sit):
    """Položka historie vzniká už úspěšným TTS: smazat ji i tehdy, když pak selže Telegram."""
    sit.tg = lambda req: httpx.Response(500, json={"ok": False})
    s = udelej_sluzby()
    r = await zavolej(s, "rekni", {"text": "Ahoj", "cil": "telegram"})
    assert "chyba" in r
    assert len(_mazani(sit)) == 1 and not [x for x in sit.pozadavky if x.url.path == "/v1/user/subscription"]
    assert _status(s)["historie"] == historie.SMAZANO


async def test_neuspesne_tts_nemaze(udelej_sluzby, sit):
    sit.el = lambda req: httpx.Response(500, text="x")
    s = udelej_sluzby()
    r = await zavolej(s, "rekni", {"text": "Ahoj", "cil": "pc"})
    assert r["chyba"]["kod"] == "ELEVENLABS_ERROR" and _mazani(sit) == [] and _status(s) is None


async def test_rekni_neceka_na_mazani(udelej_sluzby, sit, monkeypatch):
    """DELETE běží na pozadí: odpověď rekni nečeká ani na pomalé ElevenLabs."""
    s = udelej_sluzby()
    pustit = asyncio.Event()

    async def pomale(klic, item_id, transport=None, timeout_s=historie.TIMEOUT_S):
        await pustit.wait()
        return historie.SMAZANO
    monkeypatch.setattr(historie, "smaz", pomale)
    tid = "toolu_mazani000001"
    razitko(s.kfg.stav_adresar, tid, "rekni", {"text": "Ahoj", "cil": "pc"})
    async with create_connected_server_and_client_session(server.vytvor_server(s)) as klient:
        r = await asyncio.wait_for(klient.call_tool("rekni", {"text": "Ahoj", "cil": "pc"},
                                                    meta={strazce.META_KLIC: tid}), 5)
    assert json.loads(r.content[0].text)["historie_elevenlabs"] == historie.MAZE_SE and s.ulohy
    assert _status(s)["historie"] == historie.MAZE_SE
    pustit.set()
    await server.dokonci_ulohy(s)
    assert _status(s)["historie"] == historie.SMAZANO and not s.ulohy


@pytest.mark.parametrize("odpoved,hlaska", [
    (lambda req: httpx.Response(500, text="interni detail serveru"), "historie: HTTP 500 (pokus 1)"),
    (lambda req: httpx.Response(404, text="interni detail serveru"), "historie: HTTP 404 (pokus 2)"),
    (lambda req: (_ for _ in ()).throw(httpx.ConnectError(f"interni detail {req.url}", request=req)),
     "historie: síť (ConnectError)"),
], ids=["500", "404", "sit"])
async def test_klic_ani_id_neuniknou(udelej_sluzby, sit, caplog, capfd, odpoved, hlaska):
    nastav_logovani(logging.DEBUG)
    caplog.set_level(logging.DEBUG)
    sit.hist = odpoved
    s = udelej_sluzby()
    assert (await zavolej(s, "rekni", {"text": "Ahoj", "cil": "pc"}))["ok"]
    surovy = (s.kfg.stav_adresar / "status.json").read_text()
    _, stderr = capfd.readouterr()
    vse = surovy + caplog.text + stderr
    assert FALESNY_KLIC not in vse and HISTORIE_ID not in vse and "interni detail" not in vse
    assert f"WARNING mcp_voice.historie: {hlaska}" in stderr  # diagnostika jde na stderr serveru


# ------------------------------------------------------------- konfigurace a status.json

def test_konfigurace_mazat_historii(tmp_path):
    assert config.z_dat({}).elevenlabs.mazat_historii is True
    assert config.z_dat({"elevenlabs": {"mazat_historii": False}}).elevenlabs.mazat_historii is False
    for spatne in ("ne", 0, None, [True]):
        with pytest.raises(config.ChybaKonfigurace):
            config.z_dat({"elevenlabs": {"mazat_historii": spatne}})
    with pytest.raises(config.ChybaKonfigurace):
        config.z_dat({"elevenlabs": {"mazat_historie": False}})  # překlep = chyba, ne tichý výchozí stav


def test_status_historie_jen_zname_hodnoty(tmp_path):
    for v in status.STAVY_HISTORIE + (None,):
        assert status.aktualizuj(tmp_path, {"historie": v})["historie"] == v
    assert status.aktualizuj(tmp_path, {"historie": "nesmazano:http_404"})["historie"] == "nesmazano:http_404"
    for spatne in ("jina", "nesmazano:", "smazano\x1b", 1, True, "nesmazano:http_", "nesmazano:http_4040",
                   "nesmazano:http_404\n", "nesmazano:http_abc"):
        with pytest.raises(ValueError):
            status.aktualizuj(tmp_path, {"historie": spatne})
    (tmp_path / "status.json").write_text(json.dumps({"historie": "‮smazano", "historie_cas": "x\x1b"}))
    assert status.nacti(tmp_path) == {}


# ------------------------------------------------------------- opakování a přerušené TTS (rekni)

async def test_rekni_opakovani_po_404_smaze(udelej_sluzby, sit, cekani):
    sit.hist = _postupne(lambda req: httpx.Response(404, text="x"), lambda req: httpx.Response(200, json={}))
    s = udelej_sluzby()
    r = await zavolej(s, "rekni", {"text": "Ahoj", "cil": "pc"})
    assert r["ok"] and r["historie_elevenlabs"] == historie.MAZE_SE
    assert len(_mazani(sit)) == 2 and cekani == [historie.PRODLEVA_S]
    d = _status(s)
    assert d["historie"] == historie.SMAZANO and d["kredity_zbyva"] == 24612


async def _cyklus_chyby():
    raise httpx.ReadError("spojeni spadlo")


async def _cyklus_timeout():
    raise httpx.ReadTimeout("timeout")


@pytest.mark.parametrize("odpoved,kod", [
    (lambda req: httpx.Response(200, headers={"history-item-id": HISTORIE_ID}, stream=_Prerusovany(_cyklus_chyby)),
     "ELEVENLABS_ERROR"),
    (lambda req: httpx.Response(200, headers={"history-item-id": HISTORIE_ID}, stream=_Prerusovany(_cyklus_timeout)),
     "ELEVENLABS_ERROR"),
    (lambda req: httpx.Response(200, headers={"history-item-id": HISTORIE_ID}, content=b"\x01"), "ELEVENLABS_ERROR"),
    (lambda req: httpx.Response(200, headers={"history-item-id": HISTORIE_ID}, content=b"\x01\x00" * 100),
     "ELEVENLABS_ERROR"),  # s MAX_PCM = 10
], ids=["sit", "timeout", "prazdny-zvuk", "max-pcm"])
async def test_prerusene_tts_po_hlavicce_smaze(udelej_sluzby, sit, monkeypatch, request, odpoved, kod):
    """K položce historie vzniklé před přerušením TTS se server vrátí stejně jako po úspěchu."""
    if request.node.callspec.id == "max-pcm":
        monkeypatch.setattr(tts, "MAX_PCM", 10)
    sit.el = odpoved
    s = udelej_sluzby()
    r = await zavolej(s, "rekni", {"text": "Ahoj", "cil": "pc"})
    assert r["chyba"]["kod"] == kod
    (req,) = _mazani(sit)
    assert req.method == "DELETE" and req.url.path == f"/v1/history/{HISTORIE_ID}"
    assert _status(s)["historie"] == historie.SMAZANO
    assert not [x for x in sit.pozadavky if x.url.path == "/v1/user/subscription"]  # kredity jen po úspěchu


async def test_prerusene_tts_bez_hlavicky_ani_s_neplatnym_id_nemaze(udelej_sluzby, sit):
    sit.el = lambda req: httpx.Response(200, stream=_Prerusovany(_cyklus_chyby))
    s = udelej_sluzby()
    assert (await zavolej(s, "rekni", {"text": "Ahoj", "cil": "pc"}))["chyba"]["kod"] == "ELEVENLABS_ERROR"
    assert _mazani(sit) == [] and _status(s) is None
    sit.el = lambda req: httpx.Response(200, headers={"history-item-id": "a/b"}, stream=_Prerusovany(_cyklus_chyby))
    r = await zavolej(s, "rekni", {"text": "Nazdar", "cil": "pc"}, tid="toolu_jine00000002")
    assert r["chyba"]["kod"] == "ELEVENLABS_ERROR"
    assert _mazani(sit) == [] and _status(s)["historie"] == historie.NEPLATNE_ID


async def test_prerusene_tts_s_vypnutym_mazanim(udelej_sluzby, sit):
    sit.el = lambda req: httpx.Response(200, headers={"history-item-id": HISTORIE_ID}, stream=_Prerusovany(_cyklus_chyby))
    s = udelej_sluzby(elevenlabs={"mazat_historii": False})
    assert (await zavolej(s, "rekni", {"text": "Ahoj", "cil": "pc"}))["chyba"]
    assert _mazani(sit) == [] and _status(s)["historie"] == historie.VYPNUTO


async def test_zrusene_tts_po_hlavicce_smaze(udelej_sluzby, sit):
    """Zrušení volání uprostřed streamu: CancelledError se propaguje, mazání přesto proběhne."""
    zacal = asyncio.Event()

    async def navzdy():
        zacal.set()
        await asyncio.Event().wait()
    sit.el = lambda req: httpx.Response(200, headers={"history-item-id": HISTORIE_ID}, stream=_Prerusovany(navzdy))
    s = udelej_sluzby(strazce={"vyzadovat": False})
    uloha = asyncio.create_task(server.rekni_logika(s, "Ahoj", "pc"))
    await asyncio.wait_for(zacal.wait(), 5)
    uloha.cancel()
    with pytest.raises(asyncio.CancelledError):
        await uloha
    await server.dokonci_ulohy(s)
    assert len(_mazani(sit)) == 1 and _status(s)["historie"] == historie.SMAZANO


# ------------------------------------------------------------- souběh dvou rekni a zrušení v prodlevě

def _cekani_na_pokyn(monkeypatch, pustit: asyncio.Event | None = None) -> asyncio.Event:
    """_pockej ohlásí začátek prodlevy a čeká na pustit (bez něj navždy)."""
    ceka = asyncio.Event()

    async def pockej(sekund):
        ceka.set()
        await (pustit or asyncio.Event()).wait()
    monkeypatch.setattr(historie, "_pockej", pockej)
    return ceka


@pytest.mark.parametrize("prvni_kredity", [
    lambda req: httpx.Response(200, json=PREDPLATNE),  # 24612
    lambda req: httpx.Response(500, text="x"),
    lambda req: httpx.Response(401, text="x"),
    _vyjimka(httpx.ConnectError),
], ids=["starsi-zustatek", "http", "opravneni", "sit"])
async def test_starsi_kredity_neprepisou_novejsi(udelej_sluzby, sit, monkeypatch, prvni_kredity):
    """První rekni čeká na opakování DELETE (404), druhé mezitím zapíše novější kredity: ty zůstanou."""
    pustit = asyncio.Event()
    ceka = _cekani_na_pokyn(monkeypatch, pustit)
    sit.hist = _postupne(lambda req: httpx.Response(404, text="x"), lambda req: httpx.Response(204))
    sit.sub = _postupne(prvni_kredity, lambda req: httpx.Response(200, json=PREDPLATNE | {"character_count": 9000}))
    hodiny = [1_800_000_000.0]
    s = udelej_sluzby(strazce={"vyzadovat": False})
    s.hodiny = lambda: hodiny[0]
    assert (await server.rekni_logika(s, "Ahoj", "pc"))["ok"]
    await asyncio.wait_for(ceka.wait(), 5)
    prvni = set(s.ulohy)
    hodiny[0] += 10
    assert (await server.rekni_logika(s, "Nazdar", "pc"))["ok"]
    await asyncio.gather(*(s.ulohy - prvni))
    novejsi = {"kredity_zbyva": 21000, "kredity_cas": status.ted_iso(hodiny[0]), "kredity_chyba": None}
    assert {k: _status(s)[k] for k in novejsi} == novejsi
    hodiny[0] += 3
    pustit.set()
    await server.dokonci_ulohy(s)
    d = _status(s)
    assert {k: d[k] for k in novejsi} == novejsi and d["kredity_limit"] == 30000
    assert len(_mazani(sit)) == 3 and d["historie"] == historie.SMAZANO


async def test_zruseni_v_prodleve_druhy_delete_neposle(udelej_sluzby, sit, monkeypatch):
    """Zrušení úlohy během prodlevy: druhý DELETE neodejde, status zůstane maze_se."""
    ceka = _cekani_na_pokyn(monkeypatch)
    sit.hist = lambda req: httpx.Response(404, text="x")
    s = udelej_sluzby(strazce={"vyzadovat": False})
    assert (await server.rekni_logika(s, "Ahoj", "pc"))["ok"]
    (uloha,) = s.ulohy
    await asyncio.wait_for(ceka.wait(), 5)
    uloha.cancel()
    with pytest.raises(asyncio.CancelledError):
        await uloha
    await asyncio.sleep(0)  # done callback uvolní referenci
    assert not s.ulohy and len(_mazani(sit)) == 1
    d = _status(s)
    assert d["historie"] == historie.MAZE_SE and "kredity_zbyva" not in d
