"""Vzor config.yaml.example: projde validací, uvádí každý klíč s výchozí hodnotou a README je popisuje."""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from mcp_voice import config

KOREN = Path(__file__).resolve().parents[1]
CHAT = "4242" + "42"
USER_ID = 424242
VOICE_ID = "Fiktivni" + "VoiceId0000"
ZASTUPNY_VOICE_ID = "SEM_VOICE_ID_Z_ELEVENLABS"

# Zakomentovaný klíč: „# “ ve sloupci klíče, za ním klíč (nebo položka seznamu) s dvojtečkou.
# Vysvětlující komentáře ve vzoru tak nesmí začínat malým slovem s dvojtečkou.
ZAKOMENTOVANY = re.compile(r"^(\s*)# (\s*(?:- )?[a-z_][a-z0-9_]*:(?: .*)?)$")

# Podsekce, jejichž povolené klíče jsou v config._prijem a z_dat jako literály (zrcadlo kódu).
PODSEKCE = {
    "telegram.odpovedi": {"max_casti", "max_znaku_casti"},
    "telegram.retence": {"smazat_po_h"},
    "telegram.prijem.okno": {"vychozi_min", "max_min", "necinnost_min", "vychozi_rezim"},
    "telegram.prijem.rezimy": {"cteni", "prace", "plny"},
    "telegram.prijem.rezimy.cteni": {"nastroje_navic", "koreny"},
    "telegram.prijem.rezimy.prace": {"pri_dotazu", "bash_koreny"},
    "telegram.prijem.rezimy.plny": {"bash_izolace", "bash_sit", "bash_bez_dotazu", "bash_domov", "bash_skryt",
                                    "bash_koreny"},
    "telegram.prijem.rezimy.plny.bash_domov": {"pridat", "odebrat_vychozi"},
    "telegram.prijem.chranene_cesty": {"pridat", "odebrat_vychozi"},
    "telegram.prijem.chranene_zapis": {"pridat", "odebrat_vychozi"},
    "telegram.prijem.zakazane_prikazy": {"pridat", "odebrat_vychozi"},
    "telegram.prijem.zakazane_nastroje": {"pridat"},
    "telegram.prijem.konfigurace_agenta": {"pridat"},
    "telegram.prijem.vstup": {"max_znaku", "zprav_za_minutu", "zprav_za_okno", "hlasovka_max_s", "hlasovka_max_mb",
                              "dekodovani_izolace"},
    "telegram.prijem.fronta": {"max_zprav", "oznamit_po_s", "necinnost_tahu_s", "max_cekani_min"},
    "telegram.prijem.doruceni": {"potvrzeni_s"},
    "telegram.prijem.forma": {"hlas", "text", "pc"},
}
KLICE_KORENE_BASH = {"cesta", "pristup"}  # položka bash_koreny (ve vzoru prázdný seznam, popis v README)

# Hodnoty, které vzor místo výchozí ukazuje jako příklad.
PRIKLADY = {"elevenlabs.voice_settings", "cil.tiche_hodiny", "strazce.citlive_kontexty"}


def _text_vzoru() -> str:
    return (KOREN / "config.yaml.example").read_text(encoding="utf-8")


def _s_fiktivnimi(data: dict) -> dict:
    """Hodnoty, které si uživatel doplní (chat_id, user_id, voice_id), nahradí fiktivními platnými."""
    data["elevenlabs"]["hlasy"] = {"vychozi": VOICE_ID}
    t = data.setdefault("telegram", {})
    t["chat_id"] = CHAT
    t["prijem"].update(povoleno=True, povolene_user_id=[USER_ID])
    return data


def _odkomentovany() -> dict:
    radky = [ZAKOMENTOVANY.sub(r"\1\2", r) for r in _text_vzoru().splitlines()]
    return _s_fiktivnimi(config.nacti_yaml("\n".join(radky)))


def _mapa(data: dict, cesta: str) -> dict:
    for cast in cesta.split("."):
        data = data[cast]
    return data


def test_priklad_jak_je_projde():
    k = config.z_dat(config.nacti_yaml(_text_vzoru()))
    assert k.telegram.prijem.chyba is None and k.telegram.prijem.povoleno is False
    assert k.elevenlabs.data_use_vypnuto is False and k.telegram.chat_id is None
    assert k.elevenlabs.hlasy == {} and k.elevenlabs.hlas == "vychozi"


def test_zastupny_voice_id_ze_vzoru_neprojde():
    """Odkomentovaný alias bez doplněného voice_id je chyba konfigurace, ne až chyba ElevenLabs za běhu."""
    assert f'# vychozi: "{ZASTUPNY_VOICE_ID}"' in _text_vzoru()
    radky = [ZAKOMENTOVANY.sub(r"\1\2", r) for r in _text_vzoru().splitlines()]
    with pytest.raises(config.ChybaKonfigurace, match="neplatné voice_id"):
        config.z_dat(config.nacti_yaml("\n".join(radky)))


def test_priklad_odkomentovany_s_fiktivnimi_hodnotami_projde():
    k = config.z_dat(_odkomentovany())
    p = k.telegram.prijem
    assert p.chyba is None and p.povoleno is True and p.povolene_user_id == (USER_ID,)
    assert [c.nazev for c in k.strazce.citlive_kontexty] == ["ACME", "Example Corp"]


def test_priklad_uvadi_kazdy_klic():
    d = _odkomentovany()
    assert set(d) == config.KLICE_KORENE
    for sekce, klice in config.KLICE_SEKCI.items():
        assert set(d[sekce]) == klice, sekce
    assert set(d["telegram"]["prijem"]) == config.KLICE_PRIJMU
    for cesta, klice in PODSEKCE.items():
        assert set(_mapa(d, cesta)) == klice, cesta
    kontext = d["strazce"]["citlive_kontexty"][0]
    assert set(kontext) == config.KLICE_KONTEXTU
    assert set(kontext["signaly"]) == config.KLICE_SIGNALU
    assert set().union(*kontext["signaly"]["vstupy"]) == config.KLICE_VSTUPU


def _listy(obj, cesta=""):
    """Rovinné porovnání konfigurací: cesta klíče -> hodnota (datové třídy rozložené do hloubky)."""
    if dataclasses.is_dataclass(obj):
        vysledek = {}
        for f in dataclasses.fields(obj):
            vysledek |= _listy(getattr(obj, f.name), f"{cesta}.{f.name}" if cesta else f.name)
        return vysledek
    return {cesta: obj}


def test_zakomentovane_hodnoty_jsou_vychozi():
    vychozi = _listy(config.z_dat(_s_fiktivnimi(config.nacti_yaml(_text_vzoru()))))
    vse = _listy(config.z_dat(_odkomentovany()))
    rozdily = {k for k in vychozi if vychozi[k] != vse[k]}
    assert rozdily == PRIKLADY


def test_readme_popisuje_kazdy_klic():
    readme = (KOREN / "README.md").read_text(encoding="utf-8")
    sekce = readme.split("\n## Konfigurace\n", 1)[1].split("\n## ", 1)[0]
    klice = set(config.KLICE_KORENE) | config.KLICE_PRIJMU | config.KLICE_KONTEXTU | config.KLICE_SIGNALU
    klice |= config.KLICE_VSTUPU | KLICE_KORENE_BASH
    for k in config.KLICE_SEKCI.values():
        klice |= k
    for k in PODSEKCE.values():
        klice |= k
    # klíč v backticku samostatně nebo jako článek cesty (`telegram.odpovedi`, `vstup.max_znaku`)
    chybi = sorted(k for k in klice
                   if not re.search(r"`(?:[a-z_]+\.)*" + re.escape(k) + r"(?:\.[a-z_]+)*`", sekce))
    assert not chybi, chybi
