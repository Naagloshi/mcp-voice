"""Lockfily: bootstrap uv (stejná verze a hashe), hashe všech balíčků, build backend z locku, instalace v README."""
import re
import tomllib
from pathlib import Path

KOREN = Path(__file__).resolve().parents[1]


def blok_uv(jmeno: str) -> list[str]:
    """Řádky požadavku uv (verze a hashe) z lockfilu."""
    radky = (KOREN / jmeno).read_text(encoding="utf-8").splitlines()
    zacatek = next(i for i, r in enumerate(radky) if r.startswith("uv=="))
    blok = []
    for r in radky[zacatek:]:
        blok.append(r.strip())
        if not r.endswith("\\"):
            break
    return blok


def test_bootstrap_uv_shodny_s_locky():
    bootstrap = blok_uv("requirements-uv.lock")
    assert re.fullmatch(r"uv==\d+\.\d+\.\d+ \\", bootstrap[0])
    assert len(bootstrap) > 2 and all(r.startswith("--hash=sha256:") for r in bootstrap[1:])
    assert bootstrap == blok_uv("requirements.lock") == blok_uv("requirements-lokalni.lock")


def test_bootstrap_obsahuje_jen_uv():
    radky = (KOREN / "requirements-uv.lock").read_text(encoding="utf-8").splitlines()
    pozadavky = [r for r in radky if r and not r.startswith(("#", " "))]
    assert len(pozadavky) == 1 and pozadavky[0].startswith("uv==")


def balicky(jmeno: str) -> dict[str, tuple[str, list[str]]]:
    """Lockfile → {jméno: (verze, hashe)}; hlavička a komentáře `# via` se přeskočí."""
    vysledek: dict[str, tuple[str, list[str]]] = {}
    aktualni = None
    for r in (KOREN / jmeno).read_text(encoding="utf-8").splitlines():
        if r and not r.startswith(("#", " ")):
            m = re.fullmatch(r"([a-z0-9][a-z0-9._-]*)==(\S+) \\", r)
            assert m, f"{jmeno}: řádek bez přesné verze nebo bez hashe: {r!r}"
            aktualni = m.group(1)
            vysledek[aktualni] = (m.group(2), [])
        elif r.strip().startswith("--hash="):
            assert aktualni, r
            assert re.fullmatch(r"--hash=sha256:[0-9a-f]{64}( \\)?", r.strip()), r
            vysledek[aktualni][1].append(r.strip())
    return vysledek


LOCKY = ("requirements.lock", "requirements-lokalni.lock", "requirements-uv.lock")
INSTALACNI_LOCKY = ("requirements.lock", "requirements-lokalni.lock")


def test_kazdy_balicek_v_locku_ma_hash():
    for jmeno in LOCKY:
        b = balicky(jmeno)
        assert b, jmeno
        assert all(hashe for _, hashe in b.values()), jmeno


def test_build_backend_je_v_instalacnich_lockach():
    """`uv pip install --no-build-isolation -e .` bere build backend z venv: musí přijít z locku s hashi."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    def holy(r: Requirement) -> bool:
        return not r.extras and r.marker is None and r.url is None

    pyproject = tomllib.loads((KOREN / "pyproject.toml").read_text(encoding="utf-8"))
    build = pyproject["build-system"]
    assert build["build-backend"] == "hatchling.build"
    assert len(build["requires"]) == 1
    pozadavek = Requirement(build["requires"][0])
    assert canonicalize_name(pozadavek.name) == "hatchling" and pozadavek.specifier and holy(pozadavek)
    dev = [Requirement(d) for d in pyproject["project"]["optional-dependencies"]["dev"]]
    v_dev = [d for d in dev if canonicalize_name(d.name) == "hatchling"]
    assert len(v_dev) == 1 and holy(v_dev[0]), "hatchling v extra dev chybí, je víckrát nebo má extras/marker/URL"
    # SpecifierSet se porovnává významově: pořadí, mezery ani zápis verze (1.27 = 1.27.0) nehrají roli.
    assert v_dev[0].specifier == pozadavek.specifier, \
        "podmínka na hatchling v extra dev se liší od [build-system] requires"
    assert any(canonicalize_name(d.name) == "editables" for d in dev)
    for jmeno in INSTALACNI_LOCKY:
        b = balicky(jmeno)
        assert "hatchling" in b and "editables" in b, jmeno
        assert b["hatchling"][0] in pozadavek.specifier, jmeno


def test_readme_instaluje_jen_z_locku():
    """Každá instalace v README ověřuje hashe; editovatelná instalace nic nestahuje (build backend z venv)."""
    radky = [r.strip() for r in (KOREN / "README.md").read_text(encoding="utf-8").splitlines()]
    instalace = [r for r in radky if re.search(r"\b(uv pip|pip) install\b", r)]
    editovatelne = [r for r in instalace if r.endswith(" -e .")]
    z_locku = [r for r in instalace if " -r " in r]
    assert len(editovatelne) >= 2 and len(z_locku) >= 3
    assert set(editovatelne) | set(z_locku) == set(instalace), "instalace mimo lock nebo editovatelný projekt"
    for r in editovatelne:
        assert all(p in r.split() for p in ("--offline", "--no-build-isolation", "--no-deps")), r
    for r in z_locku:
        assert "--require-hashes" in r.split(), r
