import pytest

from mcp_voice.chyby import HlasChyba
from mcp_voice.cil import NEZNAMY, REMOTE, TERMINAL, StavPc, over_explicitni_pc, rozhodni_auto
from mcp_voice.config import Cil

N = Cil()  # prah 60 s, dlouhá nečinnost 600 s, okna_klienta = com.anthropic.claude
KLIENT = "com.anthropic.claude"


def S(z, n, o=None):
    return StavPc(z, n, o)


TABULKA = [
    # zdroj, teď, snímek při promptu, s od promptu, očekávaný cíl, důvod
    (TERMINAL, S(False, 5), None, 10, "pc", "terminal"),
    (TERMINAL, S(True, 5), None, 10, "telegram", "terminal+pc_zamceno"),  # O1
    (TERMINAL, S(False, 700), None, 700, "telegram", "terminal+dlouha_necinnost"),  # R-PC-3
    (TERMINAL, S(False, 300), None, 300, "pc", "terminal"),  # čeká na odpověď, pod dlouhým prahem
    (REMOTE, S(True, 5), None, 10, "telegram", "remote_control+pc_zamceno"),
    (REMOTE, S(False, 5), S(True, 100), 10, "telegram", "remote_control+zamceno_pri_promptu"),
    (REMOTE, S(False, 40), S(False, 10, KLIENT), 40, "pc", "remote_control+klient_na_pc"),
    (REMOTE, S(False, 5), S(False, 120), 30, "pc", "remote_control+aktivita_po_promptu"),
    (REMOTE, S(False, 150), S(False, 120), 30, "telegram", "remote_control+necinnost_pri_promptu"),
    (REMOTE, S(False, 50), S(False, 20), 30, "pc", "remote_control+uzivatel_u_pc"),
    (REMOTE, S(False, 90), None, 30, "telegram", "remote_control+necinnost"),
    (REMOTE, S(False, 20), None, 30, "pc", "remote_control+aktivita_po_promptu"),
    (REMOTE, S(None, 120), None, 30, "telegram", "remote_control+necinnost"),
    (NEZNAMY, S(True, 1), None, None, "telegram", "neznamy+pc_zamceno"),
    (NEZNAMY, S(False, 61), None, None, "telegram", "neznamy+necinnost"),
    (NEZNAMY, S(False, 10), None, None, "pc", "neznamy+uzivatel_u_pc"),
]


@pytest.mark.parametrize("zdroj,ted,snimek,od,cil,duvod", TABULKA)
def test_rozhodovaci_tabulka(zdroj, ted, snimek, od, cil, duvod):
    r = rozhodni_auto(zdroj, ted, snimek, od, N)
    assert (r.cil, r.duvod) == (cil, duvod)


@pytest.mark.parametrize("zdroj,ted", [
    (TERMINAL, S(None, 5)),
    (REMOTE, S(None, 5)),
    (NEZNAMY, S(None, None)),
    (NEZNAMY, S(None, 5)),
    (NEZNAMY, S(False, None)),
])
def test_nejistota_neposle_do_telegramu(zdroj, ted):
    with pytest.raises(HlasChyba) as e:
        rozhodni_auto(zdroj, ted, None, 10, N)
    assert e.value.kod == "TARGET_UNRESOLVED"


def test_degradace_nikdy_telegram_kdyz_je_uzivatel_u_pc():
    """B12: zdroj neznámý a PC odemčené s aktivitou -> vždy PC."""
    for necinnost in (0, 1, 30, 59):
        assert rozhodni_auto(NEZNAMY, S(False, necinnost), None, None, N).cil == "pc"


def test_tiche_hodiny_presmeruji_pc_na_telegram():
    n = Cil(tiche_hodiny=(22, 7))
    assert rozhodni_auto(TERMINAL, S(False, 1), None, 5, n, hodina=23).cil == "telegram"
    assert rozhodni_auto(TERMINAL, S(False, 1), None, 5, n, hodina=6).cil == "telegram"
    assert rozhodni_auto(TERMINAL, S(False, 1), None, 5, n, hodina=12).cil == "pc"


@pytest.mark.parametrize("stav,kod", [(S(True, 1), "PC_LOCKED"), (S(None, 1), "PC_STATE_UNKNOWN")])
def test_explicitni_pc_pri_zamceni_odmitne(stav, kod):
    with pytest.raises(HlasChyba) as e:
        over_explicitni_pc(stav, N)
    assert e.value.kod == kod


def test_explicitni_pc_s_prepinacem_hrat():
    assert over_explicitni_pc(S(True, 1), Cil(pc_pri_zamceni="hrat")).cil == "pc"
    assert over_explicitni_pc(S(False, 1), N).cil == "pc"
