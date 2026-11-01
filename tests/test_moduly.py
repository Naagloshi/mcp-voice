"""Plocha (jen čtení DBus), přehrávač, rozpočet, kódování, konfigurace."""
import json
import stat
import subprocess
import threading
import time
from pathlib import Path

import pytest

from mcp_voice import config, kodovani, plocha
from mcp_voice.chyby import HlasChyba, maskuj, registruj_tajemstvi
from mcp_voice.prehravac import Prehravac
from mcp_voice.rozpocet import Rozpocet

KOREN = Path(__file__).resolve().parents[1]

ZAKAZANE_METODY = ("Lock", "SetActive", "SimulateUserActivity", "ResetIdletime", "Unlock", "SetIdletime")


class Zaznamnik:
    def __init__(self, odpovedi):
        self.odpovedi = odpovedi
        self.argv = []

    def __call__(self, argv):
        self.argv.append(argv)
        for klic, hodnota in self.odpovedi.items():
            if klic in " ".join(argv):
                return hodnota
        return None


def _vsechna_volani(desktop, odpovedi):
    z = Zaznamnik(odpovedi)
    p = plocha.Plocha(desktop=desktop, spoustec=z)
    vysledek = (p.zamceno(), p.necinnost_ms())
    return vysledek, z.argv


@pytest.mark.parametrize("desktop", ["auto", "cinnamon", "gnome", "x11"])
def test_dbus_jen_povolene_metody(desktop):
    _, volani = _vsechna_volani(desktop, {})
    for argv in volani:
        if argv[0] == "gdbus":
            metoda = argv[argv.index("--method") + 1]
            assert metoda in plocha.POVOLENE_METODY
            assert not any(metoda.endswith("." + m) for m in ZAKAZANE_METODY)
        else:
            assert argv[:2] == ["loginctl", "show-session"]


def test_tabulka_metod_nema_zapisove_metody():
    for _, _, metoda in plocha.DBUS_METODY.values():
        assert metoda.rsplit(".", 1)[1] in ("GetActive", "GetIdletime")


def test_zamek_z_kterehokoli_zdroje():
    assert _vsechna_volani("cinnamon", {"ScreenSaver.GetActive": "(false,)\n", "LockedHint": "yes\n"})[0][0] is True
    assert _vsechna_volani("cinnamon", {"ScreenSaver.GetActive": "(true,)\n", "LockedHint": "no\n"})[0][0] is True
    assert _vsechna_volani("cinnamon", {"ScreenSaver.GetActive": "(false,)\n", "LockedHint": "no\n"})[0][0] is False
    assert _vsechna_volani("cinnamon", {})[0][0] is None


def test_necinnost():
    assert _vsechna_volani("cinnamon", {"GetIdletime": "(uint64 4200,)\n"})[0][1] == 4200
    assert _vsechna_volani("cinnamon", {"GetIdletime": "nesmysl"})[0][1] is None


def test_okno_jen_trida(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    z = Zaznamnik({"_NET_ACTIVE_WINDOW": "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x1a00003\n",
                   "WM_CLASS": 'WM_CLASS(STRING) = "claude", "com.anthropic.claude"\n'})
    assert plocha.Plocha(spoustec=z).okno() == "com.anthropic.claude"
    assert not any("WM_NAME" in " ".join(a) or "_NET_WM_NAME" in " ".join(a) for a in z.argv)


# ------------------------------------------------------------- přehrávač

class FalesnyProces:
    def __init__(self, zapis_zpomalit=0.0):
        self.stdin = self
        self.data = bytearray()
        self.zabit = False
        self.zpomalit = zapis_zpomalit
        self.zavreno = False

    def write(self, b):
        if self.zabit:
            raise BrokenPipeError
        time.sleep(self.zpomalit)
        self.data += b

    def close(self):
        self.zavreno = True

    def poll(self):
        return 0 if (self.zavreno or self.zabit) else None

    def kill(self):
        self.zabit = True

    def wait(self, timeout=None):
        return 0


def test_fronta_prehrava_postupne_a_zastav():
    procesy = []

    def popen(argv, **kw):
        procesy.append(FalesnyProces(0.05))
        return procesy[-1]

    p = Prehravac("paplay", lambda: False, popen=popen)
    p.dostupny = lambda: True
    for _ in range(3):
        p.pridej(b"\x00" * 48000 * 4)  # 4 s zvuku = 16 kusů po 50 ms
    time.sleep(0.2)
    assert len(procesy) == 1  # druhá odpověď nezačne, dokud hraje první
    assert p.zastav() == 3
    time.sleep(0.3)
    assert procesy[0].zabit and len(procesy) == 1


def test_zamceni_preruší_prehravani():
    zamceno = threading.Event()
    procesy = []

    def popen(argv, **kw):
        procesy.append(FalesnyProces(0.05))
        return procesy[-1]

    p = Prehravac("paplay", lambda: zamceno.is_set(), popen=popen)
    p.dostupny = lambda: True
    p.pridej(b"\x00" * 48000 * 20)
    p.pridej(b"\x00" * 48000)
    time.sleep(0.1)
    zamceno.set()
    time.sleep(1.5)
    assert procesy[0].zabit and len(procesy) == 1  # zbytek fronty zahozen


def test_chybi_prehravac():
    p = Prehravac("paplay", lambda: False)
    p.dostupny = lambda: False
    with pytest.raises(HlasChyba) as e:
        p.pridej(b"\x00\x00")
    assert e.value.kod == "PLAYER_UNAVAILABLE"


# ------------------------------------------------------------- rozpočet

def test_rozpocet_den_a_prava(tmp_path):
    r = Rozpocet(tmp_path / "s", 100, 100, 0)
    ted = time.mktime((2026, 10, 30, 12, 0, 0, 0, 0, -1))
    assert r.rezervuj(60, "a", ted) == 40
    with pytest.raises(HlasChyba) as e:
        r.rezervuj(41, "b", ted)
    assert e.value.kod == "DAILY_LIMIT"
    assert r.zbyva(ted) == 40
    assert r.rezervuj(100, "c", ted + 86400) == 0  # další den
    assert stat.S_IMODE(r.soubor.stat().st_mode) == 0o600
    assert "a" not in json.loads(r.soubor.read_text())  # žádné otisky textu na disku


def test_rozpocet_nula_nepusti_nic(tmp_path):
    with pytest.raises(HlasChyba):
        Rozpocet(tmp_path, 0, 10, 0).rezervuj(1, "x")


# ------------------------------------------------------------- kódování

async def test_ogg_opus_pro_telegram():
    pcm = b"\x00\x10" * 24000
    ogg = await kodovani.na_ogg_opus(pcm)
    assert ogg.startswith(b"OggS") and b"OpusHead" in ogg
    mp3 = await kodovani.na_mp3(pcm)
    assert mp3[:3] == b"ID3" or mp3[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
    assert kodovani.delka_s(pcm) == 1.0


# ------------------------------------------------------------- konfigurace a maskování

def test_priklad_konfigurace_je_platny():
    import yaml
    kfg = config.z_dat(yaml.safe_load((KOREN / "config.yaml.example").read_text()))
    assert kfg.elevenlabs.data_use_vypnuto is False  # kontrolní bod je ve výchozím stavu zavřený
    assert kfg.limity.znaku_za_den == 500 and kfg.strazce.vyzadovat is True


@pytest.mark.parametrize("data", [
    {"elevenlabs": {"hlasy": {"a": "kratke"}}},
    {"elevenlabs": {"model": "eleven_v4", "modely_povolene": ["eleven_flash_v2_5"]}},
    {"telegram": {"chat_id": "abc"}},
    {"cil": {"tiche_hodiny": [22]}},
    {"cil": {"pc_pri_zamceni": "vzdy"}},
    {"limity": {"max_znaku": 0}},
    {"stt": {"engine": "cloud"}},
    {"elevenlabs": "ne"},
])
def test_neplatna_konfigurace(data):
    with pytest.raises(config.ChybaKonfigurace):
        config.z_dat(data)


def test_neznamy_klic_je_ve_zprave():
    with pytest.raises(config.ChybaKonfigurace) as e:
        config.z_dat({"strazce": {"citlive_kontexy": []}})
    assert "strazce: neznámý klíč 'citlive_kontexy' (povolené:" in str(e.value)
    with pytest.raises(config.ChybaKonfigurace) as e:
        config.z_dat({"a‮b": 1, "x" * 60: 2, "c": 3, "d": 4})
    zprava = str(e.value)
    assert "‮" not in zprava and "'a\\u202eb'" in zprava and "x" * 41 not in zprava and zprava.count("'") == 6
    assert "…" in zprava


def test_tajemstvi_chybejici_backend_vs_chybejici_polozka():
    from keyring.backends import fail

    from mcp_voice import tajemstvi

    with pytest.raises(HlasChyba) as e:
        tajemstvi.nacti(tajemstvi.ELEVENLABS, fail.Keyring().get_password)
    assert e.value.kod == "KEYRING_UNAVAILABLE" and "NoKeyringError" in e.value.zprava

    def rozbity(sluzba, klic):
        raise RuntimeError("detail-backendu-xyz")

    with pytest.raises(HlasChyba) as e:
        tajemstvi.nacti(tajemstvi.ELEVENLABS, rozbity)
    assert e.value.kod == "KEYRING_UNAVAILABLE" and "detail-backendu-xyz" not in e.value.zprava

    for hodnota in (None, "", "   ", 42):
        with pytest.raises(HlasChyba) as e:
            tajemstvi.nacti(tajemstvi.TELEGRAM, lambda s, k, h=hodnota: h)
        assert e.value.kod == "SECRET_MISSING" and tajemstvi.TELEGRAM in e.value.zprava
    assert tajemstvi.nacti(tajemstvi.TELEGRAM, lambda s, k: "  hodnota-tajemstvi  ") == "hodnota-tajemstvi"


def test_maskovani():
    registruj_tajemstvi("supertajnyklic123")
    s = maskuj("url https://api.telegram.org/" + "bot" + "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ_-abc/sendVoice a supertajnyklic123")
    assert "ABCDEFGHIJ" not in s and "supertajny" not in s and "bot[TOKEN]" in s


def test_server_nastartuje_a_odpovi_na_initialize(tmp_path):
    """Skutečný proces přes stdio: initialize + tools/list, bez sítě a bez tajemství."""
    import os
    import sys
    env = dict(os.environ, MCP_VOICE_CONFIG=str(tmp_path / "neni.yaml"), HOME=str(tmp_path))
    zpravy = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    vstup = "".join(json.dumps(z) + "\n" for z in zpravy)
    proc = subprocess.Popen([sys.executable, "-m", "mcp_voice.server"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
    proc.stdin.write(vstup)
    proc.stdin.flush()
    radky = [proc.stdout.readline(), proc.stdout.readline()]
    proc.stdin.close()
    proc.wait(timeout=10)
    nastroje = {t["name"] for t in json.loads(radky[1])["result"]["tools"]}
    assert nastroje == {"rekni", "prepis", "stav", "zastav", "odpovez"}
    assert stat.S_IMODE((tmp_path / ".local" / "state" / "mcp-voice").stat().st_mode) == 0o700
