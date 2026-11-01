"""Lokální přepis: faster-whisper (volitelné extra [lokalni]). Nic neodchází ze stroje.

Model se načte z lokální cesty (stažený a ověřený příkazem mcp-voice-model), bez sítě
(HF_HUB_OFFLINE). Načte se při prvním volání a uvolní po nečinnosti (VRAM sdílí i jiné
procesy). Když nejde GPU, zkusí CPU int8; selhání = STT_LOCAL_FAILED, nikdy cloud.
Knihovny CUDA jsou pip wheely ve venv projektu a načtou se předem (RTLD_GLOBAL).
"""
from __future__ import annotations

import asyncio
import ctypes
import glob
import logging
import os
import sys
import threading
import time

from .chyby import HlasChyba
from .config import Stt

log = logging.getLogger("mcp_voice.stt")

KNIHOVNY_CUDA = (
    "cuda_runtime/lib/libcudart.so.12",
    "cublas/lib/libcublasLt.so.12",
    "cublas/lib/libcublas.so.12",
    "cudnn/lib/libcudnn*.so.9",
)


def nacti_knihovny_cuda() -> int:
    """Načte knihovny NVIDIA z pip wheelů tohoto venv. Vrací počet načtených."""
    pocet = 0
    for sp in sys.path:
        zaklad = os.path.join(sp, "nvidia")
        if not os.path.isdir(zaklad):
            continue
        for vzor in KNIHOVNY_CUDA:
            for so in sorted(glob.glob(os.path.join(zaklad, vzor))):
                try:
                    ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
                    pocet += 1
                except OSError:
                    log.warning("knihovnu CUDA nejde načíst: %s", os.path.basename(so))
        break
    return pocet


def dostupnost(nastaveni: Stt) -> str:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return "nenainstalovano"
    cesta = os.path.expanduser(nastaveni.model_cesta)
    return "dostupne" if os.path.isfile(os.path.join(cesta, "model.bin")) else "chybi_model"


class LokalniPrepis:
    def __init__(self, nastaveni: Stt):
        self.n = nastaveni
        self._model = None
        self._zarizeni: str | None = None
        self._zamek = threading.Lock()
        self._casovac: threading.Timer | None = None

    def _nacti(self):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise HlasChyba("STT_LOCAL_UNAVAILABLE",
                            "Lokální přepis není nainstalovaný (extra [lokalni]). Cloud jen na výslovný pokyn uživatele.") from None
        cesta = os.path.expanduser(self.n.model_cesta)
        if not os.path.isfile(os.path.join(cesta, "model.bin")):
            raise HlasChyba("STT_LOCAL_UNAVAILABLE", "Model pro lokální přepis není stažený (mcp-voice-model stahni).")
        pokusy = [(self.n.zarizeni, self.n.compute_type)]
        if self.n.zarizeni == "cuda":
            nacti_knihovny_cuda()
            pokusy.append(("cpu", "int8"))
        for zarizeni, typ in pokusy:
            try:
                self._model = WhisperModel(cesta, device=zarizeni, compute_type=typ, local_files_only=True)
                self._zarizeni = zarizeni
                log.info("stt model nacten zarizeni=%s", zarizeni)
                return
            except Exception as e:  # noqa: BLE001
                log.warning("stt model nejde nacist zarizeni=%s typ=%s", zarizeni, type(e).__name__)
        raise HlasChyba("STT_LOCAL_FAILED", "Model pro lokální přepis nejde načíst.")

    def uvolni(self) -> None:
        with self._zamek:
            self._model = None
            self._zarizeni = None
        log.info("stt model uvolnen")

    def _naplanuj_uvolneni(self) -> None:
        if self._casovac is not None:
            self._casovac.cancel()
        if self.n.uvolnit_po_s > 0:
            self._casovac = threading.Timer(self.n.uvolnit_po_s, self.uvolni)
            self._casovac.daemon = True
            self._casovac.start()

    def _prepis_sync(self, f32: bytes, jazyk: str | None) -> dict:
        with self._zamek:
            if self._model is None:
                self._nacti()
            import numpy as np  # závislost faster-whisper: až po _nacti, které chybějící extra ohlásí kódem

            zvuk = np.frombuffer(f32, dtype=np.float32)
            zacatek = time.monotonic()
            try:
                segmenty, info = self._model.transcribe(
                    zvuk, language=jazyk, beam_size=5, vad_filter=False,
                    hotwords=self.n.hotwords or None, initial_prompt=self.n.initial_prompt or None,
                    condition_on_previous_text=False)
                text = " ".join(s.text.strip() for s in segmenty).strip()
            except Exception as e:  # noqa: BLE001
                log.warning("stt prepis selhal typ=%s", type(e).__name__)
                raise HlasChyba("STT_LOCAL_FAILED",
                                "Lokální přepis selhal. Cloud (engine=\"cloud\") jen na výslovný pokyn uživatele.") from None
            trvani_ms = int((time.monotonic() - zacatek) * 1000)
            zarizeni = self._zarizeni
        self._naplanuj_uvolneni()
        return {"text": text, "jazyk": info.language, "jistota_jazyka": round(float(info.language_probability), 3),
                "trvani_ms": trvani_ms, "zarizeni": zarizeni}

    async def prepis(self, f32: bytes, jazyk: str | None) -> dict:
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._prepis_sync, f32, jazyk), self.n.timeout_s)
        except asyncio.TimeoutError:
            raise HlasChyba("STT_LOCAL_FAILED", "Lokální přepis nedoběhl včas.") from None
