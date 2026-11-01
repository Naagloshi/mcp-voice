"""Detekce citlivých kontextů v transkriptu Claude Code (signály z konfigurace strazce.citlive_kontexty).

Čte jen jména nástrojů, cesty, příkaz Bash, zadání subagenta a pole vstupů z pravidel `vstupy`.
Výstupem jsou krátké popisy signálů (bez hodnot), které strážce ukáže v dotazu.
"""
from __future__ import annotations

import json
import os
import re
from fnmatch import fnmatchcase

from .config import CitlivyKontext

CESTY_NASTROJU = ("file_path", "path", "notebook_path")
NASTROJE_AGENTA = ("Agent", "Task")
PRIKAZ_SLASH = re.compile(r"<command-name>/?([A-Za-z0-9:._-]{1,100})</command-name>")


def _koren(cesta: str) -> str:
    return os.path.normpath(os.path.expanduser(cesta))


def pod(cesta: str, koren: str) -> bool:
    try:
        return os.path.commonpath([cesta, koren]) == koren
    except ValueError:
        return False


def _tvary_v_textu(koreny: list[str]) -> re.Pattern | None:
    """Regex na zmínku kořene v textu: absolutně, s ~/, $HOME/ i ${HOME}/, za ní konec jména."""
    domov = os.path.normpath(os.path.expanduser("~"))
    tvary = set()
    for k in koreny:
        tvary.add(k)
        if pod(k, domov) and k != domov:
            zbytek = os.path.relpath(k, domov)
            tvary.update(("~/" + zbytek, "$HOME/" + zbytek, "${HOME}/" + zbytek))
    if not tvary:
        return None
    vzor = "|".join(re.escape(t) for t in sorted(tvary, key=len, reverse=True))
    return re.compile(r"(?:%s)(?![A-Za-z0-9._-])" % vzor)


class Detektor:
    """Předpřipravené signály jednoho kontextu."""

    def __init__(self, kontext: CitlivyKontext):
        self.kontext = kontext
        koreny = []
        for c in kontext.cesty:
            k = _koren(c)
            for x in (k, os.path.realpath(k)):
                if x not in koreny:
                    koreny.append(x)
        self.koreny = koreny
        self.zminka = _tvary_v_textu(koreny)

    def cesta(self, hodnota: object, cwd: object = None) -> bool:
        if not self.koreny or not isinstance(hodnota, str) or not hodnota or "\x00" in hodnota:
            return False
        p = os.path.expanduser(hodnota)
        if not os.path.isabs(p):
            p = os.path.join(cwd if isinstance(cwd, str) and os.path.isabs(cwd) else "/", p)
        p = os.path.normpath(p)
        try:
            real = os.path.realpath(p)
        except (OSError, ValueError):
            real = p
        return any(pod(p, k) or pod(real, k) for k in self.koreny)

    def text(self, hodnota: object) -> bool:
        if not isinstance(hodnota, str) or not hodnota:
            return False
        if any(t in hodnota for t in self.kontext.texty):
            return True
        return self.zminka is not None and self.zminka.search(hodnota) is not None

    def volani(self, jmeno: object, vstup: object, cwd: object = None) -> list[str]:
        """Signály jednoho volání nástroje (prázdný seznam = nic)."""
        if not isinstance(jmeno, str):
            return []
        vstup = vstup if isinstance(vstup, dict) else {}
        k = self.kontext
        nalezy = []
        if jmeno.startswith("mcp__") and k.mcp:
            server, odd, _ = jmeno[5:].partition("__")
            if odd and server and any(fnmatchcase(server, g) for g in k.mcp):
                nalezy.append("MCP " + server)
        if jmeno in NASTROJE_AGENTA:
            typ = vstup.get("subagent_type")
            if isinstance(typ, str) and any(fnmatchcase(typ, g) for g in k.agenti):
                nalezy.append("agent " + typ)
            if self.text(vstup.get("prompt")):
                nalezy.append("zadání agenta")
        if jmeno == "Skill":
            sk = vstup.get("skill")
            if isinstance(sk, str) and self.skill(sk):
                nalezy.append("skill " + sk)
        if jmeno == "Bash" and self.text(vstup.get("command")):
            nalezy.append("příkaz Bash")
        if self.cesta(cwd):
            nalezy.append("pracovní adresář")
        if any(self.cesta(vstup.get(p), cwd) for p in CESTY_NASTROJU):
            nalezy.append("cesta v " + jmeno)
        for r in k.vstupy:
            if not fnmatchcase(jmeno, r.nastroj):
                continue
            for pole in r.pole:
                v = vstup.get(pole)
                if isinstance(v, str) and (v in r.hodnoty or any(v.startswith(p) for p in r.predpony)):
                    nalezy.append("vstup %s.%s" % (jmeno, pole))
        return list(dict.fromkeys(nalezy))

    def skill(self, jmeno: str) -> bool:
        return any(fnmatchcase(jmeno, g) or fnmatchcase(jmeno.split(":")[0], g) for g in self.kontext.skilly)


def signaly_radku(radek: bytes, detektory: list[Detektor]) -> dict[str, list[str]]:
    """Signály jednoho řádku transkriptu (JSONL): volání nástrojů a slash příkazy uživatele."""
    try:
        d = json.loads(radek)
    except ValueError:
        return {}
    if not isinstance(d, dict):
        return {}
    msg = d.get("message")
    obsah = msg.get("content") if isinstance(msg, dict) else None
    nalezy: dict[str, list[str]] = {}

    def pridej(nazev, signaly):
        if signaly:
            nalezy.setdefault(nazev, []).extend(signaly)

    if d.get("type") == "user":
        if isinstance(obsah, str):
            texty = [obsah]
        elif isinstance(obsah, list):
            texty = [it.get("text") for it in obsah if isinstance(it, dict) and it.get("type") == "text"]
        else:
            texty = []
        for t in texty:
            if isinstance(t, str) and "<command-name>" in t:
                for m in PRIKAZ_SLASH.finditer(t):
                    for det in detektory:
                        if det.skill(m.group(1)):
                            pridej(det.kontext.nazev, ["skill " + m.group(1)])
    if isinstance(obsah, list):
        for it in obsah:
            if isinstance(it, dict) and it.get("type") == "tool_use":
                for det in detektory:
                    pridej(det.kontext.nazev, det.volani(it.get("name"), it.get("input"), d.get("cwd")))
    return nalezy
