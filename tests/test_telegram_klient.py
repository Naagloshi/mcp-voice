"""telegram.py pro příjem: allowlist metod, parametry, stažení souboru bez přesměrování a s limitem."""
from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from mcp_voice import telegram
from mcp_voice.chyby import HlasChyba

from .conftest import CHAT, FALESNY_TOKEN


def transport(odpovedi, zaznam):
    def h(req):
        zaznam.append(req)
        return odpovedi(req)
    return httpx.MockTransport(h)


def formular(req):
    return {k: v[0] for k, v in parse_qs(req.content.decode()).items()}


async def test_getupdates_parametry():
    z = []
    t = transport(lambda r: httpx.Response(200, json={"ok": True, "result": [{"update_id": 1}]}), z)
    v = await telegram.nacti_zpravy(FALESNY_TOKEN, 5, 50, 60, t)
    f = formular(z[0])
    assert v == [{"update_id": 1}] and f["offset"] == "5" and f["timeout"] == "50"
    assert json.loads(f["allowed_updates"]) == ["message"] and z[0].url.path.endswith("/getUpdates")


async def test_sendmessage_bez_parse_mode_s_reply():
    z = []
    t = transport(lambda r: httpx.Response(200, json={"ok": True, "result": {
        "message_id": 7, "chat": {"id": int(CHAT), "type": "private"}}}), z)
    mid = await telegram.posli_text(FALESNY_TOKEN, CHAT, "*ahoj*", 5, True, t, reply_to=42)
    f = formular(z[0])
    assert mid == 7 and "parse_mode" not in f and f["text"] == "*ahoj*" and f["protect_content"] == "true"
    assert json.loads(f["link_preview_options"]) == {"is_disabled": True}
    assert json.loads(f["reply_parameters"])["message_id"] == 42


async def test_odpoved_jinemu_chatu_je_chyba():
    t = transport(lambda r: httpx.Response(200, json={"ok": True, "result": {
        "message_id": 7, "chat": {"id": 1, "type": "group"}}}), [])
    with pytest.raises(HlasChyba):
        await telegram.posli_text(FALESNY_TOKEN, CHAT, "x", 5, transport=t)


@pytest.mark.parametrize("kod", [401, 403, 409])
async def test_chyby_http_nesou_kod(kod):
    t = transport(lambda r: httpx.Response(kod), [])
    with pytest.raises(HlasChyba) as e:
        await telegram.nacti_zpravy(FALESNY_TOKEN, None, 0, 5, t)
    assert e.value.extra["http"] == kod and FALESNY_TOKEN not in e.value.zprava


@pytest.mark.parametrize("cesta", ["../x", "voice/../../x", "/etc/passwd", "a b", "x" * 300, None])
def test_neplatna_cesta_souboru(cesta):
    with pytest.raises(HlasChyba):
        telegram.over_cestu_souboru(cesta)


async def test_stazeni_bez_presmerovani():
    t = transport(lambda r: httpx.Response(302, headers={"location": "https://jinde.invalid/x"}), [])
    with pytest.raises(HlasChyba):
        await telegram.stahni(FALESNY_TOKEN, "voice/file_1.oga", 1000, 5, t)


async def test_stazeni_limit_pri_cteni():
    t = transport(lambda r: httpx.Response(200, content=b"x" * 5000), [])
    with pytest.raises(HlasChyba) as e:
        await telegram.stahni(FALESNY_TOKEN, "voice/file_1.oga", 1000, 5, t)
    assert e.value.kod == "FILE_TOO_LARGE"


async def test_klient_soubor_a_smazani():
    z = []

    def odp(r):
        if r.url.path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/f.oga"}})
        if r.url.path.endswith("/deleteMessages"):
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(200, content=b"OggS")
    k = telegram.Klient(lambda: FALESNY_TOKEN, CHAT, 5, transport=transport(odp, z))
    assert await k.soubor("fid", 100) == b"OggS"
    assert z[1].url.path == "/file/bot%s/voice/f.oga" % FALESNY_TOKEN and z[1].url.host == telegram.HOST
    assert await k.smaz([1, 2])
    assert json.loads(formular(z[2])["message_ids"]) == [1, 2]
    with pytest.raises(ValueError):
        await k.smaz(list(range(101)))
    assert FALESNY_TOKEN not in json.dumps(vars(k), default=str)
