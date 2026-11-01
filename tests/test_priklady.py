"""examples/settings-hooks.json: jediný vzor hooků, platný celek pro plnou instalaci a shodný s kódem."""
import json
import re
import tomllib
from pathlib import Path

KOREN = Path(__file__).resolve().parents[1]
UDALOSTI_ROZSAHU = {"pre", "post", "subagent-start", "subagent-stop", "notifikace", "stop", "clear", "konec"}


def _vzor() -> dict:
    return json.loads((KOREN / "examples" / "settings-hooks.json").read_text(encoding="utf-8"))


def _hooky():
    for udalost, skupiny in _vzor()["hooks"].items():
        for skupina in skupiny:
            for h in skupina["hooks"]:
                yield udalost, skupina.get("matcher"), h


def test_vzor_ma_jen_klic_hooks():
    assert set(_vzor()) == {"hooks"}


def test_prikazy_vzoru_existuji():
    skripty = set(tomllib.loads((KOREN / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"])
    for udalost, matcher, h in _hooky():
        assert h["type"] == "command" and isinstance(h["timeout"], int), (udalost, matcher)
        prikaz = h["command"]
        if prikaz.startswith("VENV/bin/"):
            assert prikaz.split("/")[-1] in skripty, prikaz
            continue
        m = re.fullmatch(r"/usr/bin/python3 -I PKG/rozsah_hook\.py --udalost ([a-z-]+) --stav STAV", prikaz)
        assert m and m.group(1) in UDALOSTI_ROZSAHU, prikaz
        assert (KOREN / "src" / "mcp_voice" / "rozsah_hook.py").is_file()


def test_pre_tool_use_blokuje_pri_selhani():
    pre = [(m, h) for u, m, h in _hooky() if u == "PreToolUse"]
    assert {m for m, _ in pre} == {".*", "mcp__hlas__.*"}
    assert all(h.get("onFailure") == "block" for _, h in pre)
    assert all(h.get("onFailure") is None for u, _, h in _hooky() if u != "PreToolUse")


def test_readme_nema_druhy_vzor_hooku():
    readme = (KOREN / "README.md").read_text(encoding="utf-8")
    bloky = re.findall(r"```json\n(.*?)```", readme, re.S)
    assert not [b for b in bloky if '"hooks"' in b or '"matcher"' in b or "mcp-voice-guard" in b]
    assert "examples/settings-hooks.json" in readme
