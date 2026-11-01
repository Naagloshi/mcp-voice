"""Lokální přepis a pin modelu. Test se skutečným modelem běží jen tam, kde je model stažený."""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_voice import model, stt_lokalne
from mcp_voice.chyby import HlasChyba
from mcp_voice.config import Stt

SKUTECNY_MODEL = Path(os.path.expanduser(Stt.model_cesta))


def test_otisk_git_blob_odpovida_gitu(tmp_path):
    p = tmp_path / "a.json"
    p.write_bytes(b'{"a": 1}\n')
    git = subprocess.run(["git", "hash-object", str(p)], capture_output=True, text=True, check=True).stdout.strip()
    assert model.otisk(p, "git-sha1") == git


def test_over_adresar_najde_podvrzeny_soubor(tmp_path, monkeypatch):
    (tmp_path / "model.bin").write_bytes(b"pravy")
    import hashlib
    monkeypatch.setattr(model, "SOUBORY", {"model.bin": ("sha256", hashlib.sha256(b"pravy").hexdigest()),
                                           "config.json": ("git-sha1", "0" * 40)})
    assert model.over_adresar(tmp_path) == ["config.json"]
    (tmp_path / "model.bin").write_bytes(b"podvrh")
    assert "model.bin" in model.over_adresar(tmp_path)


def test_pin_revize_a_hashu():
    assert len(model.REVIZE) == 40 and model.SOUBORY["model.bin"][0] == "sha256"


def test_dostupnost(tmp_path):
    pytest.importorskip("faster_whisper", reason="lokální přepis není nainstalovaný (requirements-lokalni.lock)")
    assert stt_lokalne.dostupnost(Stt(model_cesta=str(tmp_path))) == "chybi_model"
    (tmp_path / "model.bin").write_bytes(b"x")
    assert stt_lokalne.dostupnost(Stt(model_cesta=str(tmp_path))) == "dostupne"


def test_dostupnost_bez_extra(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # import pak selže jako na základní instalaci
    (tmp_path / "model.bin").write_bytes(b"x")
    assert stt_lokalne.dostupnost(Stt(model_cesta=str(tmp_path))) == "nenainstalovano"


async def test_bez_extra_hlasi_kod(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    p = stt_lokalne.LokalniPrepis(Stt(model_cesta=str(tmp_path), uvolnit_po_s=0))
    with pytest.raises(HlasChyba) as e:
        await p.prepis(b"\x00" * 64000, "cs")
    assert e.value.kod == "STT_LOCAL_UNAVAILABLE" and "lokalni" in e.value.zprava


async def test_chybejici_model(tmp_path):
    pytest.importorskip("faster_whisper", reason="lokální přepis není nainstalovaný (requirements-lokalni.lock)")
    p = stt_lokalne.LokalniPrepis(Stt(model_cesta=str(tmp_path), uvolnit_po_s=0))
    with pytest.raises(HlasChyba) as e:
        await p.prepis(b"\x00" * 64000, "cs")
    assert e.value.kod == "STT_LOCAL_UNAVAILABLE"


@pytest.mark.skipif(not (SKUTECNY_MODEL / "model.bin").exists(), reason="model není stažený")
async def test_skutecny_model_prepise_ticho_a_uvolni_se():
    pytest.importorskip("faster_whisper", reason="lokální přepis není nainstalovaný (requirements-lokalni.lock)")
    p = stt_lokalne.LokalniPrepis(Stt(uvolnit_po_s=0.2))
    r = await p.prepis(b"\x00" * (16000 * 4 * 2), "cs")
    assert r["zarizeni"] in ("cuda", "cpu") and isinstance(r["text"], str)
    await asyncio.sleep(0.5)
    assert p._model is None  # uvolněno po nečinnosti


def test_model_cli_chybna_konfigurace_bez_tracebacku(tmp_path, monkeypatch, capsys):
    kfg = tmp_path / "config.yaml"
    kfg.write_text("stt:\n  model_cest: /x\n", encoding="utf-8")
    monkeypatch.setenv("MCP_VOICE_CONFIG", str(kfg))
    with pytest.raises(SystemExit) as e:
        model.main(["over"])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("mcp-voice-model: chybná konfigurace:") and "Traceback" not in err
    kfg.write_bytes(b"stt:\n  jazyk: \xff\n")
    with pytest.raises(SystemExit) as e:
        model.main(["over"])
    assert e.value.code == 2 and "konfiguraci nejde přečíst" in capsys.readouterr().err


def test_model_cli_s_cestou_konfiguraci_necte(tmp_path, monkeypatch, capsys):
    kfg = tmp_path / "config.yaml"
    kfg.write_text("neznamy: 1\n", encoding="utf-8")
    monkeypatch.setenv("MCP_VOICE_CONFIG", str(kfg))
    with pytest.raises(SystemExit) as e:
        model.main(["over", "--cesta", str(tmp_path / "model")])
    assert e.value.code == 1 and "Nesouhlasí nebo chybí" in capsys.readouterr().err


def test_model_stahni_bez_lokalniho_prepisu(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    cil = tmp_path / "model"
    with pytest.raises(SystemExit) as e:
        model.main(["stahni", "--cesta", str(cil)])
    assert "requirements-lokalni.lock" in str(e.value.code) and not cil.exists()
