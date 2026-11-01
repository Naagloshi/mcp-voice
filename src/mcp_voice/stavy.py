"""Bezpečné stavové soubory: adresář 0700, soubor 0600, zápis atomicky (mkstemp + replace)."""
from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path


def zajisti_adresar(cesta: Path) -> Path:
    """Vytvoří adresář 0700 (i rodiče). Existující musí být vlastní, ne symlink; práva srovná na 0700."""
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


def zapis_json(cesta: Path, data: object) -> None:
    zajisti_adresar(cesta.parent)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=cesta.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, cesta)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
