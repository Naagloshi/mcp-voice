"""Stav PC: zámek, nečinnost, třída aktivního okna. Jen čtení.

Volá se jen allowlist metod (pevné argv, timeout). Metody, které stav mění
(Lock, SetActive, SimulateUserActivity, ResetIdletime…), se nikdy nevolají.
Název okna (WM_NAME) se nečte, jen WM_CLASS.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Callable

from .cil import StavPc

TIMEOUT_S = 1.0

# (služba, objekt, rozhraní.metoda) -> jediné povolené volání DBus
DBUS_METODY = {
    "cinnamon_zamek": ("org.cinnamon.ScreenSaver", "/org/cinnamon/ScreenSaver", "org.cinnamon.ScreenSaver.GetActive"),
    "cinnamon_necinnost": ("org.cinnamon.Muffin.IdleMonitor", "/org/cinnamon/Muffin/IdleMonitor/Core",
                           "org.cinnamon.Muffin.IdleMonitor.GetIdletime"),
    "gnome_zamek": ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver.GetActive"),
    "gnome_necinnost": ("org.gnome.Mutter.IdleMonitor", "/org/gnome/Mutter/IdleMonitor/Core",
                        "org.gnome.Mutter.IdleMonitor.GetIdletime"),
    "freedesktop_zamek": ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver",
                          "org.freedesktop.ScreenSaver.GetActive"),
}
POVOLENE_METODY = frozenset(m for _, _, m in DBUS_METODY.values())

Spoustec = Callable[[list[str]], str | None]


def _spust(argv: list[str]) -> str | None:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_S, check=False,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def dbus_argv(klic: str) -> list[str]:
    sluzba, objekt, metoda = DBUS_METODY[klic]
    if metoda not in POVOLENE_METODY:  # pojistka proti úpravě tabulky
        raise ValueError("nepovolená metoda DBus")
    return ["gdbus", "call", "--session", "--timeout", "1", "--dest", sluzba,
            "--object-path", objekt, "--method", metoda]


_BOOL = re.compile(r"^\((true|false),\)\s*$")
_UINT = re.compile(r"^\((?:uint64 |uint32 )?(\d+),\)\s*$")


@dataclass
class Plocha:
    desktop: str = "auto"
    spoustec: Spoustec = _spust

    def _dbus_bool(self, klic: str) -> bool | None:
        out = self.spoustec(dbus_argv(klic))
        m = _BOOL.match(out or "")
        return None if not m else m.group(1) == "true"

    def _dbus_uint(self, klic: str) -> int | None:
        out = self.spoustec(dbus_argv(klic))
        m = _UINT.match(out or "")
        return int(m.group(1)) if m else None

    def _locked_hint(self) -> bool | None:
        out = self.spoustec(["loginctl", "show-session", "auto", "-p", "LockedHint", "--value"])
        v = (out or "").strip()
        return {"yes": True, "no": False}.get(v)

    def zamceno(self) -> bool | None:
        """Zamčeno = kterýkoli zdroj říká ano. Neznámé = žádný zdroj neodpověděl."""
        poradi = {"cinnamon": ["cinnamon_zamek"], "gnome": ["gnome_zamek"], "x11": ["freedesktop_zamek"],
                  "auto": ["cinnamon_zamek", "gnome_zamek", "freedesktop_zamek"]}[self.desktop]
        zdroje = []
        for klic in poradi:  # v auto režimu první prostředí, které odpoví
            zdroje.append(self._dbus_bool(klic))
            if zdroje[-1] is not None:
                break
        zdroje.append(self._locked_hint())
        if any(z is True for z in zdroje):
            return True
        return False if any(z is False for z in zdroje) else None

    def necinnost_ms(self) -> int | None:
        poradi = {"cinnamon": ["cinnamon_necinnost"], "gnome": ["gnome_necinnost"],
                  "x11": [], "auto": ["cinnamon_necinnost", "gnome_necinnost"]}[self.desktop]
        for klic in poradi:
            v = self._dbus_uint(klic)
            if v is not None:
                return v
        return None

    def okno(self) -> str | None:
        """Třída (druhá položka WM_CLASS) aktivního okna, nebo None."""
        if not os.environ.get("DISPLAY"):
            return None
        out = self.spoustec(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
        m = re.search(r"window id # (0x[0-9a-fA-F]+)", out or "")
        if not m or int(m.group(1), 16) == 0:
            return None
        out = self.spoustec(["xprop", "-id", m.group(1), "WM_CLASS"])
        tridy = re.findall(r'"([A-Za-z0-9._-]{1,128})"', out or "")
        return tridy[-1] if tridy else None

    def stav(self, s_oknem: bool = False) -> StavPc:
        ms = self.necinnost_ms()
        return StavPc(zamceno=self.zamceno(), necinnost_s=None if ms is None else ms / 1000.0,
                      okno=self.okno() if s_oknem else None)
