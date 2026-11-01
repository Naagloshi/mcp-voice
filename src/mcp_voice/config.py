"""Konfigurace: YAML z MCP_VOICE_CONFIG nebo ~/.config/mcp-voice/config.yaml.

Čte se jen při startu serveru (změna = restart). Chybějící soubor = výchozí hodnoty.
Tajemství sem nepatří (keyring, viz tajemstvi.py).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import politika

VYCHOZI_CESTA = "~/.config/mcp-voice/config.yaml"
VOICE_ID = re.compile(r"^[A-Za-z0-9]{16,40}$")
ALIAS = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MODEL_ID = re.compile(r"^[a-z0-9_]{3,64}$")
CHAT_ID = re.compile(r"^-?\d{1,20}$")
WM_CLASS = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class ChybaKonfigurace(ValueError):
    pass


@dataclass(frozen=True)
class ElevenLabs:
    model: str = "eleven_flash_v2_5"
    modely_povolene: tuple[str, ...] = ("eleven_flash_v2_5", "eleven_multilingual_v2", "eleven_v4")
    modely_s_jazykem: tuple[str, ...] = ("eleven_flash_v2_5",)
    hlas: str = "vychozi"
    hlasy: dict[str, str] = field(default_factory=dict)
    povolit_voice_id: bool = False
    jazyk: str = "cs"
    timeout_s: float = 30.0
    voice_settings: dict[str, float] = field(default_factory=dict)
    # Kontrolní bod: dokud uživatel nevypne v účtu „Data use“ (trénink), nic neodejde.
    data_use_vypnuto: bool = False
    # Po úspěšném rekni jeden dotaz na zůstatek kreditů (GET /v1/user/subscription) do status.json.
    kredity: bool = True
    # Po úspěšném TTS smazat vytvořenou položku historie účtu (DELETE /v1/history/{id}).
    mazat_historii: bool = True


FORMA_HLAS = ("řekni", "řekni mi", "hlasem", "nahlas", "přečti mi")
FORMA_TEXT = ("pošli textem", "odpověz textem", "napiš to")
FORMA_PC = ("odpověz na pc", "řekni to na pc")
ZAKAZANE_PRIKAZY_VYCHOZI = ("/[!/]*",)  # token s dalším / (cesta) není příkaz


@dataclass(frozen=True)
class Prijem:
    """Příjem zpráv z Telegramu do běžící session (telegram.prijem). Chyba validace = příjem vypnutý (chyba)."""
    povoleno: bool = False
    povolene_user_id: tuple[int, ...] = ()
    registrace: str = "hlas"
    povel: str = "/telegram"
    long_poll_s: int = 50
    vychozi_min: int = 60
    max_min: int = 240
    necinnost_min: int = 60
    vychozi_rezim: str = "prace"
    nastroje_navic: tuple[str, ...] = ()
    koreny: tuple[str, ...] = ()
    pri_dotazu: str = "cekat"
    bash_izolace: str = "bwrap"
    bash_sit: bool = False
    bash_bez_dotazu: bool = False
    bash_domov: tuple[str, ...] = politika.BASH_DOMOV_VYCHOZI
    bash_skryt: tuple[str, ...] = ()
    bash_koreny: tuple[tuple[str, str, str], ...] = ()
    konfigurace_agenta: tuple[str, ...] = ()
    chranene: tuple[str, ...] = politika.CHRANENE_VYCHOZI
    chranene_zapis: tuple[str, ...] = politika.CHRANENE_ZAPIS_VYCHOZI
    odebrane_vychozi: tuple[str, ...] = ()
    zakazane_nastroje: tuple[str, ...] = ()
    zakazane_prikazy: tuple[str, ...] = ZAKAZANE_PRIKAZY_VYCHOZI
    omezit_naplanovane: bool = True
    pojistka_bash: bool = True
    povolit_neoverenou_verzi: bool = False
    max_znaku: int = 4000
    zprav_za_minutu: int = 6
    zprav_za_okno: int = 100
    hlasovka_max_s: int = 300
    hlasovka_max_mb: int = 5
    dekodovani_izolace: str = "auto"
    fronta_max_zprav: int = 10
    oznamit_po_s: int = 120
    necinnost_tahu_s: int = 90
    max_cekani_min: int = 15
    potvrzeni_s: int = 10
    forma_hlas: tuple[str, ...] = FORMA_HLAS
    forma_text: tuple[str, ...] = FORMA_TEXT
    forma_pc: tuple[str, ...] = FORMA_PC
    oznamit_nazev_kontextu: bool = False
    jazyk_sablon: str = "cs"
    chyba: str | None = None


@dataclass(frozen=True)
class Telegram:
    chat_id: str | None = None
    timeout_s: float = 20.0
    protect_content: bool = False
    prijem: Prijem = field(default_factory=Prijem)
    max_casti: int = 3
    max_znaku_casti: int = 4000
    smazat_po_h: int = 24


@dataclass(frozen=True)
class Cil:
    prah_necinnosti_s: float = 60.0
    # Zdroj terminál: odemčené PC, ale tak dlouho bez vstupu, že u něj nejspíš nikdo není.
    prah_dlouhe_necinnosti_s: float = 600.0
    tolerance_snimku_s: float = 5.0
    okna_klienta: tuple[str, ...] = ("com.anthropic.claude",)
    pc_pri_zamceni: str = "odmitnout"  # odmitnout | hrat
    desktop: str = "auto"  # auto | cinnamon | gnome | x11
    tiche_hodiny: tuple[int, int] | None = None  # (od, do) v hodinách místního času


@dataclass(frozen=True)
class Prehravani:
    prikaz: str = "paplay"  # paplay | pw-cat


@dataclass(frozen=True)
class Limity:
    max_znaku: int = 1500
    znaku_za_den: int = 500
    volani_za_minutu: int = 6
    duplicita_s: float = 60.0


@dataclass(frozen=True)
class Stt:
    cloud_povolen: bool = True  # konfigurace smí cloud jen zakázat, výchozí engine je vždy lokální
    model_cesta: str = "~/.local/share/mcp-voice/modely/faster-whisper-large-v3-turbo"
    zarizeni: str = "cuda"  # cuda | cpu
    compute_type: str = "int8_float16"
    jazyk: str | None = "cs"
    hotwords: str = ""
    initial_prompt: str = ""
    povolene_adresare: tuple[str, ...] = ("~/.claude/uploads",)
    max_mb: float = 25.0
    max_s: float = 600.0
    uvolnit_po_s: float = 600.0
    timeout_s: float = 120.0


@dataclass(frozen=True)
class Cesty:
    stav: str = "~/.local/state/mcp-voice"
    vystup_adresar: str = "~/.local/share/mcp-voice/vystup"


@dataclass(frozen=True)
class PravidloVstupu:
    """Signál z pole vstupu nástroje: glob na jméno nástroje, přesná hodnota nebo předpona."""
    nastroj: str
    pole: tuple[str, ...]
    hodnoty: tuple[str, ...] = ()
    predpony: tuple[str, ...] = ()


@dataclass(frozen=True)
class CitlivyKontext:
    """Citlivý kontext: objeví-li se v session jeho signál, hlas se nejdřív zeptá uživatele."""
    nazev: str
    upozorneni: str
    platnost: str = "den"  # den | session | volani
    mcp: tuple[str, ...] = ()
    agenti: tuple[str, ...] = ()
    skilly: tuple[str, ...] = ()
    cesty: tuple[str, ...] = ()
    texty: tuple[str, ...] = ()
    vstupy: tuple[PravidloVstupu, ...] = ()
    telegram: str = "souhlas"  # souhlas | nikdy
    telegram_platnost: str = "kontext"  # kontext | okno
    telegram_dotaz_v_tahu: str = "rc"  # rc | odmitnout


@dataclass(frozen=True)
class Strazce:
    vyzadovat: bool = True
    max_stari_s: float = 600.0
    prepis_cloud: str = "dotaz"  # dotaz | povolit
    citlive_kontexty: tuple[CitlivyKontext, ...] = ()


@dataclass(frozen=True)
class Konfigurace:
    elevenlabs: ElevenLabs = field(default_factory=ElevenLabs)
    telegram: Telegram = field(default_factory=Telegram)
    cil: Cil = field(default_factory=Cil)
    prehravani: Prehravani = field(default_factory=Prehravani)
    limity: Limity = field(default_factory=Limity)
    stt: Stt = field(default_factory=Stt)
    cesty: Cesty = field(default_factory=Cesty)
    strazce: Strazce = field(default_factory=Strazce)
    instrukce_navic: str = ""

    @property
    def stav_adresar(self) -> Path:
        return Path(os.path.expanduser(self.cesty.stav))


# Povolené klíče kořene a sekcí. Neznámý klíč je chyba: překlep by tiše vrátil výchozí hodnotu
# (u strazce.citlive_kontexty = žádná detekce).
KLICE_SEKCI: dict[str, set[str]] = {
    "elevenlabs": {"model", "modely_povolene", "modely_s_jazykem", "hlas", "hlasy", "povolit_voice_id", "jazyk",
                   "timeout_s", "voice_settings", "data_use_vypnuto", "kredity", "mazat_historii"},
    "telegram": {"chat_id", "timeout_s", "protect_content", "prijem", "odpovedi", "retence"},
    "cil": {"prah_necinnosti_s", "prah_dlouhe_necinnosti_s", "tolerance_snimku_s", "okna_klienta",
            "pc_pri_zamceni", "desktop", "tiche_hodiny"},
    "prehravani": {"prikaz"},
    "limity": {"max_znaku", "znaku_za_den", "volani_za_minutu", "duplicita_s"},
    "stt": {"cloud_povolen", "model_cesta", "zarizeni", "compute_type", "jazyk", "hotwords", "initial_prompt",
            "povolene_adresare", "max_mb", "max_s", "uvolnit_po_s", "timeout_s"},
    "cesty": {"stav", "vystup_adresar"},
    "strazce": {"vyzadovat", "max_stari_s", "prepis_cloud", "citlive_kontexty"},
}
KLICE_KORENE = set(KLICE_SEKCI) | {"instrukce_navic"}


def _sekce(data: dict, klic: str) -> dict:
    v = data.get(klic)
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ChybaKonfigurace(f"sekce {klic} musí být mapa")
    _klice(v, KLICE_SEKCI[klic], klic)
    return v


def _cislo(sekce: dict, klic: str, vychozi: float, minimum: float, maximum: float, cele: bool = False):
    v = sekce.get(klic, vychozi)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ChybaKonfigurace(f"{klic} musí být číslo")
    if not minimum <= v <= maximum:
        raise ChybaKonfigurace(f"{klic} mimo rozsah {minimum}..{maximum}")
    return int(v) if cele else float(v)


def _bool(sekce: dict, klic: str, vychozi: bool) -> bool:
    v = sekce.get(klic, vychozi)
    if not isinstance(v, bool):
        raise ChybaKonfigurace(f"{klic} musí být true/false")
    return v


def _text(sekce: dict, klic: str, vychozi: str | None, vzor: re.Pattern | None = None,
          volby: tuple[str, ...] | None = None, max_delka: int = 500) -> str | None:
    v = sekce.get(klic, vychozi)
    if v is None:
        return None
    if isinstance(v, int) and not isinstance(v, bool):
        v = str(v)
    if not isinstance(v, str) or len(v) > max_delka:
        raise ChybaKonfigurace(f"{klic} musí být text do {max_delka} znaků")
    if vzor is not None and v and not vzor.match(v):
        raise ChybaKonfigurace(f"{klic} má neplatný tvar")
    if volby is not None and v not in volby:
        raise ChybaKonfigurace(f"{klic} musí být jedno z: {', '.join(volby)}")
    return v


def _seznam(sekce: dict, klic: str, vychozi: tuple[str, ...], vzor: re.Pattern | None = None) -> tuple[str, ...]:
    v = sekce.get(klic, list(vychozi))
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
        raise ChybaKonfigurace(f"{klic} musí být seznam neprázdných textů")
    if vzor is not None and not all(vzor.match(x) for x in v):
        raise ChybaKonfigurace(f"{klic} obsahuje neplatnou položku")
    return tuple(v)


# Citlivé kontexty strážce. Neznámý klíč je chyba: překlep ve jménu signálu by detekci tiše vypnul.
NAZEV_KONTEXTU = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$")
GLOB_JMENA = re.compile(r"^[A-Za-z0-9._:*?!\[\]-]{1,100}$")
GLOB_NASTROJE = re.compile(r"^[A-Za-z0-9._:*?!\[\]-]{1,200}$")
POLE_VSTUPU = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
RIDICI_ZNAK = re.compile(r"[\x00-\x1f\x7f\u0085  ]")
MAX_KONTEXTU = 20
MAX_POLOZEK = 100
KLICE_KONTEXTU = {"nazev", "upozorneni", "platnost", "signaly", "telegram", "telegram_platnost", "telegram_dotaz_v_tahu"}
KLICE_SIGNALU = {"mcp", "agenti", "skilly", "cesty", "texty", "vstupy"}
KLICE_VSTUPU = {"nastroj", "pole", "hodnoty", "predpony"}


def _jmeno_klice(k: Any) -> str:
    """Jméno klíče do chybové zprávy: repr (řídicí a neviditelné znaky escapované), zkrácené."""
    s = str(k)
    return repr(s if len(s) <= 40 else s[:40] + "…")


def _klice(d: dict, povolene: set[str], kde: str) -> None:
    nezname = [k for k in d if k not in povolene]
    if nezname:
        jmena = ", ".join(_jmeno_klice(k) for k in nezname[:3]) + (" …" if len(nezname) > 3 else "")
        raise ChybaKonfigurace(f"{kde}: neznámý klíč {jmena} (povolené: {', '.join(sorted(povolene))})")


def _texty_kontextu(d: dict, klic: str, kde: str, vzor: re.Pattern | None = None, min_delka: int = 1,
                    max_delka: int = 200) -> tuple[str, ...]:
    v = d.get(klic)
    if v is None:
        return ()
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list) or len(v) > MAX_POLOZEK:
        raise ChybaKonfigurace(f"{kde}.{klic} musí být seznam (nejvýš {MAX_POLOZEK} položek)")
    for x in v:
        if (not isinstance(x, str) or not min_delka <= len(x) <= max_delka or RIDICI_ZNAK.search(x)
                or (vzor is not None and not vzor.match(x))):
            raise ChybaKonfigurace(f"{kde}.{klic} obsahuje neplatnou položku")
    return tuple(v)


def _cesta_kontextu(x: str, kde: str) -> str:
    if not (x.startswith("/") or x == "~" or x.startswith("~/")):
        raise ChybaKonfigurace(f"{kde}.cesty: cesta musí být absolutní nebo začínat ~/")
    p = os.path.normpath(os.path.expanduser(x))
    if p in ("/", os.path.normpath(os.path.expanduser("~"))):
        raise ChybaKonfigurace(f"{kde}.cesty: kořen ani domovský adresář nejsou citlivý kontext")
    return x


def _vstupy(v: object, kde: str) -> tuple[PravidloVstupu, ...]:
    if v is None:
        return ()
    if not isinstance(v, list) or len(v) > MAX_POLOZEK:
        raise ChybaKonfigurace(f"{kde}.vstupy musí být seznam (nejvýš {MAX_POLOZEK} položek)")
    pravidla = []
    for i, r in enumerate(v):
        k = f"{kde}.vstupy[{i}]"
        if not isinstance(r, dict):
            raise ChybaKonfigurace(f"{k} musí být mapa")
        _klice(r, KLICE_VSTUPU, k)
        nastroj = r.get("nastroj")
        if not isinstance(nastroj, str) or not GLOB_NASTROJE.match(nastroj):
            raise ChybaKonfigurace(f"{k}.nastroj musí být jméno nebo glob nástroje")
        pole = _texty_kontextu(r, "pole", k, POLE_VSTUPU, max_delka=64)
        if not pole:
            raise ChybaKonfigurace(f"{k}.pole chybí")
        hodnoty = _texty_kontextu(r, "hodnoty", k)
        predpony = _texty_kontextu(r, "predpony", k)
        if not hodnoty and not predpony:
            raise ChybaKonfigurace(f"{k}: chybí hodnoty i predpony")
        pravidla.append(PravidloVstupu(nastroj=nastroj, pole=pole, hodnoty=hodnoty, predpony=predpony))
    return tuple(pravidla)


def _kontexty(v: object) -> tuple[CitlivyKontext, ...]:
    if v is None:
        return ()
    if not isinstance(v, list) or len(v) > MAX_KONTEXTU:
        raise ChybaKonfigurace(f"strazce.citlive_kontexty musí být seznam (nejvýš {MAX_KONTEXTU})")
    kontexty, jmena = [], set()
    for i, d in enumerate(v):
        kde = f"strazce.citlive_kontexty[{i}]"
        if not isinstance(d, dict):
            raise ChybaKonfigurace(f"{kde} musí být mapa")
        _klice(d, KLICE_KONTEXTU, kde)
        nazev = d.get("nazev")
        if not isinstance(nazev, str) or not NAZEV_KONTEXTU.match(nazev):
            raise ChybaKonfigurace(f"{kde}.nazev: 1–40 znaků, písmena, číslice, mezera, . _ -")
        if nazev.casefold() in jmena:
            raise ChybaKonfigurace(f"{kde}.nazev se opakuje")
        jmena.add(nazev.casefold())
        upozorneni = d.get("upozorneni")
        if not isinstance(upozorneni, str) or not upozorneni.strip() or len(upozorneni) > 300 \
                or RIDICI_ZNAK.search(upozorneni):
            raise ChybaKonfigurace(f"{kde}.upozorneni: povinný jednořádkový text do 300 znaků")
        platnost = _text(d, "platnost", "den", volby=("den", "session", "volani"))
        s = d.get("signaly")
        if not isinstance(s, dict):
            raise ChybaKonfigurace(f"{kde}.signaly musí být mapa")
        ks = f"{kde}.signaly"
        _klice(s, KLICE_SIGNALU, ks)
        kontext = CitlivyKontext(
            nazev=nazev,
            upozorneni=upozorneni.strip(),
            platnost=platnost,
            mcp=_texty_kontextu(s, "mcp", ks, GLOB_JMENA),
            agenti=_texty_kontextu(s, "agenti", ks, GLOB_JMENA),
            skilly=_texty_kontextu(s, "skilly", ks, GLOB_JMENA),
            cesty=tuple(_cesta_kontextu(x, ks) for x in _texty_kontextu(s, "cesty", ks, max_delka=4096)),
            texty=_texty_kontextu(s, "texty", ks, min_delka=3),
            vstupy=_vstupy(s.get("vstupy"), ks),
            telegram=_text(d, "telegram", "souhlas", volby=("souhlas", "nikdy")),
            telegram_platnost=_text(d, "telegram_platnost", "kontext", volby=("kontext", "okno")),
            telegram_dotaz_v_tahu=_text(d, "telegram_dotaz_v_tahu", "rc", volby=("rc", "odmitnout")),
        )
        if not any((kontext.mcp, kontext.agenti, kontext.skilly, kontext.cesty, kontext.texty, kontext.vstupy)):
            raise ChybaKonfigurace(f"{ks}: kontext nemá žádný signál")
        kontexty.append(kontext)
    return tuple(kontexty)


def z_dat(data: dict[str, Any] | None) -> Konfigurace:
    data = data or {}
    if not isinstance(data, dict):
        raise ChybaKonfigurace("kořen konfigurace musí být mapa")
    _klice(data, KLICE_KORENE, "kořen konfigurace")

    e = _sekce(data, "elevenlabs")
    hlasy = e.get("hlasy") or {}
    if not isinstance(hlasy, dict):
        raise ChybaKonfigurace("elevenlabs.hlasy musí být mapa alias -> voice_id")
    for alias, vid in hlasy.items():
        if not isinstance(alias, str) or not ALIAS.match(alias):
            raise ChybaKonfigurace("elevenlabs.hlasy: neplatný alias")
        if not isinstance(vid, str) or not VOICE_ID.match(vid):
            raise ChybaKonfigurace("elevenlabs.hlasy: neplatné voice_id")
    vs = e.get("voice_settings") or {}
    if not isinstance(vs, dict) or not all(isinstance(k, str) and isinstance(v, (int, float))
                                           and not isinstance(v, bool) for k, v in vs.items()):
        raise ChybaKonfigurace("elevenlabs.voice_settings musí být mapa čísel")
    modely = _seznam(e, "modely_povolene", ElevenLabs.modely_povolene, MODEL_ID)
    model = _text(e, "model", ElevenLabs.model, MODEL_ID)
    if model not in modely:
        raise ChybaKonfigurace("elevenlabs.model musí být v modely_povolene")
    el = ElevenLabs(
        model=model,
        modely_povolene=modely,
        modely_s_jazykem=_seznam(e, "modely_s_jazykem", ElevenLabs.modely_s_jazykem, MODEL_ID),
        hlas=_text(e, "hlas", ElevenLabs.hlas, ALIAS),
        hlasy=dict(hlasy),
        povolit_voice_id=_bool(e, "povolit_voice_id", False),
        jazyk=_text(e, "jazyk", "cs", re.compile(r"^[a-z]{2,3}$")),
        timeout_s=_cislo(e, "timeout_s", 30, 1, 300),
        voice_settings={k: float(v) for k, v in vs.items()},
        data_use_vypnuto=_bool(e, "data_use_vypnuto", False),
        kredity=_bool(e, "kredity", True),
        mazat_historii=_bool(e, "mazat_historii", True),
    )

    t = _sekce(data, "telegram")
    chat_id = _text(t, "chat_id", None, CHAT_ID, max_delka=21) or None
    o = _podsekce(t, "odpovedi", {"max_casti", "max_znaku_casti"}, "telegram")
    r = _podsekce(t, "retence", {"smazat_po_h"}, "telegram")
    tg_zaklad = dict(
        chat_id=chat_id,
        timeout_s=_cislo(t, "timeout_s", 20, 1, 120),
        protect_content=_bool(t, "protect_content", False),
        max_casti=_cislo(o, "max_casti", 3, 1, 5, cele=True),
        max_znaku_casti=_cislo(o, "max_znaku_casti", 4000, 100, 4096, cele=True),
        smazat_po_h=_cislo(r, "smazat_po_h", 24, 0, 47, cele=True),
    )

    c = _sekce(data, "cil")
    tiche = c.get("tiche_hodiny")
    if tiche is not None:
        if (not isinstance(tiche, list) or len(tiche) != 2
                or not all(isinstance(h, int) and not isinstance(h, bool) and 0 <= h <= 23 for h in tiche)
                or tiche[0] == tiche[1]):
            raise ChybaKonfigurace("cil.tiche_hodiny musí být [od, do], celé hodiny 0..23, od != do")
        tiche = (tiche[0], tiche[1])
    cil = Cil(
        prah_necinnosti_s=_cislo(c, "prah_necinnosti_s", 60, 5, 86400),
        prah_dlouhe_necinnosti_s=_cislo(c, "prah_dlouhe_necinnosti_s", 600, 5, 86400),
        tolerance_snimku_s=_cislo(c, "tolerance_snimku_s", 5, 0, 120),
        okna_klienta=_seznam(c, "okna_klienta", Cil.okna_klienta, WM_CLASS),
        pc_pri_zamceni=_text(c, "pc_pri_zamceni", "odmitnout", volby=("odmitnout", "hrat")),
        desktop=_text(c, "desktop", "auto", volby=("auto", "cinnamon", "gnome", "x11")),
        tiche_hodiny=tiche,
    )

    p = _sekce(data, "prehravani")
    preh = Prehravani(prikaz=_text(p, "prikaz", "paplay", volby=("paplay", "pw-cat")))

    li = _sekce(data, "limity")
    lim = Limity(
        max_znaku=_cislo(li, "max_znaku", 1500, 1, 5000, cele=True),
        znaku_za_den=_cislo(li, "znaku_za_den", 500, 0, 10_000_000, cele=True),
        volani_za_minutu=_cislo(li, "volani_za_minutu", 6, 1, 600, cele=True),
        duplicita_s=_cislo(li, "duplicita_s", 60, 0, 86400),
    )

    s = data.get("stt") if isinstance(data.get("stt"), dict) else {}
    if "engine" in s:
        # R-STT-1: výchozí engine nejde v konfiguraci přepnout na cloud.
        raise ChybaKonfigurace("stt.engine není podporováno: cloud jen parametrem volání (stt.cloud_povolen jen zakazuje)")
    s = _sekce(data, "stt")
    stt = Stt(
        cloud_povolen=_bool(s, "cloud_povolen", True),
        model_cesta=_text(s, "model_cesta", Stt.model_cesta),
        zarizeni=_text(s, "zarizeni", "cuda", volby=("cuda", "cpu")),
        compute_type=_text(s, "compute_type", "int8_float16",
                           volby=("int8_float16", "float16", "int8", "float32", "int8_float32")),
        jazyk=_text(s, "jazyk", "cs", re.compile(r"^[a-z]{2,3}$")),
        hotwords=_text(s, "hotwords", "", max_delka=300) or "",
        initial_prompt=_text(s, "initial_prompt", "", max_delka=500) or "",
        povolene_adresare=_seznam(s, "povolene_adresare", Stt.povolene_adresare),
        max_mb=_cislo(s, "max_mb", 25, 0.01, 200),
        max_s=_cislo(s, "max_s", 600, 1, 7200),
        uvolnit_po_s=_cislo(s, "uvolnit_po_s", 600, 0, 86400),
        timeout_s=_cislo(s, "timeout_s", 120, 5, 3600),
    )

    try:
        stav_prijmu = _text(_sekce(data, "cesty"), "stav", Cesty.stav) or Cesty.stav
        prijem = _prijem(t.get("prijem"), chat_id, stt.max_s, stav_prijmu)
    except ChybaKonfigurace as e:
        prijem = Prijem(povoleno=False, chyba=str(e))
    tg = Telegram(prijem=prijem, **tg_zaklad)

    ce = _sekce(data, "cesty")
    cesty = Cesty(stav=_text(ce, "stav", Cesty.stav), vystup_adresar=_text(ce, "vystup_adresar", Cesty.vystup_adresar))

    st = _sekce(data, "strazce")
    strazce = Strazce(vyzadovat=_bool(st, "vyzadovat", True), max_stari_s=_cislo(st, "max_stari_s", 600, 5, 3600),
                      prepis_cloud=_text(st, "prepis_cloud", "dotaz", volby=("dotaz", "povolit")),
                      citlive_kontexty=_kontexty(st.get("citlive_kontexty")))

    navic = _text(data, "instrukce_navic", "", max_delka=2000) or ""
    return Konfigurace(elevenlabs=el, telegram=tg, cil=cil, prehravani=preh, limity=lim, stt=stt,
                       cesty=cesty, strazce=strazce, instrukce_navic=navic)


# ---------------------------------------------------------------- příjem z Telegramu

def _podsekce(d: dict, klic: str, povolene: set[str], kde: str) -> dict:
    v = d.get(klic)
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ChybaKonfigurace(f"{kde}.{klic} musí být mapa")
    _klice(v, povolene, f"{kde}.{klic}")
    return v


KLICE_PRIJMU = {"povoleno", "povolene_user_id", "registrace", "povel", "long_poll_s", "okno", "rezimy",
                "chranene_cesty", "chranene_zapis", "zakazane_nastroje", "zakazane_prikazy", "omezit_naplanovane",
                "pojistka_bash", "povolit_neoverenou_verzi", "vstup", "fronta", "doruceni", "forma",
                "oznamit_nazev_kontextu", "jazyk_sablon", "konfigurace_agenta"}
REGISTRACE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
POVEL = re.compile(r"^/[a-z][a-z0-9_-]{1,31}$")
VZOR_JMENA = re.compile(r"^[A-Za-z0-9._*?!\[\]-]{1,100}$")
MASKA_PRIKAZU = re.compile(r"^[^\s\x00-\x1f\x7f]{1,64}$")
MAX_PRIDAT = 100


def _jmena_nastroju(d: dict, klic: str, kde: str, zakazane: tuple[str, ...] = ()) -> tuple[str, ...]:
    v = d.get(klic, [])
    if not isinstance(v, list) or len(v) > MAX_PRIDAT:
        raise ChybaKonfigurace(f"{kde}.{klic} musí být seznam (nejvýš {MAX_PRIDAT})")
    for x in v:
        if not isinstance(x, str) or not politika.JMENO_NASTROJE.match(x):
            raise ChybaKonfigurace(f"{kde}.{klic}: přesné jméno nástroje, bez globů")
        if x in zakazane:
            raise ChybaKonfigurace(f"{kde}.{klic}: {x} je tvrdě zakázaný v tahu z Telegramu")
    return tuple(v)


def _cesta_prijmu(x: object, kde: str, vzory: bool) -> str:
    if not isinstance(x, str) or not x or len(x) > 4096 or RIDICI_ZNAK.search(x) or ".." in x.split("/"):
        raise ChybaKonfigurace(f"{kde}: neplatná položka")
    if x.startswith("/") or x == "~" or x.startswith("~/"):
        p = os.path.normpath(os.path.expanduser(x))
        if p in ("/", os.path.normpath(os.path.expanduser("~"))):
            raise ChybaKonfigurace(f"{kde}: kořen ani domovský adresář nejsou povolená položka")
        return x
    if vzory and "/" not in x and VZOR_JMENA.match(x):
        return x
    raise ChybaKonfigurace(f"{kde}: cesta (absolutní nebo ~/…)" + (" nebo vzor jména bez /" if vzory else ""))


def _seznam_s_vychozimi(d: dict, klic: str, vychozi: tuple[str, ...], kontrola) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """{pridat, odebrat_vychozi}: výchozí položku jde odebrat jen přesnou shodou (vědomě)."""
    s = _podsekce(d, klic, {"pridat", "odebrat_vychozi"}, "telegram.prijem")
    kde = f"telegram.prijem.{klic}"
    pridat, odebrat = s.get("pridat", []), s.get("odebrat_vychozi", [])
    if not isinstance(pridat, list) or not isinstance(odebrat, list) or len(pridat) > MAX_PRIDAT:
        raise ChybaKonfigurace(f"{kde}: pridat a odebrat_vychozi musí být seznamy (nejvýš {MAX_PRIDAT})")
    pridat = tuple(kontrola(x, f"{kde}.pridat") for x in pridat)
    for x in odebrat:
        if x not in vychozi:
            raise ChybaKonfigurace(f"{kde}.odebrat_vychozi: položka není ve výchozím seznamu (přesná shoda)")
    return politika.efektivni(vychozi, pridat, tuple(odebrat)), tuple(odebrat)


def _maska(x: object, kde: str) -> str:
    if not isinstance(x, str) or not MASKA_PRIKAZU.match(x):
        raise ChybaKonfigurace(f"{kde}: maska 1–64 znaků bez mezer a řídicích znaků")
    return x


def _normalizuj_frazi(x: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", x)
    return " ".join("".join(c for c in t if not unicodedata.combining(c)).lower().split())


def _politika_validace(chranene: tuple[str, ...], stav: str) -> dict:
    return politika.sestav(registrace="validace", chranene=chranene, chranene_zapis=(), stav=stav, vlastni=(),
                           bash_domov=(), bash_skryt=())


def _bash_domov(x: object, kde: str, pol: dict) -> str:
    """Složka nástrojů z domova pro obal (jen pro čtení): ~/…, ne domov, ne chráněná cesta."""
    x = _cesta_prijmu(x, kde, False)
    if not x.startswith("~/"):
        raise ChybaKonfigurace(f"{kde}: jen cesta v domově (~/…)")
    c = politika.rozbal_cestu(x, pol["home"], os.environ, "", 0)
    if politika.chranena(c, "/", pol) or politika.konfigurace_agenta([c], pol):
        raise ChybaKonfigurace(f"{kde}: {x} je chráněná cesta")
    return x


def _bash_koreny(sekce: dict, rezim: str, kde: str, pol: dict) -> tuple[tuple[str, str, str], ...]:
    v = sekce.get("bash_koreny", [])
    if not isinstance(v, list) or len(v) > MAX_PRIDAT:
        raise ChybaKonfigurace(f"{kde}.bash_koreny musí být seznam (nejvýš {MAX_PRIDAT})")
    vysledek = []
    for x in v:
        if not isinstance(x, dict):
            raise ChybaKonfigurace(f"{kde}.bash_koreny: položka je mapa {{cesta, pristup}}")
        _klice(x, {"cesta", "pristup"}, f"{kde}.bash_koreny")
        cesta = _cesta_prijmu(x.get("cesta"), f"{kde}.bash_koreny.cesta", False)
        pristup = x.get("pristup")
        if pristup not in ("rw", "ro"):
            raise ChybaKonfigurace(f"{kde}.bash_koreny.pristup: rw nebo ro")
        chyba = politika.koren_bash_chyba(politika.rozbal_cestu(cesta, pol["home"], os.environ, "", 0), pol)
        if chyba:
            raise ChybaKonfigurace(f"{kde}.bash_koreny: {cesta} {chyba}")
        vysledek.append((rezim, cesta, pristup))
    return tuple(vysledek)


def _prijem(v: object, chat_id: str | None, stt_max_s: float, stav: str = "~/.local/state/mcp-voice") -> Prijem:
    v = {} if v is None else v
    if not isinstance(v, dict):
        raise ChybaKonfigurace("telegram.prijem musí být mapa")
    _klice(v, KLICE_PRIJMU, "telegram.prijem")
    kde = "telegram.prijem"
    povoleno = _bool(v, "povoleno", False)
    ids = v.get("povolene_user_id", [])
    if (not isinstance(ids, list) or len(ids) > 5
            or not all(isinstance(x, int) and not isinstance(x, bool) and 0 < x < 10**15 for x in ids)):
        raise ChybaKonfigurace(f"{kde}.povolene_user_id: 0–5 celých čísel > 0")
    if povoleno:
        if not chat_id or not ids:
            raise ChybaKonfigurace(f"{kde}: povoleno vyžaduje telegram.chat_id a povolene_user_id")
        if int(chat_id) not in ids:
            raise ChybaKonfigurace(f"{kde}: chat_id musí být mezi povolene_user_id (soukromý chat)")
    ok = _podsekce(v, "okno", {"vychozi_min", "max_min", "necinnost_min", "vychozi_rezim"}, kde)
    max_min = _cislo(ok, "max_min", 240, 5, 240, cele=True)
    vychozi_min = _cislo(ok, "vychozi_min", 60, 5, 240, cele=True)
    necinnost = _cislo(ok, "necinnost_min", 60, 5, 240, cele=True)
    if vychozi_min > max_min or necinnost > max_min:
        raise ChybaKonfigurace(f"{kde}.okno: vychozi_min a necinnost_min nejvýš max_min")
    rz = _podsekce(v, "rezimy", {"cteni", "prace", "plny"}, kde)
    cteni = _podsekce(rz, "cteni", {"nastroje_navic", "koreny"}, kde + ".rezimy")
    prace = _podsekce(rz, "prace", {"pri_dotazu", "bash_koreny"}, kde + ".rezimy")
    plny = _podsekce(rz, "plny", {"bash_izolace", "bash_sit", "bash_bez_dotazu", "bash_domov", "bash_skryt",
                                  "bash_koreny"}, kde + ".rezimy")
    zakazane_navic = _jmena_nastroju(_podsekce(v, "zakazane_nastroje", {"pridat"}, kde), "pridat",
                                     kde + ".zakazane_nastroje")
    koreny = cteni.get("koreny", [])
    if not isinstance(koreny, list) or len(koreny) > MAX_PRIDAT:
        raise ChybaKonfigurace(f"{kde}.rezimy.cteni.koreny musí být seznam")
    chranene, odebrane = _seznam_s_vychozimi(v, "chranene_cesty", politika.CHRANENE_VYCHOZI,
                                            lambda x, k: _cesta_prijmu(x, k, True))
    zapis, odebrane_z = _seznam_s_vychozimi(v, "chranene_zapis", politika.CHRANENE_ZAPIS_VYCHOZI,
                                           lambda x, k: _cesta_prijmu(x, k, True))
    prikazy, _ = _seznam_s_vychozimi(v, "zakazane_prikazy", ZAKAZANE_PRIKAZY_VYCHOZI, _maska)
    # konfigurace agenta: jen přidat (výchozí zákaz zápisu nejde odebrat)
    ka = _podsekce(v, "konfigurace_agenta", {"pridat"}, kde)
    agent = ka.get("pridat", [])
    if not isinstance(agent, list) or len(agent) > MAX_PRIDAT or not all(
            isinstance(x, str) and "/" not in x and VZOR_JMENA.match(x) for x in agent):
        raise ChybaKonfigurace(f"{kde}.konfigurace_agenta.pridat: seznam jmen nebo vzorů jmen bez /")
    pol_val = _politika_validace(chranene, stav)
    domov, _ = _seznam_s_vychozimi(plny, "bash_domov", politika.BASH_DOMOV_VYCHOZI,
                                   lambda x, k: _bash_domov(x, k, pol_val))
    skryt = plny.get("bash_skryt", [])
    if not isinstance(skryt, list) or len(skryt) > MAX_PRIDAT:
        raise ChybaKonfigurace(f"{kde}.rezimy.plny.bash_skryt musí být seznam")
    for x in skryt:
        if not isinstance(x, str) or not x.startswith("/") or os.path.normpath(x) == "/":
            raise ChybaKonfigurace(f"{kde}.rezimy.plny.bash_skryt: absolutní cesta mimo /")
        _cesta_prijmu(x, f"{kde}.rezimy.plny.bash_skryt", False)
    koreny_bash = (_bash_koreny(prace, "prace", kde + ".rezimy.prace", pol_val)
                   + _bash_koreny(plny, "plny", kde + ".rezimy.plny", pol_val))
    vs = _podsekce(v, "vstup", {"max_znaku", "zprav_za_minutu", "zprav_za_okno", "hlasovka_max_s", "hlasovka_max_mb",
                                "dekodovani_izolace"}, kde)
    fr = _podsekce(v, "fronta", {"max_zprav", "oznamit_po_s", "necinnost_tahu_s", "max_cekani_min"}, kde)
    do = _podsekce(v, "doruceni", {"potvrzeni_s"}, kde)
    fo = _podsekce(v, "forma", {"hlas", "text", "pc"}, kde)
    formy = {}
    videne: set[str] = set()
    for klic, vychozi in (("hlas", FORMA_HLAS), ("text", FORMA_TEXT), ("pc", FORMA_PC)):
        seznam = fo.get(klic, list(vychozi))
        if not isinstance(seznam, list) or len(seznam) > 50:
            raise ChybaKonfigurace(f"{kde}.forma.{klic} musí být seznam")
        for x in seznam:
            if not isinstance(x, str) or not 2 <= len(x) <= 40 or RIDICI_ZNAK.search(x):
                raise ChybaKonfigurace(f"{kde}.forma.{klic}: fráze 2–40 znaků")
            n = _normalizuj_frazi(x)
            if n in videne:
                raise ChybaKonfigurace(f"{kde}.forma: fráze se opakuje napříč seznamy")
            videne.add(n)
        formy[klic] = tuple(seznam)
    return Prijem(
        povoleno=povoleno,
        povolene_user_id=tuple(ids),
        registrace=_text(v, "registrace", "hlas", REGISTRACE, max_delka=64),
        povel=_text(v, "povel", "/telegram", POVEL, max_delka=32),
        long_poll_s=_cislo(v, "long_poll_s", 50, 10, 50, cele=True),
        vychozi_min=vychozi_min, max_min=max_min, necinnost_min=necinnost,
        vychozi_rezim=_text(ok, "vychozi_rezim", "prace", volby=politika.REZIMY),
        nastroje_navic=_jmena_nastroju(cteni, "nastroje_navic", kde + ".rezimy.cteni",
                                       politika.ZAKAZANE_V_TAHU + zakazane_navic),
        koreny=tuple(_cesta_prijmu(x, kde + ".rezimy.cteni.koreny", False) for x in koreny),
        pri_dotazu=_text(prace, "pri_dotazu", "cekat", volby=("cekat", "odmitnout")),
        bash_izolace=_text(plny, "bash_izolace", "bwrap", volby=("bwrap", "zadna")),
        bash_sit=_bool(plny, "bash_sit", False),
        bash_bez_dotazu=_bool(plny, "bash_bez_dotazu", False),
        bash_domov=domov, bash_skryt=tuple(skryt), bash_koreny=koreny_bash, konfigurace_agenta=tuple(agent),
        chranene=chranene, chranene_zapis=zapis, odebrane_vychozi=odebrane + odebrane_z,
        zakazane_nastroje=zakazane_navic,
        zakazane_prikazy=prikazy,
        omezit_naplanovane=_bool(v, "omezit_naplanovane", True),
        pojistka_bash=_bool(v, "pojistka_bash", True),
        povolit_neoverenou_verzi=_bool(v, "povolit_neoverenou_verzi", False),
        max_znaku=_cislo(vs, "max_znaku", 4000, 1, 4096, cele=True),
        zprav_za_minutu=_cislo(vs, "zprav_za_minutu", 6, 1, 30, cele=True),
        zprav_za_okno=_cislo(vs, "zprav_za_okno", 100, 1, 1000, cele=True),
        hlasovka_max_s=_cislo(vs, "hlasovka_max_s", min(300, int(stt_max_s)), 1, min(stt_max_s, 3600), cele=True),
        hlasovka_max_mb=_cislo(vs, "hlasovka_max_mb", 5, 1, 20, cele=True),
        dekodovani_izolace=_text(vs, "dekodovani_izolace", "auto", volby=("auto", "bwrap", "zadna")),
        fronta_max_zprav=_cislo(fr, "max_zprav", 10, 1, 50, cele=True),
        oznamit_po_s=_cislo(fr, "oznamit_po_s", 120, 10, 3600, cele=True),
        necinnost_tahu_s=_cislo(fr, "necinnost_tahu_s", 90, 15, 600, cele=True),
        max_cekani_min=_cislo(fr, "max_cekani_min", 15, 1, 120, cele=True),
        potvrzeni_s=_cislo(do, "potvrzeni_s", 10, 2, 60, cele=True),
        forma_hlas=formy["hlas"], forma_text=formy["text"], forma_pc=formy["pc"],
        oznamit_nazev_kontextu=_bool(v, "oznamit_nazev_kontextu", False),
        jazyk_sablon=_text(v, "jazyk_sablon", "cs", volby=("cs", "en")),
    )


class _YamlBezDuplicit(yaml.SafeLoader):
    """SafeLoader, který odmítne duplicitní klíč a merge klíč `<<` (safe_load by tiše vzal poslední hodnotu)."""

    def construct_mapping(self, node, deep=False):
        if isinstance(node, yaml.MappingNode):
            videne = set()
            for uzel, _ in node.value:
                if uzel.tag == "tag:yaml.org,2002:merge":
                    # Mezi dvěma merge by tiše platila poslední hodnota; konfigurace merge nepotřebuje.
                    raise ChybaKonfigurace(f"merge klíč << není v konfiguraci podporován (řádek {uzel.start_mark.line + 1})")
                klic = self.construct_object(uzel, deep=True)
                try:
                    if klic in videne:
                        raise ChybaKonfigurace(f"duplicitní klíč v konfiguraci (řádek {uzel.start_mark.line + 1})")
                    videne.add(klic)
                except TypeError:
                    pass  # nehashovatelný klíč odmítne SafeLoader sám
        return super().construct_mapping(node, deep=deep)


def nacti_yaml(text) -> Any:
    """YAML konfigurace: bezpečný loader bez duplicitních klíčů; chyba = ChybaKonfigurace."""
    try:
        return yaml.load(text, Loader=_YamlBezDuplicit)  # noqa: S506 – podtřída SafeLoader
    except yaml.YAMLError as e:
        raise ChybaKonfigurace("konfigurace není platný YAML") from e


def nacti(cesta: str | None = None) -> Konfigurace:
    cesta = cesta or os.environ.get("MCP_VOICE_CONFIG") or VYCHOZI_CESTA
    p = Path(os.path.expanduser(cesta))
    if not p.exists():
        return z_dat({})
    with p.open(encoding="utf-8") as fh:
        data = nacti_yaml(fh)
    return z_dat(data)
