"""Výběr cíle hlasové odpovědi (čistá funkce, bez I/O).

Pravidlo degradace: při nejistotě nikdy hůř než „PC, když je uživatel u PC“.
Do Telegramu jde auto režim jen tehdy, když stav PC říká, že u něj uživatel není.
"""
from __future__ import annotations

from dataclasses import dataclass

from .chyby import HlasChyba
from .config import Cil

TERMINAL = "terminal"
REMOTE = "remote_control"
NEZNAMY = "neznamy"
TELEGRAM = "telegram"  # tah, který přišel z Telegramu (příjem): odpověď hlasem jde zpět do Telegramu

# Uživatel se díval na okno klienta a byl aktivní: nečinnost při promptu pod touto hranicí.
AKTIVNI_U_KLIENTA_S = 30.0


@dataclass(frozen=True)
class StavPc:
    zamceno: bool | None  # None = nejde zjistit
    necinnost_s: float | None
    okno: str | None = None  # jen WM_CLASS, nikdy titulek


@dataclass(frozen=True)
class Rozhodnuti:
    cil: str  # pc | telegram | soubor
    duvod: str


def _v_tichych_hodinach(hodina: int | None, tiche: tuple[int, int] | None) -> bool:
    if tiche is None or hodina is None:
        return False
    od, do = tiche
    return od <= hodina < do if od < do else (hodina >= od or hodina < do)


def rozhodni_auto(zdroj: str, ted: StavPc, snimek: StavPc | None, od_promptu_s: float | None,
                  nastaveni: Cil, hodina: int | None = None) -> Rozhodnuti:
    """Cíl pro cil="auto". Vyhodí TARGET_UNRESOLVED, když nejde bezpečně rozhodnout."""
    if zdroj == TELEGRAM:
        return Rozhodnuti("telegram", "tah_z_telegramu")
    r = _rozhodni(zdroj, ted, snimek, od_promptu_s, nastaveni)
    if r.cil == "pc" and _v_tichych_hodinach(hodina, nastaveni.tiche_hodiny):
        return Rozhodnuti("telegram", "tiche_hodiny")
    return r


def _nejasne(duvod: str) -> HlasChyba:
    return HlasChyba("TARGET_UNRESOLVED",
                     "Nejde bezpečně určit, jestli je uživatel u PC (" + duvod + "). "
                     "Zeptej se ho, nebo zavolej s cil=\"pc\" či cil=\"telegram\".")


def _rozhodni(zdroj, ted, snimek, od_promptu_s, n: Cil) -> Rozhodnuti:
    if zdroj == TERMINAL:
        if ted.zamceno is True:
            return Rozhodnuti("telegram", "terminal+pc_zamceno")
        if ted.necinnost_s is not None and ted.necinnost_s >= n.prah_dlouhe_necinnosti_s:
            return Rozhodnuti("telegram", "terminal+dlouha_necinnost")
        if ted.zamceno is None:
            raise _nejasne("stav zámku neznámý")
        return Rozhodnuti("pc", "terminal")

    if zdroj == REMOTE:
        if ted.zamceno is True:
            return Rozhodnuti("telegram", "remote_control+pc_zamceno")
        if snimek is not None and snimek.zamceno is True:
            return Rozhodnuti("telegram", "remote_control+zamceno_pri_promptu")
        if ted.zamceno is None:
            if ted.necinnost_s is not None and ted.necinnost_s >= n.prah_necinnosti_s:
                return Rozhodnuti("telegram", "remote_control+necinnost")
            raise _nejasne("stav zámku neznámý")
        if (snimek is not None and snimek.okno and snimek.okno in n.okna_klienta
                and snimek.necinnost_s is not None and snimek.necinnost_s < AKTIVNI_U_KLIENTA_S):
            return Rozhodnuti("pc", "remote_control+klient_na_pc")
        if (od_promptu_s is not None and ted.necinnost_s is not None
                and ted.necinnost_s < od_promptu_s):
            return Rozhodnuti("pc", "remote_control+aktivita_po_promptu")
        necinnost = snimek.necinnost_s if snimek is not None and snimek.necinnost_s is not None else ted.necinnost_s
        if necinnost is None:
            raise _nejasne("nečinnost neznámá")
        if necinnost >= n.prah_necinnosti_s:
            duvod = "necinnost_pri_promptu" if snimek is not None and snimek.necinnost_s is not None else "necinnost"
            return Rozhodnuti("telegram", "remote_control+" + duvod)
        return Rozhodnuti("pc", "remote_control+uzivatel_u_pc")

    # neznámý zdroj
    if ted.zamceno is True:
        return Rozhodnuti("telegram", "neznamy+pc_zamceno")
    if ted.necinnost_s is not None and ted.necinnost_s >= n.prah_necinnosti_s:
        return Rozhodnuti("telegram", "neznamy+necinnost")
    if ted.zamceno is None or ted.necinnost_s is None:
        raise _nejasne("stav PC neznámý")
    return Rozhodnuti("pc", "neznamy+uzivatel_u_pc")


def over_explicitni_pc(ted: StavPc, nastaveni: Cil) -> Rozhodnuti:
    """cil="pc" výslovně: zamčené nebo neznámé PC jen s pc_pri_zamceni=hrat."""
    if nastaveni.pc_pri_zamceni == "hrat":
        return Rozhodnuti("pc", "vyslovne")
    if ted.zamceno is True:
        raise HlasChyba("PC_LOCKED", "PC je zamčené, nahlas se nepřehrává. Použij cil=\"telegram\".")
    if ted.zamceno is None:
        raise HlasChyba("PC_STATE_UNKNOWN", "Nejde zjistit, jestli je PC zamčené, nahlas se nepřehrává.")
    return Rozhodnuti("pc", "vyslovne")
