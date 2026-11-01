"""Konfigurace příjmu z Telegramu (kap. 13): přísná validace, chyba = vypnutý příjem, ostatní běží."""
from __future__ import annotations

import pytest

from mcp_voice import config, politika
from mcp_voice.config import ChybaKonfigurace


CHAT = "4242" + "42"


def kfg(prijem=None, chat=CHAT, **telegram):
    t = {"chat_id": chat}
    if prijem is not None:
        t["prijem"] = prijem
    t.update(telegram)
    return config.z_dat({"telegram": t})


def test_vychozi_hodnoty():
    p = kfg().telegram.prijem
    assert p.povoleno is False and p.chyba is None
    assert (p.vychozi_min, p.max_min, p.necinnost_min, p.vychozi_rezim) == (60, 240, 60, "prace")
    # G5b: dialog ask z tahu Telegramu jde potvrdit z Remote Control i při zamčeném PC → výchozí cekat
    assert p.koreny == () and p.pri_dotazu == "cekat" and p.omezit_naplanovane is True
    assert p.bash_izolace == "bwrap" and p.bash_sit is False
    assert p.chranene == politika.CHRANENE_VYCHOZI and p.zakazane_prikazy == ("/[!/]*",)
    t = kfg().telegram
    assert (t.max_casti, t.max_znaku_casti, t.smazat_po_h) == (3, 4000, 24)


def test_plny_bash_sit():
    p = kfg({"rezimy": {"plny": {"bash_sit": True}}}).telegram.prijem
    assert p.chyba is None and p.bash_sit is True
    assert kfg({"rezimy": {"plny": {"bash_sit": "ano"}}}).telegram.prijem.chyba


def test_plny_bash_bez_dotazu_domov_skryt():
    """Bash bez dotazu jen vědomě; složky nástrojů z domova a další skrytá místa."""
    p = kfg().telegram.prijem
    assert p.bash_bez_dotazu is False and p.bash_skryt == () and p.bash_koreny == ()
    assert p.bash_domov == politika.BASH_DOMOV_VYCHOZI and "~/.local/bin" in p.bash_domov
    assert p.konfigurace_agenta == ()
    p = kfg({"rezimy": {"plny": {"bash_bez_dotazu": True, "bash_skryt": ["/nfs2"],
                                 "bash_domov": {"pridat": ["~/go/pkg"], "odebrat_vychozi": ["~/.nvm"]}}}}).telegram.prijem
    assert p.chyba is None and p.bash_bez_dotazu is True and p.bash_skryt == ("/nfs2",)
    assert "~/go/pkg" in p.bash_domov and "~/.nvm" not in p.bash_domov
    for spatne in ({"bash_bez_dotazu": "ano"}, {"bash_domov": {"pridat": ["/usr/x"]}},
                   {"bash_domov": {"pridat": ["~"]}}, {"bash_domov": {"pridat": ["~/a/../.ssh"]}},
                   {"bash_domov": {"pridat": ["~/.ssh"]}}, {"bash_domov": {"pridat": ["*.env"]}},
                   {"bash_skryt": ["relativni"]}, {"bash_skryt": ["/"]}, {"bash_skryt": "/mnt"}):
        assert kfg({"rezimy": {"plny": spatne}}).telegram.prijem.chyba, spatne


def test_bash_koreny(tmp_path):
    """Kořeny Bash navíc k cwd v prace i plny; chráněný kořen nebo předek chráněné cesty neprojde."""
    p = kfg({"rezimy": {"prace": {"bash_koreny": [{"cesta": "~/projekty/a", "pristup": "ro"}]},
                        "plny": {"bash_koreny": [{"cesta": "/srv/data/b", "pristup": "rw"}]}}}).telegram.prijem
    assert p.chyba is None
    assert p.bash_koreny == (("prace", "~/projekty/a", "ro"), ("plny", "/srv/data/b", "rw"))
    for spatne in ([{"cesta": "~", "pristup": "ro"}], [{"cesta": "~/.ssh", "pristup": "ro"}],
                   [{"cesta": "~/.config", "pristup": "ro"}], [{"cesta": "/", "pristup": "ro"}],
                   [{"cesta": "~/x/.claude", "pristup": "rw"}], [{"cesta": "~/x", "pristup": "zapis"}],
                   [{"cesta": "~/x"}], [{"cesta": "rel", "pristup": "ro"}], ["~/x"],
                   [{"cesta": "~/x", "pristup": "ro", "navic": 1}], "~/x", [{"cesta": "/dev", "pristup": "ro"}],
                   [{"cesta": "/sys/class", "pristup": "ro"}], [{"cesta": "/run/user", "pristup": "rw"}],
                   [{"cesta": "/boot", "pristup": "ro"}], [{"cesta": "/tmp", "pristup": "rw"}]):
        assert kfg({"rezimy": {"plny": {"bash_koreny": spatne}}}).telegram.prijem.chyba, spatne
    # stav mcp-voice (cesty.stav) je chráněný: kořen nad ním neprojde
    stav = tmp_path / "stav"
    k = config.z_dat({"telegram": {"chat_id": CHAT, "prijem": {"rezimy": {"plny": {"bash_koreny": [
        {"cesta": str(tmp_path), "pristup": "ro"}]}}}}, "cesty": {"stav": str(stav)}})
    assert k.telegram.prijem.chyba and "chráněnou" in k.telegram.prijem.chyba
    assert kfg({"rezimy": {"cteni": {"bash_koreny": []}}}).telegram.prijem.chyba  # v cteni Bash neběží


def test_konfigurace_agenta_jen_pridat():
    p = kfg({"konfigurace_agenta": {"pridat": ["COPILOT.md", ".windsurf"]}}).telegram.prijem
    assert p.chyba is None and p.konfigurace_agenta == ("COPILOT.md", ".windsurf")
    assert kfg({"konfigurace_agenta": {"odebrat_vychozi": ["CLAUDE.md"]}}).telegram.prijem.chyba
    assert kfg({"konfigurace_agenta": {"pridat": ["a/b"]}}).telegram.prijem.chyba


def test_povoleno_platne():
    p = kfg({"povoleno": True, "povolene_user_id": [424242]}).telegram.prijem
    assert p.povoleno and p.chyba is None and p.povolene_user_id == (424242,)


@pytest.mark.parametrize("prijem, cast", [
    ({"povoleno": True, "povolene_user_id": []}, "povoleno vyžaduje"),
    ({"povoleno": True, "povolene_user_id": [1]}, "chat_id musí být"),
    ({"povolene_user_id": [True]}, "povolene_user_id"),
    ({"povolene_user_id": [1, 2, 3, 4, 5, 6]}, "povolene_user_id"),
    ({"neznamy": 1}, "neznámý klíč"),
    ({"okno": {"vychozi_min": 300}}, "mimo rozsah"),
    ({"okno": {"max_min": 60, "vychozi_min": 90}}, "nejvýš max_min"),
    ({"okno": {"max_min": 241}}, "mimo rozsah"),
    ({"okno": {"vychozi_rezim": "vse"}}, "vychozi_rezim"),
    ({"long_poll_s": True}, "číslo"),
    ({"registrace": "a b"}, "tvar"),
    ({"povel": "telegram"}, "tvar"),
    ({"rezimy": {"cteni": {"nastroje_navic": ["SendMessage"]}}}, "tvrdě zakázaný"),
    ({"rezimy": {"cteni": {"nastroje_navic": ["mcp__x__*"]}}}, "bez globů"),
    ({"rezimy": {"cteni": {"koreny": ["/"]}}}, "kořen"),
    ({"rezimy": {"cteni": {"koreny": ["~"]}}}, "kořen"),
    ({"rezimy": {"cteni": {"koreny": ["~/a/../b"]}}}, "neplatná"),
    ({"rezimy": {"cteni": {"koreny": ["relativni"]}}}, "cesta"),
    ({"rezimy": {"prace": {"pri_dotazu": "ano"}}}, "pri_dotazu"),
    ({"chranene_cesty": {"odebrat_vychozi": ["~/.ssh/"]}}, "přesná shoda"),
    ({"chranene_cesty": {"pridat": ["a/b"]}}, "cesta"),
    ({"chranene_zapis": {"odebrat_vychozi": ["CLAUDE.md"]}}, "přesná shoda"),
    ({"zakazane_prikazy": {"pridat": ["a b"]}}, "maska"),
    ({"zakazane_prikazy": {"odebrat_vychozi": ["/x"]}}, "přesná shoda"),
    ({"zakazane_prikazy": ["/*"]}, "mapa"),
    ({"forma": {"hlas": ["x"]}}, "2–40"),
    ({"forma": {"hlas": ["Nahlas"], "text": ["nahlas"]}}, "opakuje"),
    ({"vstup": {"hlasovka_max_s": 700}}, "mimo rozsah"),
    ({"vstup": {"max_znaku": 5000}}, "mimo rozsah"),
    ({"jazyk_sablon": "de"}, "jazyk_sablon"),
])
def test_chyba_vypne_jen_prijem(prijem, cast):
    k = kfg(prijem)
    p = k.telegram.prijem
    assert p.povoleno is False and p.chyba and cast in p.chyba, p.chyba
    assert k.telegram.chat_id == CHAT  # ostatní konfigurace platí


def test_odebrat_a_pridat():
    p = kfg({"chranene_cesty": {"pridat": ["~/tajne", "*.sqlite"], "odebrat_vychozi": ["*.pem"]},
             "zakazane_prikazy": {"pridat": ["!*"], "odebrat_vychozi": ["/[!/]*"]}}).telegram.prijem
    assert "*.pem" not in p.chranene and "~/tajne" in p.chranene and "*.sqlite" in p.chranene
    assert p.odebrane_vychozi == ("*.pem",) and p.zakazane_prikazy == ("!*",)


def test_hlasovka_max_s_omezena_stt():
    k = config.z_dat({"stt": {"max_s": 100}, "telegram": {"prijem": {"vstup": {"hlasovka_max_s": 200}}}})
    assert k.telegram.prijem.chyba
    k = config.z_dat({"stt": {"max_s": 100}})
    assert k.telegram.prijem.hlasovka_max_s == 100


@pytest.mark.parametrize("t", [{"odpovedi": {"max_casti": 6}}, {"retence": {"smazat_po_h": 48}},
                               {"odpovedi": {"x": 1}}, {"retence": 5}])
def test_odpovedi_a_retence_prisne(t):
    with pytest.raises(ChybaKonfigurace):
        kfg(**t)


def test_kontext_klice_telegram():
    k = config.z_dat({"strazce": {"citlive_kontexty": [{
        "nazev": "prace", "upozorneni": "Data odejdou mimo.", "signaly": {"mcp": ["prace-*"]},
        "telegram": "nikdy", "telegram_platnost": "okno", "telegram_dotaz_v_tahu": "odmitnout"}]}})
    c = k.strazce.citlive_kontexty[0]
    assert (c.telegram, c.telegram_platnost, c.telegram_dotaz_v_tahu) == ("nikdy", "okno", "odmitnout")
    with pytest.raises(ChybaKonfigurace):
        config.z_dat({"strazce": {"citlive_kontexty": [{
            "nazev": "prace", "upozorneni": "x", "signaly": {"mcp": ["a"]}, "telegram": "ano"}]}})
# Vzor config.yaml.example (i příjem) ověřuje tests/test_config_priklad.py.
