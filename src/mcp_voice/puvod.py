"""Původ tahu z transkriptu Claude Code, ověření povelu okna a sdílené stavové soubory (jen standardní knihovna).

Zprávu z Telegramu pozná podle `origin.verifiedPeerPid` a `verifiedPeerProcStart`, které Claude Code
doplní podle procesu odesílatele (odesílatel je nepodvrhne, text obálky ano). Pole transkriptu jsou
nedokumentovaná (ověřeno na Claude Code 2.1.314): neznámý tvar = přísnější třída.
Z transkriptu se čtou metadata; text záznamu jen pro porovnání otisku a hledání značky, nikdy do výstupu.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
import time
import unicodedata

TELEGRAM = "telegram"
CLOVEK = "clovek"
CIZI = "cizi"
NAPLANOVANY = "naplanovany"
ULOHA = "uloha"
NEZNAME = "nezname"
OMEZENE_TRIDY = (CIZI, NAPLANOVANY, NEZNAME)

BLOK = 64 * 1024
MAX_OTEVRENO = 1024 * 1024
MAX_ZAVRENO = 4 * 1024 * 1024
MAX_STAVOVY = 256 * 1024
MAX_ZNACEK = 200
POVOLENE_PROMPT_SOURCE = ("typed", "queued", None)
OVERENE_VERZE = (2, 1, 314)
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ZNACKA = re.compile(r"\[Telegram #(\d{1,15})[ \]]")
PROMPT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
AGENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


# ---------------------------------------------------------------- procesy

def stat_procesu(pid: int) -> tuple[str, int, str] | None:
    """(comm, ppid, start): start = pole 22 z /proc/<pid>/stat (tvar verifiedPeerProcStart)."""
    try:
        with open("/proc/%d/stat" % int(pid), "rb") as fh:
            data = fh.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return None
    l, r = data.find("("), data.rfind(")")
    if l < 0 or r < 0:
        return None
    zbytek = data[r + 2:].split()
    try:
        return data[l + 1:r], int(zbytek[1]), str(int(zbytek[19]))
    except (IndexError, ValueError):
        return None


def najdi_claude(start_pid: int, max_kroku: int = 16) -> int | None:
    pid = start_pid
    for _ in range(max_kroku):
        if pid <= 1:
            return None
        st = stat_procesu(pid)
        if st is None:
            return None
        if st[0] == "claude":
            return pid
        pid = st[1]
    return None


def start_procesu(pid: int) -> str | None:
    st = stat_procesu(pid)
    return st[2] if st else None


def zije(pid: object, start: object) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool):
        return False
    st = stat_procesu(pid)
    return st is not None and st[2] == str(start)


# ---------------------------------------------------------------- stavové soubory (sdílené s hookem)

def adresar(stav) -> str:
    return os.path.join(str(stav), "telegram")


def klic_procesu(pid: int, start: str) -> str:
    return "%d-%s" % (int(pid), re.sub(r"[^0-9]", "", str(start)))


def soubor(stav, druh: str, klic: str, pripona: str = ".json") -> str:
    return os.path.join(adresar(stav), "%s-%s%s" % (druh, klic, pripona))


def cti(cesta: str, max_bajtu: int = MAX_STAVOVY):
    """JSON bez symlinků s limitem velikosti; chybí = None, poškozený = ValueError."""
    try:
        fd = os.open(cesta, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > max_bajtu:
            raise ValueError("stavový soubor má neplatný tvar")
        data = os.read(fd, max_bajtu + 1)
    finally:
        os.close(fd)
    return json.loads(data) if data else None


def cti_tise(cesta: str, vychozi=None, max_bajtu: int = MAX_STAVOVY):
    try:
        d = cti(cesta, max_bajtu)
    except (OSError, ValueError):
        return vychozi
    return d if d is not None and (vychozi is None or isinstance(d, type(vychozi))) else vychozi


def zajisti_adresar(cesta: str) -> str:
    stary = os.umask(0o077)
    try:
        os.makedirs(cesta, mode=0o700, exist_ok=True)
    finally:
        os.umask(stary)
    st = os.lstat(cesta)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise PermissionError("stavový adresář není vlastní adresář")
    if stat.S_IMODE(st.st_mode) & 0o077:
        os.chmod(cesta, 0o700)
    return cesta


def zapis(cesta: str, data) -> None:
    """Atomický zápis JSON (0600)."""
    adr = zajisti_adresar(os.path.dirname(cesta))
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=adr)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        os.chmod(tmp, 0o600)
        os.replace(tmp, cesta)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Zamek:
    """fcntl.flock na souboru <cesta>.lock (víc hooků současně)."""

    def __init__(self, cesta: str, cekat_s: float = 3.0):
        self.cesta = cesta + ".lock"
        self.cekat_s = cekat_s
        self.fd = -1

    def __enter__(self):
        zajisti_adresar(os.path.dirname(self.cesta))
        self.fd = os.open(self.cesta, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        konec = time.monotonic() + self.cekat_s
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() > konec:
                    os.close(self.fd)
                    raise TimeoutError("zámek je obsazený")
                time.sleep(0.02)

    def __exit__(self, *exc):
        os.close(self.fd)
        return False


def pripis_jsonl(cesta: str, zaznam: dict) -> None:
    zajisti_adresar(os.path.dirname(cesta))
    with Zamek(cesta):
        fd = os.open(cesta, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            os.write(fd, (json.dumps(zaznam, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
        finally:
            os.close(fd)


def cti_jsonl(cesta: str, max_bajtu: int = 4 * 1024 * 1024) -> list[dict]:
    try:
        fd = os.open(cesta, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError:
        return []
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return []
        data = os.pread(fd, max_bajtu, max(0, st.st_size - max_bajtu))
    finally:
        os.close(fd)
    vysledek = []
    for radek in data.splitlines():
        try:
            d = json.loads(radek)
        except ValueError:
            continue
        if isinstance(d, dict):
            vysledek.append(d)
    return vysledek


def okno_plati(okno: object, session_id: object, ted: float) -> bool:
    """Otevřené okno pro tuto session: stav, session, konec a nečinnost (hook kontroluje sám, i bez serveru)."""
    if not isinstance(okno, dict) or okno.get("stav") != "otevreno" or okno.get("session_id") != session_id:
        return False
    konec, posledni, necinnost = okno.get("konec"), okno.get("posledni_zprava"), okno.get("necinnost_min")
    for v in (konec, posledni, necinnost):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False
    return ted < konec and ted < posledni + necinnost * 60


def over_transkript(session_id: object, transcript_path: object, koren: str | None = None) -> str | None:
    """UUID session, realpath pod ~/.claude/projects, jméno <session_id>.jsonl."""
    if not isinstance(session_id, str) or not UUID.match(session_id):
        return None
    if not isinstance(transcript_path, str) or not transcript_path or "\x00" in transcript_path:
        return None
    koren = os.path.realpath(koren or os.path.expanduser("~/.claude/projects"))
    real = os.path.realpath(transcript_path)
    try:
        if os.path.commonpath([real, koren]) != koren:
            return None
    except ValueError:
        return None
    return real if os.path.basename(real) == session_id + ".jsonl" else None


def identity_serveru(stav, klic: str) -> set[tuple[int, str]]:
    d = cti_tise(soubor(stav, "server", klic), {})
    vysledek = set()
    for x in d.get("identity") if isinstance(d.get("identity"), list) else []:
        if isinstance(x, dict) and isinstance(x.get("pid"), int) and isinstance(x.get("start"), str):
            vysledek.add((x["pid"], x["start"]))
    return vysledek


# ---------------------------------------------------------------- transkript

def radky_od_konce(cesta: str, max_bajtu: int):
    fd = os.open(cesta, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        konec = os.fstat(fd).st_size
        precteno, zbytek, pozice = 0, b"", konec
        while pozice > 0 and precteno < max_bajtu:
            krok = min(BLOK, pozice, max_bajtu - precteno)
            pozice -= krok
            data = os.pread(fd, krok, pozice) + zbytek
            precteno += krok
            radky = data.split(b"\n")
            zbytek = radky[0]
            for r in reversed(radky[1:]):
                if r.strip():
                    yield r
        if pozice == 0 and zbytek.strip():
            yield zbytek
    finally:
        os.close(fd)


def text_zaznamu(d: dict) -> str | None:
    if d.get("type") == "user":
        obsah = (d.get("message") or {}).get("content") if isinstance(d.get("message"), dict) else None
        if isinstance(obsah, str):
            return obsah
        if isinstance(obsah, list):
            if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in obsah):
                return None
            return "\n".join(x.get("text", "") for x in obsah
                             if isinstance(x, dict) and x.get("type") == "text" and isinstance(x.get("text"), str))
        return None
    if d.get("type") == "attachment" and isinstance(d.get("attachment"), dict):
        a = d["attachment"]
        if a.get("type") != "queued_command":
            return None
        p = a.get("prompt")
        if isinstance(p, str):
            return p
        if isinstance(p, list):
            return "\n".join(x.get("text", "") for x in p if isinstance(x, dict) and isinstance(x.get("text"), str))
    return None


def origin_zaznamu(d: dict) -> dict | None:
    if d.get("type") == "attachment":
        a = d.get("attachment")
        if isinstance(a, dict) and a.get("type") == "queued_command":
            o = a.get("origin") if isinstance(a.get("origin"), dict) else d.get("origin")
            return o if isinstance(o, dict) else None
        return None
    o = d.get("origin")
    return o if isinstance(o, dict) else None


def peer_identita(origin: dict) -> tuple[int, str] | None:
    pid, start = origin.get("verifiedPeerPid"), origin.get("verifiedPeerProcStart")
    if isinstance(pid, bool) or not isinstance(pid, int):
        return None
    if isinstance(start, int) and not isinstance(start, bool):
        start = str(start)
    return (pid, start) if isinstance(start, str) else None


def je_zacatek_tahu(d: dict) -> bool:
    # N3: shrnutí komprimace (i s turnOrigin human) tah nezačíná
    return (d.get("type") == "user" and d.get("turnOrigin") is not None and not d.get("isSidechain")
            and not d.get("isCompactSummary"))


def je_lidsky_zacatek(d: dict) -> bool:
    """Začátek tahu napsaný člověkem v terminálu nebo Remote Control (ne návrh, ne systém)."""
    origin = d.get("origin") if isinstance(d.get("origin"), dict) else {}
    return (je_zacatek_tahu(d) and origin.get("kind") == "human" and d.get("turnOrigin") == "human"
            and d.get("promptSource") in POVOLENE_PROMPT_SOURCE)


def _zaznamy(cesta: str, max_bajtu: int, filtr: tuple[bytes, ...]):
    for radek in radky_od_konce(cesta, max_bajtu):
        if filtr and not any(f in radek for f in filtr):
            continue
        try:
            d = json.loads(radek)
        except ValueError:
            continue
        if isinstance(d, dict):
            yield d


def klasifikuj(cesta: str | None, prompt_id: object = None, agent_id: object = None,
               identity: set | frozenset = frozenset(), znacky: dict | None = None, agenti: dict | None = None,
               max_bajtu: int = MAX_OTEVRENO) -> dict:
    """Třída aktuálního tahu: {"trida", "znacky": [#id zpráv serveru v tahu], "zacatek"}; s nalezeným
    začátkem tahu navíc "prompt_id", "lidsky" (je_lidsky_zacatek) a "cas_zacatku".

    Vrstvy se sčítají (stačí jedna): agent, značka prompt_id z UserPromptSubmit, transkript.
    """
    znacky = znacky or {}
    agenti = agenti or {}
    if isinstance(agent_id, str) and agent_id:
        z = agenti.get(agent_id)
        trida = z.get("trida") if isinstance(z, dict) else None
        return {"trida": trida if trida in (TELEGRAM, CLOVEK, CIZI, NAPLANOVANY, ULOHA, NEZNAME) else NEZNAME,
                "znacky": [z.get("znacka")] if isinstance(z, dict) and z.get("znacka") else [], "zacatek": None}
    vysledek = {"trida": NEZNAME, "znacky": [], "zacatek": None}
    znacka_promptu = znacky.get(prompt_id) if isinstance(prompt_id, str) else None
    lidsky_prompt = isinstance(znacka_promptu, dict) and znacka_promptu.get("trida") == CLOVEK
    telegram = znacka_promptu is not None and not lidsky_prompt
    if telegram and isinstance(znacka_promptu, dict) and znacka_promptu.get("znacka"):
        vysledek["znacky"].append(str(znacka_promptu["znacka"]))
    cizi = False
    clovek_z_agenta = False
    zacatek = None
    if cesta:
        try:
            for d in _zaznamy(cesta, max_bajtu, (b'"turnOrigin"', b'"origin"')):
                origin = origin_zaznamu(d)
                if origin is not None and origin.get("kind") == "peer" and not d.get("isSidechain"):
                    ident = peer_identita(origin)
                    if ident is not None and ident in identity:
                        telegram = True
                        t = text_zaznamu(d) or ""
                        m = ZNACKA.search(t)
                        if m and m.group(1) not in vysledek["znacky"]:
                            vysledek["znacky"].append(m.group(1))
                    elif ident is None and isinstance(origin.get("senderTaskId"), str):
                        a = agenti.get(origin["senderTaskId"])
                        trida_agenta = a.get("trida") if isinstance(a, dict) else None
                        if trida_agenta == TELEGRAM:
                            telegram = True
                        elif trida_agenta in (CLOVEK, ULOHA):
                            clovek_z_agenta = True
                        else:
                            cizi = True
                    else:
                        cizi = True
                if je_zacatek_tahu(d):
                    zacatek = d
                    break
        except OSError:
            zacatek = None
    if telegram:
        vysledek["trida"] = TELEGRAM
    elif cizi:
        vysledek["trida"] = CIZI
    elif zacatek is not None:
        to = zacatek.get("turnOrigin")
        if to == "human":
            vysledek["trida"] = CLOVEK
        elif to == "scheduled":
            vysledek["trida"] = NAPLANOVANY
        elif to == "task_notification":
            vysledek["trida"] = ULOHA
        elif to == "peer" and clovek_z_agenta:
            vysledek["trida"] = CLOVEK
        else:
            vysledek["trida"] = NEZNAME
    elif lidsky_prompt and not clovek_z_agenta:
        vysledek["trida"] = CLOVEK  # S5: začátek mimo rozpočet, značku zapsal hook rozsahu podle záznamu začátku
    if zacatek is not None:
        vysledek["zacatek"] = zacatek.get("timestamp")
        pid = zacatek.get("promptId")
        vysledek["prompt_id"] = pid if isinstance(pid, str) else None
        vysledek["lidsky"] = je_lidsky_zacatek(zacatek)
        vysledek["cas_zacatku"] = cas_zaznamu(zacatek)
    return vysledek


# ---------------------------------------------------------------- povel okna

PRIKAZ_JMENO = re.compile(r"<command-name>\s*(/?[^<\s]{1,64})\s*</command-name>")
PRIKAZ_ARGUMENTY = re.compile(r"<command-args>(.{0,500}?)</command-args>", re.S)


def normalizuj(text: str) -> str:
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.lower().split())


def otisk(text: str) -> str:
    return hashlib.sha256(normalizuj(text).encode("utf-8")).hexdigest()


def text_povelu(text: str) -> str:
    """Text záznamu bez značek příkazu (<command-name>/x</command-name><command-args>…)."""
    m = PRIKAZ_JMENO.search(text)
    if not m:
        return text
    jmeno = m.group(1) if m.group(1).startswith("/") else "/" + m.group(1)
    a = PRIKAZ_ARGUMENTY.search(text)
    return (jmeno + " " + (a.group(1) if a else "")).strip()


def cas_zaznamu(d: dict) -> float | None:
    ts = d.get("timestamp")
    if not isinstance(ts, str):
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def verze_ok(verze: object) -> bool:
    if not isinstance(verze, str) or not re.fullmatch(r"\d{1,4}\.\d{1,4}\.\d{1,6}", verze):
        return False
    v = tuple(int(x) for x in verze.split("."))
    return v[:2] == OVERENE_VERZE[:2] and v >= OVERENE_VERZE


def over_povel(cesta: str, prompt_id: object, sha: object, prefix: str, od: float, do: float,
               max_bajtu: int = MAX_ZAVRENO) -> dict:
    """Povel okna: začátek tahu s `promptId` z hooku UserPromptSubmit musí být tento povel napsaný člověkem,
    s časem v [od, do]. Claude Code zapisuje záznam promptu až po hookách UserPromptSubmit, proto ověřuje
    server, ne hook. Vazba na prompt_id: starší záznam se stejným textem neplatí.
    Kód povel_nenalezen = záznam (zatím) v přečtené části transkriptu není."""
    if not isinstance(prompt_id, str) or not PROMPT_ID.match(prompt_id) or not isinstance(sha, str):
        return {"ok": False, "kod": "povel_neplatny"}
    try:
        for d in _zaznamy(cesta, max_bajtu, (b'"turnOrigin"',)):
            if d.get("promptId") != prompt_id or not je_zacatek_tahu(d):
                continue
            if not je_lidsky_zacatek(d):
                return {"ok": False, "kod": "povel_ne_od_cloveka"}
            text = text_zaznamu(d)
            if not isinstance(text, str):
                return {"ok": False, "kod": "povel_neoveren"}
            povel = text_povelu(text)
            if d.get("promptSource") is None and not normalizuj(povel).startswith(normalizuj(prefix)):
                return {"ok": False, "kod": "povel_ne_od_cloveka"}
            if otisk(povel) != sha:
                return {"ok": False, "kod": "povel_neoveren"}
            cas = cas_zaznamu(d)
            if cas is None or not od <= cas <= do:
                return {"ok": False, "kod": "povel_stary"}
            return {"ok": True, "kod": "ok", "verze": d.get("version"), "sha": sha, "povel": povel}
    except OSError:
        return {"ok": False, "kod": "transkript_necitelny"}
    return {"ok": False, "kod": "povel_nenalezen"}


# ---------------------------------------------------------------- doručení a nečinnost

def najdi_doruceni(cesta: str, znacka: str, identity: set, max_bajtu: int = MAX_OTEVRENO) -> str | None:
    """doruceno | chyba_tvaru (značka bez ověřeného PID serveru) | None (nenalezeno)."""
    vzor = re.compile(r"\[Telegram #%s[ \]]" % re.escape(str(znacka)))
    hledat = ("#%s" % znacka).encode()
    vysledek = None
    try:
        for d in _zaznamy(cesta, max_bajtu, (hledat,)):
            if d.get("type") not in ("user", "attachment"):
                continue
            t = text_zaznamu(d)
            if not t or not vzor.search(t):
                continue
            origin = origin_zaznamu(d)
            ident = peer_identita(origin) if origin and origin.get("kind") == "peer" else None
            if ident is not None and ident in identity:
                return "doruceno"
            vysledek = "chyba_tvaru"
    except OSError:
        return None
    return vysledek


def stav_session(cesta: str, ted: float, necinnost_tahu_s: float, max_bajtu: int = MAX_OTEVRENO) -> dict:
    """{"stav": necinna|pracuje, "jak": konec|preruseno|prazdna|None, "zacatek": ts začátku tahu}."""
    try:
        mtime = os.stat(cesta).st_mtime
    except OSError:
        return {"stav": "necinna", "jak": "prazdna", "zacatek": None}
    konec = False
    pouzite, vysledky = set(), set()
    zacatek = None
    try:
        for d in _zaznamy(cesta, max_bajtu, ()):
            if d.get("isSidechain"):
                continue
            if d.get("type") == "system" and d.get("subtype") in ("turn_duration", "stop_hook_summary"):
                konec = True
            msg = d.get("message") if isinstance(d.get("message"), dict) else {}
            obsah = msg.get("content")
            if isinstance(obsah, list):
                for x in obsah:
                    if isinstance(x, dict) and x.get("type") == "tool_use" and isinstance(x.get("id"), str):
                        pouzite.add(x["id"])
                    elif isinstance(x, dict) and x.get("type") == "tool_result" and isinstance(x.get("tool_use_id"), str):
                        vysledky.add(x["tool_use_id"])
            if je_zacatek_tahu(d):
                zacatek = d
                break
    except OSError:
        return {"stav": "pracuje", "jak": None, "zacatek": None}
    if zacatek is None:
        return {"stav": "necinna", "jak": "prazdna", "zacatek": None}
    if konec:
        return {"stav": "necinna", "jak": "konec", "zacatek": zacatek.get("timestamp")}
    if ted - mtime >= necinnost_tahu_s and pouzite <= vysledky:
        return {"stav": "necinna", "jak": "preruseno", "zacatek": zacatek.get("timestamp")}
    return {"stav": "pracuje", "jak": None, "zacatek": zacatek.get("timestamp")}


def posledni_nastroj(cesta: str, max_bajtu: int = MAX_OTEVRENO) -> tuple[bool, float | None]:
    """Nejnovější tool_use hlavního vlákna v přečtené části: (nalezen, čas záznamu nebo None)."""
    for d in _zaznamy(cesta, max_bajtu, (b'"tool_use"',)):
        if d.get("type") != "assistant" or d.get("isSidechain"):
            continue
        msg = d.get("message") if isinstance(d.get("message"), dict) else {}
        obsah = msg.get("content")
        if isinstance(obsah, list) and any(isinstance(x, dict) and x.get("type") == "tool_use" for x in obsah):
            return True, cas_zaznamu(d)
    return False, None


def ma_remote_control(cesta: str, max_bajtu: int = MAX_ZAVRENO) -> bool:
    """Poslední záznam bridge_status (nebo remote_session_change) má URL = Remote Control připojený."""
    try:
        for d in _zaznamy(cesta, max_bajtu, (b'"bridge_status"', b'"remote_session_change"')):
            if d.get("subtype") == "bridge_status":
                return d.get("url") is not None
            a = d.get("attachment")
            if isinstance(a, dict) and a.get("type") == "remote_session_change":
                return a.get("url") is not None
    except OSError:
        pass
    return False
