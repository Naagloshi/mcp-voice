"""Fronta přehrávání na PC: odpovědi nikdy nemluví přes sebe, zastav() utne vše.

Během přehrávání se každou sekundu ptá na zámek PC; při zamčení přehrávání utne
a zbytek fronty zahodí (R-PC-2). Hlasitost nemění.
"""
from __future__ import annotations

import logging
import queue
import shutil
import subprocess
import threading
import time
from typing import Callable

from .chyby import HlasChyba

log = logging.getLogger("mcp_voice.prehravac")

PRIKAZY = {
    "paplay": ["paplay", "--raw", "--format=s16le", "--rate=24000", "--channels=1"],
    "pw-cat": ["pw-cat", "--playback", "--format", "s16", "--rate", "24000", "--channels", "1", "-"],
}
KUS = 24000 * 2 // 4  # čtvrt sekundy zvuku
KONTROLA_ZAMKU_S = 1.0


class Prehravac:
    def __init__(self, prikaz: str, zamceno: Callable[[], bool | None],
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen):
        self.argv = PRIKAZY[prikaz]
        self.zamceno = zamceno
        self._popen = popen
        self._fronta: queue.Queue[bytes] = queue.Queue()
        self._stop = threading.Event()
        self._proces: subprocess.Popen | None = None
        self._hraje = False
        self._zamek = threading.Lock()
        self._vlakno: threading.Thread | None = None

    def dostupny(self) -> bool:
        return shutil.which(self.argv[0]) is not None

    def pridej(self, pcm: bytes) -> int:
        if not self.dostupny():
            raise HlasChyba("PLAYER_UNAVAILABLE", f"Chybí přehrávač {self.argv[0]}.")
        with self._zamek:
            if self._vlakno is None or not self._vlakno.is_alive():
                self._vlakno = threading.Thread(target=self._smycka, name="mcp-voice-prehravac", daemon=True)
                self._vlakno.start()
            self._fronta.put(pcm)
            return self._fronta.qsize()

    def zastav(self) -> int:
        """Utne přehrávání a vyprázdní frontu. Vrací počet zahozených položek (včetně hrající)."""
        zahozeno = 0
        while True:
            try:
                self._fronta.get_nowait()
                zahozeno += 1
            except queue.Empty:
                break
        with self._zamek:
            if self._hraje:
                zahozeno += 1
                self._stop.set()
                if self._proces is not None and self._proces.poll() is None:
                    self._proces.kill()
        return zahozeno

    def _smycka(self) -> None:
        while True:
            try:
                pcm = self._fronta.get(timeout=30)
            except queue.Empty:
                return  # vlákno skončí, při dalším pridej() vznikne nové
            try:
                self._prehraj(pcm)
            except Exception as e:  # noqa: BLE001
                log.warning("prehravani selhalo: %s", type(e).__name__)

    def _prehraj(self, pcm: bytes) -> None:
        with self._zamek:
            self._stop.clear()
            self._hraje = True
            try:
                self._proces = self._popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
            except OSError:
                self._hraje = False
                log.warning("prehravac nejde spustit")
                return
            proc = self._proces
        posledni_kontrola = 0.0
        prerusit = False
        try:
            for i in range(0, len(pcm), KUS):
                if self._stop.is_set():
                    prerusit = True
                    break
                ted = time.monotonic()
                if ted - posledni_kontrola >= KONTROLA_ZAMKU_S:
                    posledni_kontrola = ted
                    if self._zamceno_prerus():
                        prerusit = True
                        break
                try:
                    proc.stdin.write(pcm[i:i + KUS])
                except (BrokenPipeError, ValueError, OSError):
                    break
            if not prerusit:
                try:
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
                while proc.poll() is None:
                    if self._stop.wait(0.2):
                        prerusit = True
                        break
                    ted = time.monotonic()
                    if ted - posledni_kontrola >= KONTROLA_ZAMKU_S:
                        posledni_kontrola = ted
                        if self._zamceno_prerus():
                            prerusit = True
                            break
        finally:
            # Přerušení = okamžitě zabít (zavřený vstup by nechal dohrát buffer přehrávače).
            if prerusit or proc.poll() is None:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            with self._zamek:
                self._hraje = False
                self._proces = None

    def _zamceno_prerus(self) -> bool:
        if self.zamceno() is True:
            log.info("prehravani preruseno: PC zamceno")
            self._zahod_frontu()
            return True
        return False

    def _zahod_frontu(self) -> None:
        while True:
            try:
                self._fronta.get_nowait()
            except queue.Empty:
                return
