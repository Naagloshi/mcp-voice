"""Příjem zpráv z Telegramu do běžící session: okno, polling, filtr, sanitizace, hlasovky, fronta,
doručení do socketu, ověření doručení, odpovědi, šablony a retence.

Bezpečnost nestojí tady, ale v hooku rozsahu (PreToolUse). Server zajišťuje, že mimo otevřené okno
nic nepřijme ani nevloží, a do Telegramu posílá jen pevné šablony a odpovědi `odpovez`.
Loguje jen metadata (typ, délka, ms, kód), obsah zpráv nikdy.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from . import okno as oknomod
from . import politika, puvod, vstup_audio
from .chyby import HlasChyba
from .config import Konfigurace
from .inbox import InboxChyba

log = logging.getLogger("mcp_voice.prijem")

KROK_S = 0.5
OVEROVANI_S = 120
OVERENI_POVELU_S = 60  # tolik se čeká, až Claude Code zapíše záznam povelu (vzniká až po hookách promptu)
STARI_POVELU_S = 30  # záznam povelu nejvýš tolik před převzetím žádosti
HEARTBEAT_MAX_S = 600  # čerstvost heartbeatu pro otevření okna (tah povelu)
HEARTBEAT_ZAPIS_S = 30  # = rozsah_hook.HEARTBEAT_S: hook zapisuje heartbeat nejvýš jednou za tolik sekund
HOOK_TOLERANCE_S = 5
HOOK_LHUTA_S = 60  # záznam tool_use vzniká před hookem; tolik se na jeho heartbeat čeká
KONTROLA_HOOKU_S = 30
ZAMEK_MAX_S = 70
RETENCE_KAZDYCH_S = 600
TELEGRAM_MAX_STARI_H = 48
POZDE_MAX_S = 3600
MAX_ODPOVED = 20000
SPECIALNI = ("forward_origin", "forward_from", "forward_from_chat", "forward_date", "is_automatic_forward",
             "via_bot", "sender_chat", "story", "paid_media", "business_connection_id")
ZNACKA_PRIKAZU = {"cs": "[zakázaný příkaz]", "en": "[blocked command]"}
VZORY_TAJEMSTVI = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bbot?\d{6,}:[A-Za-z0-9_-]{30,}"),
    re.compile(r"\b\d{6,}:AA[A-Za-z0-9_-]{30,}"),
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bsk_[a-f0-9]{40,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}"),
    # JWT (přístupové tokeny OAuth, id_token) a tokeny Google OAuth
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"),
    re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?<![A-Za-z0-9])1//0[A-Za-z0-9_-]{30,}"),
)

SABLONY = {
    "cs": {
        "okno_otevreno": "Okno otevřeno do {konec}, režim {rezim}.{navic}",
        "okno_upraveno": "Okno upraveno do {konec}, režim {rezim}.",
        "okno_zavreno": "Okno zavřeno ({duvod}).",
        "okno_presunuto": "Okno přepnuto do jiné session.",
        "okno_neovereno": "Okno se neotevřelo: povel z terminálu nešel ověřit ({duvod}).",
        "okno_zavreno_konfigurace": "Okno zavřeno: příkaz z Telegramu změnil konfiguraci agenta nebo Gitu "
                                    "(nebo ji nešlo ověřit). Před další session nebo commitem zkontroluj PC.",
        "stav": "Okno do {konec}, režim {rezim}. Ve frontě {fronta}.",
        "fronta": "Session pracuje, zpráva je ve frontě ({pocet}).",
        "ceka_potvrzeni": "Čeká potvrzení v terminálu nebo Remote Control.",
        "hotovo_bez_odpovedi": "Hotovo, odpověď je v terminálu.",
        "preruseno_bez_odpovedi": "Přerušeno, odpověď je v terminálu.",
        "doruceni_nepotvrzeno": "Doručení nepotvrzeno, zkontroluj terminál.",
        "odpoved_jen_v_terminalu": "Odpověď je jen v terminálu{kontext}.",
        "odpoved_na_pc": "Odpověď je na PC.",
        "nepodporovany_typ": "Tento typ zprávy nepřijímám (jen text a hlasovky).",
        "preposlane": "Přeposlané a speciální zprávy nepřijímám.",
        "jen_stop_stav": "Z Telegramu jde jen /stop a /stav.",
        "limit": "Příliš mnoho zpráv, zpomal.",
        "fronta_plna": "Fronta je plná, zkus to později.",
        "prepis_nedostupny": "Přepis hlasovky není dostupný.",
        "nerozumel": "Hlasovce jsem nerozuměl.",
        "prazdna": "Prázdná zpráva.",
        "prilis_dlouha": "Zpráva je příliš dlouhá (max {max} znaků).",
        "prikaz_odstranen": "Příkazy ve zprávě jsem odstranil ({pocet}).",
    },
    "en": {
        "okno_otevreno": "Window open until {konec}, mode {rezim}.{navic}",
        "okno_upraveno": "Window changed: until {konec}, mode {rezim}.",
        "okno_zavreno": "Window closed ({duvod}).",
        "okno_presunuto": "Window moved to another session.",
        "okno_neovereno": "Window not opened: the terminal command could not be verified ({duvod}).",
        "okno_zavreno_konfigurace": "Window closed: a command from Telegram changed agent or Git configuration "
                                    "(or it could not be verified). Check the PC before the next session or commit.",
        "stav": "Window until {konec}, mode {rezim}. Queue {fronta}.",
        "fronta": "Session is busy, message queued ({pocet}).",
        "ceka_potvrzeni": "Waiting for confirmation in the terminal or Remote Control.",
        "hotovo_bez_odpovedi": "Done, the answer is in the terminal.",
        "preruseno_bez_odpovedi": "Interrupted, the answer is in the terminal.",
        "doruceni_nepotvrzeno": "Delivery not confirmed, check the terminal.",
        "odpoved_jen_v_terminalu": "The answer is only in the terminal{kontext}.",
        "odpoved_na_pc": "The answer is on the PC.",
        "nepodporovany_typ": "Unsupported message type (text and voice only).",
        "preposlane": "Forwarded and special messages are not accepted.",
        "jen_stop_stav": "Only /stop and /stav work from Telegram.",
        "limit": "Too many messages, slow down.",
        "fronta_plna": "Queue is full, try later.",
        "prepis_nedostupny": "Voice transcription is unavailable.",
        "nerozumel": "Could not understand the voice message.",
        "prazdna": "Empty message.",
        "prilis_dlouha": "Message is too long (max {max} characters).",
        "prikaz_odstranen": "Removed commands from the message ({pocet}).",
    },
}
NAVIC_BEZ_RC = {"cs": " Session nemá Remote Control: dotazy z Telegramu se zamítají.",
                "en": " Session has no Remote Control: prompts from Telegram are denied."}
NAVIC_VERZE = {"cs": " Neověřená verze Claude Code.", "en": " Unverified Claude Code version."}
_RIDICI = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _smaz(cesta) -> None:
    try:
        os.unlink(cesta)
    except OSError:
        pass


# ---------------------------------------------------------------- čisté funkce

_ODSTRANIT = re.compile(
    "[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏‪-‮⁠-⁤⁦-⁩﻿"
    "︀-️\U000e0000-\U000e007f\U000e0100-\U000e01ef"
    "\u00ad\u034f\u115f\u1160\u180e\u3164\uffa0]")


def sanitizuj(text: str) -> str:
    """8.3: řídicí, bidi, zero-width, tagy, variační selektory; U+2028/9 na \\n; NFC; obal peer zprávy."""
    t = _ODSTRANIT.sub("", text)
    t = t.replace(" ", "\n").replace(" ", "\n").replace("\r", "")
    t = re.sub(r"\n{4,}", "\n\n\n", t)
    t = unicodedata.normalize("NFC", t)
    return t.replace("<cross-session-message", "‹cross-session-message").strip()


def shoda_masky(token: str, maska: str) -> bool:
    """Glob masky příkazu: jako fnmatchcase, ale * a ? nepřekročí / (absolutní cesta není příkaz)."""
    return re.fullmatch(_maska_re(maska), token, re.S) is not None


def _maska_re(maska: str) -> str:
    vystup, i = [], 0
    while i < len(maska):
        c = maska[i]
        i += 1
        if c == "*":
            vystup.append("[^/]*")
        elif c == "?":
            vystup.append("[^/]")
        elif c == "[":
            konec = maska.find("]", i + 1 if maska[i:i + 1] in ("!", "]") else i)
            if konec < 0:
                vystup.append(re.escape(c))
                continue
            trida = maska[i:konec].replace("\\", "\\\\")
            i = konec + 1
            vystup.append("[" + ("^" + trida[1:] if trida.startswith("!") else trida) + "]")
        else:
            vystup.append(re.escape(c))
    return "".join(vystup)


def odstran_prikazy(text: str, masky: tuple[str, ...], jazyk: str = "cs") -> tuple[str, int, list[str]]:
    """Token na začátku zprávy nebo po bílém znaku, který odpovídá masce, nahradí viditelnou značkou."""
    if not masky:
        return text, 0, []
    znacka = ZNACKA_PRIKAZU.get(jazyk, ZNACKA_PRIKAZU["cs"])
    trefene: list[str] = []

    def nahrad(m):
        for maska in masky:
            if shoda_masky(m.group(0), maska):
                trefene.append(maska)
                return znacka
        return m.group(0)

    return re.sub(r"(?<!\S)\S+", nahrad, text), len(trefene), sorted(set(trefene))


def _norm_fraze(text: str) -> str:
    return puvod.normalizuj(text)


def urci_formu(text: str, typ: str, hlas: tuple[str, ...], textem: tuple[str, ...], pc: tuple[str, ...]) -> str:
    """9.2: hlasovka = hlas, text = text; fráze ve zprávě mají přednost, vyhrává poslední výskyt."""
    vychozi = "hlas" if typ == "hlas" else "text"
    n = _norm_fraze(text)
    nejlepsi = None
    for forma, seznam in (("hlas", hlas), ("text", textem), ("pc", pc)):
        for fraze in seznam:
            f = _norm_fraze(fraze)
            for m in re.finditer(r"(?<!\w)" + re.escape(f) + r"(?!\w)", n):
                klic = (m.start(), len(f))
                if nejlepsi is None or klic > nejlepsi[0]:
                    nejlepsi = (klic, forma)
    return nejlepsi[1] if nejlepsi else vychozi


def obalka(message_id: int, typ: str, delka_s: float | None, forma: str, konec: float, rezim: str, jazyk: str,
           odstraneno: int = 0) -> str:
    """První řádek tvoří jen server z pevných polí; text odesílatele začíná na druhém řádku."""
    cs = jazyk != "en"
    if typ == "hlas":
        s = int(round(delka_s or 0))
        druh = ("hlasovka %d:%02d" if cs else "voice %d:%02d") % (s // 60, s % 60)
    else:
        druh = "text"
    formy = {"hlas": "hlas", "text": "text", "pc": "PC"} if cs else {"hlas": "voice", "text": "text", "pc": "PC"}
    casti = ["Telegram #%d" % int(message_id), druh, ("odpověď: " if cs else "reply: ") + formy.get(forma, forma),
             ("okno do " if cs else "window until ") + oknomod.hhmm(konec),
             oknomod.POPIS_REZIMU["cs" if cs else "en"].get(rezim, rezim)]
    if odstraneno:
        casti.append(("odstraněno příkazů: %d" if cs else "commands removed: %d") % odstraneno)
    return "[" + " · ".join(casti) + "]"


def tajemstvi_v_textu(text: str) -> bool:
    return any(v.search(text) for v in VZORY_TAJEMSTVI)


def rozdel(text: str, max_casti: int, max_znaku: int, jazyk: str = "cs") -> list[str]:
    """Po odstavcích na nejvýš max_casti zpráv; zbytek se zkrátí a doplní poznámkou."""
    dovetek = " … (zbytek v terminálu)" if jazyk != "en" else " … (rest in the terminal)"
    casti: list[str] = []
    akt = ""
    for odst in re.split(r"(\n\s*\n)", text):
        if len(akt) + len(odst) <= max_znaku:
            akt += odst
            continue
        if akt.strip():
            casti.append(akt.strip())
        akt = odst
        while len(akt) > max_znaku:
            casti.append(akt[:max_znaku])
            akt = akt[max_znaku:]
    if akt.strip():
        casti.append(akt.strip())
    if len(casti) > max_casti:
        casti = casti[:max_casti]
        casti[-1] = casti[-1][:max_znaku - len(dovetek)] + dovetek
    return casti or [""]


def otisk_allowlistu(ids: tuple[int, ...], chat_id: str | None) -> str:
    return hashlib.sha256((json.dumps(sorted(ids)) + "|" + str(chat_id)).encode()).hexdigest()


def allowlist_potvrzen(stav: Path, ids: tuple[int, ...], chat_id: str | None) -> bool:
    d = puvod.cti_tise(str(oknomod.adresar(stav) / "allowlist-otisk.json"), {})
    return d.get("otisk") == otisk_allowlistu(ids, chat_id)


def potvrd_allowlist(stav: Path, ids: tuple[int, ...], chat_id: str | None, ted: float | None = None) -> None:
    puvod.zapis(str(oknomod.adresar(stav) / "allowlist-otisk.json"),
                {"otisk": otisk_allowlistu(ids, chat_id), "cas": time.time() if ted is None else ted})


def zapis_identitu(stav: Path, klic: str, pid: int, start: str, ted: float) -> None:
    """server-P.json: posledních 5 identit serveru (restart pozná záznamy předchůdce)."""
    cesta = str(oknomod.cesta(stav, "server", klic))
    with puvod.Zamek(cesta):
        d = puvod.cti_tise(cesta, {})
        identity = [x for x in d.get("identity", []) if isinstance(x, dict)] if isinstance(d.get("identity"), list) else []
        identity = [x for x in identity if (x.get("pid"), x.get("start")) != (pid, start)]
        identity.append({"pid": pid, "start": start, "od": ted})
        puvod.zapis(cesta, {"identity": identity[-5:], "verze": 1})


def bwrap_funguje(cesta: str | None = None, argv: list[str] | None = None) -> str | None:
    """Cesta k bwrap, když projde zkušební spuštění; jinak None."""
    import subprocess
    b = cesta or shutil.which("bwrap")
    if not b:
        return None
    try:
        r = subprocess.run(argv or politika.zaklad_bwrap(b) + ["true"], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return b if r.returncode == 0 else None


# ---------------------------------------------------------------- server

@dataclass
class Zprava:
    message_id: int
    typ: str  # text | hlas
    text: str
    forma: str
    prijato: float
    delka_s: float | None = None
    odstraneno: int = 0


@dataclass
class Prijem:
    kfg: Konfigurace
    claude_pid: int
    claude_start: str
    klient: object | None  # telegram.Klient
    inbox: object | None  # inbox.Inbox
    prepis: Callable[[bytes], Awaitable[dict]] | None = None
    hodiny: Callable[[], float] = time.time
    server_pid: int = field(default_factory=os.getpid)
    server_start: str | None = None
    bwrap: str | None = None
    dekodovani_obal: list[str] | None = None
    fronta: list = field(default_factory=list)
    cekajici: dict = field(default_factory=dict)
    pozde: dict = field(default_factory=dict)  # nepotvrzené vložení, které může doručit později
    doruceno: list = field(default_factory=list)
    oznameno_fronta: bool = False
    notifikovano: set = field(default_factory=set)
    casy_zprav: list = field(default_factory=list)
    pocet_v_okne: int = 0
    prekroceni: int = 0
    offset: int | None = None
    overovani_od: float | None = None
    povel_ceka: dict | None = None  # žádost start převzatá od hooku, záznam povelu se ještě ověřuje
    zamek_od: float | None = None
    posledni_retence: float = 0.0
    posledni_kontrola_hooku: float = 0.0
    chyba: str | None = None
    poll_uloha: asyncio.Task | None = None
    polling: bool = True  # úloha long pollu (testy volají poll_jednou ručně)

    def __post_init__(self):
        self.p = self.kfg.telegram.prijem
        self.stav = self.kfg.stav_adresar
        self.klic = puvod.klic_procesu(self.claude_pid, self.claude_start)
        self.jazyk = self.p.jazyk_sablon
        self.zamek = oknomod.PollZamek(self.stav)
        if self.server_start is None:
            self.server_start = puvod.start_procesu(self.server_pid) or "0"
        self.okno = oknomod.nacti_okno(self.stav, self.klic) or {"v": 1, "stav": "zavreno"}

    # ------------------------------------------------------------ stav

    @property
    def vypnuto(self) -> str | None:
        """Proč příjem neběží (kód), nebo None."""
        if self.p.chyba:
            return "konfigurace"
        if not self.p.povoleno:
            return "vypnuto"
        if not allowlist_potvrzen(self.stav, self.p.povolene_user_id, self.kfg.telegram.chat_id):
            return "allowlist_nepotvrzen"
        return None

    def start(self) -> None:
        ted = self.hodiny()
        puvod.zajisti_adresar(str(oknomod.adresar(self.stav)))
        zapis_identitu(self.stav, self.klic, self.server_pid, self.server_start, ted)
        oknomod.uklid(self.stav, ted, self.klic)
        if self.okno.get("stav") != "zavreno":  # okno předchozího serveru téhož procesu
            self.okno.update(stav="zavreno", duvod_zavreni="konec_serveru", zavreno_v=ted)
            self._uloz()
        pojistka = oknomod.adresar(self.stav) / "pojistka-vypnuta"
        if not self.p.pojistka_bash:
            puvod.zapis(str(pojistka), {"cas": ted})
        elif os.path.lexists(pojistka):
            os.unlink(pojistka)

    def _uloz(self) -> None:
        oknomod.uloz_okno(self.stav, self.klic, self.okno)

    def otevreno(self) -> bool:
        return puvod.okno_plati(self.okno, self.okno.get("session_id"), self.hodiny())

    def _transkript(self) -> str | None:
        return puvod.over_transkript(self.okno.get("session_id"), self.okno.get("transcript_path"))

    def heartbeat_ok(self, ted: float) -> bool:
        sid = self.okno.get("session_id")
        if not isinstance(sid, str) or not puvod.UUID.match(sid):
            return False
        d = puvod.cti_tise(str(oknomod.cesta(self.stav, "rozsah", sid)), {})
        cas = d.get("cas")
        return (d.get("claude_pid") == self.claude_pid and isinstance(cas, (int, float))
                and ted - HEARTBEAT_MAX_S <= cas <= ted + 5)

    def zivost_hooku(self, ted: float) -> str:
        """V2: ok | cekej | nebezi | chyba. Hook rozsahu běží, pokud má session heartbeat tohoto procesu
        a v transkriptu není tool_use hlavního vlákna novější, než by hook heartbeat obnovil.
        Klidná session bez nástrojů hook nepotřebuje, stáří heartbeatu samo o sobě nerozhoduje."""
        sid = self.okno.get("session_id")
        if not isinstance(sid, str) or not puvod.UUID.match(sid):
            return "nebezi"
        d = puvod.cti_tise(str(oknomod.cesta(self.stav, "rozsah", sid)), {})
        cas = d.get("cas")
        if (d.get("claude_pid") != self.claude_pid or isinstance(cas, bool) or not isinstance(cas, (int, float))
                or cas > ted + HOOK_TOLERANCE_S):
            return "nebezi"
        tp = self._transkript()
        if tp is None:
            return "chyba"
        try:
            nalezen, t = puvod.posledni_nastroj(tp)
        except OSError:
            return "chyba"
        if not nalezen:
            return "ok"
        if t is None:
            return "chyba"
        if t <= cas + HEARTBEAT_ZAPIS_S + HOOK_TOLERANCE_S:
            return "ok"
        return "cekej" if ted - t < HOOK_LHUTA_S else "nebezi"

    async def _hlidej_hook(self, ted: float) -> bool:
        """Zavře okno, když hook rozsahu prokazatelně neběží. True = okno zůstává."""
        z = self.zivost_hooku(ted)
        if z in ("ok", "cekej"):
            return True
        self.chyba = "hook_rozsahu_nebezi" if z == "nebezi" else "neznamy_tvar_transkriptu"
        await self.zavri("chyba")
        return False

    def identity(self) -> set:
        return puvod.identity_serveru(self.stav, self.klic)

    def trida_tahu(self) -> dict:
        tp = self._transkript()
        sid = self.okno.get("session_id")
        agenti = puvod.cti_tise(str(oknomod.cesta(self.stav, "agenti", sid)), {}) if isinstance(sid, str) else {}
        return puvod.klasifikuj(tp, None, None, self.identity(), {}, agenti)

    # ------------------------------------------------------------ šablony a ledger

    async def sablona(self, kod: str, reply_to: int | None = None, **param) -> None:
        if self.klient is None:
            return
        text = SABLONY.get(self.jazyk, SABLONY["cs"])[kod].format(**param)
        try:
            mid = await self.klient.text(text, reply_to)
            self._retence_pridej(mid)
        except HlasChyba as e:
            log.info("sablona=%s chyba=%s", kod, e.kod)
        except Exception as e:  # noqa: BLE001 – šablona nesmí shodit příjem
            log.warning("sablona=%s typ=%s", kod, type(e).__name__)

    def _ledger(self) -> str:
        return str(oknomod.cesta(self.stav, "ledger", self.klic, ".jsonl"))

    def ledger_zapis(self, zaznam: dict) -> None:
        puvod.pripis_jsonl(self._ledger(), dict(zaznam, cas=self.hodiny()))

    def ledger(self) -> dict:
        vysledek: dict = {}
        for z in puvod.cti_jsonl(self._ledger()):
            if isinstance(z.get("znacka"), str):
                vysledek.setdefault(z["znacka"], {}).update(z)
        return vysledek

    def _retence_pridej(self, message_id: object) -> None:
        if isinstance(message_id, int) and not isinstance(message_id, bool) and self.kfg.telegram.smazat_po_h:
            try:
                puvod.pripis_jsonl(str(oknomod.adresar(self.stav) / "retence.jsonl"),
                                   {"message_id": message_id, "cas": self.hodiny()})
            except OSError as e:
                log.warning("retence: %s", type(e).__name__)

    # ------------------------------------------------------------ krok

    async def krok(self) -> None:
        ted = self.hodiny()
        await self.zpracuj_zadosti(ted)
        await self.over_cekajici_povel(ted)
        stav = self.okno.get("stav")
        if stav == "overovani":
            if ted - (self.overovani_od or ted) >= OVEROVANI_S:
                if self.heartbeat_ok(ted):
                    await self.otevri(ted)
                else:
                    await self.zavri("hook_rozsahu_nebezi")
            elif self.zamek_od is not None:
                await self.otevri(ted)
        elif stav == "otevreno":
            if not self.otevreno():
                konec = self.okno.get("konec", 0)
                await self.zavri("cas" if ted >= konec else "necinnost")
            elif self._presun():
                await self.zavri("presun", sablona="okno_presunuto")
            elif await self._kontrola_hooku(ted):
                await self._udalosti(ted)
                await self.over_doruceni(ted)
                await self.konec_tahu(ted)
                await self.dorucuj(ted)
                if self.okno.get("stav") == "otevreno":
                    await self._oznam_frontu(ted)
                    self._spust_polling()
                    self._status()
        if ted - self.posledni_retence >= RETENCE_KAZDYCH_S:
            self.posledni_retence = ted
            await self.retence(ted)

    async def _kontrola_hooku(self, ted: float) -> bool:
        """Průběžné hlídání hooku (i bez fronty), nejvýš jednou za KONTROLA_HOOKU_S."""
        if ted - self.posledni_kontrola_hooku < KONTROLA_HOOKU_S:
            return True
        self.posledni_kontrola_hooku = ted
        if not await self._hlidej_hook(ted):
            return False
        self._obnov_rc()
        return True

    def _obnov_rc(self) -> None:
        """N9: připojení nebo odpojení Remote Control během okna změní, zda se z Telegramu smí ptát."""
        tp = self._transkript()
        if tp is None:
            return
        bez_rc = not puvod.ma_remote_control(tp)
        if bez_rc != bool(self.okno.get("bez_rc")):
            self.okno.update(bez_rc=bez_rc, pri_dotazu="odmitnout" if bez_rc else self.p.pri_dotazu)
            self._uloz()
            self._zapis_politiku(bez_rc)
            log.info("remote control bez_rc=%s", bez_rc)

    def _presun(self) -> bool:
        a = puvod.cti_tise(str(oknomod.adresar(self.stav) / "aktivni.json"), {})
        return bool(a) and (a.get("claude_pid"), str(a.get("claude_start"))) != (self.claude_pid, str(self.claude_start))

    def _status(self) -> None:
        nove = {"fronta": len(self.fronta), "tah": self.doruceno[-1]["znacka"] if self.doruceno else None,
                "chyba": self.chyba}
        if any(self.okno.get(k) != v for k, v in nove.items()):
            self.okno.update(nove)
            self._uloz()

    # ------------------------------------------------------------ žádosti z hooků

    async def zpracuj_zadosti(self, ted: float) -> None:
        for cesta, d in oknomod.zadosti(self.stav, self.claude_pid, self.claude_start):
            akce = d.get("akce")
            if akce not in ("stop", "zavrit"):
                _smaz(cesta)
            if akce == "start":
                kod, zprava, uprava = self._prevezmi_start(d, ted)
                self._odpoved(d.get("prompt_id"), kod == "overuje_se", kod, zprava, uprava=uprava)
            elif akce in ("stop", "zavrit"):
                if self.povel_ceka is not None and self.povel_ceka["d"].get("session_id") == d.get("session_id"):
                    log.info("povel okna zrusen zadosti %s", akce)
                    self.povel_ceka = None
                # žádost o zavření zmizí až po zavření: hook ji do té doby vidí a tah z Telegramu zamítá
                try:
                    await self._zavri_podle_zadosti(akce, d)
                    self._odpoved(d.get("prompt_id"), True, "zavreno", oknomod.popis(self.okno, ted, self.jazyk))
                finally:
                    _smaz(cesta)

    async def _zavri_podle_zadosti(self, akce: str, d: dict) -> None:
        if self.okno.get("stav") not in ("overovani", "otevreno"):
            return
        duvod = d.get("duvod") if akce == "zavrit" else None
        if duvod == "bash_bez_obalu":
            # jiný hook zahodil obal bwrap; další příkazy z Telegramu už nepustit
            log.warning("Bash z Telegramu bezel bez obalu bwrap, okno se zavira")
            self.chyba = duvod
            await self.zavri(duvod)
        elif duvod == "konfigurace_agenta":
            # Bash z Telegramu založil, smazal nebo nahradil konfiguraci agenta či Gitu (nebo kontrola selhala)
            cesty = [_RIDICI.sub("?", c)[:300] for c in d.get("cesty") or [] if isinstance(c, str)][:50]
            log.warning("Bash z Telegramu zmenil konfiguraci agenta nebo Gitu, okno se zavira: %s",
                        ", ".join(cesty) or "kontrola nedobehla")
            self.chyba = duvod
            await self.zavri(duvod, sablona="okno_zavreno_konfigurace")
        else:
            await self.zavri("stop_terminal" if akce == "stop" or duvod == "stop_terminal" else "clear")

    def _odpoved(self, prompt_id: object, ok: bool, kod: str, zprava: str, **navic) -> None:
        if isinstance(prompt_id, str) and puvod.PROMPT_ID.match(prompt_id):
            puvod.zapis(str(oknomod.cesta(self.stav, "odpoved", prompt_id)),
                        dict(navic, ok=ok, kod=kod, zprava=zprava, cas=self.hodiny()))

    def _zprava_vypnuto(self) -> str:
        return "Příjem z Telegramu neběží: %s." % self.vypnuto + (
            " Spusť v terminálu: mcp-voice-telegram potvrd" if self.vypnuto == "allowlist_nepotvrzen" else "")

    def _prevezmi_start(self, d: dict, ted: float) -> tuple[str, str, bool]:
        """Žádost start od hooku: tvar a stav příjmu hned, povel až podle záznamu v transkriptu
        (over_cekajici_povel). Hook dostane overuje_se, nebo chybu."""
        if self.vypnuto:
            return self.vypnuto, self._zprava_vypnuto(), False
        sid = d.get("session_id")
        tp = puvod.over_transkript(sid, d.get("transcript_path"))
        minut, rezim, prompt_id = d.get("minut"), d.get("rezim"), d.get("prompt_id")
        if (tp is None or isinstance(minut, bool) or not isinstance(minut, int) or not 5 <= minut <= self.p.max_min
                or rezim not in politika.REZIMY or not isinstance(d.get("sha"), str)
                or not isinstance(prompt_id, str) or not puvod.PROMPT_ID.match(prompt_id)):
            return "zadost_neplatna", "Žádost o okno má neplatný tvar.", False
        self.povel_ceka = {"d": d, "tp": tp, "prijato": ted}
        if not os.path.lexists(oknomod.cesta(self.stav, "okno", self.klic)):
            self._uloz()  # soubor (zavřeného) okna: hook rozsahu v tahu povelu zapíše heartbeat (P4)
        uprava = self.otevreno() and self.okno.get("session_id") == sid
        return "overuje_se", "Telegram: žádost přijata, povel se ověří po odeslání promptu.", uprava

    async def over_cekajici_povel(self, ted: float) -> str | None:
        """Ověří převzatý povel podle záznamu v transkriptu (prompt_id z hooku). None = nic nečeká,
        cekej = záznam zatím není, ok | upraveno = okno ve stavu overovani nebo upravené, jinak kód chyby.
        Neověřený povel (i po OVERENI_POVELU_S) okno neotevře: log a šablona do Telegramu."""
        c = self.povel_ceka
        if c is None:
            return None
        d, prijato = c["d"], c["prijato"]
        v = puvod.over_povel(c["tp"], d.get("prompt_id"), d.get("sha"), self.p.povel, prijato - STARI_POVELU_S,
                             prijato + OVERENI_POVELU_S + 5)
        if not v["ok"] and v["kod"] in ("povel_nenalezen", "transkript_necitelny"):
            if ted - prijato < OVERENI_POVELU_S:
                return "cekej"
            v = dict(v, kod="povel_neoveren" if v["kod"] == "povel_nenalezen" else v["kod"])
        self.povel_ceka = None
        kod = (await self._start(d, c["tp"], v, ted))[0] if v["ok"] else v["kod"]
        if kod in ("ok", "upraveno"):
            log.info("povel okna overen kod=%s", kod)
        else:
            log.warning("povel okna neovereny kod=%s", kod)
            await self.sablona("okno_neovereno", duvod=kod)
        return kod

    async def _start(self, d: dict, tp: str, v: dict, ted: float) -> tuple[str, str]:
        """Ověřený povel (v = puvod.over_povel): okno do stavu overovani, nebo úprava otevřeného okna."""
        if self.vypnuto:
            return self.vypnuto, self._zprava_vypnuto()
        sid, minut, rezim = d.get("session_id"), d.get("minut"), d.get("rezim")
        try:  # N1: délku a režim určuje povel v transkriptu, ne soubor žádosti
            pv = oknomod.rozeber_povel(v["povel"], self.p.povel, self.p.vychozi_min, self.p.max_min,
                                       self.p.vychozi_rezim)
        except oknomod.ChybaPovelu:
            return "povel_nesouhlasi", "Žádost o okno nesouhlasí s povelem v transkriptu."
        if (pv.akce, pv.minut, pv.rezim) != ("start", minut, rezim):
            return "povel_nesouhlasi", "Žádost o okno nesouhlasí s povelem v transkriptu."
        neoverena = not puvod.verze_ok(v.get("verze"))
        if neoverena and not self.p.povolit_neoverenou_verzi:
            return "verze_neoverena", "Neověřená verze Claude Code, okno se neotevře (povolit_neoverenou_verzi)."
        bez_rc = not puvod.ma_remote_control(tp)
        cwd = d.get("cwd") if isinstance(d.get("cwd"), str) and os.path.isabs(d["cwd"]) else None
        if self.otevreno() and self.okno.get("session_id") == sid:
            self.okno.update(konec=ted + minut * 60, rezim=rezim, otevreno_prompt_id=d.get("prompt_id"))
            self._uloz()
            self._zapis_politiku(bez_rc)
            await self.sablona("okno_upraveno", konec=oknomod.hhmm(self.okno["konec"]),
                               rezim=oknomod.POPIS_REZIMU[self.jazyk][rezim])
            return "upraveno", "Telegram: okno upraveno do %s, režim %s." % (
                oknomod.hhmm(self.okno["konec"]), oknomod.POPIS_REZIMU[self.jazyk][rezim])
        if self.okno.get("stav") == "otevreno":
            await self.zavri("presun", sablona="okno_presunuto")
        self.okno = {"v": 1, "stav": "overovani", "session_id": sid, "transcript_path": tp, "cwd": cwd,
                     "rezim": rezim, "minut": minut, "otevreno": None, "konec": None, "posledni_zprava": None,
                     "necinnost_min": self.p.necinnost_min, "otevreno_prompt_id": d.get("prompt_id"),
                     "registrace": self.p.registrace, "claude_pid": self.claude_pid, "claude_start": self.claude_start,
                     "server_pid": self.server_pid, "server_start": self.server_start, "verze_cc": v.get("verze"),
                     "neoverena_verze": neoverena, "bez_rc": bez_rc, "bwrap": bool(self.bwrap),
                     "pri_dotazu": "odmitnout" if bez_rc else self.p.pri_dotazu, "duvod_zavreni": None,
                     "posledni_vlozeni": self.okno.get("posledni_vlozeni")}
        self.overovani_od = ted
        self.zamek_od = None
        self._uloz()
        self._zapis_politiku(bez_rc)
        return "ok", "Telegram: žádost přijata, okno se ověřuje."

    def _zapis_politiku(self, bez_rc: bool) -> None:
        pol = politika.sestav(
            registrace=self.p.registrace, chranene=self.p.chranene, chranene_zapis=self.p.chranene_zapis,
            zakazane_navic=self.p.zakazane_nastroje, cteni_navic=self.p.nastroje_navic, koreny=self.p.koreny,
            pri_dotazu=self.p.pri_dotazu, bez_rc=bez_rc,
            bwrap=self.bwrap if self.p.bash_izolace == "bwrap" else None,
            omezit_naplanovane=self.p.omezit_naplanovane, stav=str(self.stav),
            overena_verze=not self.okno.get("neoverena_verze", True), bwrap_sit=self.p.bash_sit,
            konfigurace=os.environ.get("MCP_VOICE_CONFIG"), konfigurace_agenta=self.p.konfigurace_agenta,
            bash_bez_dotazu=self.p.bash_bez_dotazu, bash_domov=self.p.bash_domov, bash_skryt=self.p.bash_skryt,
            bash_koreny=self.p.bash_koreny)
        puvod.zapis(str(oknomod.cesta(self.stav, "politika", self.klic)), pol)

    # ------------------------------------------------------------ otevření a zavření

    async def otevri(self, ted: float) -> bool:
        if self.zamek_od is None:
            self.zamek_od = ted
            puvod.zapis(str(oknomod.adresar(self.stav) / "aktivni.json"),
                        {"claude_pid": self.claude_pid, "claude_start": self.claude_start,
                         "session_id": self.okno.get("session_id"), "od": ted})
        if not self.zamek.zkus():
            if ted - self.zamek_od > ZAMEK_MAX_S:
                await self.zavri("chyba")
            return False
        self.zamek_od = None
        try:
            zpravy = await self.klient.zpravy(-1, 0)
            for u in zpravy:  # N7: zahozenou zprávu z allowlistu smaže retence
                m = u.get("message") if isinstance(u, dict) else None
                if (isinstance(m, dict) and isinstance(m.get("from"), dict)
                        and m["from"].get("id") in self.p.povolene_user_id):
                    self._retence_pridej(m.get("message_id"))
            posledni = max((u.get("update_id") for u in zpravy if isinstance(u, dict)
                            and isinstance(u.get("update_id"), int)), default=None)
            if posledni is not None:
                self.offset = posledni + 1
                await self.klient.zpravy(self.offset, 0)
        except HlasChyba as e:
            self.chyba = e.kod
            await self.zavri("chyba", sablona=None)
            return False
        minut = self.okno.get("minut") or self.p.vychozi_min
        self.okno.update(stav="otevreno", otevreno=ted, konec=ted + minut * 60, posledni_zprava=ted)
        self._reset_okna()
        self._uloz()
        navic = (NAVIC_BEZ_RC[self.jazyk] if self.okno.get("bez_rc") else "") + (
            NAVIC_VERZE[self.jazyk] if self.okno.get("neoverena_verze") else "")
        await self.sablona("okno_otevreno", konec=oknomod.hhmm(self.okno["konec"]),
                           rezim=oknomod.POPIS_REZIMU[self.jazyk].get(self.okno.get("rezim"), "?"), navic=navic)
        log.info("okno otevreno rezim=%s minut=%s", self.okno.get("rezim"), minut)
        self._spust_polling()
        return True

    def _reset_okna(self) -> None:
        self.fronta.clear()
        self.cekajici.clear()
        self.doruceno.clear()
        self.casy_zprav.clear()
        self.notifikovano.clear()
        self.pocet_v_okne = self.prekroceni = 0
        self.oznameno_fronta = False
        self.chyba = None

    async def zavri(self, duvod: str, sablona: str | None = "okno_zavreno") -> None:
        bylo_otevreno = self.okno.get("stav") == "otevreno"
        uloha, self.poll_uloha = self.poll_uloha, None
        if uloha is not None and uloha is not asyncio.current_task():
            uloha.cancel()
        self.fronta.clear()
        self.cekajici.clear()
        if bylo_otevreno and self.offset is not None and self.zamek.fd is not None and self.klient is not None:
            try:
                await self.klient.zpravy(self.offset, 0)
            except Exception as e:  # noqa: BLE001
                log.info("potvrzeni offsetu pri zavreni: %s", type(e).__name__)
        self.okno.update(stav="zavreno", duvod_zavreni=duvod, zavreno_v=self.hodiny(), fronta=0, tah=None)
        self._uloz()
        try:
            os.unlink(oknomod.cesta(self.stav, "politika", self.klic))
        except OSError:
            pass
        self.zamek.uvolni()
        self.zamek_od = None
        log.info("okno zavreno duvod=%s", duvod)
        if bylo_otevreno and sablona:
            if sablona == "okno_zavreno":
                await self.sablona("okno_zavreno", duvod=duvod)
            else:
                await self.sablona(sablona)

    # ------------------------------------------------------------ polling

    def _spust_polling(self) -> None:
        if self.polling and self.poll_uloha is None and self.zamek.fd is not None and self.klient is not None:
            try:
                self.poll_uloha = asyncio.get_running_loop().create_task(self._poll_smycka())
            except RuntimeError:
                self.poll_uloha = None

    async def _poll_smycka(self) -> None:
        cekani = 1.0
        while self.okno.get("stav") == "otevreno" and self.zamek.fd is not None:
            try:
                await self.poll_jednou()
                cekani = 1.0
            except asyncio.CancelledError:
                raise
            except HlasChyba as e:
                if e.extra.get("http") in (401, 403, 409):
                    self.chyba = "telegram_%s" % e.extra["http"]
                    self.poll_uloha = None
                    await self.zavri("chyba")
                    return
                await asyncio.sleep(cekani)
                cekani = min(60.0, cekani * 2)
            except Exception as e:  # noqa: BLE001
                log.warning("polling: %s", type(e).__name__)
                await asyncio.sleep(cekani)
                cekani = min(60.0, cekani * 2)

    async def poll_jednou(self) -> None:
        zpravy = await self.klient.zpravy(self.offset, self.p.long_poll_s)
        for u in zpravy:
            if not isinstance(u, dict) or not isinstance(u.get("update_id"), int):
                continue
            self.offset = max(self.offset or 0, u["update_id"] + 1)
            if self.okno.get("stav") != "otevreno":
                return
            await self.prijmi(u)

    # ------------------------------------------------------------ příjem jedné zprávy (8.2–8.4)

    async def prijmi(self, update: dict) -> None:
        ted = self.hodiny()
        m = update.get("message")
        if not isinstance(m, dict):
            return
        od, chat = m.get("from"), m.get("chat")
        if not isinstance(od, dict) or not isinstance(chat, dict):
            return
        uid = od.get("id")
        if uid not in self.p.povolene_user_id or od.get("is_bot") is not False:
            return
        chat_id = self.kfg.telegram.chat_id
        if chat.get("type") != "private" or chat.get("id") != uid or str(uid) != str(chat_id):
            return
        datum = m.get("date")
        if not isinstance(datum, int) or datum < int(self.okno.get("otevreno") or ted):
            return
        mid = m.get("message_id")
        if not isinstance(mid, int) or isinstance(mid, bool):
            return
        self._retence_pridej(mid)
        self.okno["posledni_zprava"] = ted
        self._uloz()
        if any(k in m for k in SPECIALNI):
            log.info("zprava typ=specialni")
            await self.sablona("preposlane", mid)
            return
        text = m.get("text")
        if isinstance(text, str):
            prikaz = text.strip().split("@", 1)[0].lower() if text.strip().startswith("/") else None
            if prikaz in ("/stop", "/konec"):
                await self.zavri("stop_telegram")
                return
            if prikaz == "/stav":
                await self.sablona("stav", mid, konec=oknomod.hhmm(self.okno.get("konec")),
                                   rezim=oknomod.POPIS_REZIMU[self.jazyk].get(self.okno.get("rezim"), "?"),
                                   fronta=len(self.fronta))
                return
            typ = "text"
        elif isinstance(m.get("voice"), dict):
            typ = "hlas"
        else:
            log.info("zprava typ=nepodporovany")
            await self.sablona("nepodporovany_typ", mid)
            return
        if not self._v_limitu(ted):
            self.prekroceni += 1
            if self.prekroceni >= 2:
                await self.zavri("limit")
            else:
                await self.sablona("limit", mid)
            return
        if len(self.fronta) >= self.p.fronta_max_zprav:
            await self.sablona("fronta_plna", mid)
            return
        delka_s = None
        if typ == "hlas":
            prepis = await self._hlasovka(m["voice"], mid)
            if prepis is None:
                return
            text, delka_s = prepis
        text = sanitizuj(text)
        if not text:
            await self.sablona("prazdna", mid)
            return
        if len(text) > self.p.max_znaku:
            await self.sablona("prilis_dlouha", mid, max=self.p.max_znaku)
            return
        text, odstraneno, masky = odstran_prikazy(text, self.p.zakazane_prikazy, self.jazyk)
        if odstraneno:
            log.info("zprava prikazu_odstraneno=%d masky=%s", odstraneno, ",".join(masky))
            await self.sablona("prikaz_odstranen", mid, pocet=odstraneno)
        forma = urci_formu(text, typ, self.p.forma_hlas, self.p.forma_text, self.p.forma_pc)
        z = Zprava(mid, typ, text, forma, ted, delka_s, odstraneno)
        self.fronta.append(z)
        self.ledger_zapis({"znacka": str(mid), "message_id": mid, "typ": typ, "delka": len(text), "forma": forma,
                           "stav": "fronta"})
        log.info("zprava typ=%s delka=%d forma=%s", typ, len(text), forma)

    def _v_limitu(self, ted: float) -> bool:
        self.casy_zprav = [t for t in self.casy_zprav if ted - t < 60]
        if len(self.casy_zprav) >= self.p.zprav_za_minutu or self.pocet_v_okne >= self.p.zprav_za_okno:
            return False
        self.casy_zprav.append(ted)
        self.pocet_v_okne += 1
        return True

    async def _hlasovka(self, v: dict, mid: int) -> tuple[str, float] | None:
        """8.4: jen voice, kontroly před stažením, ogg/opus, přepis jen lokálně (nikdy cloud)."""
        dur, velikost, mime, fid = v.get("duration"), v.get("file_size"), v.get("mime_type"), v.get("file_id")
        max_b = self.p.hlasovka_max_mb * 1024 * 1024
        if (mime != "audio/ogg" or not isinstance(dur, int) or isinstance(dur, bool) or dur > self.p.hlasovka_max_s
                or not isinstance(velikost, int) or velikost > max_b or not isinstance(fid, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", fid)):
            log.info("hlasovka odmitnuta pred stazenim")
            await self.sablona("nepodporovany_typ", mid)
            return None
        zacatek = time.monotonic()
        data = None
        try:
            data = await self.klient.soubor(fid, max_b)
            info = await vstup_audio.over_format(data, self.p.hlasovka_max_s, obal=self.dekodovani_obal, kodek="opus")
            if info.format_name != "ogg":
                raise HlasChyba("AUDIO_UNREADABLE")
            f32 = await vstup_audio.dekoduj_f32_16k(data, info, self.p.hlasovka_max_s, obal=self.dekodovani_obal,
                                                    dekoder="libopus")
            if self.prepis is None:
                raise HlasChyba("STT_LOCAL_UNAVAILABLE")
            vysledek = await self.prepis(f32)
        except HlasChyba as e:
            log.info("hlasovka chyba=%s", e.kod)
            if e.kod in ("AUDIO_UNREADABLE", "AUDIO_TOO_LONG", "FILE_TOO_LARGE"):
                await self.sablona("nepodporovany_typ", mid)
            else:
                await self.sablona("prepis_nedostupny", mid)
            return None
        except Exception as e:  # noqa: BLE001
            log.warning("hlasovka typ=%s", type(e).__name__)
            await self.sablona("prepis_nedostupny", mid)
            return None
        finally:
            data = None
        text = vysledek.get("text") if isinstance(vysledek, dict) else None
        log.info("hlasovka delka_s=%d ms=%d", dur, int((time.monotonic() - zacatek) * 1000))
        if not isinstance(text, str) or not text.strip():
            await self.sablona("nerozumel", mid)
            return None
        return text, float(dur)

    # ------------------------------------------------------------ doručení (8.6–8.8)

    async def dorucuj(self, ted: float) -> None:
        if not self.fronta or self.cekajici:
            return
        z = self.zivost_hooku(ted)
        if z == "cekej":
            return
        if z != "ok":
            await self._hlidej_hook(ted)
            return
        tp = self._transkript()
        if tp is None:
            await self.zavri("chyba")
            return
        st = puvod.stav_session(tp, ted, self.p.necinnost_tahu_s)
        if st["stav"] != "necinna" and ted - self.fronta[0].prijato < self.p.max_cekani_min * 60:
            return
        z = self.fronta.pop(0)
        if not self.fronta:
            self.oznameno_fronta = False
        text = obalka(z.message_id, z.typ, z.delka_s, z.forma, self.okno.get("konec") or ted,
                      self.okno.get("rezim"), self.jazyk, z.odstraneno) + "\n" + z.text
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.ledger_zapis({"znacka": str(z.message_id), "sha256_obsahu": sha, "stav": "odeslano",
                           "vlozeno": ted, "forma": z.forma})
        try:
            await asyncio.to_thread(self.inbox.posli, text)
        except InboxChyba as e:
            self.chyba = e.kod
            log.warning("doruceni chyba=%s", e.kod)
            self.ledger_zapis({"znacka": str(z.message_id), "stav": "nepotvrzeno"})
            await self.sablona("doruceni_nepotvrzeno", z.message_id)
            return
        self.cekajici[str(z.message_id)] = (ted, z)
        self._vlozeno(ted)  # S5: starší značky „člověk“ hook rozsahu nebere

    def _vlozeno(self, ted: float) -> None:
        """S5: čas vložení zprávy do session (odeslání i potvrzení doručení, i opožděného).
        Značky „člověk“ starší než tento čas hook rozsahu nebere."""
        if not isinstance(self.okno.get("posledni_vlozeni"), (int, float)) or ted > self.okno["posledni_vlozeni"]:
            self.okno["posledni_vlozeni"] = ted
        self._uloz()

    async def over_doruceni(self, ted: float) -> None:
        if not self.cekajici and not self.pozde:
            return
        tp = self._transkript()
        identity = self.identity()
        for znacka, cas in list(self.pozde.items()):
            if ted - cas > POZDE_MAX_S:
                del self.pozde[znacka]
            elif tp and puvod.najdi_doruceni(tp, znacka, identity) == "doruceno":
                del self.pozde[znacka]
                self._vlozeno(ted)
        for znacka, (cas, z) in list(self.cekajici.items()):
            r = puvod.najdi_doruceni(tp, znacka, identity) if tp else None
            if r == "doruceno":
                del self.cekajici[znacka]
                self._vlozeno(ted)
                self.ledger_zapis({"znacka": znacka, "stav": "doruceno"})
                self.doruceno.append({"znacka": znacka, "message_id": z.message_id, "forma": z.forma, "cas": ted})
            elif r == "chyba_tvaru":
                self.chyba = "neznamy_tvar_transkriptu"
                await self.zavri("chyba")
                return
            elif ted - cas > self.p.potvrzeni_s:
                del self.cekajici[znacka]
                self.pozde[znacka] = cas
                self._vlozeno(ted)
                self.ledger_zapis({"znacka": znacka, "stav": "nepotvrzeno"})
                await self.sablona("doruceni_nepotvrzeno", z.message_id)

    async def konec_tahu(self, ted: float) -> None:
        """R10: tah z Telegramu skončil bez odpovez → šablona (Esc pozná podle nečinnosti)."""
        if not self.doruceno:
            return
        tp = self._transkript()
        st = puvod.stav_session(tp, ted, self.p.necinnost_tahu_s) if tp else {"stav": "necinna", "jak": "konec"}
        if st["stav"] != "necinna":
            return
        for d in self.doruceno:
            self.ledger_zapis({"znacka": d["znacka"], "stav": "bez_odpovedi"})
        posledni = self.doruceno[-1]
        self.doruceno.clear()
        await self.sablona("preruseno_bez_odpovedi" if st["jak"] == "preruseno" else "hotovo_bez_odpovedi",
                           posledni["message_id"])

    async def _udalosti(self, ted: float) -> None:
        sid = self.okno.get("session_id")
        if not isinstance(sid, str):
            return
        cesta = str(oknomod.cesta(self.stav, "udalosti", sid, ".jsonl"))
        if not os.path.lexists(cesta):
            return
        try:
            with puvod.Zamek(cesta, 0.2):
                udalosti = puvod.cti_jsonl(cesta)
                os.unlink(cesta)
        except (OSError, TimeoutError):
            return
        for u in udalosti:
            pid = u.get("prompt_id")
            if u.get("udalost") == "odpoved_zamitnuta" and self.doruceno:
                k = u.get("kontext") if self.p.oznamit_nazev_kontextu and isinstance(u.get("kontext"), str) else None
                posledni = self.doruceno.pop()
                self.ledger_zapis({"znacka": posledni["znacka"], "stav": "bez_odpovedi"})
                await self.sablona("odpoved_jen_v_terminalu", posledni["message_id"],
                                   kontext=(" (%s)" % k[:40]) if k else "")
                continue
            if u.get("udalost") == "notifikace" and self.doruceno and pid not in self.notifikovano:
                self.notifikovano.add(pid)
                self.okno["ceka_potvrzeni"] = True
                self._uloz()
                await self.sablona("ceka_potvrzeni", self.doruceno[-1]["message_id"])

    async def _oznam_frontu(self, ted: float) -> None:
        if self.fronta and not self.oznameno_fronta and ted - self.fronta[0].prijato >= self.p.oznamit_po_s:
            self.oznameno_fronta = True
            await self.sablona("fronta", self.fronta[0].message_id, pocet=len(self.fronta))

    # ------------------------------------------------------------ odpovědi (9.1)

    def cil_odpovedi(self) -> dict:
        """Kontrola pro odpovez: otevřené okno, tah z Telegramu, doručená zpráva. Vrací {znacka, message_id, forma}."""
        if self.okno.get("stav") != "otevreno" or not self.otevreno():
            raise HlasChyba("WINDOW_CLOSED", "Okno Telegramu je zavřené.")
        t = self.trida_tahu()
        if t["trida"] != puvod.TELEGRAM:
            raise HlasChyba("NOT_TELEGRAM_TURN", "odpovez jen v tahu, který přišel z Telegramu.")
        ledger = self.ledger()
        for znacka in t["znacky"]:
            z = ledger.get(znacka)
            if z and z.get("stav") in ("doruceno", "odpovezeno", "odeslano", "bez_odpovedi"):
                return {"znacka": znacka, "message_id": z.get("message_id"), "forma": z.get("forma", "text")}
        raise HlasChyba("NOT_TELEGRAM_TURN", "V tomto tahu není doručená zpráva z Telegramu.")

    def oznac_odpoved(self, znacka: str) -> None:
        self.ledger_zapis({"znacka": znacka, "stav": "odpovezeno"})
        self.doruceno = [d for d in self.doruceno if d["znacka"] != znacka]
        self.okno["ceka_potvrzeni"] = False

    async def posli_text(self, text: str, reply_to: int | None) -> int:
        casti = rozdel(text, self.kfg.telegram.max_casti, self.kfg.telegram.max_znaku_casti, self.jazyk)
        for cast in casti:
            mid = await self.klient.text(cast, reply_to)
            self._retence_pridej(mid)
        return len(casti)

    async def potvrd_otevreni(self) -> dict:
        """odpovez v tahu povelu: okno ve stavu overovani se otevře (heartbeat hooku ověřen). Čekající povel
        se nejdřív ověří (záznam promptu už v transkriptu je); bez ověřeného povelu se okno neotevře."""
        ted = self.hodiny()
        kod = await self.over_cekajici_povel(ted)
        if kod == "upraveno":
            return {"ok": True, "odeslano": "text", "casti": 1, "sablona": "okno_upraveno"}
        if self.okno.get("stav") != "overovani":
            if kod == "cekej":
                raise HlasChyba("WINDOW_CLOSED", "Povel okna se ještě ověřuje, okno se zatím neotevře.")
            if kod not in (None, "ok"):
                raise HlasChyba("WINDOW_CLOSED", "Povel okna nešel ověřit (%s), okno se neotevře." % kod)
            raise HlasChyba("NOT_TELEGRAM_TURN", "odpovez jen v tahu z Telegramu.")
        if self.trida_tahu()["trida"] != puvod.CLOVEK:
            raise HlasChyba("NOT_TELEGRAM_TURN", "Okno potvrzuje jen tah povelu napsaný člověkem.")
        if not self.heartbeat_ok(ted):
            raise HlasChyba("WINDOW_CLOSED", "Hook rozsahu v této session neběží, okno se neotevře.")
        if not await self.otevri(ted):
            raise HlasChyba("WINDOW_CLOSED", "Okno se nepodařilo otevřít (%s)." % (self.chyba or "zámek"))
        return {"ok": True, "odeslano": "text", "casti": 1, "sablona": "okno_otevreno"}

    # ------------------------------------------------------------ retence (9.4)

    async def retence(self, ted: float) -> None:
        h = self.kfg.telegram.smazat_po_h
        cesta = str(oknomod.adresar(self.stav) / "retence.jsonl")
        if not h or self.klient is None or not os.path.lexists(cesta):
            return
        try:
            with puvod.Zamek(cesta, 1.0):
                zaznamy = puvod.cti_jsonl(cesta)
                hranice = ted - h * 3600
                nejstarsi = ted - TELEGRAM_MAX_STARI_H * 3600 + 60
                smazat = [z["message_id"] for z in zaznamy if isinstance(z.get("message_id"), int)
                          and isinstance(z.get("cas"), (int, float)) and nejstarsi < z["cas"] <= hranice]
                zbyva = [z for z in zaznamy if isinstance(z.get("cas"), (int, float)) and z["cas"] > hranice]
                for i in range(0, len(smazat), 100):
                    try:
                        await self.klient.smaz(smazat[i:i + 100])
                    except HlasChyba as e:
                        log.info("retence chyba=%s", e.kod)
                tmp = cesta + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    for z in zbyva:
                        fh.write(json.dumps(z) + "\n")
                os.chmod(tmp, 0o600)
                os.replace(tmp, cesta)
        except (OSError, TimeoutError) as e:
            log.warning("retence: %s", type(e).__name__)
