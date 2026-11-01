"""Hook strážce (`mcp-voice-guard`): kdo smí hlas použít a kdy se nejdřív zeptat uživatele.

PreToolUse (matcher mcp__<registrace>__.*):
  - volání ze subagenta (agent_type nebo agent_id) -> deny, razítko nevznikne;
  - stav, zastav -> bez zásahu;
  - prepis lokálně -> razítko, bez dotazu (nic neodchází);
  - prepis s engine="cloud" -> ask při každém volání (strazce.prepis_cloud: dotaz, výchozí);
  - rekni v session s citlivým kontextem bez platného souhlasu -> ask;
  - jinak razítko, bez dotazu.
  Citlivý kontext (strazce.citlive_kontexty) = signál v transkriptu session (hlavním i subagentů):
  MCP server, typ subagenta, skill, cesta, text v příkazu Bash nebo zadání subagenta, pole vstupu.
  Text pro TTS se neprohledává. Nalezený kontext platí pro session trvale.
  Při ask nebo bez zásahu zapíše razítko pro server (protokol: strazce.py).
  Ask jen v režimech, kde se klient uživatele zeptá (REZIMY_S_DOTAZEM); jinak (bypassPermissions,
  dontAsk, neznámý nebo chybějící režim) se ask nahradí deny a razítko nevznikne.
PostToolUse (mcp__<registrace>__(rekni|prepis)): nástroj proběhl po dotazu = uživatel souhlasil
  -> souhlas pro (session, kontext) podle platnosti kontextu. Registrovat jen PostToolUse,
  ne PostToolUseFailure (ta může přijít i po zamítnutí v dialogu).

Telegram (odpovez, rekni s cílem Telegram, rekni auto v tahu z Telegramu), podle klíčů kontextu:
  telegram: nikdy -> deny i se souhlasem; telegram_platnost: okno -> souhlas jen z téhož okna;
  bez souhlasu v tahu z Telegramu -> ask jen při telegram_dotaz_v_tahu: rc, prace.pri_dotazu: cekat
  a session s Remote Control, jinak deny (server pošle šablonu „odpověď jen v terminálu“).
PostToolUse (Read|Grep|Glob|mcp__.*) v tahu z Telegramu: textové signály kontextů ve výsledku
  nástroje označí session kontextem (R-CTX-6).
Vlastní chyba hooku -> ask s důvodem (fail-to-ask); neplatná konfigurace -> deny. Pád nebo timeout
hooku pokryje "onFailure": "block" v nastavení klienta a razítko (bez něj server nic nepošle).
Je to pojistka proti omylu, ne hranice: proces téhož uživatele může stavové soubory přepsat.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import os
import re
import signal
import sys
import time
from pathlib import Path

from .config import ChybaKonfigurace, CitlivyKontext, Konfigurace, nacti
from .kontexty import Detektor, signaly_radku
from .stavy import zajisti_adresar, zapis_json
from .strazce import TOOL_USE_ID, adresar as adresar_razitek, kanonicky_hash

VERZE = 2
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
NASTROJE_S_RAZITKEM = ("rekni", "prepis", "odpovez")
MAX_VYSLEDEK = 2 * 1024 * 1024
LIMIT_CTENI = 256 * 1024 * 1024  # na jeden běh hooku; další běh pokračuje od uloženého offsetu
HLIDAC_S = 8
MAX_VSTUP = 8 * 1024 * 1024
MAX_STAVOVY = 4 * 1024 * 1024
MAX_SIGNALU = 20
STARI_SESSION_S = 30 * 86400
# Režimy, v nichž klient dotaz z hooku ukáže uživateli. dontAsk ho sám zamítne, bypassPermissions
# by ho mohl přeskočit; neznámý (budoucí) režim se bere jako „nejde se zeptat“.
REZIMY_S_DOTAZEM = frozenset({"default", "acceptEdits", "plan", "auto"})


class StrazceChyba(Exception):
    pass


# ---------------------------------------------------------------- stav

def dnes() -> str:
    return datetime.date.today().isoformat()


def strazce_adr(stav: Path) -> Path:
    return zajisti_adresar(zajisti_adresar(stav) / "strazce")


def nacti_json(cesta: Path, vychozi):
    try:
        fd = os.open(cesta, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError:
        return vychozi
    try:
        with os.fdopen(fd, "rb") as fh:
            d = json.loads(fh.read(MAX_STAVOVY) or b"null")
    except (OSError, ValueError):
        return vychozi
    return d if isinstance(d, type(vychozi)) else vychozi


def zapis_razitko(stav: Path, tool_use_id: object, nastroj: str, argumenty: dict) -> None:
    """Protokol strazce.py: <stav>/razitka/<tool_use_id>.json, 0600, O_EXCL."""
    if not isinstance(tool_use_id, str) or not TOOL_USE_ID.match(tool_use_id):
        raise StrazceChyba("neplatné tool_use_id")
    adr = zajisti_adresar(adresar_razitek(zajisti_adresar(stav)))
    fd = os.open(adr / f"{tool_use_id}.json",
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"v": 1, "nastroj": nastroj, "sha256": kanonicky_hash(argumenty), "cas": time.time(),
                   "hook": "mcp-voice-guard/%d" % VERZE}, fh)


# ---------------------------------------------------------------- detekce kontextů

def otisk(kontexty: tuple[CitlivyKontext, ...]) -> str:
    """Změna signálů v konfiguraci = nový průchod transkriptem od začátku."""
    data = {"k": [dataclasses.asdict(k) for k in kontexty], "home": os.path.expanduser("~")}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def transkripty_session(transcript_path: str, session_id: str) -> list[str]:
    soubory = [transcript_path]
    sub = os.path.join(os.path.dirname(transcript_path), session_id, "subagents")
    try:
        soubory += sorted(os.path.join(sub, n) for n in os.listdir(sub) if n.endswith(".jsonl"))
    except OSError:
        pass
    return soubory


def _pridej(nalezy: dict[str, list[str]], nove: dict[str, list[str]]) -> None:
    for nazev, signaly in nove.items():
        nalezy[nazev] = list(dict.fromkeys(nalezy.get(nazev, []) + signaly))[:MAX_SIGNALU]


def detekuj(data: dict, kfg: Konfigurace) -> dict[str, list[str]]:
    """{název kontextu: [signály]} pro celou session. Nálezy a offsety se ukládají (trvalý příznak)."""
    kontexty = kfg.strazce.citlive_kontexty
    if not kontexty:
        return {}
    sid, tp, cwd = data.get("session_id"), data.get("transcript_path"), data.get("cwd")
    if not isinstance(sid, str) or not UUID.match(sid):
        raise StrazceChyba("neplatné session_id")
    detektory = [Detektor(k) for k in kontexty]
    nalezy: dict[str, list[str]] = {}
    for det in detektory:
        if det.cesta(cwd):
            nalezy[det.kontext.nazev] = ["pracovní adresář"]
    sken_cesta = strazce_adr(kfg.stav_adresar) / f"sken-{sid}.json"
    sken = nacti_json(sken_cesta, {})
    ot = otisk(kontexty)
    if sken.get("v") != VERZE or sken.get("otisk") != ot:
        sken = {}
    ulozene = sken.get("nalezy") if isinstance(sken.get("nalezy"), dict) else {}
    ulozene = {k: [s for s in v if isinstance(s, str)] for k, v in ulozene.items() if isinstance(v, list)}
    _pridej(nalezy, ulozene)
    zbyvaji = [d for d in detektory if d.kontext.nazev not in ulozene]
    if not zbyvaji:
        return nalezy
    if not isinstance(tp, str) or not tp.endswith(sid + ".jsonl"):
        raise StrazceChyba("neplatná cesta transkriptu")
    offsety = sken.get("offsety") if isinstance(sken.get("offsety"), dict) else {}

    def uloz():
        zapis_json(sken_cesta, {"v": VERZE, "otisk": ot, "nalezy": ulozene, "offsety": offsety})

    precteno = 0
    for soubor in transkripty_session(os.path.realpath(tp), sid):
        start = offsety.get(soubor, 0)
        start = start if isinstance(start, int) and not isinstance(start, bool) and start >= 0 else 0
        try:
            fd = os.open(soubor, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        except FileNotFoundError:
            continue
        with os.fdopen(fd, "rb") as fh:
            if start > os.fstat(fh.fileno()).st_size:  # soubor se zkrátil -> znovu od začátku
                start = 0
            fh.seek(start)
            pozice = start
            for radek in fh:
                if not radek.endswith(b"\n"):
                    break  # neúplný poslední řádek dočte příští běh
                pozice += len(radek)
                precteno += len(radek)
                if b'"tool_use"' in radek or b"<command-name>" in radek:
                    _pridej(ulozene, signaly_radku(radek, zbyvaji))
                if precteno > LIMIT_CTENI:
                    offsety[soubor] = pozice
                    uloz()
                    raise StrazceChyba("transkript je příliš velký na jeden průchod")
            offsety[soubor] = pozice
    uloz()
    _pridej(nalezy, ulozene)
    return nalezy


# ---------------------------------------------------------------- souhlasy

def souhlas_plati(souhlasy: dict, sid: str, kontext: CitlivyKontext) -> bool:
    if kontext.platnost == "volani":
        return False
    zaznam = souhlasy.get(sid, {}).get(kontext.nazev) if isinstance(souhlasy.get(sid), dict) else None
    if not isinstance(zaznam, dict) or zaznam.get("platnost") != kontext.platnost:
        return False
    return kontext.platnost == "session" or zaznam.get("den") == dnes()


def _cesta_souhlasu(stav: Path) -> Path:
    return strazce_adr(stav) / "souhlasy-v2.json"


def uloz_cekajici(stav: Path, tool_use_id: str, sid: str, kontexty: list[CitlivyKontext],
                  okno_id: float | None = None) -> None:
    zapis_json(strazce_adr(stav) / f"cekajici-{tool_use_id}.json",
               {"v": VERZE, "session_id": sid, "cas": time.time(), "okno": okno_id,
                "kontexty": [{"nazev": k.nazev, "platnost": k.platnost} for k in kontexty]})


def potvrd(data: dict, stav: Path) -> None:
    tid = data.get("tool_use_id")
    if not isinstance(tid, str) or not TOOL_USE_ID.match(tid) or data.get("agent_type") or data.get("agent_id"):
        return
    adr = strazce_adr(stav)
    cesta = adr / f"cekajici-{tid}.json"
    cek = nacti_json(cesta, {})
    try:
        os.unlink(cesta)
    except OSError:
        pass
    sid = cek.get("session_id")
    if cek.get("v") != VERZE or not isinstance(sid, str) or sid != data.get("session_id"):
        return
    den, ted = dnes(), time.time()
    souhlasy = nacti_json(_cesta_souhlasu(stav), {})
    for k in cek.get("kontexty") if isinstance(cek.get("kontexty"), list) else []:
        if isinstance(k, dict) and isinstance(k.get("nazev"), str) and k.get("platnost") in ("den", "session"):
            zaznamy = souhlasy.get(sid) if isinstance(souhlasy.get(sid), dict) else {}
            zaznamy[k["nazev"]] = {"platnost": k["platnost"], "den": den, "cas": ted,
                                   "okno": cek.get("okno") if isinstance(cek.get("okno"), (int, float)) else None}
            souhlasy[sid] = zaznamy
    zapis_json(_cesta_souhlasu(stav), _prorez_souhlasy(souhlasy, den, ted))
    try:
        statistika(stav, souhlas=True)
    except Exception:  # noqa: BLE001 – jen informativní
        pass


def _prorez_souhlasy(souhlasy: dict, den: str, ted: float) -> dict:
    """Denní souhlasy jen z dneška, souhlasy na session nejvýš 30 dní."""
    vysledek = {}
    for sid, zaznamy in souhlasy.items():
        if not isinstance(zaznamy, dict):
            continue
        platne = {}
        for nazev, z in zaznamy.items():
            if not isinstance(z, dict):
                continue
            cas = z.get("cas")
            if z.get("platnost") == "den" and z.get("den") == den:
                platne[nazev] = z
            elif (z.get("platnost") == "session" and isinstance(cas, (int, float))
                  and ted - cas < STARI_SESSION_S):
                platne[nazev] = z
        if platne:
            vysledek[sid] = platne
    return vysledek


def statistika(stav: Path, dotaz: bool = False, souhlas: bool = False) -> None:
    """Počty dotazů a souhlasů za den (bez obsahu) pro pozdější prořezání falešných poplachů."""
    cesta = strazce_adr(stav) / "statistika.json"
    d = nacti_json(cesta, {})
    den = d.setdefault(dnes(), {"dotazy": 0, "souhlasy": 0})
    if not isinstance(den, dict):
        den = d[dnes()] = {"dotazy": 0, "souhlasy": 0}
    den["dotazy"] = int(den.get("dotazy", 0)) + int(dotaz)
    den["souhlasy"] = int(den.get("souhlasy", 0)) + int(souhlas)
    zapis_json(cesta, dict(sorted(d.items())[-60:]))


def uklid(stav: Path, ted: float | None = None) -> None:
    ted = ted or time.time()
    adr = strazce_adr(stav)
    for jmeno in os.listdir(adr):
        p = adr / jmeno
        stari = 86400 if jmeno.startswith("cekajici-") else STARI_SESSION_S if jmeno.startswith("sken-") else None
        try:
            if stari and ted - os.lstat(p).st_mtime > stari:
                os.unlink(p)
        except OSError:
            pass


# ---------------------------------------------------------------- rozhodnutí

def vystup(rozhodnuti: str, duvod: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": rozhodnuti,
                                             "permissionDecisionReason": duvod}}, ensure_ascii=False))


def popis_volani(nastroj: str, vstup: dict, cil_telegram: bool = False) -> str:
    """R-CTX-1: popis jmenuje příjemce."""
    text = vstup.get("text")
    delka = len(text) if isinstance(text, str) else 0
    if nastroj == "odpovez":
        if vstup.get("forma") == "hlas":
            return "Hlas %d znaků -> ElevenLabs -> Telegram." % delka
        if vstup.get("forma") == "pc":
            return "Hlas %d znaků -> ElevenLabs -> PC." % delka
        if vstup.get("forma") == "text":
            return "Text %d znaků -> Telegram (cloud, bez koncového šifrování)." % delka
        return ("Text %d znaků -> Telegram (cloud, bez koncového šifrování), podle zprávy i hlasem přes "
                "ElevenLabs." % delka)
    if nastroj == "rekni" and cil_telegram:
        return "Hlas %d znaků -> ElevenLabs -> Telegram." % delka
    if nastroj == "rekni":
        text = vstup.get("text")
        delka = len(text) if isinstance(text, str) else 0
        cil = vstup.get("cil") if vstup.get("cil") in ("pc", "telegram", "soubor") else "auto (PC nebo Telegram)"
        return "Text %d znaků -> ElevenLabs, cíl %s." % (delka, cil)
    return "Hlasovka -> ElevenLabs Scribe (cloud)."


PLATNOST = {"den": "do konce dne pro tuto session", "session": "do konce této session",
            "volani": "jen pro toto volání"}


def duvod_dotazu(kontexty: list[CitlivyKontext], nalezy: dict[str, list[str]], popis: str, cloud: bool) -> str:
    casti = ["Cloudový přepis: " + popis] if cloud else []
    for k in kontexty:
        casti.append("%s (%s: %s)" % (k.upozorneni, k.nazev, ", ".join(nalezy.get(k.nazev, [])[:5])))
    if not cloud:
        casti.append(popis)
    if kontexty:
        casti.append("Souhlas platí: " + "; ".join("%s %s" % (k.nazev, PLATNOST[k.platnost]) for k in kontexty) + ".")
    return " ".join(casti)


# ---------------------------------------------------------------- Telegram

def identita_claude() -> tuple[int, str] | None:
    from . import puvod
    claude = puvod.najdi_claude(os.getppid())
    start = puvod.start_procesu(claude) if claude else None
    return (claude, start) if claude and start else None


def stav_telegramu(data: dict, kfg: Konfigurace) -> dict:
    """{"tah": bool (tah z Telegramu), "okno": dict okna tohoto procesu}. Bez okna = bez I/O navíc."""
    from . import puvod
    identita = identita_claude()
    if identita is None:
        return {"tah": False, "okno": {}}
    stav, klic, sid = kfg.stav_adresar, puvod.klic_procesu(*identita), data.get("session_id")
    okno = puvod.cti_tise(puvod.soubor(stav, "okno", klic), {})
    if not okno or not isinstance(sid, str) or not UUID.match(sid):
        return {"tah": False, "okno": okno}
    t = puvod.klasifikuj(puvod.over_transkript(sid, data.get("transcript_path")), data.get("prompt_id"), None,
                         puvod.identity_serveru(stav, klic), puvod.cti_tise(puvod.soubor(stav, "znacky", sid), {}),
                         puvod.cti_tise(puvod.soubor(stav, "agenti", sid), {}))
    return {"tah": t["trida"] == puvod.TELEGRAM, "okno": okno,
            "otevreno": puvod.okno_plati(okno, sid, time.time())}


def _udalost_serveru(data: dict, kfg: Konfigurace, kontexty: list[CitlivyKontext]) -> None:
    """Zamítnutá odpověď do Telegramu: server pošle šablonu „odpověď jen v terminálu“."""
    from . import puvod
    sid = data.get("session_id")
    if isinstance(sid, str) and UUID.match(sid):
        puvod.pripis_jsonl(puvod.soubor(kfg.stav_adresar, "udalosti", sid, ".jsonl"),
                           {"udalost": "odpoved_zamitnuta", "prompt_id": data.get("prompt_id"), "cas": time.time(),
                            "kontext": kontexty[0].nazev if kontexty else None})


def souhlas_pro_telegram(souhlasy: dict, sid: str, k: CitlivyKontext, okno: dict) -> bool:
    if not souhlas_plati(souhlasy, sid, k):
        return False
    if k.telegram_platnost != "okno":
        return True
    zaznam = souhlasy.get(sid, {}).get(k.nazev, {})
    return (okno.get("stav") == "otevreno" and isinstance(okno.get("otevreno"), (int, float))
            and zaznam.get("okno") == okno.get("otevreno"))


def tah_agenta_z_telegramu(data: dict, kfg: Konfigurace) -> bool:
    """Subagent: třída tahu, ve kterém vznikl (záznam hooku rozsahu při SubagentStart)."""
    from . import puvod
    identita = identita_claude()
    sid, agent_id = data.get("session_id"), data.get("agent_id")
    if identita is None or not isinstance(agent_id, str):
        return False
    if not os.path.lexists(puvod.soubor(kfg.stav_adresar, "okno", puvod.klic_procesu(*identita))):
        return False
    z = puvod.cti_tise(puvod.soubor(kfg.stav_adresar, "agenti", sid), {}).get(agent_id)
    return isinstance(z, dict) and z.get("trida") == puvod.TELEGRAM


def signaly_z_vysledku(data: dict, kfg: Konfigurace) -> list[str]:
    """R-CTX-6: textové signály kontextů ve výsledku libovolného nástroje (i Bash, Agent, WebFetch)
    v tahu z Telegramu, u subagenta podle třídy tahu, ve kterém vznikl."""
    kontexty = [k for k in kfg.strazce.citlive_kontexty if k.texty]
    sid = data.get("session_id")
    jmeno = data.get("tool_name")
    if not kontexty or not isinstance(sid, str) or not UUID.match(sid) or not isinstance(jmeno, str):
        return []
    if data.get("agent_id"):
        if not tah_agenta_z_telegramu(data, kfg):
            return []
    elif not stav_telegramu(data, kfg)["tah"]:
        return []
    text = json.dumps(data.get("tool_response"), ensure_ascii=False)[:MAX_VYSLEDEK]
    nove = [k.nazev for k in kontexty if any(s in text for s in k.texty)]
    if not nove:
        return []
    cesta = strazce_adr(kfg.stav_adresar) / f"sken-{sid}.json"
    sken = nacti_json(cesta, {})
    if sken.get("v") != VERZE or sken.get("otisk") != otisk(kfg.strazce.citlive_kontexty):
        sken = {"v": VERZE, "otisk": otisk(kfg.strazce.citlive_kontexty), "nalezy": {}, "offsety": {}}
    nalezy = sken.get("nalezy") if isinstance(sken.get("nalezy"), dict) else {}
    for n in nove:
        _pridej(nalezy, {n: ["výsledek " + jmeno.split("__")[0]]})
    sken["nalezy"] = nalezy
    zapis_json(cesta, sken)
    return nove


def pred_volanim(data: dict, kfg: Konfigurace) -> None:
    jmeno = data.get("tool_name")
    nastroj = jmeno.rsplit("__", 1)[-1]
    if nastroj not in NASTROJE_S_RAZITKEM:
        return
    stav = kfg.stav_adresar
    vstup = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    tid = data.get("tool_use_id")
    cloud = nastroj == "prepis" and vstup.get("engine") == "cloud"
    if nastroj == "prepis" and not cloud:
        zapis_razitko(stav, tid, nastroj, vstup)
        return
    sid = data.get("session_id")
    nalezy = detekuj(data, kfg)
    nalezene = [k for k in kfg.strazce.citlive_kontexty if k.nazev in nalezy]
    tg = stav_telegramu(data, kfg) if nastroj in ("odpovez", "rekni") else {"tah": False, "okno": {}}
    cil_telegram = nastroj == "odpovez" or (nastroj == "rekni" and (
        vstup.get("cil") == "telegram" or (vstup.get("cil") in (None, "auto") and tg["tah"])))
    if cil_telegram:
        nikdy = [k for k in nalezene if k.telegram == "nikdy"]
        if nikdy:
            _udalost_serveru(data, kfg, nikdy)
            vystup("deny", "Data kontextu %s do Telegramu nesmí (telegram: nikdy). Odpověď jen v terminálu."
                   % ", ".join(k.nazev for k in nikdy))
            return
    dotaz_cloud = cloud and kfg.strazce.prepis_cloud == "dotaz"
    if dotaz_cloud:
        kontexty = nalezene
    else:
        souhlasy = nacti_json(_cesta_souhlasu(stav), {}) if nalezene else {}
        if cil_telegram:
            kontexty = [k for k in nalezene if not souhlas_pro_telegram(souhlasy, sid, k, tg["okno"])]
        else:
            kontexty = [k for k in nalezene if not souhlas_plati(souhlasy, sid, k)]
    if not dotaz_cloud and not kontexty:
        zapis_razitko(stav, tid, nastroj, vstup)
        return
    duvod = duvod_dotazu(kontexty, nalezy, popis_volani(nastroj, vstup, cil_telegram), dotaz_cloud)
    if tg["tah"] and kontexty:
        okno = tg["okno"]
        rc = (all(k.telegram_dotaz_v_tahu == "rc" for k in kontexty) and okno.get("pri_dotazu", "cekat") == "cekat"
              and not okno.get("bez_rc"))
        if not rc:
            _udalost_serveru(data, kfg, kontexty)
            vystup("deny", duvod + " V tahu z Telegramu se nejde zeptat, odpověď jen v terminálu.")
            return
    if not lze_se_zeptat(data):
        vystup("deny", duvod + " V režimu %s se nejde zeptat, proto zamítnuto." % _rezim(data))
        return
    try:
        statistika(stav, dotaz=True)
    except Exception:  # noqa: BLE001 – statistika je jen informativní, dotaz s upozorněním nesmí zablokovat
        pass
    if not isinstance(tid, str) or not TOOL_USE_ID.match(tid):
        raise StrazceChyba("neplatné tool_use_id")
    try:
        okno_id = tg["okno"].get("otevreno") if tg["okno"].get("stav") == "otevreno" else None
        uloz_cekajici(stav, tid, sid, kontexty, okno_id if isinstance(okno_id, (int, float)) else None)
        zapis_razitko(stav, tid, nastroj, vstup)  # až těsně před dotazem: bez výstupu ask nesmí zůstat razítko
    except BaseException:
        # Obecný dotaz z main() upozornění kontextu neukáže: jeho „ano“ nesmí založit souhlas s kontextem.
        try:
            os.unlink(strazce_adr(stav) / f"cekajici-{tid}.json")
        except OSError:
            pass
        raise
    vystup("ask", duvod)


def lze_se_zeptat(data: dict) -> bool:
    r = data.get("permission_mode")
    return isinstance(r, str) and r in REZIMY_S_DOTAZEM


def _rezim(data: dict) -> str:
    r = data.get("permission_mode")
    return r if isinstance(r, str) and re.fullmatch(r"[A-Za-z]{1,32}", r) else "neznámém"


def _ze_subagenta(data: dict) -> bool:
    return bool(data.get("agent_type") or data.get("agent_id"))


def main() -> None:
    os.umask(0o077)
    try:
        data = json.loads(sys.stdin.read(MAX_VSTUP))
        if not isinstance(data, dict):
            raise StrazceChyba("vstup hooku není objekt")
    except (ValueError, StrazceChyba):
        # Bez čitelného vstupu nejde poznat ani subagenta: zamítnout (exit 2 = blokace).
        sys.stderr.write("mcp-voice-guard: nečitelný vstup hooku, volání zamítnuto\n")
        sys.exit(2)
    udalost = data.get("hook_event_name")
    if udalost == "PostToolUse":
        try:
            kfg = nacti()
            stav = kfg.stav_adresar
            jmeno = data.get("tool_name")
            if isinstance(jmeno, str) and jmeno.startswith("mcp__") and jmeno.rsplit("__", 1)[-1] in NASTROJE_S_RAZITKEM:
                potvrd(data, stav)
            signaly_z_vysledku(data, kfg)
            uklid(stav)
        except Exception:  # noqa: BLE001 – po volání nic neblokovat
            pass
        return
    if udalost != "PreToolUse":
        return
    if _ze_subagenta(data):
        vystup("deny", "Hlas smí používat jen hlavní session (ne subagent).")
        return
    jmeno = data.get("tool_name")
    if not isinstance(jmeno, str) or not jmeno.startswith("mcp__"):
        return
    nastroj = jmeno.rsplit("__", 1)[-1]
    bez_dotazu = not lze_se_zeptat(data)

    def _hlidac(signum, frame):
        raise StrazceChyba("hlídací časovač")

    signal.signal(signal.SIGALRM, _hlidac)
    signal.alarm(HLIDAC_S)
    try:
        try:
            kfg = nacti()
        except BaseException as exc:  # noqa: BLE001 – bez konfigurace není kam zapsat razítko
            if nastroj in NASTROJE_S_RAZITKEM:
                popis = str(exc) if isinstance(exc, ChybaKonfigurace) else type(exc).__name__
                vystup("deny", "Konfiguraci mcp-voice nejde načíst (%s), hlas zamítnut." % popis)
            return
        try:
            pred_volanim(data, kfg)
        except BaseException as exc:  # noqa: BLE001 – fail-to-ask
            signal.alarm(0)
            try:
                if nastroj in NASTROJE_S_RAZITKEM and not bez_dotazu:
                    vstup = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
                    zapis_razitko(kfg.stav_adresar, data.get("tool_use_id"), nastroj, vstup)
            except BaseException:  # noqa: BLE001 – bez razítka server volání odmítne (fail-closed)
                pass
            if bez_dotazu:
                vystup("deny", "Kontrolu citlivých kontextů pro hlas nešlo provést (%s) a v režimu %s "
                               "se nejde zeptat." % (type(exc).__name__, _rezim(data)))
            else:
                vystup("ask", "Kontrolu citlivých kontextů pro hlas nešlo provést (%s). Pustit volání?"
                       % type(exc).__name__)
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    main()
