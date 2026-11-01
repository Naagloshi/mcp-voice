"""prepis: allowlist souborů a nedůvěryhodné audio (R-SOUB-1..6), cloud jen výslovně (R-STT-1..4)."""
import json
import os
import subprocess

import httpx
import pytest

from mcp_voice import config
from mcp_voice.chyby import HlasChyba

from .conftest import FALESNY_KLIC, FalesnyLokalni
from .test_server import zavolej


def ffmpeg_ton(cesta, fmt_args, sekund=1):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={sekund}", *fmt_args, str(cesta)], check=True)
    return cesta


@pytest.fixture
def lokalni_dostupny(monkeypatch):
    """Server bere lokální přepis za dostupný i bez extra [lokalni]: test ověřuje logiku serveru s falešným enginem."""
    from mcp_voice import stt_lokalne
    monkeypatch.setattr(stt_lokalne, "dostupnost", lambda n: "dostupne")


@pytest.fixture
def uploads(tmp_path):
    u = tmp_path / "uploads"
    u.mkdir(exist_ok=True)
    return u


@pytest.mark.parametrize("jmeno,fmt", [
    ("a.ogg", ["-c:a", "libopus"]),
    ("a.m4a", ["-c:a", "aac"]),
    ("a.mp3", ["-c:a", "libmp3lame"]),
    ("a.wav", []),
])
async def test_prepis_lokalne_formaty(udelej_sluzby, sit, uploads, lokalni_dostupny, jmeno, fmt):
    lok = FalesnyLokalni()
    s = udelej_sluzby(lokalni=lok)
    ffmpeg_ton(uploads / jmeno, fmt)
    r = await zavolej(s, "prepis", {"cesta": str(uploads / jmeno)})
    assert r["ok"] and r["engine"] == "lokalne" and r["zdroj"] == "prepis_hlasovky"
    assert lok.volano == 1 and sit.pozadavky == []  # nic neodešlo


def _pripravy(tmp_path, uploads):
    """Vrátí (cesta, očekávaný kód) pro odmítnuté vstupy."""
    sour = tmp_path / "uploads-evil"
    sour.mkdir()
    ffmpeg_ton(sour / "x.ogg", ["-c:a", "libopus"])
    venku = ffmpeg_ton(tmp_path / "venku.ogg", ["-c:a", "libopus"])
    os.symlink(venku, uploads / "odkaz.ogg")
    os.symlink(tmp_path, uploads / "odkaz-adr")
    (uploads / "adresar").mkdir()
    os.mkfifo(uploads / "fifo.ogg")
    (uploads / "dokument.ogg").write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")
    (uploads / "playlist.ogg").write_text("#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\n"
                                          f"file://{venku}\n#EXT-X-ENDLIST\n")
    (uploads / "concat.ogg").write_text(f"ffconcat version 1.0\nfile '{venku}'\n")
    (uploads / "prazdny.ogg").write_bytes(b"")
    return [
        (str(sour / "x.ogg"), "FILE_NOT_ALLOWED"),  # sourozenec se stejným prefixem
        (str(uploads / ".." / "venku.ogg"), "FILE_NOT_ALLOWED"),
        (str(uploads / "odkaz.ogg"), "FILE_NOT_ALLOWED"),
        (str(uploads / "odkaz-adr" / "venku.ogg"), "FILE_NOT_ALLOWED"),
        (str(uploads / "adresar"), "FILE_NOT_ALLOWED"),
        (str(uploads / "fifo.ogg"), "FILE_NOT_ALLOWED"),
        ("/dev/zero", "FILE_NOT_ALLOWED"),
        ("relativni.ogg", "FILE_NOT_ALLOWED"),
        (str(uploads / "neni.ogg"), "FILE_NOT_FOUND"),
        (str(uploads / "dokument.ogg"), "AUDIO_UNREADABLE"),
        (str(uploads / "playlist.ogg"), "AUDIO_UNREADABLE"),
        (str(uploads / "concat.ogg"), "AUDIO_UNREADABLE"),
        (str(uploads / "prazdny.ogg"), "AUDIO_UNREADABLE"),
    ]


async def test_prepis_odmitne_nebezpecne_vstupy(udelej_sluzby, sit, tmp_path, uploads):
    for cesta, kod in _pripravy(tmp_path, uploads):
        lok = FalesnyLokalni()
        s = udelej_sluzby(lokalni=lok)
        for engine in ("lokalne", "cloud"):
            r = await zavolej(s, "prepis", {"cesta": cesta, "engine": engine}, tid=f"toolu_{engine}_x01")
            assert r.get("chyba", {}).get("kod") == kod, (cesta, engine, r)
        assert lok.volano == 0 and sit.pozadavky == []


async def test_prepis_limity(udelej_sluzby, sit, uploads):
    ffmpeg_ton(uploads / "dlouhy.ogg", ["-c:a", "libopus"], sekund=5)
    s = udelej_sluzby(stt={"max_s": 2})
    r = await zavolej(s, "prepis", {"cesta": str(uploads / "dlouhy.ogg")})
    assert r["chyba"]["kod"] == "AUDIO_TOO_LONG"
    s = udelej_sluzby(stt={"max_mb": 0.01})
    r = await zavolej(s, "prepis", {"cesta": str(uploads / "dlouhy.ogg")}, tid="toolu_limit0002")
    assert r["chyba"]["kod"] == "FILE_TOO_LARGE" and sit.pozadavky == []


async def test_bez_automaticke_zalohy_do_cloudu(udelej_sluzby, sit, uploads, lokalni_dostupny):
    lok = FalesnyLokalni(chyba=HlasChyba("STT_LOCAL_FAILED", "x"))
    s = udelej_sluzby(lokalni=lok)
    ffmpeg_ton(uploads / "a.ogg", ["-c:a", "libopus"])
    r = await zavolej(s, "prepis", {"cesta": str(uploads / "a.ogg")})
    assert r["chyba"]["kod"] == "STT_LOCAL_FAILED" and sit.pozadavky == []


async def test_prepis_cloud_jen_vyslovne(udelej_sluzby, sit, uploads):
    sit.el = lambda req: httpx.Response(200, json={"text": " Ahoj ", "language_code": "ces",
                                                   "language_probability": 0.9})
    ffmpeg_ton(uploads / "a.ogg", ["-c:a", "libopus"])
    obsah = (uploads / "a.ogg").read_bytes()
    r = await zavolej(udelej_sluzby(), "prepis", {"cesta": str(uploads / "a.ogg"), "engine": "cloud"})
    assert r["ok"] and r["engine"] == "cloud" and r["text"] == "Ahoj" and r["model"] == "scribe_v2"
    (req,) = sit.pozadavky
    assert req.url.path == "/v1/speech-to-text" and req.headers["xi-api-key"] == FALESNY_KLIC
    assert obsah in req.content  # odešly přesně ověřené bajty


async def test_cloud_zakazany_konfiguraci(udelej_sluzby, sit, uploads):
    ffmpeg_ton(uploads / "a.ogg", ["-c:a", "libopus"])
    r = await zavolej(udelej_sluzby(stt={"cloud_povolen": False}), "prepis",
                      {"cesta": str(uploads / "a.ogg"), "engine": "cloud"})
    assert r["chyba"]["kod"] == "CLOUD_DISABLED" and sit.pozadavky == []


async def test_cloud_bez_potvrzeni_data_use(udelej_sluzby, sit, uploads):
    ffmpeg_ton(uploads / "a.ogg", ["-c:a", "libopus"])
    r = await zavolej(udelej_sluzby(elevenlabs={"data_use_vypnuto": False}), "prepis",
                      {"cesta": str(uploads / "a.ogg"), "engine": "cloud"})
    assert r["chyba"]["kod"] == "DATA_USE_NOT_CONFIRMED" and sit.pozadavky == []


def test_konfigurace_nesmi_nastavit_cloud_jako_vychozi():
    with pytest.raises(config.ChybaKonfigurace):
        config.z_dat({"stt": {"engine": "cloud"}})


async def test_lokalni_extra_chybi(udelej_sluzby, sit, uploads, monkeypatch):
    from mcp_voice import stt_lokalne
    monkeypatch.setattr(stt_lokalne, "dostupnost", lambda n: "nenainstalovano")
    ffmpeg_ton(uploads / "a.ogg", ["-c:a", "libopus"])
    r = await zavolej(udelej_sluzby(), "prepis", {"cesta": str(uploads / "a.ogg")})
    assert r["chyba"]["kod"] == "STT_LOCAL_UNAVAILABLE" and sit.pozadavky == []
    assert "cloud" in json.dumps(r, ensure_ascii=False).lower()
