"""Tajemství z keyringu (služba mcp-voice). Čte se při každém volání, nedrží se v globální proměnné.

Keyring není hranice: čte ho každý proces uživatele. Chrání jen před únikem přes env a soubory.
"""
from __future__ import annotations

from typing import Callable

from .chyby import HlasChyba, registruj_tajemstvi

SLUZBA = "mcp-voice"
ELEVENLABS = "elevenlabs:api_key"
TELEGRAM = "telegram:bot_token"

Cteni = Callable[[str, str], "str | None"]


def _keyring_get(sluzba: str, klic: str) -> str | None:
    import keyring

    return keyring.get_password(sluzba, klic)


def nacti(klic: str, cteni: Cteni | None = None) -> str:
    """Hodnota tajemství. KEYRING_UNAVAILABLE = backend nejde použít, SECRET_MISSING = položka chybí."""
    try:
        hodnota = (cteni or _keyring_get)(SLUZBA, klic)
    except Exception as e:  # noqa: BLE001 – ven jen typ chyby, text může nést detail backendu
        raise HlasChyba("KEYRING_UNAVAILABLE",
                        f"Keyring není dostupný ({type(e).__name__}): chybí, neběží nebo je zamčená služba "
                        "Secret Service. Ověření backendu: README, sekce Tajemství.") from None
    hodnota = hodnota.strip() if isinstance(hodnota, str) else ""
    if not hodnota:
        raise HlasChyba("SECRET_MISSING", f"V keyringu chybí položka: služba {SLUZBA}, klíč {klic}.")
    registruj_tajemstvi(hodnota)
    return hodnota
