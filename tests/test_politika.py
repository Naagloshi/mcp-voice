"""politika.py: tvrdé zákazy (G1), chráněné cesty (H2), režimy, obal bwrap."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from mcp_voice import politika


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    for d in (".ssh", ".claude", ".config/mcp-voice", "proj/.git/hooks", "proj/src", "venku"):
        (h / d).mkdir(parents=True, exist_ok=True)
    (h / ".ssh" / "id_ed25519").write_text("x")
    (h / ".netrc").write_text("x")
    (h / ".bashrc").write_text("x")
    (h / "proj" / ".env").write_text("x")
    (h / "proj" / "CLAUDE.md").write_text("x")
    (h / "proj" / "src" / "a.py").write_text("x")
    os.symlink(h / ".ssh", h / "proj" / "odkaz")
    return h


def pol(home, tmp_path, **kw):
    arg = dict(registrace="hlas", chranene=politika.CHRANENE_VYCHOZI, chranene_zapis=politika.CHRANENE_ZAPIS_VYCHOZI,
               stav=str(tmp_path / "stav"), home=str(home), env={"XDG_RUNTIME_DIR": str(tmp_path / "run")}, uid=1000,
               vlastni=())
    arg.update(kw)
    return politika.sestav(**arg)


def r(p, rezim, nastroj, vstup, cwd):
    return politika.rozhodni(p, rezim, nastroj, vstup, str(cwd), znacka="4821")


ZAKAZANE = [("SendMessage", {"to": "x"}), ("ListAgents", {}), ("CronCreate", {}), ("ScheduleWakeup", {}),
            ("RemoteTrigger", {}), ("Monitor", {}), ("ExitPlanMode", {}), ("AskUserQuestion", {}),
            ("Agent", {"prompt": "x", "run_in_background": True}), ("Bash", {"command": "ls", "run_in_background": True}),
            ("mcp__hlas__prepis", {"cesta": "/x", "engine": "cloud"}), ("TaskStop", {}), ("EnterWorktree", {})]


@pytest.mark.parametrize("rezim", ["cteni", "prace", "plny", "omezeny"])
@pytest.mark.parametrize("nastroj, vstup", ZAKAZANE)
def test_tvrde_zakazy_ve_vsech_rezimech(home, tmp_path, rezim, nastroj, vstup):
    v = r(pol(home, tmp_path), rezim, nastroj, vstup, home / "proj")
    assert v["rozhodnuti"] == "deny"


def test_rozsireni_zakazanych(home, tmp_path):
    p = pol(home, tmp_path, zakazane_navic=("mcp__notes__smaz",))
    assert r(p, "plny", "mcp__notes__smaz", {}, home)["rozhodnuti"] == "deny"


@pytest.mark.parametrize("rezim", ["cteni", "prace", "plny"])
@pytest.mark.parametrize("cesta", ["~/.ssh/x", "~/.claude/.credentials.json", "/proc/self/environ", ".env",
                                   "a.pem", "odkaz/id_ed25519", "src/../../.ssh/config", "~/.ssh/neexistuje/hluboko",
                                   "$HOME/.netrc", "~/.config/mcp-voice/config.yaml", "x/credentials.json"])
def test_chranene_cesty_read(home, tmp_path, rezim, cesta):
    assert r(pol(home, tmp_path), rezim, "Read", {"file_path": cesta}, home / "proj")["rozhodnuti"] == "deny"


@pytest.mark.parametrize("cesta", [
    "~/.codex/auth.json", "~/.gemini/google_accounts.json", "~/.config/gh/hosts.yml", "~/.config/Claude/config.json",
    "~/.thunderbird/x/logins.json", "~/.config/evolution/sources/a", "~/.local/share/evolution/mail/x",
    "~/.config/Signal/config.json", "~/.local/share/TelegramDesktop/tdata/x", "~/.config/Slack/Cookies",
    "~/.config/discord/Local Storage/x", "~/.config/microsoft-edge/Default/Cookies",
    "~/.config/google-chrome-for-testing/x", "~/.config/BraveSoftware/x", "~/.config/rclone/rclone.conf",
    "~/.config/filezilla/sitemanager.xml", "~/.local/share/remmina/x.remmina", "~/.vault-token",
    "~/.bash_history", "~/.zsh_history", "~/.python_history", "~/.lesshst", "~/.histfile", "~/.viminfo",
    "~/.local/share/fish/fish_history", "~/.local/state/nvim/swap/x.swp", "~/.cache/cliphist/db",
    "src/.psql_history", "~/.config/app-autocommit.env", "deploy/prod.env", ".envrc", "web/.npmrc", "x/.pypirc"])
def test_chranene_vychozi_rozsirene(home, tmp_path, cesta):
    """úložiště tajemství a historie, které výchozí seznam dřív nepokrýval (čtení i zápis)."""
    p = pol(home, tmp_path)
    assert r(p, "cteni", "Read", {"file_path": cesta}, home / "proj")["rozhodnuti"] == "deny"
    assert r(p, "plny", "Write", {"file_path": cesta, "content": "x"}, home / "proj")["rozhodnuti"] == "deny"


@pytest.mark.parametrize("cesta", ["src/environment.py", "docs/history.md", "src/a.py", "venv.txt", "src/env.py"])
def test_chranene_vychozi_bez_falesnych_shod(home, tmp_path, cesta):
    assert r(pol(home, tmp_path), "cteni", "Read", {"file_path": cesta}, home / "proj")["rozhodnuti"] == "allow"


def test_stav_a_socket_chranene(home, tmp_path):
    p = pol(home, tmp_path)
    assert r(p, "plny", "Read", {"file_path": str(tmp_path / "stav" / "telegram" / "okno.json")}, home)["rozhodnuti"] == "deny"
    assert r(p, "plny", "Read", {"file_path": str(tmp_path / "run" / "cc-socks" / "1.sock")}, home)["rozhodnuti"] == "deny"
    assert r(p, "plny", "Read", {"file_path": "/tmp/cc-socks-1000/1.sock"}, home)["rozhodnuti"] == "deny"


def test_grep(home, tmp_path):
    p = pol(home, tmp_path)
    v = r(p, "prace", "Grep", {"pattern": "x"}, home)  # bez path v ~ = předek ~/.ssh
    assert v["rozhodnuti"] == "deny"
    v = r(p, "prace", "Grep", {"pattern": "x"}, home / "proj" / "src")
    assert v["rozhodnuti"] == "allow" and v["vstup"] == {"pattern": "x", "output_mode": "files_with_matches"}
    # G7 neověřeno: obsahový výstup se zamítá (updatedInput s allow nemusí platit)
    v = r(p, "prace", "Grep", {"pattern": "x", "output_mode": "content"}, home / "proj" / "src")
    assert v["rozhodnuti"] == "deny" and "files_with_matches" in v["duvod"]
    assert r(p, "plny", "Grep", {"pattern": "x", "path": "/"}, home)["rozhodnuti"] == "deny"
    assert r(p, "plny", "Glob", {"pattern": "*", "path": "~/.ssh"}, home)["rozhodnuti"] == "deny"
    assert r(p, "cteni", "Glob", {"pattern": "**/*.py"}, home / "proj")["rozhodnuti"] == "allow"


@pytest.mark.parametrize("cesta", [".claude/settings.json", ".git/hooks/pre-commit", ".git/config", "CLAUDE.md",
                                   "sub/AGENTS.md", ".mcp.json", "~/.bashrc", "~/.local/bin/x", "~/.config/systemd/u.service"])
def test_chraneny_zapis(home, tmp_path, cesta):
    p = pol(home, tmp_path)
    assert r(p, "plny", "Write", {"file_path": cesta, "content": "x"}, home / "proj")["rozhodnuti"] == "deny"
    assert r(p, "plny", "Read", {"file_path": cesta}, home / "proj")["rozhodnuti"] is None or cesta.startswith("~/.local")


def test_mcp_s_vnorenou_cestou(home, tmp_path):
    p = pol(home, tmp_path)
    v = r(p, "plny", "mcp__fs__read", {"args": {"files": ["/tmp/a", "~/.ssh/id_ed25519"]}}, home)
    assert v["rozhodnuti"] == "deny"
    assert r(p, "plny", "mcp__fs__read", {"args": {"files": ["/tmp/a"]}}, home)["rozhodnuti"] == "ask"
    assert r(p, "prace", "mcp__fs__read", {"args": {"files": ["/tmp/a"]}}, home)["rozhodnuti"] == "ask"


def test_odebrat_vychozi_a_pridat(home, tmp_path):
    chranene = politika.efektivni(politika.CHRANENE_VYCHOZI, ("~/tajne",), ("*.pem",))
    p = pol(home, tmp_path, chranene=chranene)
    assert r(p, "plny", "Read", {"file_path": "a.pem"}, home / "proj")["rozhodnuti"] is None
    assert r(p, "plny", "Read", {"file_path": "~/tajne/x"}, home / "proj")["rozhodnuti"] == "deny"


def test_rezim_cteni(home, tmp_path):
    p = pol(home, tmp_path)
    assert r(p, "cteni", "Bash", {"command": "ls"}, home / "proj")["rozhodnuti"] == "deny"
    assert r(p, "cteni", "Read", {"file_path": "src/a.py"}, home / "proj")["rozhodnuti"] == "allow"
    assert r(p, "cteni", "mcp__hlas__odpovez", {"text": "x"}, home)["rozhodnuti"] == "allow"
    assert r(p, "cteni", "WebFetch", {"url": "https://example.invalid"}, home)["rozhodnuti"] == "deny"


def test_koreny_volitelne(home, tmp_path):
    p = pol(home, tmp_path)  # O3: bez kořenů jen chráněné cesty
    assert r(p, "cteni", "Read", {"file_path": "~/venku/x"}, home / "proj")["rozhodnuti"] == "allow"
    p = pol(home, tmp_path, koreny=(str(home / "proj"),))
    assert r(p, "cteni", "Read", {"file_path": "~/venku/x"}, home / "proj")["rozhodnuti"] == "deny"
    assert r(p, "prace", "Read", {"file_path": "~/venku/x"}, home / "proj")["rozhodnuti"] == "ask"
    assert r(p, "prace", "Read", {"file_path": "src/a.py"}, home / "proj")["rozhodnuti"] == "allow"


def test_rezim_prace(home, tmp_path):
    p = pol(home, tmp_path)
    v = r(p, "prace", "Bash", {"command": "git pull"}, home / "proj")
    assert v["rozhodnuti"] == "ask" and "Telegram #4821 · práce: Bash `git pull`" in v["duvod"]
    assert v["vstup"] is None  # bez bwrap bez masky
    assert r(p, "prace", "Write", {"file_path": "x.txt", "content": "a"}, home / "proj")["rozhodnuti"] == "ask"
    assert r(p, "prace", "WebFetch", {"url": "https://example.invalid"}, home)["rozhodnuti"] == "ask"
    assert r(p, "prace", "mcp__jiny__zapis", {}, home)["rozhodnuti"] == "ask"
    p = pol(home, tmp_path, pri_dotazu="odmitnout")
    v = r(p, "prace", "Bash", {"command": "ls"}, home / "proj")
    assert v["rozhodnuti"] == "deny" and "u PC" in v["duvod"]
    p = pol(home, tmp_path, bez_rc=True)
    assert r(p, "prace", "Bash", {"command": "ls"}, home / "proj")["rozhodnuti"] == "deny"


@pytest.mark.parametrize("prepinac, overena", [(False, True), (True, False)])
def test_rezim_plny_bez_g7_bash_ask(home, tmp_path, monkeypatch, prepinac, overena):
    """K1/G7: bez ověřeného updatedInput (přepínač nebo neověřená verze CC) se Bash v plny ptá s obalem."""
    monkeypatch.setattr(politika, "BASH_UPDATED_INPUT_OVERENO", prepinac)
    p = pol(home, tmp_path, bwrap="/usr/bin/bwrap", overena_verze=overena)
    v = r(p, "plny", "Bash", {"command": "ls -la", "description": "výpis"}, home / "proj")
    assert v["rozhodnuti"] == "ask" and "v izolaci bwrap" in v["duvod"]
    assert v["vstup"]["description"] == "výpis" and v["vstup"]["command"].startswith("/usr/bin/bwrap ")


def test_g7_prepinace():
    assert politika.BASH_UPDATED_INPUT_OVERENO is True and politika.GREP_UPDATED_INPUT_OVERENO is False


def test_rezim_plny_bash_vychozi_ask(home, tmp_path):
    """bez vědomé volby bash_bez_dotazu se Bash v plny ptá (s obalem), i na ověřené verzi."""
    v = r(pol(home, tmp_path, bwrap="/usr/bin/bwrap", overena_verze=True), "plny", "Bash", {"command": "ls"},
          home / "proj")
    assert v["rozhodnuti"] == "ask" and v["vstup"]["command"].startswith("/usr/bin/bwrap ")


def test_rezim_plny(home, tmp_path):
    p = pol(home, tmp_path, bwrap="/usr/bin/bwrap", overena_verze=True, bash_bez_dotazu=True)
    v = r(p, "plny", "Bash", {"command": "ls -la", "description": "výpis"}, home / "proj")
    assert v["rozhodnuti"] == "allow" and v["vstup"]["description"] == "výpis"
    argv = shlex.split(v["vstup"]["command"])
    assert argv[:5] == ["/usr/bin/bwrap", "--unshare-all", "--new-session", "--die-with-parent", "--clearenv"]
    assert argv[-6:] == ["--chdir", str(home / "proj"), "--", "/bin/bash", "-c", "ls -la"]
    p = pol(home, tmp_path)
    assert r(p, "plny", "Bash", {"command": "ls"}, home / "proj")["rozhodnuti"] == "ask"
    assert r(p, "plny", "NovyNastroj", {}, home)["rozhodnuti"] == "ask"
    assert r(p, "plny", "Write", {"file_path": "x.txt"}, home / "proj")["rozhodnuti"] is None
    # S3: MCP mimo výslovný seznam se v plny ptá
    assert r(p, "plny", "mcp__jiny__x", {}, home)["rozhodnuti"] == "ask"
    p = pol(home, tmp_path, cteni_navic=("mcp__jiny__x",))
    assert r(p, "plny", "mcp__jiny__x", {}, home)["rozhodnuti"] is None


def test_omezeny(home, tmp_path):
    p = pol(home, tmp_path)
    om = lambda n, v: politika.rozhodni(p, "omezeny", n, v, str(home / "proj"), trida="cizi")  # noqa: E731
    assert om("mcp__hlas__odpovez", {"text": "x"})["rozhodnuti"] == "deny"
    assert om("mcp__hlas__rekni", {"text": "x"})["rozhodnuti"] == "deny"
    assert om("Read", {"file_path": "src/a.py"})["rozhodnuti"] == "allow"
    assert om("Bash", {"command": "ls"})["rozhodnuti"] == "deny"
    assert om("mcp__hlas__stav", {})["rozhodnuti"] == "allow"


def trojice(argv):
    return list(zip(argv, argv[1:], argv[2:]))


@pytest.fixture
def bez_tmp(monkeypatch):
    """Testovací domov leží v /tmp, který je v obalu soukromý; pro kontrolu argv ho nevynechávat."""
    monkeypatch.setattr(politika, "ZAKRYTE", ("/proc", "/run", "/dev"))


def test_bwrap_obal(home, tmp_path, bez_tmp):
    p = pol(home, tmp_path)
    argv = shlex.split(politika.obal_bwrap("bwrap", "echo 'a b' && cat x", str(home / "proj"), p))
    assert argv[-1] == "echo 'a b' && cat x"
    dvojice = list(zip(argv, argv[1:]))
    # celý domov skrytý, zpět jen pracovní adresář; chráněné cesty domova není třeba maskovat
    assert ("--tmpfs", str(home)) in dvojice
    assert argv.index(str(home)) < argv.index("--bind")
    assert ("--tmpfs", str(home / ".ssh")) not in dvojice and ("--ro-bind", "/dev/null", str(home / ".netrc")) not in trojice(argv)
    for x in politika.SKRYTE_VYCHOZI:
        if os.path.isdir(x) and not os.path.islink(x) and os.path.realpath(x) != "/":
            assert ("--tmpfs", os.path.realpath(x)) in dvojice, x
    assert "/proc" not in [b for a, b in dvojice if a == "--tmpfs"]
    # kořen jen pro čtení, zapisovatelný je jen pracovní adresář (bez .git hooků a CLAUDE.md)
    assert ("--ro-bind", "/", "/") in trojice(argv)
    assert ("--bind", str(home / "proj"), str(home / "proj")) in trojice(argv)
    assert ("--ro-bind", str(home / "proj" / "CLAUDE.md")) in dvojice
    assert ("--ro-bind", str(home / "proj" / ".git" / "hooks")) in dvojice
    assert ("--ro-bind", "/dev/null", str(home / "proj" / ".env")) in trojice(argv)  # chráněné jméno v cwd


def test_bwrap_domov_nastroje_jen_pro_cteni(home, tmp_path, bez_tmp):
    for d in (".local/bin", ".cargo/bin", ".nvm/versions"):
        (home / d).mkdir(parents=True)
    p = pol(home, tmp_path, bash_domov=("~/.local/bin", "~/.cargo/bin", "~/.nvm", "~/.ssh", "~/.claude", "~/neni"))
    argv = shlex.split(politika.obal_bwrap("bwrap", "ls", str(home / "proj"), p))
    t = trojice(argv)
    for d in (".local/bin", ".cargo/bin", ".nvm"):
        assert ("--ro-bind", str(home / d), str(home / d)) in t, d
    assert ("--ro-bind", str(home / ".ssh"), str(home / ".ssh")) not in t  # chráněná cesta se nevrátí
    assert ("--ro-bind", str(home / ".claude"), str(home / ".claude")) not in t
    assert argv.index("--tmpfs", argv.index(str(home)) - 1) < argv.index(str(home / ".local" / "bin"))


def test_bwrap_koreny(home, tmp_path, bez_tmp):
    """bash_koreny režimu zpřístupní další adresáře (rw/ro); chráněná jména a zápis platí i v nich."""
    for d in ("jiny/sub/.claude", "druhy"):
        (home / d).mkdir(parents=True)
    (home / "jiny" / ".env").write_text("x")
    (home / "jiny" / "sub" / "CLAUDE.md").write_text("x")
    kor = (("plny", "~/jiny", "rw"), ("plny", "~/druhy", "ro"), ("prace", "~/druhy", "rw"),
           ("plny", "~", "rw"), ("plny", "~/.ssh", "ro"), ("plny", "~/.config", "ro"))
    p = pol(home, tmp_path, bash_koreny=kor)
    assert p["bash_koreny"] == {"plny": [[str(home / "jiny"), "rw"], [str(home / "druhy"), "ro"]],
                                "prace": [[str(home / "druhy"), "rw"]]}
    t = trojice(shlex.split(politika.obal_bwrap("bwrap", "ls", str(home / "proj"), p, "plny")))
    assert ("--bind", str(home / "jiny"), str(home / "jiny")) in t
    assert ("--ro-bind", str(home / "druhy"), str(home / "druhy")) in t
    assert ("--ro-bind", "/dev/null", str(home / "jiny" / ".env")) in t
    for c in ("jiny/sub/.claude", "jiny/sub/CLAUDE.md"):
        assert ("--ro-bind", str(home / c), str(home / c)) in t, c
    t = trojice(shlex.split(politika.obal_bwrap("bwrap", "ls", str(home / "proj"), p, "prace")))
    assert ("--bind", str(home / "druhy"), str(home / "druhy")) in t and str(home / "jiny") not in sum(t, ())
    t = trojice(shlex.split(politika.obal_bwrap("bwrap", "ls", str(home / "proj"), p, "cteni")))
    assert str(home / "druhy") not in sum(t, ())


@pytest.mark.parametrize("cesta, chyba", [("~", "obsahuje"), ("~/.ssh", "chráněná"), ("~/.ssh/x", "chráněná"),
                                          ("~/.config", "obsahuje chráněnou"), ("~/proj/.claude", "chráněná"),
                                          ("/", "absolutní"), ("~/proj", None), ("/opt/x", None)])
def test_koren_bash_chyba(home, tmp_path, cesta, chyba):
    p = pol(home, tmp_path)
    c = politika.rozbal_cestu(cesta, str(home), {}, str(tmp_path / "stav"), 1000)
    vysledek = politika.koren_bash_chyba(c, p)
    assert (vysledek is None) if chyba is None else (chyba in vysledek)


def test_bwrap_vnorena_konfigurace(home, tmp_path, bez_tmp):
    """vnořené .claude/, CLAUDE.md, .mcp.json, AGENTS.md, .vscode … v libovolné hloubce jen pro čtení;
    u symlinku jen pro čtení cíl."""
    proj = home / "proj"
    for d in ("sub/.claude", "a/b/c", "sub/.vscode", "docs"):
        (proj / d).mkdir(parents=True)
    for f in ("sub/CLAUDE.md", "a/b/c/.mcp.json", "a/b/CLAUDE.local.md", "a/AGENTS.md", "docs/agenti.txt"):
        (proj / f).write_text("x")
    os.symlink(proj / "docs" / "agenti.txt", proj / "a" / "b" / "c" / "CLAUDE.md")
    t = trojice(shlex.split(politika.obal_bwrap("bwrap", "ls", str(proj), pol(home, tmp_path))))
    for c in ("sub/.claude", "sub/CLAUDE.md", "a/b/c/.mcp.json", "a/b/CLAUDE.local.md", "a/AGENTS.md",
              "sub/.vscode", "docs/agenti.txt"):
        assert ("--ro-bind", str(proj / c), str(proj / c)) in t, c


def test_bwrap_cwd_domov_a_velky_strom(home, tmp_path, monkeypatch):
    """pracovní adresář = domov nebo jeho předek by vrátil celý domov → obal None (Bash se ptá)."""
    p = pol(home, tmp_path)
    for cwd in (home, tmp_path, "/"):
        assert politika.obal_bwrap("bwrap", "ls", str(cwd), p) is None
    for limit, hodnota in (("SKEN_MAX_POLOZEK", 3), ("SKEN_MAX_HLOUBKA", 1), ("SKEN_MAX_S", 0.0)):
        with monkeypatch.context() as m:
            m.setattr(politika, limit, hodnota)
            assert politika.obal_bwrap("bwrap", "ls", str(home / "proj"), p) is None, limit
    monkeypatch.setattr(politika, "SKEN_MAX_POLOZEK", 3)
    v = r(pol(home, tmp_path, bwrap="/usr/bin/bwrap", overena_verze=True, bash_bez_dotazu=True), "plny", "Bash",
          {"command": "ls"}, home / "proj")
    assert v["rozhodnuti"] == "ask" and v["vstup"] is None and "bez izolace" in v["duvod"]


def test_sken_vendor_prochazi_node_modules_ne(home, tmp_path):
    proj = home / "proj"
    for d in ("vendor/balik/certs", "node_modules/x", "a/.cache"):
        (proj / d).mkdir(parents=True)
    for f in ("vendor/balik/certs/key.pem", "node_modules/x/.env", "a/.cache/token.secret"):
        (proj / f).write_text("x")
    sken = politika.prohledej(str(proj), pol(home, tmp_path))
    assert sken["uplne"] and str(proj / "vendor/balik/certs/key.pem") in sken["chranene"]
    assert str(proj / "a/.cache/token.secret") in sken["chranene"]
    assert str(proj / "node_modules/x/.env") not in sken["chranene"]
    assert {str(proj / "vendor"), str(proj / "node_modules")} <= set(sken["zavislosti"])


def test_bwrap_k1_izolace(home, tmp_path, bez_tmp):
    """K1: bez sítě, IPC a PID, nová session, čisté prostředí, maska /run, /tmp a runtime adresáře."""
    (tmp_path / "run").mkdir()
    env = {"XDG_RUNTIME_DIR": str(tmp_path / "run"), "PATH": "/usr/bin:relativni:/bin", "LANG": "cs_CZ.UTF-8",
           "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus", "SSH_AUTH_SOCK": "/tmp/a", "DISPLAY": ":0",
           "CLAUDE_CODE_MESSAGING_SOCKET": "/x"}
    argv = shlex.split(politika.obal_bwrap("bwrap", "ls", str(home / "proj"), pol(home, tmp_path, env=env)))
    for prepinac in ("--unshare-all", "--new-session", "--die-with-parent", "--clearenv"):
        assert prepinac in argv
    assert "--share-net" not in argv and "--dev-bind" not in argv
    dvojice = list(zip(argv, argv[1:]))
    for maska in ("/tmp", "/run", str(tmp_path / "run")):
        assert ("--tmpfs", maska) in dvojice
    assert ("--dev", "/dev") in dvojice and ("--proc", "/proc") in dvojice
    prostredi = {a: b for x, a, b in trojice(argv) if x == "--setenv"}
    assert prostredi["PATH"] == "/usr/bin:/bin" and prostredi["HOME"] == str(home)
    assert prostredi["LANG"] == "cs_CZ.UTF-8"
    assert not {"DBUS_SESSION_BUS_ADDRESS", "SSH_AUTH_SOCK", "DISPLAY", "CLAUDE_CODE_MESSAGING_SOCKET"} & set(prostredi)
    argv = shlex.split(politika.obal_bwrap("bwrap", "ls", str(home / "proj"), pol(home, tmp_path, bwrap_sit=True)))
    assert argv[:3] == ["bwrap", "--unshare-all", "--share-net"]


def test_bwrap_vnorene_repo_a_zavislosti(home, tmp_path, bez_tmp):
    for d in ("proj/sub/.git/hooks", "proj/.venv/lib", "proj/web/node_modules/x"):
        (home / d).mkdir(parents=True)
    (home / "proj" / "sub" / ".git" / "config").write_text("x")
    argv = shlex.split(politika.obal_bwrap("bwrap", "ls", str(home / "proj"), pol(home, tmp_path)))
    dvojice = list(zip(argv, argv[1:]))
    for c in ("sub/.git/hooks", "sub/.git/config", ".venv", "web/node_modules"):
        assert ("--ro-bind", str(home / "proj" / c)) in dvojice, c


BWRAP = shutil.which("bwrap")


def _bwrap_jede():
    if not BWRAP:
        return False
    try:
        return subprocess.run(politika.zaklad_bwrap(BWRAP) + ["true"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


SONDA = """\
echo "env=[$DBUS_SESSION_BUS_ADDRESS$SSH_AUTH_SOCK$DISPLAY$WAYLAND_DISPLAY$XDG_RUNTIME_DIR$CLAUDE_CODE_MESSAGING_SOCKET]"
test -e /run/user/{uid}/bus && echo bus=ano || echo bus=ne
test -e /tmp/.X11-unix && echo x11=ano || echo x11=ne
test -e /run/docker.sock && echo docker=ano || echo docker=ne
echo "sit=$(tail -n +3 /proc/net/dev | cut -d: -f1 | tr -d ' ' | tr '\\n' ,)"
echo ok > novy.txt && echo cwd=zapis
echo x > {venku} 2>/dev/null && echo venku=zapis || echo venku=ne
echo x >> CLAUDE.md 2>/dev/null && echo claude=zapis || echo claude=ne
echo x >> src/a.py 2>/dev/null && echo balicek=zapis || echo balicek=ne
echo "env_soubor=$(cat .env 2>/dev/null | wc -c)"
"""


@pytest.mark.skipif(not _bwrap_jede(), reason="bwrap s --unshare-all tu nejde spustit")
def test_bwrap_skutecny_obal(home, tmp_path):
    """K1 a V1 integračně (jen čtecí dotazy a zápis do tmp): uvnitř nejsou proměnné ani sockety session,
    síť, /tmp hostitele, zápis mimo pracovní adresář ani do chráněného zápisu v něm."""
    uid = os.getuid()
    venku = Path(__file__).parent / (".bwrap-sonda-%d" % os.getpid())  # mimo /tmp: skutečný souborový systém
    p = pol(home, tmp_path, env=dict(os.environ), uid=uid, vlastni=(str(home / "proj" / "src"),))
    obal = politika.obal_bwrap(BWRAP, SONDA.format(uid=uid, venku=venku), str(home / "proj"), p)
    try:
        v = subprocess.run(["/bin/bash", "-c", obal], capture_output=True, text=True, timeout=20)
        venku_existuje = venku.exists()
    finally:
        venku.unlink(missing_ok=True)
    assert v.returncode == 0, v.stderr
    vystup = dict(x.split("=", 1) for x in v.stdout.split() if "=" in x)
    assert vystup["env"] == "[]" and vystup["bus"] == "ne" and vystup["x11"] == "ne" and vystup["docker"] == "ne"
    assert vystup["sit"] == "lo,"
    assert vystup["cwd"] == "zapis" and (home / "proj" / "novy.txt").read_text() == "ok\n"
    assert vystup["venku"] == "ne" and not venku_existuje
    assert vystup["claude"] == "ne" and vystup["balicek"] == "ne" and vystup["env_soubor"] == "0"


# ---------------------------------------------------------------- V1: vlastní instalace a persistence

def test_vlastni_instalace_odvozena_za_behu():
    import sys
    vl = politika.vlastni_instalace()
    assert os.path.dirname(os.path.realpath(politika.__file__)) in vl
    if sys.prefix != sys.base_prefix:
        assert sys.prefix in vl


@pytest.mark.parametrize("rezim", ["cteni", "prace", "plny"])
def test_zapis_do_balicku_a_venv_zamitnut(home, tmp_path, rezim):
    pkg = home / "venku" / "pkg"
    p = pol(home, tmp_path, vlastni=(str(pkg),))
    for nastroj in ("Write", "Edit"):
        v = r(p, rezim, nastroj, {"file_path": str(pkg / "politika.py"), "content": "x"}, home / "proj")
        assert v["rozhodnuti"] == "deny"
    p = pol(home, tmp_path, vlastni=None)  # výchozí: skutečný balíček
    cil = os.path.join(os.path.dirname(os.path.realpath(politika.__file__)), "politika.py")
    assert r(p, rezim, "Write", {"file_path": cil, "content": "x"}, home / "proj")["rozhodnuti"] == "deny"
    assert r(p, rezim, "Bash", {"command": "sed -i s/a/b/ " + cil}, home / "proj")["rozhodnuti"] == "deny"


def test_balicek_v_cwd_jen_pro_cteni_v_bwrap(home, tmp_path):
    p = pol(home, tmp_path, vlastni=(str(home / "proj" / "src"),))
    argv = shlex.split(politika.obal_bwrap("bwrap", "ls", str(home / "proj"), p))
    assert ("--ro-bind", str(home / "proj" / "src"), str(home / "proj" / "src")) in trojice(argv)


@pytest.mark.parametrize("cesta", ["~/.gitconfig", "~/.config/git/config", "~/venku/.git/hooks/post-checkout",
                                   "~/venku/.git/config", "~/.bash_aliases", "~/.bash_login", "~/.zprofile",
                                   "~/.config/autostart/x.desktop", "~/.config/systemd/user/x.service",
                                   "~/.local/bin/git", "~/.config/environment.d/x.conf", "~/.config/fish/config.fish",
                                   "~/.local/share/applications/x.desktop", "/var/spool/cron/crontabs/u"])
@pytest.mark.parametrize("rezim", ["prace", "plny"])
def test_persistence_chraneny_zapis(home, tmp_path, cesta, rezim):
    v = r(pol(home, tmp_path), rezim, "Write", {"file_path": cesta, "content": "x"}, home / "proj")
    assert v["rozhodnuti"] == "deny", cesta


@pytest.mark.parametrize("prikaz", ["crontab -l", "ls; crontab x", "X=1 systemd-run --user sh", "echo x | at now",
                                    "git config --global core.hooksPath /x # ~/.gitconfig"])
def test_bash_persistence(home, tmp_path, prikaz):
    assert r(pol(home, tmp_path, bwrap="/usr/bin/bwrap"), "plny", "Bash", {"command": prikaz},
             home / "proj")["rozhodnuti"] == "deny"


# ---------------------------------------------------------------- S1, S3

@pytest.mark.parametrize("cesta", ["Secrets.yaml", "client_Secret.json", ".ENV", "Credentials.json", "ID_RSA",
                                   "x/KEY.PEM"])
def test_vzory_bez_ohledu_na_velikost(home, tmp_path, cesta):
    assert r(pol(home, tmp_path), "cteni", "Read", {"file_path": cesta}, home / "proj")["rozhodnuti"] == "deny"


@pytest.mark.parametrize("vstup", [{"pathInProject": ".env"}, {"path": "config/secrets.yaml"},
                                   {"url": "file:///etc/x/.env"}, {"uri": "FILE://%s/.ssh/id_ed25519"}])
def test_mcp_relativni_cesta_a_file_url(home, tmp_path, vstup):
    vstup = {k: v.replace("%s", str(home)) for k, v in vstup.items()}
    for rezim in ("prace", "plny"):
        assert r(pol(home, tmp_path), rezim, "mcp__ide__cti", vstup, home / "proj")["rozhodnuti"] == "deny"
    assert r(pol(home, tmp_path), "plny", "mcp__ide__cti", {"text": "obyčejná věta. Bez cesty"},
             home / "proj")["rozhodnuti"] == "ask"


def test_grep_orakulum_chranenych_souboru(home, tmp_path):
    p = pol(home, tmp_path)
    proj = home / "proj"
    # glob míří na chráněný vzor
    assert r(p, "cteni", "Grep", {"pattern": "^K=a", "glob": "**/.env"}, proj / "src")["rozhodnuti"] == "deny"
    assert r(p, "cteni", "Grep", {"pattern": "a", "glob": "*.{py,pem}"}, proj / "src")["rozhodnuti"] == "deny"
    # strom obsahuje .env: bez glob čtení zamítnuto, práce a plný se ptá
    assert r(p, "cteni", "Grep", {"pattern": "^K=a"}, proj)["rozhodnuti"] == "deny"
    assert r(p, "prace", "Grep", {"pattern": "^K=a"}, proj)["rozhodnuti"] == "ask"
    assert r(p, "plny", "Grep", {"pattern": "^K=a"}, proj)["rozhodnuti"] == "ask"
    # glob, který na .env netrefí, a strom bez chráněných souborů projdou
    assert r(p, "cteni", "Grep", {"pattern": "a", "glob": "*.py"}, proj)["rozhodnuti"] == "allow"
    assert r(p, "cteni", "Grep", {"pattern": "a", "glob": "src/**/*.py"}, proj)["rozhodnuti"] == "allow"
    assert r(p, "cteni", "Grep", {"pattern": "a"}, proj / "src")["rozhodnuti"] == "allow"
    assert r(p, "cteni", "Grep", {"pattern": "a", "type": "py"}, proj)["rozhodnuti"] == "deny"


def test_grep_velky_strom(home, tmp_path, monkeypatch):
    monkeypatch.setattr(politika, "SKEN_MAX_POLOZEK", 1)
    assert r(pol(home, tmp_path), "cteni", "Grep", {"pattern": "a"}, home / "proj" / "src")["rozhodnuti"] == "allow"
    (home / "proj" / "src" / "b.py").write_text("x")
    assert r(pol(home, tmp_path), "cteni", "Grep", {"pattern": "a"}, home / "proj" / "src")["rozhodnuti"] == "deny"


@pytest.mark.parametrize("prikaz", ["cat ~/.ssh/id_x", "ls $HOME/.gnupg", "cat ${HOME}/.netrc", "cat .env",
                                    "openssl x a.pem", "echo x > .claude/settings.json", "cat /proc/1/environ",
                                    "echo >> ~/.bashrc", "cp x .git/hooks/pre-commit"])
def test_bash_textova_kontrola(home, tmp_path, prikaz):
    p = pol(home, tmp_path, bwrap="/usr/bin/bwrap")
    assert r(p, "plny", "Bash", {"command": prikaz}, home / "proj")["rozhodnuti"] == "deny"


@pytest.mark.parametrize("prikaz", ["ls -la", "git status", "python3 -m pytest -q", "cat src/a.py | wc -l"])
def test_bash_bez_nalezu(home, tmp_path, prikaz):
    p = pol(home, tmp_path, bwrap="/usr/bin/bwrap", overena_verze=True, bash_bez_dotazu=True)
    assert r(p, "plny", "Bash", {"command": prikaz}, home / "proj")["rozhodnuti"] == "allow"


@pytest.mark.parametrize("prikaz", ["ls /run/user/1000/cc-socks", "env | grep CLAUDE_CODE_MESSAGING",
                                    "script -qc 'mcp-voice-telegram potvrd' /dev/null",
                                    "cat ~/.local/state/mcp-voice/x", "rm $HOME/.config/mcp-voice/config.yaml"])
def test_pojistka_tvary(prikaz, tmp_path):
    home = str(tmp_path)
    tvary = politika.pojistka_tvary(home, os.path.join(home, ".local/state/mcp-voice"), {})
    assert politika.pojistka_nalez(prikaz, tvary)


@pytest.mark.parametrize("vzor, rozhodnuti", [("~/.ssh/*", "deny"), ("$HOME/.gnupg/**", "deny"), ("~/*", "deny"),
                                               ("/*/x", "deny"), ("PROJ/**/*.py", "allow"), ("**/*.py", "allow")])
def test_glob_absolutni_vzor(home, tmp_path, vzor, rozhodnuti):
    """N10: Glob s absolutním vzorem nesmí vypsat jména v chráněném adresáři."""
    vzor = vzor.replace("PROJ", str(home / "proj" / "src"))
    assert r(pol(home, tmp_path), "cteni", "Glob", {"pattern": vzor}, home / "proj" / "src")["rozhodnuti"] == rozhodnuti


def test_neplatne_jmeno_nastroje(home, tmp_path):
    assert r(pol(home, tmp_path), "plny", "a b", {}, home)["rozhodnuti"] == "deny"


# ---------------------------------------------------------------- konfigurace agenta: zápis z Telegramu zakázaný

AGENT_ZAPIS = [
    ("Write", {"file_path": "sub/x/.claude/settings.json", "content": "x"}),
    ("Write", {"file_path": ".mcp.json", "content": "{}"}),
    ("Edit", {"file_path": "hluboko/a/b/CLAUDE.md", "old_string": "a", "new_string": "b"}),
    ("MultiEdit", {"file_path": "~/.claude/settings.json", "edits": []}),
    ("NotebookEdit", {"notebook_path": "a/CLAUDE.local.md", "new_source": "x"}),
    ("Write", {"file_path": "src/../sub/.claude/agents/x.md", "content": "x"}),
    ("mcp__jiny__zapis", {"path": "sub/.claude/hooks.json", "obsah": "x"}),
    ("mcp__jiny__zapis", {"uri": "file:///x/.mcp.json"}),
    ("Bash", {"command": "echo x > sub/.claude/settings.json"}),
    ("Bash", {"command": "tee -a hluboko/CLAUDE.md < x"}),
    ("Bash", {"command": "cp a $HOME/.claude/settings.json"}),
]


@pytest.mark.parametrize("rezim", ["cteni", "prace", "plny", "omezeny"])
@pytest.mark.parametrize("nastroj, vstup", AGENT_ZAPIS)
def test_zapis_konfigurace_agenta_zakazany(home, tmp_path, rezim, nastroj, vstup):
    p = pol(home, tmp_path, bwrap="/usr/bin/bwrap", overena_verze=True, bash_bez_dotazu=True,
            cteni_navic=("mcp__jiny__zapis",))
    v = politika.rozhodni(p, rezim, nastroj, vstup, str(home / "proj"), znacka="4821")
    assert v["rozhodnuti"] == "deny" and "konfigurace agenta" in v["duvod"] and "z Telegramu zakázaný" in v["duvod"]


@pytest.mark.parametrize("nastroj, vstup", AGENT_ZAPIS[:5])
def test_zapis_konfigurace_agenta_nejde_odebrat(home, tmp_path, nastroj, vstup):
    """Ani odebrání výchozího chráněného zápisu zákaz nezruší."""
    bez = tuple(x for x in politika.CHRANENE_ZAPIS_VYCHOZI if "claude" not in x.lower() and "mcp" not in x)
    v = r(pol(home, tmp_path, chranene_zapis=bez), "plny", nastroj, vstup, home / "proj")
    assert v["rozhodnuti"] == "deny" and "konfigurace agenta" in v["duvod"]


def test_konfigurace_agenta_rozsireni_a_cteni(home, tmp_path):
    p = pol(home, tmp_path, konfigurace_agenta=("COPILOT.md",))
    v = r(p, "plny", "Write", {"file_path": "a/COPILOT.md", "content": "x"}, home / "proj")
    assert v["rozhodnuti"] == "deny" and "COPILOT.md" in v["duvod"]
    # čtení se nemění
    assert r(p, "cteni", "Read", {"file_path": "CLAUDE.md"}, home / "proj")["rozhodnuti"] == "allow"
    assert r(p, "cteni", "Read", {"file_path": "sub/.claude/settings.json"}, home / "proj")["rozhodnuti"] == "allow"
    assert r(p, "plny", "Write", {"file_path": "src/claude_md.py", "content": "x"}, home / "proj")["rozhodnuti"] is None


SONDA_DOMOV = """\
test -r {domov}/jiny/.env && echo jiny_env=ano || echo jiny_env=ne
test -e {domov}/jiny/data.txt && echo jiny=ano || echo jiny=ne
test -e {domov}/trezor.kdbx && echo kdbx=ano || echo kdbx=ne
test -e {domov}/.local/bin/nastroj && echo nastroj=ano || echo nastroj=ne
echo x > {domov}/.local/bin/nastroj 2>/dev/null && echo nastroj_zapis=ano || echo nastroj_zapis=ne
echo "mnt=$(ls -A /mnt 2>/dev/null | wc -l) media=$(ls -A /media 2>/dev/null | wc -l)"
echo "cwd=$(cat src/a.py)"
echo "env_cwd=$(cat .env 2>/dev/null | wc -c)"
echo x > sub/.claude/s.json 2>/dev/null && echo vnoreny_claude=zapis || echo vnoreny_claude=ne
echo x >> sub/CLAUDE.md 2>/dev/null && echo vnoreny_md=zapis || echo vnoreny_md=ne
"""


@pytest.mark.skipif(not _bwrap_jede(), reason="bwrap s --unshare-all tu nejde spustit")
def test_bwrap_skutecny_obal_skryje_domov(tmp_path):
    """Integračně: domov mimo /tmp (v adresáři testů), soubory mimo cwd nejsou vidět,
    složka nástrojů jen pro čtení, vnořená konfigurace agenta jen pro čtení. Jen fiktivní soubory."""
    domov = Path(__file__).parent / (".bwrap-domov-%d" % os.getpid())
    try:
        for d in ("proj/src", "proj/sub/.claude", "jiny", ".local/bin"):
            (domov / d).mkdir(parents=True)
        for f, obsah in (("proj/src/a.py", "ok"), ("proj/.env", "TAJNE=1"), ("proj/sub/CLAUDE.md", "x"),
                         ("jiny/.env", "TAJNE=1"), ("jiny/data.txt", "x"), ("trezor.kdbx", "x"),
                         (".local/bin/nastroj", "x")):
            (domov / f).write_text(obsah)
        p = pol(domov, tmp_path, env=dict(os.environ), uid=os.getuid(), bash_domov=("~/.local/bin",))
        obal = politika.obal_bwrap(BWRAP, SONDA_DOMOV.format(domov=domov), str(domov / "proj"), p)
        v = subprocess.run(["/bin/bash", "-c", obal], capture_output=True, text=True, timeout=20)
        assert v.returncode == 0, v.stderr
        vystup = dict(x.split("=", 1) for x in v.stdout.split() if "=" in x)
        assert vystup["jiny_env"] == "ne" and vystup["jiny"] == "ne" and vystup["kdbx"] == "ne"
        assert vystup["nastroj"] == "ano" and vystup["nastroj_zapis"] == "ne"
        assert vystup["mnt"] == "0" and vystup["media"] == "0"
        assert vystup["cwd"] == "ok" and vystup["env_cwd"] == "0"
        assert vystup["vnoreny_claude"] == "ne" and vystup["vnoreny_md"] == "ne"
    finally:
        shutil.rmtree(domov, ignore_errors=True)


def test_bwrap_prilis_dlouhy_obal(home, tmp_path, monkeypatch, bez_tmp):
    """Obal je jeden argument bash -c: nad limit délky se nesestaví (Bash se ptá bez obalu)."""
    monkeypatch.setattr(politika, "MAX_DELKA_OBALU", 50)
    assert politika.obal_bwrap("bwrap", "ls", str(home / "proj"), pol(home, tmp_path)) is None


# ---------------------------------------------------------------- přejmenování rodiče, háčky Gitu, integrita

def _poradi(argv, *dvojice):
    """indexy dvojic (přepínač, cesta) v argv; chybějící = AssertionError."""
    t = list(zip(argv, argv[1:]))
    return [t.index(d) for d in dvojice]


def test_bwrap_predci_jen_pro_cteni_pripojeni_na_sebe(home, tmp_path, bez_tmp):
    """předci ro položky v zapisovatelném kořeni se připojí sami na sebe (mv/rmdir rodiče = EBUSY),
    a to dřív než položka; kořen ani předek mimo kořen ne."""
    proj = home / "proj"
    (proj / "sub" / "hluboko" / ".claude").mkdir(parents=True)
    (proj / ".git" / "config").write_text("x")
    argv = shlex.split(politika.obal_bwrap("bwrap", "ls", str(proj), pol(home, tmp_path)))
    t = trojice(argv)
    for d in ("sub", "sub/hluboko", ".git"):
        assert ("--bind", str(proj / d), str(proj / d)) in t, d
    a, b, c = _poradi(argv, ("--bind", str(proj / "sub")), ("--bind", str(proj / "sub" / "hluboko")),
                      ("--ro-bind", str(proj / "sub" / "hluboko" / ".claude")))
    assert a < b < c
    g, h = _poradi(argv, ("--bind", str(proj / ".git")), ("--ro-bind", str(proj / ".git" / "hooks")))
    assert g < h
    assert argv.count(str(proj)) == 3  # jen --bind kořene a --chdir (2× v --bind)
    assert ("--bind", str(home), str(home)) not in t


def test_bwrap_hluboke_pripojeni_az_po_melcich(home, tmp_path, bez_tmp):
    """kořen ro uvnitř zapisovatelného kořene: jeho předek se připojí dřív než on (jinak by ho překryl)."""
    proj = home / "proj"
    (proj / "a" / "ro").mkdir(parents=True)
    p = pol(home, tmp_path, bash_koreny=(("plny", str(proj / "a" / "ro"), "ro"),))
    argv = shlex.split(politika.obal_bwrap("bwrap", "ls", str(proj), p, "plny"))
    x, y = _poradi(argv, ("--bind", str(proj / "a")), ("--ro-bind", str(proj / "a" / "ro")))
    assert x < y


def test_bwrap_hacky_gitu_submoduly_a_hookspath(home, tmp_path, bez_tmp):
    """.git/modules/**/hooks a config, worktrees/*/config.worktree, core.hooksPath (relativní i v ~/.gitconfig)
    jen pro čtení; chybějící hooks = prázdný tmpfs jen pro čtení; soubor .git (worktree) jen pro čtení."""
    proj = home / "proj"
    g = proj / ".git"
    (g / "config").write_text('[core]\n\tbare = false\n\thooksPath = "nastroje/hacky"\n')
    for d in ("modules/lib/a/hooks", "modules/lib/a/modules/vnoreny", "worktrees/w", "modules/lib/a/objects/xx",
              "modules/bez_head/hooks"):
        (g / d).mkdir(parents=True)
    for f in ("modules/lib/a/HEAD", "modules/lib/a/config", "modules/lib/a/modules/vnoreny/HEAD",
              "modules/lib/a/modules/vnoreny/config", "worktrees/w/config.worktree", "config.worktree"):
        (g / f).write_text("x")
    (proj / "nastroje" / "hacky").mkdir(parents=True)
    (proj / "wt").mkdir()
    (proj / "wt" / ".git").write_text("gitdir: ../.git/worktrees/w\n")
    (home / ".gitconfig").write_text("[core]\n    hookspath = .githooks\n")
    (proj / ".githooks").mkdir()
    argv = shlex.split(politika.obal_bwrap("bwrap", "ls", str(proj), pol(home, tmp_path)))
    t, d = trojice(argv), list(zip(argv, argv[1:]))
    for c in (".git/modules/lib/a/hooks", ".git/modules/lib/a/config", ".git/modules/lib/a/modules/vnoreny/config",
              ".git/worktrees/w/config.worktree", ".git/config.worktree", "nastroje/hacky", ".githooks", "wt/.git",
              ".git/modules/bez_head/hooks"):
        assert ("--ro-bind", str(proj / c), str(proj / c)) in t, c
    for c in (".git/modules/lib/a/modules/vnoreny/hooks",):
        i = d.index(("--tmpfs", str(proj / c)))
        assert ("--remount-ro", str(proj / c)) in d[i:]
    assert ("--bind", str(g / "modules" / "lib" / "a"), str(g / "modules" / "lib" / "a")) in t


def test_bwrap_chybejici_hooks_tmpfs_jen_pro_cteni(home, tmp_path, bez_tmp):
    proj = home / "proj"
    (proj / ".git" / "hooks").rmdir()
    argv = shlex.split(politika.obal_bwrap("bwrap", "ls", str(proj), pol(home, tmp_path)))
    d = list(zip(argv, argv[1:]))
    i = d.index(("--tmpfs", str(proj / ".git" / "hooks")))
    assert d.index(("--remount-ro", str(proj / ".git" / "hooks"))) > i
    assert d.index(("--bind", str(proj / ".git"))) < i


def test_sken_sdileny_rozpocet(home, tmp_path, monkeypatch, bez_tmp):
    """limit času skenu platí pro všechny kořeny obalu dohromady."""
    (home / "druhy").mkdir()
    konce = []
    puvodni = politika.prohledej

    def zaznam(koren, p, *a, **kw):
        konce.append(kw.get("konec"))
        return puvodni(koren, p, *a, **kw)
    monkeypatch.setattr(politika, "prohledej", zaznam)
    p = pol(home, tmp_path, bash_koreny=(("plny", "~/druhy", "rw"),))
    assert politika.obal_bwrap("bwrap", "ls", str(home / "proj"), p, "plny")
    assert len(konce) == 2 and konce[0] is not None and konce[0] == konce[1]


@pytest.mark.parametrize("cesta, chyba", [("/dev", "systém"), ("/dev/snd", "systém"), ("/sys", "systém"),
                                          ("/proc/self", "systém"), ("/run/user", "systém"), ("/boot", "systém"),
                                          ("/var", "systém"), ("/tmp", "systém"), ("/var/tmp", None)])
def test_koren_bash_systemove_slozky(home, tmp_path, cesta, chyba):
    vysledek = politika.koren_bash_chyba(cesta, pol(home, tmp_path))
    assert (vysledek is None) if chyba is None else (vysledek is not None and chyba in vysledek), vysledek


def test_bin_v_domove_chraneny_zapis(home, tmp_path, bez_tmp):
    """~/bin je v PATH: kořen s rw se připojí jen pro čtení, Write do něj z Telegramu zamítnut."""
    (home / "bin").mkdir()
    p = pol(home, tmp_path, bash_koreny=(("plny", "~/bin", "rw"),))
    t = trojice(shlex.split(politika.obal_bwrap("bwrap", "ls", str(home / "proj"), p, "plny")))
    assert ("--ro-bind", str(home / "bin"), str(home / "bin")) in t
    assert r(p, "plny", "Write", {"file_path": "~/bin/git", "content": "x"}, home / "proj")["rozhodnuti"] == "deny"


@pytest.mark.parametrize("vstup", [{"pathInProject": "moje slozka/.claude/settings.json"},
                                   {"path": "a b/CLAUDE.md"}, {"x": ["C:\\proj\\.claude\\settings.json"]},
                                   {"path": "moje slozka/x/../CLAUDE.local.md"}])
def test_mcp_cesta_s_mezerou_do_konfigurace_agenta(home, tmp_path, vstup):
    v = r(pol(home, tmp_path), "plny", "mcp__ide__create_file", vstup, home / "proj")
    assert v["rozhodnuti"] == "deny" and "konfigurace agenta" in v["duvod"]


def test_odpovez_s_cestou_konfigurace_neni_zapis(home, tmp_path):
    """nástroje čtení serveru (odpovez, rekni, stav) nejsou zápis konfigurace agenta."""
    p = pol(home, tmp_path)
    for text in ("sub/.claude/settings.json", "Upravil jsem moje slozka/.claude/settings.json"):
        assert r(p, "plny", "mcp__hlas__odpovez", {"text": text}, home / "proj")["rozhodnuti"] != "deny", text
    assert r(p, "plny", "mcp__jiny__x", {"text": "sub/.claude/settings.json"}, home / "proj")["rozhodnuti"] == "deny"


def test_integrita_zmeny_konfigurace(home, tmp_path, bez_tmp):
    """snímek zapisovatelných kořenů před během a kontrola po něm: nová, smazaná a nahrazená konfigurace
    agenta, symlink nahrazený souborem, nový .git a háček; beze změny prázdný seznam."""
    proj = home / "proj"
    (proj / "AGENTS.md").write_text("x")
    (proj / "CLAUDE.md").unlink()
    os.symlink("AGENTS.md", proj / "CLAUDE.md")
    (proj / "sub" / ".claude").mkdir(parents=True)
    p = pol(home, tmp_path, bwrap="bwrap")
    info = politika.obal_info("bwrap", "ls", str(proj), p)
    snimek = info["integrita"]
    assert snimek["koreny"] == [str(proj)] and str(proj / "CLAUDE.md") in snimek["polozky"]
    assert politika.kontrola_integrity(snimek) == {"zmeny": [], "uplne": True}
    (proj / "CLAUDE.md").unlink()
    (proj / "CLAUDE.md").write_text("evil")
    (proj / "nove").mkdir()
    (proj / "nove" / "CLAUDE.md").write_text("evil")
    (proj / ".mcp.json").write_text("{}")
    os.rename(proj / "sub", proj / "sub_x")
    (proj / "sub" / ".claude").mkdir(parents=True)
    (proj / "x" / ".git").mkdir(parents=True)
    os.rename(proj / ".git" / "hooks", proj / ".git" / "hooks_x")
    (proj / ".git" / "hooks").mkdir()
    (proj / ".git" / "config.worktree").write_text("[core]\n\thooksPath = /x\n")
    zmeny = politika.kontrola_integrity(snimek)["zmeny"]
    for c in ("CLAUDE.md", "nove/CLAUDE.md", ".mcp.json", "sub/.claude", "sub_x/.claude", "x/.git", ".git/hooks",
              ".git/config.worktree"):
        assert str(proj / c) in zmeny, c


def test_git_konfigurace_jen_bezny_soubor(home, tmp_path, bez_tmp):
    """[include] na FIFO hook nezablokuje; chybějící soubor z [include] se sleduje."""
    g = home / "proj" / ".git"
    (g / "config").write_text("[include]\n\tpath = ../fifo\n[include]\n\tpath = ../neni.conf\n")
    os.mkfifo(home / "proj" / "fifo")
    gp = politika.git_polozky(str(g), [], str(home), politika.time.monotonic() + 3)
    assert gp["uplne"] and str(home / "proj" / "neni.conf") in gp["sledovat"]
    assert str(home / "proj" / "fifo") in gp["ro"]


def test_integrita_nedobehla(home, tmp_path, monkeypatch, bez_tmp):
    snimek = politika.obal_info("bwrap", "ls", str(home / "proj"), pol(home, tmp_path))["integrita"]
    monkeypatch.setattr(politika, "SKEN_MAX_POLOZEK", 1)
    assert politika.kontrola_integrity(snimek)["uplne"] is False


def test_integrita_jen_zapisovatelne_koreny(home, tmp_path, bez_tmp):
    (home / "ro").mkdir()
    p = pol(home, tmp_path, bash_koreny=(("plny", "~/ro", "ro"),))
    assert politika.obal_info("bwrap", "ls", str(home / "proj"), p, "plny")["integrita"]["koreny"] == [str(home / "proj")]


def test_rozhodni_bash_vraci_snimek_integrity(home, tmp_path):
    p = pol(home, tmp_path, bwrap="/usr/bin/bwrap", overena_verze=True)
    v = r(p, "prace", "Bash", {"command": "ls"}, home / "proj")
    assert v["rozhodnuti"] == "ask" and v["integrita"]["koreny"] == [str(home / "proj")]
    assert r(p, "prace", "Read", {"file_path": "src/a.py"}, home / "proj").get("integrita") is None


SONDA_PREJMENOVANI = """\
t() { ( eval "$1" ) >/dev/null 2>&1 && echo "$2=ano" || echo "$2=ne"; }
t "mv sub sub_x" mv_sub
t "mv .git .git_x" mv_git
t "rmdir .git/hooks" rm_hooks
t "echo x > .git/modules/m/hooks/post-checkout" modul_hook
t "echo x > .git/modules/m/config" modul_config
t "echo x > sub/novy.txt" zapis_sub
t "git -c user.email=a@b -c user.name=a commit -qm t --allow-empty" commit
t "git config core.hooksPath /x" hookspath
"""


@pytest.mark.skipif(not _bwrap_jede() or not shutil.which("git"), reason="bwrap nebo git tu nejde spustit")
def test_bwrap_skutecny_obal_prejmenovani_rodice(tmp_path):
    """Integračně: mv rodiče ro položky selže, háčky a config submodulu jen pro čtení,
    git commit uvnitř funguje. Jen fiktivní strom v adresáři testů."""
    domov = Path(__file__).parent / (".bwrap-prejmenovani-%d" % os.getpid())
    proj = domov / "proj"
    try:
        (proj / "sub" / ".claude").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(proj)], check=True)
        subprocess.run(["git", "-C", str(proj), "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q",
                        "--allow-empty", "-m", "init"], check=True)
        for d in ("modules/m/hooks",):
            (proj / ".git" / d).mkdir(parents=True)
        (proj / ".git" / "modules" / "m" / "HEAD").write_text("ref: refs/heads/main\n")
        (proj / ".git" / "modules" / "m" / "config").write_text("[core]\n")
        p = pol(domov, tmp_path, env=dict(os.environ), uid=os.getuid())
        obal = politika.obal_bwrap(BWRAP, SONDA_PREJMENOVANI, str(proj), p)
        v = subprocess.run(["/bin/bash", "-c", obal], capture_output=True, text=True, timeout=30)
        assert v.returncode == 0, v.stderr
        vystup = dict(x.split("=", 1) for x in v.stdout.split() if "=" in x)
        assert vystup == {"mv_sub": "ne", "mv_git": "ne", "rm_hooks": "ne", "modul_hook": "ne", "modul_config": "ne",
                          "zapis_sub": "ano", "commit": "ano", "hookspath": "ne"}, vystup
        assert (proj / "sub" / ".claude").is_dir() and (proj / ".git" / "HEAD").is_file()
    finally:
        shutil.rmtree(domov, ignore_errors=True)
