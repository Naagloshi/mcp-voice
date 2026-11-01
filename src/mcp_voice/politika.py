"""Politika nástrojů v tahu z Telegramu: chráněné cesty, tvrdé zákazy, režimy (jen standardní knihovna).

Server ji při otevření okna složí z kódu a konfigurace (`sestav`) a zapíše jako snímek do
`telegram/politika-<pid>-<start>.json`. Hook rozsahu čte jen tento snímek (bez YAML a venv)
a pro každé volání nástroje zavolá `rozhodni`. Kontrola cest je pojistka nad rozbalenými cestami
(`realpath`, `commonpath`); u Bash je spolehlivá jen izolace `bwrap`, textová kontrola je pojistka.
"""
from __future__ import annotations

import os
import re
import shlex
import stat
import sys
import time
from fnmatch import fnmatchcase, translate

VERZE = 3

# G7: Claude Code použije `updatedInput` z hooku i s rozhodnutím allow (Bash dostane obal bwrap, Grep
# vynucený output_mode). Bez ověření platí záloha: Bash v `plny` se ptá (s obalem v updatedInput)
# a Grep s obsahovým výstupem se zamítá. Přepínat jen podle pokusu na ověřené verzi Claude Code.
# Bash: ověřeno na 2.1.314 (allow, ask i bez rozhodnutí); platí jen v okně s ověřenou verzí (overena_verze).
# Grep: neověřeno (pokus nástroj nepoužil).
BASH_UPDATED_INPUT_OVERENO = True
GREP_UPDATED_INPUT_OVERENO = False
# Uvnitř bwrap vlastní (prázdné): cesty pod nimi nejsou z obalu vidět, maskovat je netřeba.
ZAKRYTE = ("/proc", "/tmp", "/run", "/dev")

# Čtení i zápis, ve všech režimech. Cesta = adresář nebo soubor i vše pod ním; jméno = shoda
# se jménem kterékoli složky cesty. <stav> = cesty.stav, <uid> = číselné uid uživatele.
CHRANENE_VYCHOZI = (
    "~/.ssh", "~/.gnupg", "~/.pki", "~/.password-store", "~/.claude", "~/.claude.json",
    "$XDG_CONFIG_HOME/mcp-voice", "<stav>", "~/.local/share/keyrings", "~/.local/share/kwalletd",
    "~/.config/keepassxc", "~/.netrc", "~/.git-credentials", "~/.docker/config.json", "~/.kube", "~/.aws",
    "~/.azure", "~/.config/gcloud", "~/.npmrc", "~/.pypirc", "~/.mozilla", "~/.config/google-chrome",
    "~/.config/chromium", "~/.var/app", "~/.Xauthority", "/proc", "$XDG_RUNTIME_DIR", "$XDG_RUNTIME_DIR/cc-socks",
    "/tmp/cc-socks-<uid>", "/run/docker.sock", "/run/dbus",
    # přihlášení CLI agentů a nástrojů, pošta a komunikace, další prohlížeče, uložená hesla
    "~/.codex", "~/.gemini", "~/.config/gh", "~/.config/github-copilot", "~/.config/Claude", "~/.config/op",
    "~/.config/Bitwarden", "~/.config/rclone", "~/.config/filezilla", "~/.config/remmina", "~/.local/share/remmina",
    "~/.vault-token", "~/.terraform.d", "~/.oci", "~/.config/doctl", "~/.config/hcloud",
    "~/.thunderbird", "~/.mozilla-thunderbird", "~/.config/evolution", "~/.local/share/evolution",
    "~/.config/Signal", "~/.local/share/TelegramDesktop", "~/.config/Slack", "~/.config/discord",
    "~/.config/Element", "~/.config/teams-for-linux", "~/.config/Microsoft", "~/.muttrc", "~/.mutt",
    "~/.config/neomutt", "~/.msmtprc", "~/.mbsyncrc", "~/.fetchmailrc",
    "~/.config/microsoft-edge", "~/.config/google-chrome-for-testing", "~/.config/google-chrome-beta",
    "~/.config/google-chrome-unstable", "~/.config/BraveSoftware", "~/.config/vivaldi", "~/.config/opera",
    "~/.librewolf",
    # historie příkazů a schránky, odkládací soubory editoru (obsah upravovaných souborů)
    "~/.zhistory", "~/.histfile", "~/.lesshst", "~/.viminfo", "~/.local/share/fish", "~/.local/state/nvim",
    "~/.local/share/gpaste", "~/.local/share/klipper", "~/.cache/cliphist", "~/.config/copyq",
    "~/.local/share/copyq",
    ".env", ".env.*", "*.env", ".envrc", ".*_history", ".netrc", ".npmrc", ".pypirc", ".git-credentials",
    "*.kdbx", "*.kdb", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore", "*.jks",
    "id_rsa*", "id_ed25519*", "id_ecdsa*", "*credentials*", "*secret*", "*.ovpn",
)
# Jen zápis (persistence po injekci): `**` = libovolný počet složek.
# Navíc vždy (nejde odebrat): balíček mcp-voice, jeho venv a repozitář editovatelné instalace (`vlastni_instalace`).
CHRANENE_ZAPIS_VYCHOZI = (
    "**/.claude/**", "**/.mcp.json", "**/CLAUDE.md", "**/CLAUDE.local.md", "**/AGENTS.md",
    "**/.git/hooks/**", "**/.git/config", "**/.envrc", "~/.bashrc", "~/.bash_profile", "~/.profile",
    "~/.bash_aliases", "~/.bash_login", "~/.bash_logout", "~/.zshrc", "~/.zshenv", "~/.zprofile", "~/.zlogin",
    "~/.config/fish", "~/.gitconfig", "~/.config/git", "~/.config/systemd", "~/.config/autostart",
    "~/.config/environment.d", "~/.local/bin", "~/.local/share/applications", "~/.pam_environment",
    "~/.xprofile", "~/.xsessionrc", "~/.xinitrc", "/var/spool/cron", "~/bin",
    # instrukce a konfigurace jiných agentů a nástrojů, které spouštějí kód při otevření nebo commitu
    "**/GEMINI.md", "**/.gemini/**", "**/.codex/**", "**/.cursor/**", "**/.vscode/**", "**/.husky/**",
    "**/.pre-commit-config.yaml",
)
# Konfigurace agenta: zápis z Telegramu je tvrdě zakázaný (deny ve všech režimech a nástrojích, nejde odebrat,
# konfigurace jen přidá). Jména (i vzory) se porovnávají s každou složkou cesty, tedy v libovolné hloubce.
KONFIGURACE_AGENTA = (".claude", "CLAUDE.md", "CLAUDE.local.md", ".mcp.json")
# jména, která obal bwrap v zapisovatelném kořeni připojí jen pro čtení v libovolné hloubce
# (navíc konfigurace agenta, jména a vzory `**/jméno` z chráněného zápisu a .git/hooks, .git/config).
ZAPIS_V_CWD = ("AGENTS.md", "GEMINI.md", ".gemini", ".codex", ".cursor", ".vscode", ".husky",
               ".pre-commit-config.yaml", ".envrc")
# Příkazy Bash, které zakládají persistenci mimo soubory (textová kontrola, všechny režimy).
PRIKAZY_PERSISTENCE = ("crontab", "systemd-run", "at", "batch")
_PRIKAZ_PERSISTENCE = re.compile(
    r"(?:^|[;&|(`{\n]|\$\(|\b(?:then|do|else|xargs|sudo|env|nohup|exec|command|time)\s)\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*"
    r"(?:\S*/)?(?:%s)(?![A-Za-z0-9_.-])" % "|".join(re.escape(x) for x in PRIKAZY_PERSISTENCE))
# Prohledání kořenů pro bwrap a Grep: .git, závislosti (jen pro čtení), chráněná jména, jména chráněného zápisu.
# Závislosti z ZAVISLOSTI_NEPROCHAZET se neprocházejí (kód třetích stran, desítky tisíc souborů), vendor ano
# (bývají v něm klíče a certifikáty balíčků).
ZAVISLOSTI = ("node_modules", ".venv", "venv", "vendor")
ZAVISLOSTI_NEPROCHAZET = ("node_modules", ".venv", "venv")
NEPROCHAZET = ("__pycache__", ".mypy_cache", ".pytest_cache")
# limity skenu (fail-closed: nad limit = obal se nesestaví, Bash se ptá; Grep se zamítne/ptá).
SKEN_MAX_POLOZEK = 500_000
SKEN_MAX_HLOUBKA = 32
SKEN_MAX_S = 3.0  # pro všechny kořeny jednoho volání dohromady (timeout hooku je 5 s)
# Git: v každém gitdiru (.git, .git/modules/**) jen pro čtení hooks/, config, config.worktree,
# worktrees/*/config.worktree, soubory z [include] a cíl core.hooksPath. Chybějící hooks/ = prázdný tmpfs.
GIT_SOUBORY = ("config", "config.worktree")
GIT_MAX_POLOZEK = 20_000
GIT_MAX_HLOUBKA = 8
# Systémové složky nesmí být kořenem Bash (zařízení, jádro, sockety); /tmp jen podsložka.
SYSTEMOVE_KORENY = ("/dev", "/proc", "/sys", "/run", "/var/run", "/boot")
PROSTREDI_BWRAP = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TERM")
# obal skryje celý domov (tmpfs) a tato místa; zpět jen kořeny Bash (cwd, bash_koreny) a složky
# nástrojů z domova jen pro čtení (bash_domov).
SKRYTE_VYCHOZI = ("/mnt", "/media", "/srv", "/home", "/net", "/nfs", "/afs")
BASH_DOMOV_VYCHOZI = ("~/.local/bin", "~/.local/lib", "~/.local/share/uv", "~/.local/share/pnpm",
                      "~/.cargo/bin", "~/.rustup", "~/.nvm", "~/.pyenv", "~/.sdkman", "~/go/bin", "~/.deno/bin",
                      "~/.bun/bin", "~/.volta")

ZAKAZANE_V_TAHU = (
    "SendMessage", "ListAgents", "CronCreate", "CronDelete", "CronList", "ScheduleWakeup", "RemoteTrigger",
    "Monitor", "TaskStop", "EnterPlanMode", "ExitPlanMode", "EnterWorktree", "ExitWorktree", "AskUserQuestion",
)
NASTROJE_CTENI_ZAKLAD = ("Read", "Grep", "Glob", "ToolSearch", "TaskList", "TaskGet", "TodoWrite")
NASTROJE_MCP_CTENI = ("odpovez", "rekni", "stav")
ZNAME_VESTAVENE = (
    "Read", "Grep", "Glob", "Write", "Edit", "MultiEdit", "NotebookEdit", "NotebookRead", "Bash", "Agent", "Skill",
    "ToolSearch", "TodoWrite", "TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "WebFetch", "WebSearch", "LSP",
)
NASTROJE_CTENI_SOUBORU = ("Read", "NotebookRead")
NASTROJE_ZAPISU = ("Write", "Edit", "MultiEdit", "NotebookEdit")
NASTROJE_HLEDANI = ("Grep", "Glob")
POJISTKA_RETEZCE = ("cc-socks", "CLAUDE_CODE_MESSAGING", "mcp-voice-telegram")
REZIMY = ("cteni", "prace", "plny")
POPIS_REZIMU = {"cteni": "čtení", "prace": "práce", "plny": "plný", "omezeny": "omezený"}
JMENO_NASTROJE = re.compile(r"^[A-Za-z0-9_]{1,64}$|^mcp__[A-Za-z0-9_-]{1,64}__[A-Za-z0-9_-]{1,64}$")
_ODDELOVACE = re.compile(r"[\s'\"`;|&<>(){}=,]+")
_RIDICI = re.compile(r"[\x00-\x1f\x7f-\x9f  ‪-‮⁦-⁩]")
MAX_HLOUBKA = 8


# ---------------------------------------------------------------- cesty

def pod(cesta: str, koren: str) -> bool:
    try:
        return os.path.commonpath([cesta, koren]) == koren
    except ValueError:
        return False


def druh_polozky(polozka: str) -> str:
    """cesta | jmeno | glob."""
    if polozka.startswith(("/", "~", "$", "<stav>")):
        return "cesta"
    return "glob" if "/" in polozka else "jmeno"


def rozbal_cestu(polozka: str, home: str, env: dict, stav: str, uid: int) -> str:
    p = polozka.replace("<uid>", str(uid))
    if p.startswith("<stav>"):
        p = stav + p[len("<stav>"):]
    elif p.startswith("$XDG_CONFIG_HOME"):
        p = (env.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")) + p[len("$XDG_CONFIG_HOME"):]
    elif p.startswith("$XDG_RUNTIME_DIR"):
        p = (env.get("XDG_RUNTIME_DIR") or "/run/user/%d" % uid) + p[len("$XDG_RUNTIME_DIR"):]
    elif p == "~" or p.startswith("~/"):
        p = home + p[1:]
    return os.path.normpath(p)


def _seznam(polozky, home, env, stav, uid) -> dict:
    cesty, jmena, globy = [], [], []
    for x in polozky:
        druh = druh_polozky(x)
        if druh == "cesta":
            c = rozbal_cestu(x, home, env, stav, uid)
            for v in (c, os.path.realpath(c)):
                if os.path.isabs(v) and v != "/" and v not in cesty:
                    cesty.append(v)
        elif druh == "jmeno":
            jmena.append(x)
        else:
            globy.append([k for k in x.split("/") if k])
    return {"cesty": cesty, "jmena": jmena, "globy": globy}


def efektivni(vychozi: tuple[str, ...], pridat: tuple[str, ...], odebrat: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(x for x in vychozi if x not in odebrat) + tuple(x for x in pridat if x not in vychozi)


def sestav(*, registrace: str, chranene: tuple[str, ...], chranene_zapis: tuple[str, ...],
           zakazane_navic: tuple[str, ...] = (), cteni_navic: tuple[str, ...] = (), koreny: tuple[str, ...] = (),
           pri_dotazu: str = "cekat", bez_rc: bool = False, bwrap: str | None = None, bwrap_sit: bool = False,
           overena_verze: bool = False,
           omezit_naplanovane: bool = True, stav: str, konfigurace: str | None = None,
           konfigurace_agenta: tuple[str, ...] = (), bash_bez_dotazu: bool = False,
           bash_domov: tuple[str, ...] = BASH_DOMOV_VYCHOZI, bash_skryt: tuple[str, ...] = (),
           bash_koreny: tuple[tuple[str, str, str], ...] = (),
           home: str | None = None, env: dict | None = None, uid: int | None = None,
           vlastni: tuple[str, ...] | None = None) -> dict:
    """Snímek politiky pro hook (JSON). Cesty rozbalené pro tohoto uživatele."""
    home = os.path.normpath(home or os.path.expanduser("~"))
    env = os.environ if env is None else env
    uid = os.getuid() if uid is None else uid
    stav = os.path.normpath(os.path.expanduser(stav))
    seznam = _seznam(chranene, home, env, stav, uid)
    zapis = _seznam(chranene_zapis, home, env, stav, uid)
    for c in (vlastni_instalace() if vlastni is None else vlastni):
        for v in (os.path.normpath(c), os.path.realpath(c)):
            if os.path.isabs(v) and v != "/" and v not in zapis["cesty"]:
                zapis["cesty"].append(v)
    if konfigurace:
        k = os.path.normpath(os.path.expanduser(konfigurace))
        for v in (k, os.path.realpath(k)):
            if v not in seznam["cesty"]:
                seznam["cesty"].append(v)
    mcp = ["mcp__%s__%s" % (registrace, n) for n in NASTROJE_MCP_CTENI]
    cteni = list(dict.fromkeys(list(NASTROJE_CTENI_ZAKLAD) + mcp + list(cteni_navic)))
    pojistka = list(POJISTKA_RETEZCE)
    for adr in (rozbal_cestu("$XDG_CONFIG_HOME/mcp-voice", home, env, stav, uid), stav):
        pojistka += _tvary(adr, home)
    pol = {
        "v": VERZE, "registrace": registrace, "home": home,
        "chranene": seznam,
        "zapis": zapis,
        "agent": list(dict.fromkeys(KONFIGURACE_AGENTA + tuple(konfigurace_agenta))),
        "zakazane": list(dict.fromkeys(list(ZAKAZANE_V_TAHU) + list(zakazane_navic))),
        "cteni": cteni,
        "omezeny": [n for n in cteni if n not in ("mcp__%s__odpovez" % registrace, "mcp__%s__rekni" % registrace)],
        "koreny": [os.path.realpath(rozbal_cestu(k, home, env, stav, uid)) for k in koreny],
        "pri_dotazu": pri_dotazu, "bez_rc": bool(bez_rc), "bwrap": bwrap, "bwrap_sit": bool(bwrap_sit),
        "overena_verze": bool(overena_verze),
        "bwrap_env": prostredi_bwrap(env, home),
        "runtime": os.path.normpath(env.get("XDG_RUNTIME_DIR") or "/run/user/%d" % uid),
        "omezit_naplanovane": bool(omezit_naplanovane),
        "pojistka": pojistka, "zname": list(ZNAME_VESTAVENE),
        "bash_bez_dotazu": bool(bash_bez_dotazu),
        "bash_domov": [rozbal_cestu(x, home, env, stav, uid) for x in bash_domov],
        "bash_skryt": [rozbal_cestu(x, home, env, stav, uid) for x in SKRYTE_VYCHOZI + tuple(bash_skryt)],
        "xdg_config": os.path.normpath(env.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")),
    }
    # Kořeny Bash: chybný kořen (chráněný nebo předek chráněné cesty) se vynechá, i když prošel validací.
    kor: dict = {}
    for rezim, cesta, pristup in bash_koreny:
        c = rozbal_cestu(cesta, home, env, stav, uid)
        if rezim in REZIMY and pristup in ("rw", "ro") and koren_bash_chyba(c, pol) is None:
            kor.setdefault(rezim, []).append([c, pristup])
    pol["bash_koreny"] = kor
    return pol


def koren_bash_chyba(cesta: str, pol: dict) -> str | None:
    """Důvod, proč adresář nesmí být kořenem Bash v obalu (None = smí): chráněná cesta, předek chráněné
    cesty (třeba domov) nebo konfigurace agenta."""
    if not isinstance(cesta, str) or not os.path.isabs(cesta) or os.path.normpath(cesta) == "/":
        return "není absolutní cesta"
    cesty = [os.path.normpath(cesta)]
    r = os.path.realpath(cesty[0])
    if r != cesty[0]:
        cesty.append(r)
    if any(pod(c, s) or pod(s, c) for c in cesty for s in SYSTEMOVE_KORENY) or "/tmp" in cesty:
        return "je systémová složka nebo ji obsahuje"
    if ve_seznamu(cesty, pol["chranene"]) or konfigurace_agenta(cesty, pol):
        return "je chráněná cesta"
    if any(pod(x, c) for c in cesty for x in pol["chranene"]["cesty"]):
        return "obsahuje chráněnou cestu"
    if any(pod(pol["home"], c) for c in cesty):
        return "obsahuje domovský adresář"
    return None


def vlastni_instalace() -> list[str]:
    """V1: zdroj hooku a instalace mcp-voice odvozené za běhu (adresář balíčku, venv, repozitář
    editovatelné instalace). Tah z Telegramu je nesmí přepsat v žádném režimu."""
    balicek = os.path.dirname(os.path.realpath(__file__))
    cesty = [balicek]
    if sys.prefix != sys.base_prefix:
        cesty.append(sys.prefix)
    rodic = os.path.dirname(balicek)
    repo = os.path.dirname(rodic) if os.path.basename(rodic) == "src" else rodic
    if os.path.isfile(os.path.join(repo, "pyproject.toml")):
        cesty.append(repo)
    return [c for c in dict.fromkeys(cesty) if c != "/"]


_HODNOTA_ENV = re.compile(r"^[A-Za-z0-9_.@:/+-]{1,256}$")


def prostredi_bwrap(env: dict, home: str) -> dict:
    """K1: prostředí uvnitř bwrap (--clearenv): jen tyto proměnné, PATH jen z absolutních složek."""
    vysledek = {"HOME": home, "PATH": "/usr/local/bin:/usr/bin:/bin", "SHELL": "/bin/bash", "TMPDIR": "/tmp"}
    for k in PROSTREDI_BWRAP:
        v = env.get(k)
        if k != "HOME" and isinstance(v, str) and _HODNOTA_ENV.match(v):
            vysledek[k] = v
    cesta = env.get("PATH")
    if isinstance(cesta, str):
        slozky = [x for x in cesta.split(":") if x.startswith("/") and _HODNOTA_ENV.match(x)]
        if slozky:
            vysledek["PATH"] = ":".join(dict.fromkeys(slozky))
    return vysledek


def _tvary(adr: str, home: str) -> list[str]:
    tvary = [adr]
    if pod(adr, home) and adr != home:
        zbytek = os.path.relpath(adr, home)
        tvary += ["~/" + zbytek, "$HOME/" + zbytek, "${HOME}/" + zbytek]
    return tvary


def pojistka_tvary(home: str, stav: str, env: dict | None = None) -> list[str]:
    """Řetězce P0 pro hook bez snímku politiky (mimo okno)."""
    env = os.environ if env is None else env
    konf = env.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    vychozi_stav = os.path.join(home, ".local", "state", "mcp-voice")
    return (list(POJISTKA_RETEZCE) + _tvary(os.path.join(konf, "mcp-voice"), home) + _tvary(stav, home)
            + (_tvary(vychozi_stav, home) if vychozi_stav != stav else []))


def pojistka_nalez(prikaz: object, tvary: list[str]) -> bool:
    return isinstance(prikaz, str) and any(t and t in prikaz for t in tvary)


def normalizuj(cesta: object, cwd: object, home: str) -> list[str]:
    """Absolutní normalizovaná cesta a její realpath (prázdný seznam = není cesta)."""
    if not isinstance(cesta, str) or not cesta or "\x00" in cesta or len(cesta) > 4096:
        return []
    p = cesta
    if p == "~" or p.startswith("~/"):
        p = home + p[1:]
    elif p == "$HOME" or p.startswith("$HOME/"):
        p = home + p[5:]
    elif p == "${HOME}" or p.startswith("${HOME}/"):
        p = home + p[7:]
    if not os.path.isabs(p):
        p = os.path.join(cwd if isinstance(cwd, str) and os.path.isabs(cwd) else "/", p)
    a = os.path.normpath(p)
    try:
        r = os.path.realpath(a)
    except (OSError, ValueError):
        r = a
    return [a] if r == a else [a, r]


def _glob(komponenty: list[str], vzor: list[str]) -> bool:
    if not vzor:
        return not komponenty
    if vzor[0] == "**":
        return any(_glob(komponenty[i:], vzor[1:]) for i in range(len(komponenty) + 1))
    return bool(komponenty) and _shoda(komponenty[0], vzor[0]) and _glob(komponenty[1:], vzor[1:])


def _shoda(jmeno: str, vzor: str) -> bool:
    """S3: vzory jmen bez ohledu na velikost písmen (Secrets.yaml, .ENV, ID_RSA)."""
    return fnmatchcase(jmeno.lower(), vzor.lower())


def ve_seznamu(cesty: list[str], seznam: dict) -> bool:
    for c in cesty:
        if any(pod(c, x) for x in seznam["cesty"]):
            return True
        komponenty = [k for k in c.split("/") if k]
        if any(_shoda(k, j) for k in komponenty for j in seznam["jmena"]):
            return True
        if any(_glob(komponenty, g) for g in seznam["globy"]):
            return True
    return False


def konfigurace_agenta(cesty: list[str], pol: dict) -> bool:
    """Cesta vede do konfigurace agenta (.claude/, CLAUDE.md, .mcp.json …) v libovolné hloubce."""
    jmena = pol.get("agent") or KONFIGURACE_AGENTA
    return any(_shoda(k, j) for c in cesty for k in c.split("/") if k for j in jmena)


def zapis_agenta(cesta: object, cwd: object, pol: dict) -> bool:
    return konfigurace_agenta(normalizuj(cesta, cwd, pol["home"]), pol)


def chranena(cesta: object, cwd: object, pol: dict, zapis: bool = False) -> bool:
    cesty = normalizuj(cesta, cwd, pol["home"])
    if not cesty:
        return False
    return ve_seznamu(cesty, pol["chranene"]) or (zapis and ve_seznamu(cesty, pol["zapis"]))


def koren_hledani_chraneny(koren: object, cwd: object, pol: dict) -> bool:
    """Kořen Grep/Glob: chráněný, pod chráněnou cestou, nebo předek chráněného adresáře."""
    cesty = normalizuj(koren if isinstance(koren, str) and koren else cwd, cwd, pol["home"])
    if not cesty:
        return True
    if ve_seznamu(cesty, pol["chranene"]):
        return True
    return any(pod(x, c) for c in cesty for x in pol["chranene"]["cesty"])


def vzor_hledani_chraneny(vstup: dict, cwd: object, pol: dict) -> bool:
    """N10: Glob s absolutním vzorem (~/.ssh/*) prozradí jména v chráněném adresáři; kontroluje se
    pevná část vzoru před prvním zástupným znakem."""
    vzor = vstup.get("pattern") if isinstance(vstup.get("pattern"), str) else ""
    if not vzor.startswith(("/", "~", "$HOME", "${HOME}")):
        return False
    pevna = re.split(r"[*?\[{]", vzor, maxsplit=1)[0]
    if pevna != vzor:
        pevna = pevna.rsplit("/", 1)[0] or "/"
    return koren_hledani_chraneny(pevna, cwd, pol)


def v_korenech(cesta: object, cwd: object, pol: dict) -> bool:
    """O3: bez nastavených kořenů se čtení neomezuje (chráněné cesty platí zvlášť)."""
    if not pol["koreny"]:
        return True
    cesty = normalizuj(cesta, cwd, pol["home"])
    return bool(cesty) and all(any(pod(c, k) for k in pol["koreny"]) for c in cesty)


def bash_chraneny(prikaz: object, cwd: object, pol: dict) -> bool:
    """Textová kontrola příkazu Bash (pojistka): chráněné cesty, vzory jmen, chráněný zápis, persistence."""
    if not isinstance(prikaz, str):
        return True
    if _PRIKAZ_PERSISTENCE.search(prikaz):
        return True
    for seznam in (pol["chranene"], pol["zapis"]):
        for c in seznam["cesty"]:
            for t in _tvary(c, pol["home"]):
                if re.search(re.escape(t) + r"(?![A-Za-z0-9._-])", prikaz):
                    return True
    for token in _ODDELOVACE.split(prikaz):
        if not token or token.startswith("-"):
            continue
        cesty = normalizuj(token, cwd, pol["home"])
        relativni = [k for k in token.split("/") if k]
        if any(_shoda(k, j) for k in relativni for j in pol["chranene"]["jmena"]):
            return True
        if ("/" in token or token.startswith(("~", "$", "."))) and (
                ve_seznamu(cesty, pol["chranene"]) or ve_seznamu(cesty, pol["zapis"])):
            return True
        if any(_shoda(k, j) for k in relativni for j in pol["zapis"]["jmena"]):
            return True
        if any(_glob(relativni, g[1:] if g and g[0] == "**" else g) for g in pol["zapis"]["globy"]):
            return True
    return False


def _retezce(hodnota, hloubka=0):
    if hloubka > MAX_HLOUBKA:
        return
    if isinstance(hodnota, str):
        yield hodnota
    elif isinstance(hodnota, dict):
        for v in hodnota.values():
            yield from _retezce(v, hloubka + 1)
    elif isinstance(hodnota, list):
        for v in hodnota:
            yield from _retezce(v, hloubka + 1)


_BEZ_MEZER = re.compile(r"^\S{1,4096}$")


def vstup_chraneny(vstup: object, cwd: object, pol: dict, zapis: bool = True) -> bool:
    """MCP a ostatní: každý řetězec, který vypadá jako cesta (S3: i relativní cesta, jméno a file:// URL).
    zapis=False: jen chráněné cesty (nástroje čtení serveru, chráněný zápis se jich netýká)."""
    from urllib.parse import unquote, urlsplit
    for s in _retezce(vstup):
        if s[:7].lower() == "file://":
            s = unquote(urlsplit(s).path) or "/"
        if s.startswith(("/", "~", "$HOME", "${HOME}", "./", "../")):
            if chranena(s, cwd, pol, zapis=zapis):
                return True
        elif _BEZ_MEZER.match(s) and ("/" in s or "." in s) and chranena(s, cwd, pol, zapis=zapis):
            return True
    return False


def bash_agent(prikaz: object, pol: dict) -> bool:
    """Textová kontrola Bash: příkaz zmiňuje konfiguraci agenta (pojistka, v obalu je navíc jen pro čtení)."""
    if not isinstance(prikaz, str):
        return True
    return any(konfigurace_agenta([t], pol) for t in _ODDELOVACE.split(prikaz) if t)


def vstup_agent(vstup: object, cwd: object, pol: dict) -> bool:
    """MCP a ostatní: jednořádkový řetězec, ve kterém je jméno konfigurace agenta celou složkou cesty
    (i s mezerami a zpětnými lomítky), nebo cesta, která tam vede přes symlink."""
    from urllib.parse import unquote, urlsplit
    for s in _retezce(vstup):
        if s[:7].lower() == "file://":
            s = unquote(urlsplit(s).path) or "/"
        if "\n" in s or len(s) > 4096:
            continue
        if konfigurace_agenta([s.replace("\\", "/")], pol) or zapis_agenta(s, cwd, pol):
            return True
    return False


# ---------------------------------------------------------------- prohledání stromu (bwrap, Grep)

_REGEXY: dict = {}


def _regex_jmen(jmena) -> re.Pattern | None:
    """Jeden regulární výraz pro seznam vzorů jmen (bez ohledu na velikost písmen, jako `_shoda`)."""
    klic = tuple(jmena)
    if not klic:
        return None
    rx = _REGEXY.get(klic)
    if rx is None:
        rx = re.compile("|".join("(?:%s)" % translate(j.lower()) for j in klic))
        if len(_REGEXY) > 32:
            _REGEXY.clear()
        _REGEXY[klic] = rx
    return rx


def jmena_zapisu(pol: dict) -> list[str]:
    """jména chráněná proti zápisu v libovolné hloubce kořene (konfigurace agenta, ZAPIS_V_CWD,
    jména a vzory `**/jméno`, `**/jméno/**` z chráněného zápisu)."""
    jmena = list(pol.get("agent") or KONFIGURACE_AGENTA) + list(ZAPIS_V_CWD) + list(pol["zapis"]["jmena"])
    for g in pol["zapis"]["globy"]:
        if len(g) in (2, 3) and g[0] == "**" and (len(g) == 2 or g[2] == "**") and g[1] != "**":
            jmena.append(g[1])
    return list(dict.fromkeys(jmena))


def prohledej(koren: str, pol: dict, max_polozek: int | None = None, max_hloubka: int | None = None,
              max_s: float | None = None, konec: float | None = None) -> dict:
    """Strom pod kořenem bez symlinků: .git adresáře, závislosti, položky s chráněným jménem a jménem
    chráněného zápisu. `uplne` = False, když strom přesáhl počet položek, hloubku nebo čas (fail-closed).
    Neprochází .git, závislosti z ZAVISLOSTI_NEPROCHAZET, NEPROCHAZET, chráněné cesty a chráněná jména
    (ta se maskují celá). Vzory jsou jeden regulární výraz, ~0,3 s na 170 tisíc položek. `konec` = sdílený
    časový limit (time.monotonic) pro víc kořenů. Soubor `.git` (worktree, submodul) patří do `zapis`."""
    max_polozek = SKEN_MAX_POLOZEK if max_polozek is None else max_polozek
    max_hloubka = SKEN_MAX_HLOUBKA if max_hloubka is None else max_hloubka
    max_s = SKEN_MAX_S if max_s is None else max_s
    vysledek: dict = {"git": [], "zavislosti": [], "chranene": [], "zapis": [], "uplne": True, "polozek": 0}
    rx_chr = _regex_jmen(pol["chranene"]["jmena"])
    rx_zap = _regex_jmen(jmena_zapisu(pol))
    # rodič chráněný nebyl (do chráněných se nevstupuje), stačí přesná shoda
    chranene_cesty = frozenset(pol["chranene"]["cesty"])
    konec = time.monotonic() + max_s if konec is None else konec
    pocet = 0
    zasobnik = [(koren, 0)]
    while zasobnik:
        adr, hloubka = zasobnik.pop()
        try:
            polozky = list(os.scandir(adr))
        except OSError:
            continue
        for e in polozky:
            pocet += 1
            if pocet > max_polozek or (pocet & 1023 == 0 and time.monotonic() > konec):
                vysledek["uplne"] = False
                vysledek["polozek"] = pocet
                return vysledek
            try:
                je_adr = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            jmeno = e.name.lower()
            if rx_chr is not None and rx_chr.match(jmeno):
                vysledek["chranene"].append(e.path)
                continue
            if (rx_zap is not None and rx_zap.match(jmeno)) or (e.name == ".git" and not je_adr):
                vysledek["zapis"].append(e.path)
            if not je_adr:
                continue
            if e.name == ".git":
                vysledek["git"].append(e.path)
                continue
            if e.name in ZAVISLOSTI:
                vysledek["zavislosti"].append(e.path)
                if e.name in ZAVISLOSTI_NEPROCHAZET:
                    continue
            if e.name in NEPROCHAZET or e.path in chranene_cesty:
                continue
            if hloubka + 1 >= max_hloubka:
                vysledek["uplne"] = False
                vysledek["polozek"] = pocet
                return vysledek
            zasobnik.append((e.path, hloubka + 1))
    if time.monotonic() > konec:
        vysledek["uplne"] = False
    vysledek["polozek"] = pocet
    return vysledek


def _varianty(vzor: str) -> list[str]:
    """Rozbalí jednu úroveň {a,b} (glob Grep/ripgrep)."""
    m = re.search(r"\{([^{}]*)\}", vzor)
    if not m:
        return [vzor]
    return [vzor[:m.start()] + x + vzor[m.end():] for x in m.group(1).split(",")][:50]


def grep_orakulum(vstup: dict, cwd: object, pol: dict) -> bool:
    """S1: Grep (i jen se jmény souborů) prozradí po bitech obsah chráněného souboru pod kořenem.
    True, když glob míří na chráněný vzor, nebo strom obsahuje chráněný soubor, na který glob může
    trefit (bez glob každý), nebo se strom nedal projít celý."""
    glob = vstup.get("glob")
    varianty = _varianty(glob) if isinstance(glob, str) and glob else []
    for v in varianty:
        posledni = v.rstrip("/").split("/")[-1]
        if any(_shoda(posledni, j) or posledni.lower() == j.lower() for j in pol["chranene"]["jmena"]):
            return True
    koren = vstup.get("path") if isinstance(vstup.get("path"), str) and vstup.get("path") else cwd
    cesty = normalizuj(koren, cwd, pol["home"])
    if not cesty:
        return True
    if not os.path.isdir(cesty[-1]):
        return False
    sken = prohledej(cesty[-1], pol)
    if not sken["uplne"]:
        return True
    if not sken["chranene"]:
        return False
    if not varianty:
        return True
    for c in sken["chranene"]:
        rel = [k for k in os.path.relpath(c, cesty[-1]).split("/") if k]
        for v in varianty:
            casti = [k for k in v.split("/") if k]
            if ("/" not in v and _shoda(rel[-1], v)) or ("/" in v and _glob(rel, casti if casti[:1] == ["**"]
                                                                            else ["**"] + casti)):
                return True
    return False


# ---------------------------------------------------------------- bwrap

def zaklad_bwrap(bwrap: str, sit: bool = False, prostredi: dict | None = None,
                 runtime: str | None = None) -> list[str]:
    """K1: oddělené jmenné prostory (bez sítě, IPC, PID), nová session (TIOCSTI), čisté prostředí,
    souborový systém jen pro čtení, vlastní /dev, /proc, /tmp a /run (sockety D-Bus, docker, agentů,
    X11 a session nejsou vidět; abstraktní sockety odděluje síťový namespace)."""
    argv = [bwrap, "--unshare-all"] + (["--share-net"] if sit else []) + [
        "--new-session", "--die-with-parent", "--clearenv"]
    for k, v in sorted((prostredi or {"PATH": "/usr/bin:/bin"}).items()):
        argv += ["--setenv", k, v]
    argv += ["--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--tmpfs", "/run"]
    if os.path.isdir("/var/run") and not os.path.islink("/var/run"):
        argv += ["--tmpfs", "/var/run"]
    if runtime and os.path.isabs(runtime) and os.path.isdir(runtime) and not any(pod(runtime, z) for z in ZAKRYTE):
        argv += ["--tmpfs", runtime]
    if sit and os.path.isdir("/run/systemd/resolve"):
        argv += ["--ro-bind", "/run/systemd/resolve", "/run/systemd/resolve"]
    return argv


# Obal je jeden argument `bash -c` (limit jádra 128 KiB na argument): počet masek i délka s rezervou.
MAX_MASEK = 2000
MAX_DELKA_OBALU = 100_000


def _skryte(pol: dict) -> list[str]:
    """domov a místa připojení (NAS, disky), která obal skryje (realpath, existující adresáře)."""
    vysledek = []
    for c in [pol["home"]] + list(pol.get("bash_skryt", SKRYTE_VYCHOZI)):
        if isinstance(c, str) and os.path.isabs(c):
            r = os.path.realpath(c)
            if r != "/" and os.path.isdir(r):
                vysledek.append(r)
    return sorted(set(vysledek))


# ---------------------------------------------------------------- Git: háčky a konfigurace

def _git_konfigurace(soubor: str) -> dict:
    """Minimální čtení konfigurace Gitu: core.hookspath, core.worktree a cesty [include]/[includeIf]."""
    vysledek: dict = {"hookspath": [], "worktree": None, "include": []}
    try:  # jen běžný soubor (FIFO nebo zařízení by hook zablokovaly)
        fd = os.open(soubor, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return vysledek
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return vysledek
        text = os.read(fd, 256 * 1024).decode("utf-8", "replace")
    except OSError:
        return vysledek
    finally:
        os.close(fd)
    sekce = ""
    for radek in text.splitlines():
        s = radek.strip()
        if not s or s[0] in "#;":
            continue
        if s.startswith("["):
            konec = s.find("]")
            sekce = s[1:konec].strip().split(None, 1)[0].lower() if konec > 1 else ""
            s = s[konec + 1:].strip() if konec > 0 else ""
            if not s:
                continue
        if "=" not in s:
            continue
        klic, hodnota = (x.strip() for x in s.split("=", 1))
        if len(hodnota) >= 2 and hodnota[0] == hodnota[-1] == '"':
            hodnota = hodnota[1:-1]
        klic = klic.lower()
        if not hodnota:
            continue
        if sekce == "core" and klic == "hookspath":
            vysledek["hookspath"].append(hodnota)
        elif sekce == "core" and klic == "worktree":
            vysledek["worktree"] = hodnota
        elif sekce in ("include", "includeif") and klic == "path":
            vysledek["include"].append(hodnota)
    return vysledek


def _git_cesta(hodnota: str, zaklad: str | None, home: str) -> str | None:
    if hodnota == "~" or hodnota.startswith("~/"):
        return os.path.normpath(home + hodnota[1:])
    if os.path.isabs(hodnota):
        return os.path.normpath(hodnota)
    return os.path.normpath(os.path.join(zaklad, hodnota)) if zaklad else None


def git_globalni(home: str, env: dict | None = None) -> list[str]:
    """core.hooksPath z globální konfigurace (v obalu skrytá, relativní hodnota platí v každém repozitáři)."""
    env = os.environ if env is None else env
    konf = env.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    hodnoty: list[str] = []
    for soubor in (os.path.join(home, ".gitconfig"), os.path.join(konf, "git", "config")):
        hodnoty += _git_konfigurace(soubor)["hookspath"]
    return hodnoty


def git_polozky(g: str, globalni: list[str], home: str, konec: float) -> dict:
    """Gitdir `g` (.git) a gitdiry submodulů pod ním: co musí být v obalu jen pro čtení (`ro`, existující),
    chybějící hooks/ (`hooks`, obal je vytvoří jako prázdný tmpfs jen pro čtení) a chybějící konfigurace,
    soubory [include] a cíle core.hooksPath (`sledovat`, jen kontrola integrity: vzniknou-li během příkazu,
    okno se zavře). `uplne` = False nad limit (fail-closed)."""
    vysledek: dict = {"ro": [], "hooks": [], "sledovat": [], "uplne": True}
    pocet = 0
    fronta = [(g, os.path.dirname(g), 0)]
    while fronta:
        gd, worktree, hloubka = fronta.pop()
        hooks = os.path.join(gd, "hooks")
        (vysledek["ro"] if os.path.lexists(hooks) else vysledek["hooks"]).append(hooks)
        hookspath = list(globalni)
        konfigurace = [os.path.join(gd, f) for f in GIT_SOUBORY]
        videne: set = set()
        while konfigurace:
            soubor = konfigurace.pop()
            if soubor in videne or len(videne) > 32:
                continue
            videne.add(soubor)
            if not os.path.lexists(soubor):
                vysledek["sledovat"].append(soubor)
                continue
            vysledek["ro"].append(soubor)
            k = _git_konfigurace(soubor)
            hookspath += k["hookspath"]
            konfigurace += [c for c in (_git_cesta(x, os.path.dirname(soubor), home) for x in k["include"]) if c]
            if k["worktree"] and os.path.basename(soubor) == "config":
                worktree = _git_cesta(k["worktree"], gd, home)
        for x in hookspath:
            c = _git_cesta(x, worktree, home)
            if c:
                (vysledek["ro"] if os.path.lexists(c) else vysledek["sledovat"]).append(c)
        try:
            stromy = list(os.scandir(os.path.join(gd, "worktrees")))
        except OSError:
            stromy = []
        for e in stromy[:1000]:
            c = os.path.join(e.path, "config.worktree")
            if e.is_dir(follow_symlinks=False):
                (vysledek["ro"] if os.path.lexists(c) else vysledek["sledovat"]).append(c)
        # submoduly: .git/modules/<jméno se složkami>/ je gitdir (HEAD, config nebo hooks), do objektů se nevstupuje
        zasobnik = [os.path.join(gd, "modules")] if hloubka < GIT_MAX_HLOUBKA else []
        while zasobnik:
            try:
                polozky = list(os.scandir(zasobnik.pop()))
            except OSError:
                continue
            for e in polozky:
                pocet += 1
                if pocet > GIT_MAX_POLOZEK or (pocet & 255 == 0 and time.monotonic() > konec):
                    vysledek["uplne"] = False
                    return vysledek
                if not e.is_dir(follow_symlinks=False):
                    continue
                if any(os.path.lexists(os.path.join(e.path, x)) for x in ("HEAD", "config", "hooks")):
                    fronta.append((e.path, None, hloubka + 1))  # gitdir (i neúplný, git ho může dostat)
                elif e.path.count("/") - gd.count("/") < 32:
                    zasobnik.append(e.path)
    if time.monotonic() > konec:
        vysledek["uplne"] = False
    return vysledek


# ---------------------------------------------------------------- integrita konfigurace agenta

PRAZDNY = "prazdny-adresar"


def _podpis(c: str) -> str | None:
    """typ a inode (symlink i s cílem): existující položky jsou v obalu jen pro čtení, změna typu nebo inode
    znamená nahrazení, smazání nebo přejmenování; mtime ne (Claude Code sám zapisuje do .claude/)."""
    try:
        st = os.lstat(c)
    except OSError:
        return None
    if stat.S_ISLNK(st.st_mode):
        try:
            return "l:%d:%d:%s" % (st.st_dev, st.st_ino, os.readlink(c))
        except OSError:
            return "l:%d:%d:" % (st.st_dev, st.st_ino)
    return "%s:%d:%d" % ("d" if stat.S_ISDIR(st.st_mode) else "f", st.st_dev, st.st_ino)


def _mini_politika(pol: dict, koreny: list[str]) -> dict:
    """co potřebuje sken pro kontrolu integrity (uloží se ke snímku, hook po běhu nečte politiku)."""
    return {"chranene": {"jmena": list(pol["chranene"]["jmena"]), "globy": [],
                         "cesty": [c for c in pol["chranene"]["cesty"] if any(pod(c, k) for k in koreny)]},
            "agent": jmena_zapisu(pol), "zapis": {"jmena": [], "globy": [], "cesty": []}}


def _sledovane(koreny: list[str], mini: dict, home: str, globalni: list[str], konec: float,
               skeny: list[dict] | None = None) -> dict | None:
    """podpisy sledovaných položek zapisovatelných kořenů (konfigurace agenta a chráněný zápis podle jmen,
    soubory .git, gitdiry a jejich háčky a konfigurace); None = sken nedoběhl."""
    rx = _regex_jmen(jmena_zapisu(mini))
    polozky: dict = {}
    for i, k in enumerate(koreny):
        sken = skeny[i] if skeny is not None else prohledej(k, mini, konec=konec)
        if not sken["uplne"]:
            return None
        cesty = list(sken["zapis"]) + list(sken["git"])
        cesty += [c for c in sken["chranene"] if rx is not None and rx.match(os.path.basename(c).lower())]
        for g in sken["git"]:
            gp = git_polozky(g, globalni, home, konec)
            if not gp["uplne"]:
                return None
            cesty += gp["ro"] + gp["hooks"] + gp["sledovat"]
        for c in cesty:
            if pod(c, k):
                polozky[c] = _podpis(c)
    return polozky


def kontrola_integrity(snimek: dict) -> dict:
    """Po běhu Bash: znovu prohledá zapisovatelné kořeny snímku a vrátí nové, smazané a nahrazené položky.
    `uplne` = False, když sken nedoběhl (volající to bere jako změnu, fail-closed)."""
    konec = time.monotonic() + SKEN_MAX_S
    ted = _sledovane(list(snimek["koreny"]), snimek["mini"], snimek["home"], list(snimek.get("git_globalni") or []),
                     konec)
    if ted is None:
        return {"zmeny": [], "uplne": False}
    pred = snimek["polozky"]
    zmeny = []
    for c in sorted(set(pred) | {c for c, v in ted.items() if v is not None}):
        a, b = pred.get(c), ted.get(c)
        if a == PRAZDNY:
            if b is None or (b.startswith("d:") and not _neprazdny(c)):
                continue
        elif a == b:
            continue
        zmeny.append(c)
    return {"zmeny": zmeny, "uplne": True}


def _neprazdny(c: str) -> bool:
    try:
        with os.scandir(c) as it:
            return any(True for _ in it)
    except OSError:
        return True


# ---------------------------------------------------------------- obal bwrap

def obal_bwrap(bwrap: str, prikaz: str, cwd: str, pol: dict, rezim: str | None = None) -> str | None:
    """Příkaz pro Bash v obalu bwrap (viz `obal_info`), None = bezpečný obal nejde sestavit."""
    return obal_info(bwrap, prikaz, cwd, pol, rezim)["obal"]


def obal_info(bwrap: str, prikaz: str, cwd: str, pol: dict, rezim: str | None = None) -> dict:
    """Obal bwrap s maskou chráněných cest, původní příkaz jako jeden argument, a snímek integrity.

    domov a místa připojení jsou skrytá (tmpfs). Vidět je jen pracovní adresář, kořeny
    `bash_koreny` režimu a složky nástrojů z domova jen pro čtení (`bash_domov`). Zapisovat jde jen do
    zapisovatelných kořenů (bez chráněného zápisu, konfigurace agenta, háčků a konfigurace Gitu,
    závislostí) a do soukromého /tmp. Předci každé položky jen pro čtení v zapisovatelném kořeni se připojí
    sami na sebe, takže je nejde přejmenovat ani smazat (EBUSY). Připojení jdou od mělkých k hlubokým.
    Výsledek {"obal": str | None, "integrita": snímek pro `kontrola_integrity` | None}; obal None = nejde
    sestavit (pracovní adresář je domov nebo jeho předek, strom nedoběhl, příliš mnoho masek), Bash se
    pak ptá bez obalu."""
    nic = {"obal": None, "integrita": None}
    cwd = os.path.realpath(cwd)
    if not os.path.isdir(cwd):
        return nic
    zakryte = ZAKRYTE + ((pol["runtime"],) if pol.get("runtime") else ())
    skryte = _skryte(pol)
    if any(pod(x, cwd) for x in skryte) or cwd == "/":
        return nic  # cwd by vrátil celý domov nebo místo připojení
    koreny = [(cwd, "rw")]
    for c, pristup in (pol.get("bash_koreny") or {}).get(rezim, []) if rezim else []:
        k = os.path.realpath(c)
        if (os.path.isdir(k) and k not in [x for x, _ in koreny] and not any(pod(x, k) for x in skryte)
                and koren_bash_chyba(k, pol) is None):
            koreny.append((k, "rw" if pristup == "rw" else "ro"))
    konec = time.monotonic() + SKEN_MAX_S  # sdílený rozpočet všech kořenů
    skeny = []
    for k, _ in koreny:
        sken = prohledej(k, pol, konec=konec)
        if not sken["uplne"]:
            return nic
        skeny.append(sken)
    zapisovatelne = [k for k, pristup in koreny
                     if pristup == "rw" and not chranena(k, "/", pol, zapis=True)
                     and not any(pod(k, z) for z in zakryte if z != "/tmp")]
    globalni = git_globalni(pol["home"], {"XDG_CONFIG_HOME": pol.get("xdg_config") or ""})
    git_ro: list[str] = []
    git_hooks: list[str] = []
    for sken in skeny:
        for g in sken["git"]:
            gp = git_polozky(g, globalni, pol["home"], konec)
            if not gp["uplne"]:
                return nic
            git_ro += gp["ro"]
            git_hooks += gp["hooks"]

    argv = zaklad_bwrap(bwrap, bool(pol.get("bwrap_sit")), pol.get("bwrap_env"), pol.get("runtime"))
    operace: list = []  # (hloubka, pořadí, argumenty): rodič se připojí dřív než potomek

    def pridej(cesta: str, *args: str) -> None:
        operace.append((cesta.rstrip("/").count("/"), len(operace), list(args)))

    for x in skryte:
        if not any(pod(x, z) for z in zakryte):  # pod /tmp a /run je obal už soukromý
            pridej(x, "--tmpfs", x)

    def skryto(c: str) -> bool:
        return any(pod(c, x) for x in skryte) or any(pod(c, z) for z in zakryte)

    domov = []
    for d in pol.get("bash_domov") or []:
        d = os.path.normpath(d) if isinstance(d, str) and os.path.isabs(d) else None
        src = os.path.realpath(d) if d else None
        if (not d or not os.path.isdir(src) or not skryto(d) or chranena(d, "/", pol) or chranena(src, "/", pol)
                or konfigurace_agenta([d, src], pol) or any(pod(d, k) or pod(k, d) for k, _ in koreny)):
            continue
        pridej(d, "--ro-bind", src, d)
        domov.append(d)

    for k, _ in koreny:
        pridej(k, "--bind" if k in zapisovatelne else "--ro-bind", k, k)

    viditelne = [k for k, _ in koreny] + domov

    def maskovat(c: str) -> bool:
        # vidět je: pod kořenem nebo složkou nástrojů, předek kořene (skryje i kořen), mimo skrytá místa
        return any(pod(c, v) or pod(v, c) for v in viditelne) or not skryto(c)

    maskovane: list[str] = []
    masky = 0
    chranene = {os.path.realpath(x) for x in pol["chranene"]["cesty"]}
    for sken in skeny:
        chranene |= set(sken["chranene"])
    for c in sorted(chranene):
        if not maskovat(c) or any(pod(c, m) for m in maskovane):
            continue
        if any(pod(c, z) for z in zakryte) and not any(pod(c, v) for v in viditelne):
            continue
        if os.path.isdir(c) and not os.path.islink(c):
            pridej(c, "--tmpfs", c)
            maskovane.append(c)
        elif os.path.lexists(c) and not os.path.islink(c):
            pridej(c, "--ro-bind", "/dev/null", c)
        else:
            continue
        masky += 1

    def nejblizsi_koren(c: str) -> str | None:
        """nejhlubší kořen, pod kterým c leží (c ≠ kořen)."""
        kandidati = [k for k, _ in koreny if pod(c, k) and c != k]
        return max(kandidati, key=len) if kandidati else None

    chranit: list[str] = []  # položky jen pro čtení, jejichž předky je třeba připojit na sebe
    vytvorene: list[str] = []  # chybějící hooks/, které bwrap založí jako místo připojení
    if zapisovatelne:
        jen_cteni = [os.path.realpath(x) for x in pol["zapis"]["cesty"]] + git_ro
        for sken in skeny:
            jen_cteni += sken["zapis"] + sken["zavislosti"]
        hotovo = set()
        for c in sorted(set(jen_cteni)):
            # symlink (CLAUDE.md -> AGENTS.md) i symlink ve složce cesty: jen pro čtení se připojí cíl
            cil = os.path.realpath(c)
            if (cil in hotovo or not os.path.exists(cil) or not any(pod(cil, k) for k in zapisovatelne)
                    or any(pod(cil, m) for m in maskovane)):
                continue
            pridej(cil, "--ro-bind", cil, cil)
            hotovo.add(cil)
            chranit.append(cil)
            masky += 1
        for h in sorted(set(git_hooks)):
            if any(pod(h, k) for k in zapisovatelne) and not any(pod(h, m) for m in maskovane):
                pridej(h, "--tmpfs", h, "--remount-ro", h)
                chranit.append(h)
                vytvorene.append(h)
                masky += 1
        chranit += [k for k, p in koreny if p == "ro"]
    predci: set = set()
    for c in chranit:
        k = nejblizsi_koren(c)
        if k is None or k not in zapisovatelne:
            continue
        d = os.path.dirname(c)
        while d != k and pod(d, k):
            predci.add(d)
            d = os.path.dirname(d)
    for d in sorted(predci):
        pridej(d, "--bind", d, d)
        masky += 1
    if masky > MAX_MASEK:
        return nic
    for _, _, args in sorted(operace, key=lambda x: (x[0], x[1])):
        argv += args
    argv += ["--chdir", cwd, "--", "/bin/bash", "-c", prikaz]
    obal = shlex.join(argv)
    if len(obal) > MAX_DELKA_OBALU:
        return nic
    integrita = None
    if zapisovatelne:
        mini = _mini_politika(pol, zapisovatelne)
        idx = [i for i, (k, _) in enumerate(koreny) if k in zapisovatelne]
        polozky = _sledovane(zapisovatelne, mini, pol["home"], globalni, konec, [skeny[i] for i in idx])
        if polozky is None:
            return nic
        for h in vytvorene:
            if polozky.get(h) is None:
                polozky[h] = PRAZDNY  # obal vytvoří prázdný adresář (místo připojení)
        integrita = {"v": 1, "koreny": zapisovatelne, "home": pol["home"], "git_globalni": globalni, "mini": mini,
                     "polozky": {c: v for c, v in polozky.items() if v is not None}}
    return {"obal": obal, "integrita": integrita}


# ---------------------------------------------------------------- rozhodnutí

def nahled(text: object, delka: int = 60) -> str:
    s = _RIDICI.sub(" ", text if isinstance(text, str) else "")
    s = " ".join(s.split()).replace("`", "'")
    return s if len(s) <= delka else s[:delka - 1] + "…"


def _vysledek(rozhodnuti, duvod, vstup=None) -> dict:
    return {"rozhodnuti": rozhodnuti, "duvod": duvod, "vstup": vstup}


def _cesta_nastroje(nastroj: str, vstup: dict) -> object:
    if nastroj in NASTROJE_HLEDANI:
        return vstup.get("path")
    return vstup.get("notebook_path") if nastroj.startswith("Notebook") and vstup.get("notebook_path") else vstup.get("file_path")


def rozhodni(pol: dict, rezim: str, nastroj: object, vstup: object, cwd: object,
             znacka: str | None = None, trida: str = "telegram") -> dict:
    """Rozhodnutí pro tah třídy telegram (rezim cteni|prace|plny) nebo omezený tah (rezim omezeny).

    Výsledek: {"rozhodnuti": allow|ask|deny|None, "duvod": str, "vstup": upravený vstup nebo None}.
    None = hook nevrací rozhodnutí (běžný tok Claude Code).
    """
    if not isinstance(nastroj, str) or not JMENO_NASTROJE.match(nastroj):
        return _vysledek("deny", "Telegram: neznámý tvar jména nástroje.")
    vstup = vstup if isinstance(vstup, dict) else {}
    reg = pol["registrace"]
    popis = POPIS_REZIMU.get(rezim, rezim)
    hlavicka = ("Telegram #%s · %s" % (znacka, popis)) if znacka and rezim != "omezeny" else (
        "Telegram · %s" % popis if rezim != "omezeny" else "Omezený tah (%s) v otevřeném okně Telegramu" % trida)

    # 1. tvrdé zákazy a běh na pozadí
    if nastroj in pol["zakazane"]:
        return _vysledek("deny", "%s: %s není v tahu z Telegramu povolený." % (hlavicka, nastroj))
    if vstup.get("run_in_background") is True:
        return _vysledek("deny", "%s: běh na pozadí (%s) není v tahu z Telegramu povolený." % (hlavicka, nastroj))
    if nastroj == "mcp__%s__prepis" % reg and vstup.get("engine") == "cloud":
        return _vysledek("deny", "%s: cloudový přepis není v tahu z Telegramu povolený." % hlavicka)

    # 2. konfigurace agenta: zápis z Telegramu tvrdě zakázaný (všechny režimy, nejde odebrat)
    cteni_serveru = ["mcp__%s__%s" % (reg, n) for n in NASTROJE_MCP_CTENI]
    if nastroj in NASTROJE_ZAPISU:
        agent = zapis_agenta(_cesta_nastroje(nastroj, vstup), cwd, pol)
    elif nastroj == "Bash":
        agent = bash_agent(vstup.get("command"), pol)
    elif nastroj in NASTROJE_CTENI_SOUBORU + NASTROJE_HLEDANI or nastroj in cteni_serveru:
        agent = False  # čtení; odpovez, rekni a stav nic nezapisují (text s cestou je jen zpráva)
    else:
        agent = vstup_agent(vstup, cwd, pol)
    if agent:
        return _vysledek("deny", "%s: zápis do konfigurace agenta (%s) je z Telegramu zakázaný, %s zamítnut." % (
            hlavicka, ", ".join(pol.get("agent") or KONFIGURACE_AGENTA), nastroj))

    # 3. chráněné cesty a chráněný zápis
    chraneno = False
    if nastroj in NASTROJE_CTENI_SOUBORU:
        chraneno = chranena(_cesta_nastroje(nastroj, vstup), cwd, pol)
    elif nastroj in NASTROJE_ZAPISU:
        chraneno = chranena(_cesta_nastroje(nastroj, vstup), cwd, pol, zapis=True)
    elif nastroj in NASTROJE_HLEDANI:
        chraneno = koren_hledani_chraneny(vstup.get("path"), cwd, pol) or vzor_hledani_chraneny(vstup, cwd, pol)
    elif nastroj == "Bash":
        chraneno = bash_chraneny(vstup.get("command"), cwd, pol) or pojistka_nalez(vstup.get("command"), pol["pojistka"])
    else:
        chraneno = vstup_chraneny(vstup, cwd, pol, zapis=nastroj not in cteni_serveru)
    if chraneno:
        return _vysledek("deny", "%s: %s sahá na chráněnou cestu, zamítnuto." % (hlavicka, nastroj))
    if nastroj == "Grep" and not GREP_UPDATED_INPUT_OVERENO and vstup.get("output_mode") not in (None, "files_with_matches"):
        return _vysledek("deny", "%s: Grep z Telegramu jen se jmény souborů (output_mode files_with_matches)." % hlavicka)
    orakulum = nastroj == "Grep" and grep_orakulum(vstup, cwd, pol)

    # 4. režim
    cteni = pol["omezeny"] if rezim == "omezeny" else pol["cteni"]
    if nastroj in NASTROJE_CTENI_SOUBORU + NASTROJE_HLEDANI:
        cesta = _cesta_nastroje(nastroj, vstup)
        v_korenu = v_korenech(cesta if isinstance(cesta, str) and cesta else cwd, cwd, pol)
    else:
        v_korenu = True
    upraveny = dict(vstup, output_mode="files_with_matches") if nastroj == "Grep" else None
    cteci = nastroj in cteni and v_korenu

    if rezim in ("omezeny", "cteni"):
        if orakulum:
            return _vysledek("deny", "%s: Grep nad stromem s chráněnými soubory (.env, klíče) nebo příliš velkým "
                                     "stromem, zúž path nebo glob." % hlavicka)
        if cteci:
            return _vysledek("allow", "%s: čtení." % hlavicka, upraveny)
        return _vysledek("deny", "%s: %s není v tomto režimu povolený." % (hlavicka, nastroj))

    def dotaz(duvod_nastroje, upraveny_vstup=None):
        if pol.get("pri_dotazu") == "odmitnout" or pol.get("bez_rc"):
            return _vysledek("deny", "%s: %s vyžaduje potvrzení u PC, z Telegramu zamítnuto." % (hlavicka, duvod_nastroje))
        return _vysledek("ask", "%s: %s – potvrď v terminálu nebo Remote Control" % (hlavicka, duvod_nastroje),
                         upraveny_vstup)

    prikaz = vstup.get("command") if nastroj == "Bash" else None
    popis_volani = "%s `%s`" % (nastroj, nahled(prikaz)) if nastroj == "Bash" else nastroj
    if orakulum:
        popis_volani = "Grep nad stromem s chráněnými soubory"
    bwrap_vstup = None
    integrita = None
    if nastroj == "Bash" and pol.get("bwrap") and isinstance(prikaz, str) and isinstance(cwd, str) and os.path.isabs(cwd):
        info = obal_info(pol["bwrap"], prikaz, cwd, pol, rezim)
        bwrap_vstup = dict(vstup, command=info["obal"]) if info["obal"] else None
        integrita = info["integrita"] if info["obal"] else None

    def s_integritou(v: dict) -> dict:
        """Bash s obalem: snímek pro kontrolu integrity po běhu (hook ho uloží k tool_use_id)."""
        if bwrap_vstup is not None and v["vstup"] is bwrap_vstup and v["rozhodnuti"] in ("allow", "ask"):
            v["integrita"] = integrita
        return v
    if nastroj == "Bash":
        popis_volani += " v izolaci bwrap" if bwrap_vstup else " (bez izolace bwrap)"

    if rezim == "prace":
        if cteci and not orakulum:
            return _vysledek("allow", "%s: čtení." % hlavicka, upraveny)
        return s_integritou(dotaz(popis_volani, bwrap_vstup if nastroj == "Bash" else upraveny))

    if rezim == "plny":
        if nastroj == "Bash":
            # allow jen vědomě (bash_bez_dotazu); cizí hook s allow nebo ask může obal zahodit
            if (bwrap_vstup is not None and BASH_UPDATED_INPUT_OVERENO and pol.get("overena_verze")
                    and pol.get("bash_bez_dotazu") is True):
                return s_integritou(_vysledek("allow", "%s: Bash v izolaci bwrap." % hlavicka, bwrap_vstup))
            return s_integritou(dotaz(popis_volani, bwrap_vstup))
        if nastroj == "Grep":
            if orakulum:
                return dotaz(popis_volani, upraveny)
            return _vysledek("allow", "%s: hledání jen se jmény souborů." % hlavicka, upraveny)
        if nastroj.startswith("mcp__") and nastroj not in pol["cteni"]:
            return dotaz(nastroj)
        if not nastroj.startswith("mcp__") and nastroj not in pol["zname"]:
            return dotaz("neznámý nástroj " + nastroj)
        return _vysledek(None, "")

    return _vysledek("deny", "%s: neznámý režim okna." % hlavicka)
