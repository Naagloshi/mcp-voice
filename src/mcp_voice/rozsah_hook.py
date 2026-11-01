"""Hook rozsahu (jen standardní knihovna): co smí tah z Telegramu, když je okno otevřené.

Registrace: `/usr/bin/python3 -I <cesta>/rozsah_hook.py --udalost pre [--stav <adresář stavu>]` s `onFailure: block`.
Neimportuje balíček mcp_voice ani YAML: sourozenecké moduly `puvod.py` a `politika.py` načte z cesty
vlastního souboru a politiku čte ze snímku, který zapsal server. Adresář stavu: `--stav`, jinak
proměnná MCP_VOICE_STATE_DIR, jinak ~/.local/state/mcp-voice (musí sedět s `cesty.stav`).

Události (`--udalost`):
  pre             PreToolUse, rozhodovací tabulka P0–P7 (stdout = hookSpecificOutput)
  post            PostToolUse, PostToolUseFailure (matcher Bash): Bash z tahu z Telegramu, kterému hook dal
                  obal bwrap, běžel s jiným příkazem (obal zahodil jiný hook), nebo v zapisovatelných kořenech
                  vznikla, zmizela či se nahradila konfigurace agenta, háček nebo konfigurace Gitu (snímek z pre),
                  nebo kontrola selhala → žádost o zavření okna a upozornění v terminálu (systemMessage)
  subagent-start  SubagentStart: třída hlavního tahu pro agent_id
  subagent-stop   SubagentStop: agent označen jako ukončený
  notifikace      Notification (permission_prompt, elicitation_dialog): událost pro server
  stop            Stop, StopFailure: urychlení konce tahu
  clear, konec    SessionStart (clear), SessionEnd: žádost o zavření okna
Mimo okno tohoto procesu je to jen `stat` (P1); výjimka v otevřeném okně = deny (P2).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time

VERZE = 1
MAX_VSTUP = 8 * 1024 * 1024
HEARTBEAT_S = 30
AGENT_STOP_S = 3600
STARI_S = 48 * 3600
OBAL_STARI_S = 24 * 3600
OBAL_MAX = 200
DUVOD_BEZ_OBALU = "bash_bez_obalu"
DUVOD_KONFIGURACE = "konfigurace_agenta"
MAX_SNIMEK = 4 * 1024 * 1024
MAX_CEST = 50


def _sourozenec(jmeno: str):
    cesta = os.path.join(os.path.dirname(os.path.realpath(__file__)), jmeno + ".py")
    spec = importlib.util.spec_from_file_location("mcp_voice_rozsah_" + jmeno, cesta)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


if __package__:
    from . import politika, puvod
else:  # /usr/bin/python3 -I <cesta>/rozsah_hook.py
    puvod = _sourozenec("puvod")
    politika = _sourozenec("politika")


def adresar_stavu(arg: str | None, env: dict | None = None) -> str:
    env = os.environ if env is None else env
    return os.path.expanduser(arg or env.get("MCP_VOICE_STATE_DIR") or "~/.local/state/mcp-voice")


def vystup_pre(rozhodnuti: str, duvod: str, vstup: dict | None = None) -> dict:
    hso = {"hookEventName": "PreToolUse", "permissionDecision": rozhodnuti, "permissionDecisionReason": duvod}
    if vstup is not None:
        hso["updatedInput"] = vstup
    return {"hookSpecificOutput": hso}


def _identita_claude():
    pid = puvod.najdi_claude(os.getppid())
    if pid is None:
        return None
    start = puvod.start_procesu(pid)
    return None if start is None else (pid, start)


def nejake_okno_otevrene(stav: str, ted: float) -> bool:
    adr = puvod.adresar(stav)
    try:
        jmena = os.listdir(adr)
    except OSError:
        return False
    for j in jmena:
        if j.startswith("okno-") and j.endswith(".json"):
            d = puvod.cti_tise(os.path.join(adr, j), {})
            if d.get("stav") == "otevreno" and isinstance(d.get("konec"), (int, float)) and ted < d["konec"]:
                return True
    return False


def heartbeat(stav: str, sid: str, claude_pid: int, ted: float) -> None:
    cesta = puvod.soubor(stav, "rozsah", sid)
    try:
        if ted - os.lstat(cesta).st_mtime < HEARTBEAT_S:
            return
    except OSError:
        pass
    puvod.zapis(cesta, {"verze": VERZE, "cas": ted, "claude_pid": claude_pid})


def heartbeat_v_okne(data: dict, stav: str, ted: float, identita) -> None:
    """Heartbeat z události bez rozhodování (jen když má proces soubor okna)."""
    if identita is None or not os.path.lexists(puvod.soubor(stav, "okno", puvod.klic_procesu(*identita))):
        return
    heartbeat(stav, _session(data), identita[0], ted)


def platne_znacky(znacky: dict, okno: dict) -> dict:
    """S5: značka „člověk“ starší než poslední vložení zprávy serverem neplatí (zpráva vložená do běžícího
    tahu člověka může být mimo rozpočet čtení transkriptu)."""
    vlozeno = okno.get("posledni_vlozeni")
    if isinstance(vlozeno, bool) or not isinstance(vlozeno, (int, float)):
        vlozeno = None

    def plati(v) -> bool:
        if not (isinstance(v, dict) and v.get("trida") == puvod.CLOVEK):
            return True
        cas = v.get("cas")
        return isinstance(cas, (int, float)) and (vlozeno is None or cas > vlozeno)
    return {k: v for k, v in znacky.items() if plati(v)}


def oznac_lidsky_tah(stav: str, sid: str, prompt_id: object, t: dict) -> None:
    """S5: prompt_id tahu člověka pro pozdější část dlouhého tahu (začátek mimo rozpočet čtení). Píše se
    tady, ne v UserPromptSubmit: záznam promptu vzniká až po něm. Jen když přečtený začátek tahu nese tento
    prompt_id a je lidský; čas značky = čas začátku (platne_znacky). Chyba = bez značky (přísnější třída)."""
    if (not isinstance(prompt_id, str) or not puvod.PROMPT_ID.match(prompt_id) or not t.get("lidsky")
            or t.get("prompt_id") != prompt_id):
        return
    cas = t.get("cas_zacatku")
    if isinstance(cas, bool) or not isinstance(cas, (int, float)):
        return
    cesta = puvod.soubor(stav, "znacky", sid)
    try:
        with puvod.Zamek(cesta, 0.5):
            znacky = puvod.cti_tise(cesta, {})
            if prompt_id in znacky:
                return
            znacky[prompt_id] = {"trida": puvod.CLOVEK, "cas": cas}
            znacky = dict(sorted(znacky.items(), key=lambda kv: kv[1].get("cas", 0) if isinstance(kv[1], dict) else 0)
                          [-puvod.MAX_ZNACEK:])
            puvod.zapis(cesta, znacky)
    except (OSError, TimeoutError, ValueError):
        pass


def _otisk(prikaz: object) -> str | None:
    return hashlib.sha256(prikaz.encode("utf-8", "surrogatepass")).hexdigest() if isinstance(prikaz, str) else None


def _tool_use_id(data: dict) -> str | None:
    t = data.get("tool_use_id")
    return t if isinstance(t, str) and 8 <= len(t) <= 128 and t.replace("_", "").replace("-", "").isalnum() else None


def _soubor_integrity(stav: str, sid: str, tool_use_id: str) -> str:
    return puvod.soubor(stav, "integrita", "%s-%s" % (sid, tool_use_id))


def _smaz(cesta: str) -> None:
    try:
        os.unlink(cesta)
    except OSError:
        pass


def zapis_obal(stav: str, sid: str, tool_use_id: str | None, prikaz: str, ted: float,
               integrita: dict | None = None) -> None:
    """očekávaný příkaz s obalem a snímek integrity pro kontrolu v PostToolUse (bez tool_use_id = chyba,
    fail-closed). Snímek leží zvlášť (integrita-<session>-<tool_use_id>.json), záznam jen nese příznak."""
    if tool_use_id is None:
        raise ValueError("tool_use_id")
    if integrita is not None:
        puvod.zapis(_soubor_integrity(stav, sid, tool_use_id), integrita)
    cesta = puvod.soubor(stav, "obal", sid)
    with puvod.Zamek(cesta):
        d = puvod.cti_tise(cesta, {})
        platne = {k: v for k, v in d.items() if isinstance(v, dict) and ted - v.get("cas", 0) < OBAL_STARI_S}
        platne[tool_use_id] = {"otisk": _otisk(prikaz), "cas": ted, "integrita": integrita is not None}
        if len(platne) > OBAL_MAX:
            platne = dict(sorted(platne.items(), key=lambda kv: kv[1].get("cas", 0))[-OBAL_MAX:])
        for k in set(d) - set(platne):  # zamítnutý dotaz PostToolUse nemá: snímek uklidit se záznamem
            if _tool_use_id({"tool_use_id": k}):
                _smaz(_soubor_integrity(stav, sid, k))
        puvod.zapis(cesta, platne)


def _kontrola(stav: str, sid: str, tid: str, z: dict, vstup: dict) -> dict | None:
    if z.get("otisk") != _otisk(vstup.get("command")):
        return {"duvod": DUVOD_BEZ_OBALU, "cesty": [], "nedobehla": False}
    if not z.get("integrita"):
        return None
    snimek = puvod.cti(_soubor_integrity(stav, sid, tid), MAX_SNIMEK)
    if not isinstance(snimek, dict):
        raise ValueError("snímek integrity chybí")
    k = politika.kontrola_integrity(snimek)
    if not k["uplne"]:
        return {"duvod": DUVOD_KONFIGURACE, "cesty": [], "nedobehla": True}
    if k["zmeny"]:
        return {"duvod": DUVOD_KONFIGURACE, "cesty": k["zmeny"][:MAX_CEST], "nedobehla": False}
    return None


def post(data: dict, stav: str, ted: float, identita=None) -> dict | None:
    """Bash, kterému pre dal obal, musí běžet přesně s ním (jinak obal zahodil jiný hook) a nesmí v zapisovatelných
    kořenech založit, smazat ani nahradit konfiguraci agenta, háčky ani konfiguraci Gitu. Při nálezu nebo chybě
    kontroly (fail-closed) žádost o zavření okna. Vrací {"duvod", "cesty", "nedobehla"}, nebo None."""
    if data.get("tool_name") != "Bash":
        return None
    sid = _session(data)
    cesta = puvod.soubor(stav, "obal", sid)
    if not os.path.lexists(cesta):
        return None
    tid = _tool_use_id(data)
    try:
        with puvod.Zamek(cesta):
            d = puvod.cti_tise(cesta, {})
            z = d.pop(tid, None) if tid else None
            if z is not None:
                puvod.zapis(cesta, d)
        if z is None:
            return None
        try:
            vstup = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
            vysledek = _kontrola(stav, sid, tid, z if isinstance(z, dict) else {}, vstup)
        finally:
            _smaz(_soubor_integrity(stav, sid, tid))
    except Exception:  # noqa: BLE001 – kontrola po běhu Bash z Telegramu je fail-closed
        vysledek = {"duvod": DUVOD_KONFIGURACE, "cesty": [], "nedobehla": True}
    if vysledek is None:
        return None
    identita = identita if identita is not None else _identita_claude()
    if identita is None:
        raise ValueError("proces claude nenalezen")
    zavrit(data, stav, ted, identita, vysledek["duvod"], vysledek["cesty"])
    return vysledek


def vystup_post(vysledek: dict) -> dict:
    """Upozornění pro uživatele v terminálu (systemMessage; model ho nevidí)."""
    if vysledek["duvod"] == DUVOD_BEZ_OBALU:
        text = "mcp-voice: Bash z Telegramu běžel bez obalu bwrap, okno Telegramu se zavírá."
    elif vysledek.get("nedobehla"):
        text = ("mcp-voice: kontrola konfigurace agenta po Bash z Telegramu nedoběhla, okno Telegramu se zavírá. "
                "Zkontroluj .claude/, CLAUDE.md, .mcp.json a .git/hooks v pracovním adresáři.")
    else:
        text = ("mcp-voice: Bash z Telegramu změnil konfiguraci agenta nebo Gitu, okno Telegramu se zavírá. "
                "Zkontroluj před další session nebo commitem: " + ", ".join(vysledek["cesty"]))
    return {"systemMessage": politika.nahled(text, 4000)}


def _session(data: dict) -> str:
    sid = data.get("session_id")
    if not isinstance(sid, str) or not puvod.UUID.match(sid):
        raise ValueError("neplatné session_id")
    return sid


def _transkript(data: dict, sid: str) -> str | None:
    return puvod.over_transkript(sid, data.get("transcript_path"))


def pre(data: dict, stav: str, ted: float | None = None, identita=None) -> dict | None:
    """Rozhodovací tabulka 7.5. Vrací výstup hooku, nebo None (bez výstupu)."""
    ted = time.time() if ted is None else ted
    nastroj = data.get("tool_name")
    vstup = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}

    # P0: pojistka v Bash v každém tahu, i bez okna
    if nastroj == "Bash":
        tvary = politika.pojistka_tvary(os.path.normpath(os.path.expanduser("~")), os.path.normpath(stav))
        if politika.pojistka_nalez(vstup.get("command"), tvary) and not os.path.lexists(
                os.path.join(puvod.adresar(stav), "pojistka-vypnuta")):
            try:  # zamítnutý nástroj je v transkriptu taky: heartbeat, aby server nehlásil nefunkční hook
                heartbeat_v_okne(data, stav, ted, identita if identita is not None else _identita_claude())
            except Exception:  # noqa: BLE001
                pass
            return vystup_pre("deny", "pojistka mcp-voice: příkaz sahá na socket session nebo stav mcp-voice.")

    identita = identita if identita is not None else _identita_claude()
    if identita is None:
        # P1/P2: bez procesu claude okno nejde přiřadit
        if nejake_okno_otevrene(stav, ted):
            return vystup_pre("deny", "chyba hooku rozsahu: proces claude nenalezen a okno Telegramu je otevřené.")
        return None
    klic = puvod.klic_procesu(*identita)
    cesta_okna = puvod.soubor(stav, "okno", klic)
    if not os.path.lexists(cesta_okna):
        return None  # P1
    otevreno = None
    trida = None
    try:
        okno = puvod.cti(cesta_okna)
        if not isinstance(okno, dict):
            raise ValueError("soubor okna")
        sid = _session(data)
        otevreno = puvod.okno_plati(okno, sid, ted)
        registrace = okno.get("registrace") if isinstance(okno.get("registrace"), str) else "hlas"
        identity = puvod.identity_serveru(stav, klic)
        znacky_vse = puvod.cti_tise(puvod.soubor(stav, "znacky", sid), {})
        znacky = platne_znacky(znacky_vse, okno)
        agenti = puvod.cti_tise(puvod.soubor(stav, "agenti", sid), {})
        tp = _transkript(data, sid)
        t = puvod.klasifikuj(tp, data.get("prompt_id"), data.get("agent_id"), identity, znacky, agenti,
                             puvod.MAX_OTEVRENO if otevreno else puvod.MAX_ZAVRENO)
        trida = t["trida"]
        if trida == puvod.CLOVEK and isinstance(data.get("prompt_id"), str) and data["prompt_id"] not in znacky_vse:
            oznac_lidsky_tah(stav, sid, data.get("prompt_id"), t)
        if not otevreno:
            if trida == puvod.TELEGRAM:  # P3
                if nastroj == "mcp__%s__stav" % registrace:
                    return None
                return vystup_pre("deny", "Telegram: okno je zavřené, tah z Telegramu nesmí nic kromě stav.")
            heartbeat(stav, sid, identita[0], ted)  # P4
            return None
        pol = puvod.cti(puvod.soubor(stav, "politika", klic))
        if not isinstance(pol, dict) or pol.get("v") != politika.VERZE:
            raise ValueError("snímek politiky")
        heartbeat(stav, sid, identita[0], ted)
        omezit_naplanovane = pol.get("omezit_naplanovane", True)
        if trida in (puvod.CLOVEK, puvod.ULOHA) or (trida == puvod.NAPLANOVANY and not omezit_naplanovane):  # P5
            if nastroj == "mcp__%s__odpovez" % registrace and data.get("prompt_id") != okno.get("otevreno_prompt_id"):
                return vystup_pre("deny", "NOT_TELEGRAM_TURN: odpovez jen v tahu z Telegramu.")
            return None
        zavreni = ceka_zavreni(stav, sid, identita)
        if zavreni is not None:  # okno se zavírá (žádost čeká na server): tah z Telegramu už nic nesmí
            return vystup_pre("deny", "Telegram: okno se zavírá (%s), volání zamítnuto." % zavreni)
        znacka = t["znacky"][0] if t["znacky"] else None
        if trida != puvod.TELEGRAM:  # P6
            r = politika.rozhodni(pol, "omezeny", nastroj, vstup, data.get("cwd"), trida=trida)
        else:  # P7
            rezim = okno.get("rezim")
            agent = agenti.get(data.get("agent_id")) if isinstance(data.get("agent_id"), str) else None
            if isinstance(agent, dict) and agent.get("rezim") in politika.REZIMY:
                rezim = agent["rezim"]
            if rezim not in politika.REZIMY:
                raise ValueError("režim okna")
            r = politika.rozhodni(pol, rezim, nastroj, vstup, data.get("cwd"), znacka=znacka)
        if r["rozhodnuti"] is None:
            return None
        if nastroj == "Bash" and r["rozhodnuti"] in ("allow", "ask") and isinstance(r["vstup"], dict):
            zapis_obal(stav, sid, _tool_use_id(data), r["vstup"].get("command"), ted, r.get("integrita"))
        return vystup_pre(r["rozhodnuti"], r["duvod"], r["vstup"])
    except Exception as e:  # noqa: BLE001 – P2: v otevřeném okně fail-closed
        if otevreno is False and trida is not None and trida != puvod.TELEGRAM:
            return None  # okno prokazatelně zavřené, tah není z Telegramu: chyba nesmí ochromit práci
        return vystup_pre("deny", "chyba hooku rozsahu (%s), volání zamítnuto." % type(e).__name__)


def subagent_start(data: dict, stav: str, ted: float, identita) -> None:
    sid = _session(data)
    agent_id = data.get("agent_id")
    if not isinstance(agent_id, str) or not puvod.AGENT_ID.match(agent_id):
        return
    klic = puvod.klic_procesu(*identita)
    okno = puvod.cti_tise(puvod.soubor(stav, "okno", klic), {})
    identity = puvod.identity_serveru(stav, klic)
    znacky = puvod.cti_tise(puvod.soubor(stav, "znacky", sid), {})
    cesta = puvod.soubor(stav, "agenti", sid)
    with puvod.Zamek(cesta):
        agenti = puvod.cti_tise(cesta, {})
        t = puvod.klasifikuj(_transkript(data, sid), data.get("prompt_id"), None, identity, znacky, agenti,
                             puvod.MAX_ZAVRENO)
        agenti = {k: v for k, v in agenti.items()
                  if isinstance(v, dict) and not (v.get("stop") and ted - v["stop"] > AGENT_STOP_S)
                  and ted - v.get("start", ted) < STARI_S}
        agenti[agent_id] = {"trida": t["trida"], "rezim": okno.get("rezim") if okno.get("stav") == "otevreno" else None,
                            "start": ted, "stop": None, "prompt_id": data.get("prompt_id"),
                            "znacka": t["znacky"][0] if t["znacky"] else None}
        puvod.zapis(cesta, agenti)
    heartbeat_v_okne(data, stav, ted, identita)


def subagent_stop(data: dict, stav: str, ted: float, identita=None) -> None:
    sid = _session(data)
    heartbeat_v_okne(data, stav, ted, identita)
    cesta = puvod.soubor(stav, "agenti", sid)
    if not os.path.lexists(cesta):
        return
    with puvod.Zamek(cesta):
        agenti = puvod.cti_tise(cesta, {})
        z = agenti.get(data.get("agent_id"))
        if isinstance(z, dict):
            z["stop"] = ted
        agenti = {k: v for k, v in agenti.items()
                  if isinstance(v, dict) and not (v.get("stop") and ted - v["stop"] > AGENT_STOP_S)}
        puvod.zapis(cesta, agenti)


def udalost(data: dict, stav: str, ted: float, identita, druh: str) -> None:
    if not os.path.lexists(puvod.soubor(stav, "okno", puvod.klic_procesu(*identita))):
        return
    sid = _session(data)
    heartbeat_v_okne(data, stav, ted, identita)
    typ = data.get("notification_type") if druh == "notifikace" else data.get("hook_event_name")
    if druh == "notifikace" and typ not in ("permission_prompt", "elicitation_dialog"):
        return
    prompt_id = data.get("prompt_id") if isinstance(data.get("prompt_id"), str) else None
    puvod.pripis_jsonl(puvod.soubor(stav, "udalosti", sid, ".jsonl"),
                       {"udalost": druh, "prompt_id": prompt_id, "cas": ted,
                        "typ": typ if isinstance(typ, str) and len(typ) < 40 else None})


def zavrit(data: dict, stav: str, ted: float, identita, duvod: str, cesty: list | None = None) -> None:
    klic = puvod.klic_procesu(*identita)
    if not os.path.lexists(puvod.soubor(stav, "okno", klic)):
        return
    sid = _session(data)
    z = {"akce": "zavrit", "duvod": duvod, "cas": ted, "claude_pid": identita[0], "claude_start": identita[1],
         "session_id": sid}
    if cesty:
        z["cesty"] = [c for c in cesty if isinstance(c, str)][:MAX_CEST]
    puvod.zapis(puvod.soubor(stav, "zadost", sid), z)


def ceka_zavreni(stav: str, sid: str, identita) -> str | None:
    """Důvod čekající žádosti tohoto procesu claude o zavření okna (server ji ještě nezpracoval), jinak None.
    Žádost jiného procesu server nechá být, proto se tu nepočítá."""
    z = puvod.cti_tise(puvod.soubor(stav, "zadost", sid), {})
    if (z.get("akce") in ("zavrit", "stop") and z.get("claude_pid") == identita[0]
            and str(z.get("claude_start")) == str(identita[1])):
        d = z.get("duvod")
        return d if isinstance(d, str) and d.replace("_", "").isalnum() and len(d) < 40 else z["akce"]
    return None


def main(argv: list[str] | None = None) -> None:
    os.umask(0o077)
    p = argparse.ArgumentParser(prog="mcp-voice-rozsah")
    p.add_argument("--udalost", required=True,
                   choices=("pre", "post", "subagent-start", "subagent-stop", "notifikace", "stop", "clear", "konec"))
    p.add_argument("--stav")
    a = p.parse_args(argv)
    stav = adresar_stavu(a.stav)
    if not os.path.isdir(stav):
        # Jen varování: překlep v --stav jinak projde tiše a okno se nikdy neotevře.
        sys.stderr.write("mcp-voice-rozsah: adresář stavu %r neexistuje (--stav musí být shodný s cesty.stav; "
                         "před prvním startem serveru je to v pořádku)\n" % stav)
    try:
        data = json.loads(sys.stdin.buffer.read(MAX_VSTUP))
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        sys.stderr.write("mcp-voice-rozsah: nečitelný vstup hooku\n")
        sys.exit(2)
    ted = time.time()
    if a.udalost == "pre":
        vysledek = pre(data, stav, ted)
        if vysledek is not None:
            sys.stdout.write(json.dumps(vysledek, ensure_ascii=False))
        sys.exit(0)
    try:
        identita = _identita_claude()
        if identita is not None:
            if a.udalost == "subagent-start":
                subagent_start(data, stav, ted, identita)
            elif a.udalost == "subagent-stop":
                subagent_stop(data, stav, ted, identita)
            elif a.udalost in ("notifikace", "stop"):
                udalost(data, stav, ted, identita, a.udalost)
            elif a.udalost == "post":
                v = post(data, stav, ted, identita)
                if v:
                    sys.stderr.write("mcp-voice-rozsah: okno Telegramu se zavírá (%s)\n" % v["duvod"])
                    sys.stdout.write(json.dumps(vystup_post(v), ensure_ascii=False))
            elif a.udalost == "clear":
                if data.get("source") == "clear":
                    zavrit(data, stav, ted, identita, "clear")
            else:
                zavrit(data, stav, ted, identita, "clear")
    except Exception as e:  # noqa: BLE001 – ostatní události nic neblokují
        sys.stderr.write("mcp-voice-rozsah: %s\n" % type(e).__name__)
    sys.exit(0)


if __name__ == "__main__":
    main()
