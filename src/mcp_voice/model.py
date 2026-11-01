"""mcp-voice-model: jednorázové stažení modelu pro lokální přepis s pevnou revizí a ověřením hashů.

Za běhu serveru se model načítá jen z lokální cesty, bez sítě.
Původní repozitář mobiuslabsgmbh/faster-whisper-large-v3-turbo se přesunul pod nového vlastníka,
proto pin na commit (nezměnitelný) a hash každého souboru.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = "dropbox-dash/faster-whisper-large-v3-turbo"
REVIZE = "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
# soubor -> ("sha256", hex) pro LFS, ("git-sha1", hex) pro běžné soubory (git blob)
SOUBORY = {
    "model.bin": ("sha256", "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"),
    "config.json": ("git-sha1", "0351d1d6870005e865747b781b5d7c23ea0459cd"),
    "preprocessor_config.json": ("git-sha1", "931c77a740890c46365c7ae0c9d350ba3cca908f"),
    "tokenizer.json": ("git-sha1", "17456db595adc78a973f97d69d8cb50bc87c0b1c"),
    "vocabulary.json": ("git-sha1", "0adcd01e7c237205d593b707e66dd5d7bc785d2d"),
}


def otisk(cesta: Path, druh: str) -> str:
    if druh == "sha256":
        h = hashlib.sha256()
    else:
        h = hashlib.sha1()  # noqa: S324 – git blob id, ne bezpečnostní hash (ověřuje se i sha256 modelu)
        h.update(b"blob %d\x00" % cesta.stat().st_size)
    with cesta.open("rb") as fh:
        for kus in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(kus)
    return h.hexdigest()


def over_adresar(adresar: Path) -> list[str]:
    """Seznam souborů, které chybí nebo nesouhlasí."""
    spatne = []
    for jmeno, (druh, ocekavany) in SOUBORY.items():
        p = adresar / jmeno
        if not p.is_file() or p.is_symlink() or otisk(p, druh) != ocekavany:
            spatne.append(jmeno)
    return spatne


def stahni(cil: Path) -> None:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        raise SystemExit("mcp-voice-model stahni: lokální přepis není nainstalovaný, model by nebylo čím použít. "
                         "Nainstaluj variantu s lokálním přepisem (requirements-lokalni.lock, README Instalace).") from None

    cil = cil.expanduser()
    if cil.exists():
        spatne = over_adresar(cil)
        if not spatne:
            print(f"Model už je stažený a ověřený: {cil}")
            return
        raise SystemExit(f"Cílový adresář existuje a nesouhlasí ({', '.join(spatne)}). Smaž ho ručně: {cil}")
    cil.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    docasny = Path(tempfile.mkdtemp(prefix=".stahovani-", dir=cil.parent))
    try:
        for jmeno in SOUBORY:
            print(f"stahuji {jmeno} …", flush=True)
            cesta = hf_hub_download(REPO, jmeno, revision=REVIZE, local_dir=docasny)
            if Path(cesta).resolve().parent != docasny.resolve():
                raise SystemExit("neočekávané umístění staženého souboru")
        shutil.rmtree(docasny / ".cache", ignore_errors=True)
        spatne = over_adresar(docasny)
        if spatne:
            raise SystemExit(f"Hash nesouhlasí: {', '.join(spatne)}. Model nepoužívej.")
        for jmeno in SOUBORY:
            os.chmod(docasny / jmeno, 0o444)  # model jen ke čtení
        os.chmod(docasny, 0o755)
        os.rename(docasny, cil)
        print(f"Hotovo, ověřeno: {cil}")
    finally:
        if docasny.exists():
            shutil.rmtree(docasny, ignore_errors=True)


def main(argv: list[str] | None = None) -> None:
    from .config import ChybaKonfigurace, nacti

    ap = argparse.ArgumentParser(prog="mcp-voice-model", description="Model pro lokální přepis (pevná revize).")
    ap.add_argument("prikaz", choices=["stahni", "over"])
    ap.add_argument("--cesta", help="cílový adresář (výchozí stt.model_cesta z konfigurace)")
    a = ap.parse_args(argv)
    if a.cesta:
        cesta = Path(a.cesta).expanduser()
    else:
        try:
            cesta = Path(nacti().stt.model_cesta).expanduser()
        except ChybaKonfigurace as e:
            sys.stderr.write("mcp-voice-model: chybná konfigurace: %s\n" % e)
            raise SystemExit(2) from None
        except (OSError, ValueError) as e:  # nečitelný soubor, neplatné UTF-8
            sys.stderr.write("mcp-voice-model: konfiguraci nejde přečíst (%s)\n" % type(e).__name__)
            raise SystemExit(2) from None
    if a.prikaz == "stahni":
        stahni(cesta)
    else:
        spatne = over_adresar(cesta)
        if spatne:
            print(f"Nesouhlasí nebo chybí: {', '.join(spatne)}", file=sys.stderr)
            raise SystemExit(1)
        print(f"Model ověřen: {cesta}")


if __name__ == "__main__":
    main()
