"""Zůstatek kreditů (kredity.py), status.json a příkaz mcp-voice-status. Síť jen přes MockTransport."""
from __future__ import annotations

import asyncio
import datetime as dt
import fcntl
import json
import logging
import os
import stat
import subprocess
import sys
import time

import httpx
import pytest

from mcp.shared.memory import create_connected_server_and_client_session

from mcp_voice import kredity, server, status, strazce
from mcp_voice.chyby import nastav_logovani

from .conftest import FALESNY_KLIC, PREDPLATNE, razitko
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


# ------------------------------------------------------------- kredity.zjisti

async def test_kredity_uspech():
    t, zaznam = _transport(lambda req: httpx.Response(200, json=PREDPLATNE))
    d = await kredity.zjisti(FALESNY_KLIC, t)
    assert d["kredity_zbyva"] == 30000 - 5388 and d["kredity_limit"] == 30000 and d["kredity_chyba"] is None
    assert d["obnova"] == dt.datetime.fromtimestamp(1767225600).astimezone().isoformat(timespec="seconds")
    assert set(d) == {"kredity_zbyva", "kredity_limit", "obnova", "kredity_chyba"}
    (req,) = zaznam
    assert req.method == "GET" and req.url.host == "api.elevenlabs.io" and req.url.path == "/v1/user/subscription"
    assert req.headers["xi-api-key"] == FALESNY_KLIC and not req.content


@pytest.mark.parametrize("kod", [401, 403])
async def test_kredity_bez_opravneni(kod):
    t, _ = _transport(lambda req: httpx.Response(kod, json={"detail": {"status": "missing_permissions"}}))
    assert await kredity.zjisti(FALESNY_KLIC, t) == {"kredity_zbyva": None, "kredity_limit": None, "obnova": None,
                                                     "kredity_chyba": "opravneni"}


@pytest.mark.parametrize("odpoved,chyba", [
    (lambda req: httpx.Response(500, text="x"), "http"),
    (lambda req: httpx.Response(302, headers={"location": "https://example.com/"}), "http"),
    (_vyjimka(httpx.ReadTimeout), "sit"),
    (_vyjimka(httpx.ConnectError), "sit"),
    (lambda req: httpx.Response(200, text="není json"), "format"),
    (lambda req: httpx.Response(200, content=b"\xff\xfe"), "format"),
    (lambda req: httpx.Response(200, json=[1]), "format"),
    (lambda req: httpx.Response(200, json={"character_count": 1}), "format"),
    (lambda req: httpx.Response(200, json={"character_count": True, "character_limit": 10}), "format"),
    (lambda req: httpx.Response(200, json={"character_count": -1, "character_limit": 10}), "format"),
    (lambda req: httpx.Response(200, json={"character_count": 1, "character_limit": status.MAX_CISLO}), "format"),
    (lambda req: httpx.Response(200, content=b" " * (kredity.MAX_TELO + 10)), "format"),
])
async def test_kredity_chyby(odpoved, chyba):
    t, zaznam = _transport(odpoved)
    assert await kredity.zjisti(FALESNY_KLIC, t) == {"kredity_chyba": chyba}
    assert len(zaznam) == 1  # bez opakování, bez přesměrování


async def test_kredity_vycerpane_a_bez_obnovy():
    t, _ = _transport(lambda req: httpx.Response(200, json={"character_count": 50, "character_limit": 40}))
    assert await kredity.zjisti(FALESNY_KLIC, t) == {"kredity_zbyva": 0, "kredity_limit": 40, "obnova": None,
                                                     "kredity_chyba": None}


# ------------------------------------------------------------- rekni a stav -> status.json

def _status(s):
    p = s.kfg.stav_adresar / "status.json"
    return json.loads(p.read_text()) if p.exists() else None


async def test_rekni_zapise_status(udelej_sluzby, sit, caplog, capfd):
    nastav_logovani(logging.DEBUG)
    caplog.set_level(logging.DEBUG)
    s = udelej_sluzby()
    r = await zavolej(s, "rekni", {"text": "Ahoj světe", "cil": "telegram"})
    assert r["ok"]
    d = _status(s)
    assert d["den"] == status.den() and d["znaku_dnes"] == 10 and d["limit_den"] == 10000
    assert d["kredity_zbyva"] == 24612 and d["kredity_limit"] == 30000 and d["kredity_chyba"] is None
    assert d["kredity_cas"] and d["cas"] and d["v"] == 1
    assert set(d) <= set(status.POLE) | {"v"}
    p = s.kfg.stav_adresar / "status.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    surovy = p.read_text()
    vse = surovy + caplog.text + "".join(capfd.readouterr())
    assert FALESNY_KLIC not in vse and "starter" not in surovy and "Ahoj" not in surovy
    sub = [x for x in sit.pozadavky if x.url.path == "/v1/user/subscription"]
    assert len(sub) == 1


async def test_rekni_neceka_na_kredity(udelej_sluzby, sit, monkeypatch):
    """Dotaz na kredity běží na pozadí: odpověď rekni nečeká ani na pomalé ElevenLabs."""
    s = udelej_sluzby()
    pustit = asyncio.Event()

    async def pomale(klic, transport=None, timeout_s=kredity.TIMEOUT_S):
        await pustit.wait()
        return {"kredity_zbyva": 1, "kredity_limit": 2, "obnova": None, "kredity_chyba": None}
    monkeypatch.setattr(kredity, "zjisti", pomale)
    tid = "toolu_pozadi00001"
    razitko(s.kfg.stav_adresar, tid, "rekni", {"text": "Ahoj", "cil": "pc"})
    async with create_connected_server_and_client_session(server.vytvor_server(s)) as klient:
        r = await asyncio.wait_for(klient.call_tool("rekni", {"text": "Ahoj", "cil": "pc"},
                                                    meta={strazce.META_KLIC: tid}), 5)
    assert json.loads(r.content[0].text)["ok"] and s.ulohy
    assert _status(s)["znaku_dnes"] == 4 and "kredity_zbyva" not in _status(s)
    pustit.set()
    await server.dokonci_ulohy(s)
    assert _status(s)["kredity_zbyva"] == 1 and not s.ulohy


async def test_rekni_bez_opravneni_neselze(udelej_sluzby, sit):
    sit.sub = lambda req: httpx.Response(401, json={"detail": {"status": "missing_permissions"}})
    s = udelej_sluzby()
    assert (await zavolej(s, "rekni", {"text": "Ahoj", "cil": "pc"}))["ok"]
    d = _status(s)
    assert d["kredity_zbyva"] is None and d["kredity_limit"] is None and d["kredity_chyba"] == "opravneni"
    assert d["znaku_dnes"] == 4


async def test_rekni_vyjimka_v_kreditech_neselze(udelej_sluzby, sit):
    def bum(req):
        raise RuntimeError("bum")
    sit.sub = bum
    s = udelej_sluzby()
    assert (await zavolej(s, "rekni", {"text": "Ahoj", "cil": "pc"}))["ok"]
    assert _status(s)["znaku_dnes"] == 4


async def test_vypadek_site_necha_posledni_kredity(udelej_sluzby, sit):
    s = udelej_sluzby()
    assert (await zavolej(s, "rekni", {"text": "Ahoj", "cil": "pc"}, tid="toolu_prvni0001"))["ok"]
    cas = _status(s)["kredity_cas"]
    sit.sub = _vyjimka(httpx.ConnectError)
    assert (await zavolej(s, "rekni", {"text": "Nazdar", "cil": "pc"}, tid="toolu_druhy0002"))["ok"]
    d = _status(s)
    assert d["kredity_zbyva"] == 24612 and d["kredity_cas"] == cas and d["kredity_chyba"] == "sit"
    assert d["znaku_dnes"] == 10


async def test_kredity_vypnute(udelej_sluzby, sit):
    s = udelej_sluzby(elevenlabs={"kredity": False})
    assert (await zavolej(s, "rekni", {"text": "Ahoj", "cil": "pc"}))["ok"]
    assert not [x for x in sit.pozadavky if x.url.path == "/v1/user/subscription"]
    d = _status(s)
    assert d["kredity_chyba"] == "vypnuto" and d["kredity_zbyva"] is None and d["znaku_dnes"] == 4


async def test_neuspesne_rekni_kredity_nezjistuje(udelej_sluzby, sit):
    sit.el = lambda req: httpx.Response(500, text="x")
    s = udelej_sluzby()
    r = await zavolej(s, "rekni", {"text": "Ahoj", "cil": "pc"})
    assert r["chyba"]["kod"] == "ELEVENLABS_ERROR"
    assert not [x for x in sit.pozadavky if x.url.path == "/v1/user/subscription"] and _status(s) is None


async def test_stav_bez_site_s_kredity(udelej_sluzby, sit):
    s = udelej_sluzby()
    status.aktualizuj(s.kfg.stav_adresar, {"den": "2000-01-01", "znaku_dnes": 99, "limit_den": 10000,
                                           "kredity_zbyva": 1234, "kredity_limit": 30000, "obnova": None,
                                           "kredity_cas": "2026-01-01T10:00:00+01:00", "kredity_chyba": None})
    s.rozpocet.rezervuj(7, "x")
    d = await zavolej(s, "stav", {}, s_razitkem=False)
    assert sit.pozadavky == []
    assert d["kredity"] == {"zbyva": 1234, "limit": 30000, "obnova": None, "cas": "2026-01-01T10:00:00+01:00",
                            "chyba": None}
    st = _status(s)
    assert st["den"] == status.den() and st["znaku_dnes"] == 7 and st["kredity_zbyva"] == 1234


async def test_stav_bez_souboru(udelej_sluzby, sit):
    d = await zavolej(udelej_sluzby(), "stav", {}, s_razitkem=False)
    assert d["kredity"] == {"zbyva": None, "limit": None, "obnova": None, "cas": None, "chyba": None}


# ------------------------------------------------------------- status.json

def test_aktualizuj_jen_povolena_pole(tmp_path):
    for zmeny in ({"email": "x"}, {"kredity_zbyva": "10"}, {"kredity_zbyva": True}, {"znaku_dnes": -1},
                  {"kredity_chyba": "jina"}, {"cas": "x"}, {"den": "x" * 41}, {"kredity_zbyva": status.MAX_CISLO},
                  {"limit_den": 10**4000}, {"den": ""}, {"obnova": "2026-01-01\u009b31m"},
                  {"kredity_cas": "\u202e2026-01-01"}, {"den": "2026 01 01"}, {"obnova": "2026-01-01\x1b[31m"}):
        with pytest.raises(ValueError):
            status.aktualizuj(tmp_path, zmeny)
    assert not (tmp_path / "status.json").exists()


def test_aktualizuj_platne_hodnoty(tmp_path):
    zmeny = {"den": "2026-01-02", "znaku_dnes": 0, "kredity_zbyva": status.MAX_CISLO - 1,
             "obnova": "2026-01-01T00:00:00+01:00", "kredity_cas": "2026-01-01T00:00:00-05:00"}
    d = status.aktualizuj(tmp_path, zmeny)
    assert {k: d[k] for k in zmeny} == zmeny and status.RETEZEC.fullmatch(d["cas"])


def test_nacti_odfiltruje_ridici_znaky_a_obri_cisla(tmp_path):
    podvrh = {"den": "2026-01-02", "znaku_dnes": 10**4000, "limit_den": 500, "kredity_zbyva": status.MAX_CISLO,
              "obnova": "\u009b31m", "kredity_cas": "\u202eabc", "cas": "x\x1b"}
    (tmp_path / "status.json").write_text(json.dumps(podvrh, ensure_ascii=False))
    assert status.nacti(tmp_path) == {"den": "2026-01-02", "limit_den": 500}
    r, _ = _cli("--stav", str(tmp_path), "--json")
    assert r.returncode == 0 and "\u009b" not in r.stdout.decode() and "\u202e" not in r.stdout.decode()


def test_starsi_dotaz_na_kredity_neprepise_novejsi(tmp_path):
    """kredity_k = čas odpovědi dotazu; uložený novější kredity_cas vyhraje, ostatní pole se zapíšou."""
    t0, iso = 1_800_000_000, status.ted_iso
    status.aktualizuj(tmp_path, {"kredity_zbyva": 21000, "kredity_cas": iso(t0 + 10), "kredity_chyba": None},
                      t0 + 10, iso(t0 + 10))
    d = status.aktualizuj(tmp_path, {"kredity_zbyva": 24612, "kredity_cas": iso(t0), "kredity_chyba": None,
                                     "historie": "smazano"}, t0 + 13, iso(t0))
    assert d["kredity_zbyva"] == 21000 and d["kredity_cas"] == iso(t0 + 10) and d["historie"] == "smazano"
    for chyba in ({"kredity_chyba": "sit"}, {"kredity_zbyva": None, "kredity_limit": None, "obnova": None,
                                             "kredity_chyba": "opravneni"}):
        d = status.aktualizuj(tmp_path, chyba, t0 + 13, iso(t0 + 5))
        assert d["kredity_zbyva"] == 21000 and d["kredity_chyba"] is None
    # novější i stejně starý dotaz projde, chyba novějšího dotazu nechá poslední známé kredity
    d = status.aktualizuj(tmp_path, {"kredity_zbyva": 20000, "kredity_cas": iso(t0 + 10)}, t0 + 13, iso(t0 + 10))
    assert d["kredity_zbyva"] == 20000
    d = status.aktualizuj(tmp_path, {"kredity_chyba": "sit"}, t0 + 20, iso(t0 + 20))
    assert d["kredity_zbyva"] == 20000 and d["kredity_cas"] == iso(t0 + 10) and d["kredity_chyba"] == "sit"
    # bez kredity_k (stav, denní část) se nic nevynechává
    assert status.aktualizuj(tmp_path, {"kredity_chyba": "vypnuto"}, t0 + 21)["kredity_chyba"] == "vypnuto"


def test_kredity_cas_v_budoucnosti_neblokuje(tmp_path):
    """Hodiny šly zpět (nebo podvržený soubor): uložený čas z budoucnosti zápis nezastaví."""
    t0, iso = 1_800_000_000, status.ted_iso
    status.aktualizuj(tmp_path, {"kredity_zbyva": 1, "kredity_cas": iso(t0 + 3600)}, t0 + 3600, iso(t0 + 3600))
    assert status.aktualizuj(tmp_path, {"kredity_zbyva": 2, "kredity_cas": iso(t0)}, t0, iso(t0))["kredity_zbyva"] == 2
    (tmp_path / "status.json").write_text(json.dumps({"kredity_zbyva": 3, "kredity_cas": "2026-13-45T99:00:00"}))
    assert status.aktualizuj(tmp_path, {"kredity_zbyva": 4}, t0, iso(t0))["kredity_zbyva"] == 4
    assert status.aktualizuj(tmp_path, {"kredity_zbyva": 5}, t0, "nesmysl")["kredity_zbyva"] == 5


def test_zamek_drzeny_jinde_neblokuje(tmp_path, caplog):
    status.aktualizuj(tmp_path, {"znaku_dnes": 1})
    fd = os.open(tmp_path / status.ZAMEK, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        t = time.monotonic()
        with pytest.raises(BlockingIOError):
            status.aktualizuj(tmp_path, {"znaku_dnes": 2})
        assert time.monotonic() - t < 1
    finally:
        os.close(fd)
    assert status.nacti(tmp_path)["znaku_dnes"] == 1
    assert status.aktualizuj(tmp_path, {"znaku_dnes": 3})["znaku_dnes"] == 3


@pytest.mark.parametrize("obsah", [b"", b"null", b"[1]", b"{", b"{\"znaku_dnes\": \"x\"}",
                                   b" " * (status.MAX_BAJTU + 1)])
def test_nacti_poskozeny(tmp_path, obsah):
    (tmp_path / "status.json").write_bytes(obsah)
    assert status.nacti(tmp_path) == {}


def test_nacti_symlink(tmp_path):
    jinde = tmp_path / "jinde.json"
    jinde.write_text(json.dumps({"den": "2026-01-01", "znaku_dnes": 1, "limit_den": 5}))
    (tmp_path / "status.json").symlink_to(jinde)
    assert status.nacti(tmp_path) == {}


@pytest.mark.parametrize("n,text", [(0, "0"), (999, "999"), (1000, "1,0k"), (24612, "24,6k"), (24699, "24,6k"),
                                    (999_999, "999,9k"), (1_234_567, "1,2M")])
def test_zkratka(n, text):
    assert status.zkratka(n) == text


def test_segment():
    d = {"den": "2026-01-02", "znaku_dnes": 193, "limit_den": 500, "kredity_zbyva": 24612}
    assert status.segment(d, "2026-01-02") == "🔊 193/500 · 24,6k kr"
    assert status.segment(d, "2026-01-03") == "🔊 0/500 · 24,6k kr"
    assert status.segment(d | {"kredity_zbyva": None}, "2026-01-02") == "🔊 193/500"
    assert status.segment({}, "2026-01-02") == ""


# ------------------------------------------------------------- příkaz mcp-voice-status

def _cli(*args, env=None):
    t = time.monotonic()
    r = subprocess.run([sys.executable, "-I", "-m", "mcp_voice.status", *args], capture_output=True, timeout=20,
                       env=env)
    return r, time.monotonic() - t


def test_cli_bez_souboru(tmp_path):
    r, _ = _cli("--stav", str(tmp_path))
    assert r.returncode == 0 and r.stdout == b"" and r.stderr == b""


def test_cli_segment_a_json(tmp_path):
    status.aktualizuj(tmp_path, {"den": status.den(), "znaku_dnes": 193, "limit_den": 500, "kredity_zbyva": 24612,
                                 "kredity_limit": 30000, "kredity_chyba": None})
    r, trvani = _cli("--stav", str(tmp_path))
    assert r.returncode == 0 and r.stdout.decode() == "🔊 193/500 · 24,6k kr\n"
    assert trvani < 5
    r, _ = _cli("--stav", str(tmp_path), "--json")
    d = json.loads(r.stdout)
    assert d["znaku_dnes"] == 193 and d["kredity_zbyva"] == 24612


def test_cli_novy_den(tmp_path):
    status.aktualizuj(tmp_path, {"den": "2000-01-01", "znaku_dnes": 193, "limit_den": 500})
    r, _ = _cli("--stav", str(tmp_path))
    assert r.stdout.decode() == "🔊 0/500\n"


def test_cli_ze_stavu_v_konfiguraci_a_chybna_konfigurace(tmp_path):
    stav = tmp_path / "stav"
    status.aktualizuj(stav, {"den": status.den(), "znaku_dnes": 1, "limit_den": 5})
    kfg = tmp_path / "config.yaml"
    kfg.write_text("cesty: {stav: %s}\n" % json.dumps(str(stav)))
    env = dict(os.environ, MCP_VOICE_CONFIG=str(kfg))
    r, _ = _cli(env=env)
    assert r.stdout.decode() == "🔊 1/5\n"
    kfg.write_text("cesty: {stav: x}\ncesty: {stav: y}\n")
    r, _ = _cli(env=env)
    assert r.returncode == 0 and r.stdout == b"" and r.stderr == b""


def test_cli_bez_site():
    """Příkaz pro status bar nenačte HTTP klienta ani server."""
    kod = ("import sys; from mcp_voice import status; "
           "print(sorted(m for m in ('httpx', 'mcp', 'mcp_voice.server', 'mcp_voice.kredity', 'keyring') "
           "if m in sys.modules))")
    r = subprocess.run([sys.executable, "-I", "-c", kod], capture_output=True, text=True, timeout=20)
    assert r.stdout.strip() == "[]"


# ---------------------------------------------------------------- segment okna Telegramu

def test_segment_telegramu(tmp_path):
    import os as _os
    from mcp_voice import puvod
    from . import tg
    ident = (_os.getpid(), "1")
    assert status.segment_telegramu(tmp_path, ident) == ""
    assert status.segment_telegramu(tmp_path, None) == ""
    o = tg.stav_okna(tmp_path, ident, rezim="prace")
    assert status.segment_telegramu(tmp_path, ident).startswith("TG do ") and "práce" in status.segment_telegramu(tmp_path, ident)
    klic = puvod.klic_procesu(*ident)
    for zmena, ocekavane in (({"tah": "4821"}, "TG tah #4821"), ({"fronta": 2}, "TG fronta 2"),
                             ({"ceka_potvrzeni": True}, "TG čeká potvrzení"),
                             ({"chyba": "hook_rozsahu_nebezi"}, "TG chyba: hook"), ({"stav": "overovani"}, "TG ověřuji"),
                             ({"stav": "zavreno"}, ""), ({"tah": "\x1b[31m"}, None)):
        puvod.zapis(puvod.soubor(tmp_path, "okno", klic), dict(o, **zmena))
        v = status.segment_telegramu(tmp_path, ident)
        assert v == ocekavane if ocekavane is not None else v.startswith("TG do ")
