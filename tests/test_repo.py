"""Veřejný repozitář: žádná osobní cesta ani identifikátor (R-REPO-1, R-REPO-2).

Kontrola soukromých slov (jména, domény) je volitelná: seznam slov v repozitáři není, test ho čte ze souboru,
jehož cestu dá proměnná MCP_VOICE_SOUKROMA_SLOVA (jedno slovo na řádek, `#` = komentář). Bez ní se přeskočí.
"""
import os
import re
import subprocess
from pathlib import Path

import pytest

KOREN = Path(__file__).resolve().parents[1]
PROMENNA_SLOV = "MCP_VOICE_SOUKROMA_SLOVA"

VZORY = [
    re.compile(r"/" + "home/"),
    re.compile(r"chat_id\s*[:=]\s*\"?-?\d{5,}"),
    re.compile(r"\bbot\d{6,}:[A-Za-z0-9_-]{30,}"),
    re.compile(r"sk_[a-f0-9]{40,}"),
]


def soubory_repa():
    r = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=KOREN,
                       capture_output=True, text=True, check=True)
    return [KOREN / p for p in r.stdout.splitlines() if p and not p.endswith(".lock")]


def texty_repa():
    for p in soubory_repa():
        try:
            yield p.relative_to(KOREN), p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue


def nacti_slova(cesta: str) -> list[str]:
    radky = Path(cesta).expanduser().read_text(encoding="utf-8").splitlines()
    return [r.strip().lower() for r in radky if r.strip() and not r.lstrip().startswith("#")]


def najdi_slova(text: str, slova: list[str]) -> list[str]:
    """Slova (malými písmeny) nalezená v textu jako celé slovo."""
    maly = text.lower()
    return [s for s in slova if re.search(r"(?<!\w)" + re.escape(s) + r"(?!\w)", maly)]


def test_zadne_osobni_cesty_ani_tokeny():
    nalezy = [f"{jmeno}: {rx.pattern}" for jmeno, text in texty_repa() for rx in VZORY if rx.search(text)]
    assert not nalezy, nalezy


def test_zadna_soukroma_slova():
    cesta = os.environ.get(PROMENNA_SLOV)
    if not cesta:
        pytest.skip(f"volitelný test soukromí: nastav {PROMENNA_SLOV} na soubor se slovy (jedno na řádek)")
    try:
        slova = nacti_slova(cesta)
    except OSError as e:
        pytest.fail(f"{PROMENNA_SLOV}: soubor nejde přečíst ({type(e).__name__})")
    assert slova, f"{PROMENNA_SLOV}: soubor neobsahuje žádné slovo"
    nalezy = [f"{jmeno}: {s}" for jmeno, text in texty_repa() for s in najdi_slova(text, slova)]
    assert not nalezy, nalezy


def test_najdi_slova_cela_slova_bez_ohledu_na_velikost():
    assert najdi_slova("Projekt ACME, acme-db a /x/acme/", ["acme"]) == ["acme"]
    assert najdi_slova("acmecorp, xacme", ["acme"]) == []
    assert najdi_slova("Šťastný Řehoř", ["řehoř"]) == ["řehoř"]


def test_nacti_slova(tmp_path):
    p = tmp_path / "slova.txt"
    p.write_text("# komentář\n  Acme \n\nexample-corp\n", encoding="utf-8")
    assert nacti_slova(str(p)) == ["acme", "example-corp"]


def test_konfigurace_neni_v_repozitari():
    jmena = {p.name for p in soubory_repa()}
    assert "config.yaml" not in jmena and "config.yaml.example" in jmena
