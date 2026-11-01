# mcp-voice

Lokální MCP server (Python, stdio) pro hlasový výstup a přepis hlasovek v Claude Code:

- **`rekni`** – text → ElevenLabs TTS → reproduktory PC (`paplay`), hlasovka do Telegramu (`sendVoice`), nebo MP3 soubor.
  Cíl `auto` vybere podle toho, odkud přišla poslední zpráva (terminál, nebo Remote Control) a jestli je uživatel u PC (zámek, nečinnost).
- **`prepis`** – hlasovka → text. Výchozí **lokálně** (faster-whisper `large-v3-turbo`, GPU nebo CPU), ElevenLabs Scribe v2 jen výslovně (`engine="cloud"`).
- **`stav`** – co by udělal `rekni(cil="auto")` právě teď a proč (bez sítě).
- **`zastav`** – utne přehrávání na PC a vyprázdní frontu.
- **`odpovez`** – odpověď na zprávu z Telegramu (text, hlasovka, nebo přehrání na PC), jen v tahu, který z Telegramu přišel.
- **Příjem z Telegramu** – volitelně: zprávy a hlasovky z Telegramu se v časovém okně doručí do běžící session (sekce „Příjem z Telegramu“).

Mluvený text se v odpovědi nástroje nevrací. Všechny odpovědi jsou kompaktní JSON `{"ok": true, …}` nebo `{"ok": false, "chyba": {"kod", "zprava"}}`.

## Požadavky

- **Linux x86_64 a Python 3.12.** Lockfily jsou vygenerované pro `--python-version 3.12 --python-platform x86_64-unknown-linux-gnu`. Na jiné verzi Pythonu nebo jiné architektuře (ARM) je potřeba vygenerovat vlastní lock (konec sekce Instalace); taková kombinace není ověřená. Na Debianu a Ubuntu je potřeba i balíček `python3.12-venv`, jinak `python3.12 -m venv` skončí chybou o chybějícím `ensurepip`.
- **Claude Code**: ověřeno na verzi 2.1.314. Strážce, hooky a příjem z Telegramu stojí na formátech, které Claude Code nedokumentuje (vstup hooků, transkript, `params._meta`), starší verze nejsou ověřené. Okno příjmu z Telegramu server otevře jen na 2.1.314 a novějších 2.1.x, jinak jen s `telegram.prijem.povolit_neoverenou_verzi: true`. Jiný klient MCP viz `strazce.vyzadovat`.
- **Systémové programy:** `ffmpeg` a `ffprobe` (s `libopus` a `libmp3lame`), `paplay` nebo `pw-cat`, `gdbus`, `loginctl`, volitelně `xprop` a `prlimit`.
- **Keyring:** služba Secret Service v přihlášené relaci (GNOME Keyring, KeePassXC s integrací Secret Service apod.), odemčená. Bez ní server hlásí `KEYRING_UNAVAILABLE`. Ověření je v sekci „Tajemství“.
- **Detekce zámku a nečinnosti:** Cinnamon nebo GNOME přes DBus (jen čtení), logind `LockedHint`.
- **Lokální přepis (volitelný):** NVIDIA GPU s ovladačem pro CUDA 12 (knihovny CUDA jsou pip wheely, systémová CUDA není potřeba), jinak CPU.
- **Příjem z Telegramu (volitelný):**
  - systémový Python ≥ 3.12 jako `/usr/bin/python3`: hook rozsahu běží mimo venv (`/usr/bin/python3 -I`, jen standardní knihovna). Leží-li systémový Python jinde, uprav cestu ve vzoru hooků (sekce „Hooky: vzor a test před nasazením“);
  - `bwrap` (balíček `bubblewrap`) a neprivilegované user namespaces. Ověření: `bwrap --unshare-all --ro-bind / / true; echo $?` vypíše `0`. Pokud distribuce user namespaces omezuje (například Ubuntu 24.04 a novější s AppArmorem, kde `sysctl kernel.apparmor_restrict_unprivileged_userns` vrací `1`), může tento test skončit chybou oprávnění. Pak je potřeba profil AppArmoru, který `bwrap` user namespaces povolí, podle dokumentace distribuce. Vypnutí omezení přes sysctl oslabí ochranu celého systému. Bez funkčního `bwrap` příjem funguje dál, jen opatrněji: Bash z Telegramu se vždy ptá a běží bez obalu, hlasovky se dekódují bez izolace (`vstup.dekodovani_izolace: auto`), nebo se odmítnou (`bwrap`).

## Instalace

Krok 0, stažení:

```bash
git clone https://github.com/Naagloshi/mcp-voice.git
cd mcp-voice
```

Krok 1, venv a nástroj `uv` (přesná verze s ověřením hashe, stejná jako v lockfilech):

```bash
python3.12 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements-uv.lock
```

Krok 2, vyber **jednu** variantu.

**Varianta A, základ:** TTS (`rekni`), Telegram a cloudový přepis. Stáhne zhruba 100 MB.

```bash
.venv/bin/uv pip install --python .venv/bin/python --require-hashes -r requirements.lock
.venv/bin/uv pip install --python .venv/bin/python --offline --no-build-isolation --no-deps -e .
```

První příkaz nainstaluje závislosti z locku a ověří hash každého balíčku. Druhý nainstaluje samotný projekt (editovatelně, ze zdrojů v repozitáři). Build backend `hatchling` přitom nestahuje: vezme ho z venv, kam ho první příkaz nainstaloval z locku (`--no-build-isolation`), a `--offline` zajistí, že se nic mimo lock nestáhne. Bez `--no-build-isolation` by si `uv` stáhl `hatchling` a jeho závislosti z PyPI bez ověření hashe.

**Varianta B, navíc lokální přepis** (faster-whisper): stáhne zhruba 3 GB knihoven CUDA 12 (pip wheely kvůli GPU; stáhnou se i na stroji bez NVIDIA GPU, přepis pak běží na CPU) a potom model asi 1,6 GB.

```bash
.venv/bin/uv pip install --python .venv/bin/python --require-hashes -r requirements-lokalni.lock
.venv/bin/uv pip install --python .venv/bin/python --offline --no-build-isolation --no-deps -e .
```

Druhý příkaz je stejný jako ve variantě A (build backend z locku, nic se nestahuje).

Model pro lokální přepis se stahuje jednou, z pevné revize a s ověřením hashe každého souboru (repozitář modelu změnil vlastníka, proto pin na commit). `mcp-voice-model stahni` funguje jen ve variantě B, v základu skončí hláškou, že lokální přepis není nainstalovaný:

```bash
.venv/bin/mcp-voice-model stahni      # do stt.model_cesta
.venv/bin/mcp-voice-model over
```

Za běhu se model načítá jen z lokální cesty (`HF_HUB_OFFLINE=1`).

Lockfily obsahují hashe všech balíčků, včetně build backendu (`hatchling` a `editables` jsou v extra `dev`). Po změně `pyproject.toml`:
`uv pip compile pyproject.toml --extra dev [--extra lokalni] --generate-hashes --python-version 3.12 --python-platform x86_64-unknown-linux-gnu -o <lock>` a `pip-audit -r <lock> --require-hashes`. Když se v locku změní verze `uv`, zkopíruj jeho řádky i do `requirements-uv.lock`. Test `tests/test_locky.py` hlídá shodu `uv`, hashe u všech balíčků a to, že build backend v locku odpovídá `[build-system]`.

## Rychlý start

Minimum pro `rekni` na reproduktory PC. Podrobnosti jsou v odkazovaných sekcích.

1. Instalace, varianta A (výše).
2. Ověř keyring (sekce „Tajemství“): výpis `.venv/bin/python -c 'import keyring; print(keyring.get_keyring())'` má začínat na `keyring.backends.SecretService` (za ním bývá `(priority: …)`). V pořádku je i `keyring.backends.chainer.ChainerBackend` (častý na KDE).
3. V účtu ElevenLabs vypni „Data use“ (Terms and Privacy) a vytvoř API klíč s oprávněním Text to Speech. Ulož ho do keyringu (hodnotu vložíš na výzvu, do historie shellu se nedostane):

   ```bash
   .venv/bin/keyring set mcp-voice elevenlabs:api_key
   ```

4. Konfigurace ze vzoru:

   ```bash
   mkdir -p ~/.config/mcp-voice
   test -e ~/.config/mcp-voice/config.yaml || install -m 600 config.yaml.example ~/.config/mcp-voice/config.yaml
   ```

   Příkazy jsou pro bash (fungují i v `sh`). Existující konfiguraci druhý příkaz nepřepíše, takže rychlý start jde zopakovat. V souboru odkomentuj `vychozi:` pod `elevenlabs.hlasy` a zástupnou hodnotu nahraď voice_id (ID hlasu z webu ElevenLabs, sekce Voices). Zástupná hodnota validací neprojde. Dál nastav `elevenlabs.data_use_vypnuto: true`. Denní limit je výchozí 500 znaků (`limity.znaku_za_den`).
5. Registruj server v projektu, kde ho chceš používat (příkaz spusť v adresáři toho projektu, cestu k venv uveď absolutně):

   ```bash
   claude mcp add --scope local hlas -- /cesta/k/mcp-voice/.venv/bin/mcp-voice
   ```

6. Hooky: z `examples/settings-hooks.json` zkopíruj do `.claude/settings.json` projektu položky pro `mcp-voice-hook` a `mcp-voice-guard` a za `VENV` dosaď absolutní cestu k `.venv` (sekce „Hooky: vzor a test před nasazením“). Bez strážce server nic neodešle (`GUARD_MISSING`).
7. Spusť Claude Code v projektu znovu a požádej: „Řekni nahlas: zkouška.“ Nástroj `stav` (bez sítě) ukáže, co by `rekni` udělal a proč.

Dál podle potřeby: Telegram („Telegram: první nastavení“), lokální přepis (varianta B), status bar („Status bar: `mcp-voice-status`“), příjem zpráv z Telegramu („Příjem z Telegramu“).

## Konfigurace

`~/.config/mcp-voice/config.yaml` (jiná cesta: env `MCP_VOICE_CONFIG`, stejná pro server i hooky), vzor s komentáři v `config.yaml.example`. Chybějící nebo prázdný soubor = výchozí hodnoty. Tajemství do konfigurace nepatří (viz „Tajemství“ níže).
Důležité body:

- `elevenlabs.data_use_vypnuto: false` je **kontrolní bod**: dokud ho nenastavíš na `true` (po vypnutí „Data use“ v účtu ElevenLabs, sekce Terms and Privacy), server do ElevenLabs nic nepošle. Opt-out platí jen pro data poslaná po vypnutí.
- `stt.engine` neexistuje záměrně: výchozí přepis je vždy lokální a cloud se dá jen zakázat (`stt.cloud_povolen: false`). Uvedení `stt.engine` je chyba.
- `limity.znaku_za_den` – denní rozpočet znaků TTS (počítá se před odesláním), `volani_za_minutu` a `duplicita_s` brzdí smyčky.
- `elevenlabs.kredity` (výchozí `true`) – po každém úspěšném `rekni` jeden dotaz na zůstatek kreditů do `<cesty.stav>/status.json` (viz „Status bar“). `false` = žádný dotaz navíc.
- `elevenlabs.mazat_historii` (výchozí `true`) – po každém TTS smaže položku, kterou TTS vytvořil v historii účtu ElevenLabs (viz „Historie ElevenLabs“). `false` = položky v historii zůstanou.
- `strazce.prepis_cloud` a `strazce.citlive_kontexty` nečte server, ale hook `mcp-voice-guard`, a to při každém volání (viz „Strážce `mcp-voice-guard` a citlivé kontexty“).

### Kdo konfiguraci čte

| Komponenta | Kdy | Co používá | Při neplatné konfiguraci |
|---|---|---|---|
| server `mcp-voice` | jen při startu (změna = restart serveru) | vše | nenastartuje (stderr `chybná konfigurace: …`, kód 2); chyba jen v `telegram.prijem` vypne jen příjem |
| `mcp-voice-guard` | při každém volání nástroje hlasu | validuje celý soubor, používá hlavně `strazce` a `cesty.stav` | `deny` pro `rekni`, `prepis` a `odpovez` |
| `mcp-voice-hook` (`UserPromptSubmit`) | při každém promptu | `cesty.stav`, `cil.desktop`, `telegram.prijem` (povel, okno, šablony) | nic nezapíše, na stderr jen typ chyby (`mcp-voice-hook: ChybaKonfigurace`), kód 0, prompt neblokuje; `rekni` pak nezná zdroj zprávy |
| `rozsah_hook.py` | – | konfiguraci nečte: politiku bere ze snímku, který zapíše server, adresář stavu z `--stav` | – (neexistující adresář stavu jen ohlásí varováním na stderr) |
| `mcp-voice-telegram` | při spuštění | `telegram`, `cesty.stav` | kód 2 |
| `mcp-voice-status` | při spuštění | `cesty.stav` (s `--stav` konfiguraci nečte) | prázdný výstup, kód 0 |
| `mcp-voice-model` | při spuštění | `stt.model_cesta` (s `--cesta` nic) | stderr `mcp-voice-model: chybná konfigurace: …`, kód 2 |

Hook čte konfiguraci hned, server až po restartu. Po změně proto server restartuj, jinak se rozejdou (třeba změněný `telegram.prijem.povel`: hook žádost převezme, server ji odmítne jako `povel_nesouhlasi`).

### Validace

- **YAML:** bezpečný loader (`SafeLoader`). Duplicitní klíč a merge klíč `<<` jsou chyba s číslem řádku: obojí by tiše vzalo poslední hodnotu. Kotvy a aliasy (`&`, `*`) projdou. Kořen musí být mapa.
- **Neznámý klíč je chyba** (zpráva ho jmenuje) v každé mapě: v kořeni, v sekci, v každé podsekci (`telegram.prijem.okno`, `rezimy.plny.bash_domov` …), v citlivém kontextu, v `signaly`, v položce `vstupy` i `bash_koreny`. Překlep by jinak tiše vrátil výchozí hodnotu, u `strazce.citlive_kontexty` by to znamenalo žádnou detekci.
- **Typy:** přepínače jen `true`/`false` (ne `"ano"` ani `1`), čísla nesmí být `true`/`false` a musí být v rozsahu (u celočíselných klíčů se desetinná část usekne), výčtové hodnoty přesně podle tabulek. Seznam musí být seznam, jen seznamy v citlivém kontextu (signály, `pole`, `hodnoty`, `predpony`) smí být i jeden text. Text smí být i číslo (`chat_id: 123…`). Pokud tabulka neříká jinak, má text nejvýš 500 znaků.
- **Rozsah chyby:** chyba kdekoli v `telegram.prijem` (i v jeho podsekcích) nastaví celý příjem na výchozí hodnoty s `povoleno: false`. Důvod ukáže nástroj `stav` (`chyba_konfigurace`) a odpověď na povel okna, ostatní funkce běží dál. Chyba jinde, včetně `telegram.odpovedi`, `telegram.retence` a `strazce.citlive_kontexty`, zneplatní celou konfiguraci (tabulka výše).
- **Co validace nehlídá:** že alias `elevenlabs.hlas` je v `elevenlabs.hlasy` (jinak `rekni` vrátí `VOICE_UNKNOWN`), že adresáře a model existují, a prázdný text u klíčů s předepsaným tvarem (`registrace`, `povel`, `hlas`, `jazyk`), který projde, ale nedává smysl. Cesty v `cesty` a `stt` piš absolutně nebo `~/…`: relativní cesta projde a vztáhne se k pracovnímu adresáři procesu (server a hooky ho mají každý jiný), `null` projde a selže až za běhu (u `cesty.stav` hned při startu serveru). Relativní položku `stt.povolene_adresare` server tiše vynechá.

### Přehled klíčů

Rozsah „a–b“ je včetně krajů. Pro sekci `telegram.prijem` je podrobný význam v sekci „Příjem z Telegramu“, pro `strazce` v sekci „Strážce `mcp-voice-guard` a citlivé kontexty“.

**`elevenlabs`**

| Klíč | Výchozí | Hodnoty | Význam |
|---|---|---|---|
| `model` | `eleven_flash_v2_5` | `[a-z0-9_]{3,64}`, musí být v `modely_povolene` | model TTS, když ho `rekni` nezadá |
| `modely_povolene` | `[eleven_flash_v2_5, eleven_multilingual_v2, eleven_v4]` | seznam jmen modelů | parametr `model` mimo seznam = `MODEL_NOT_ALLOWED` |
| `modely_s_jazykem` | `[eleven_flash_v2_5]` | seznam jmen modelů | modelům v seznamu se posílá `language_code` = `jazyk`, ostatní jazyk určí samy |
| `hlas` | `vychozi` | alias `[a-z0-9][a-z0-9_-]{0,31}` | výchozí hlas, klíč z `hlasy` |
| `hlasy` | `{}` | mapa alias → voice_id (`[A-Za-z0-9]{16,40}`) | hlasy pro `rekni`; bez aspoň jednoho `rekni` nefunguje |
| `povolit_voice_id` | `false` | `true`/`false` | `true` = parametr `hlas` smí být i surové voice_id, model pak může zvolit libovolný hlas |
| `jazyk` | `cs` | 2–3 malá písmena | `language_code` pro `modely_s_jazykem` |
| `timeout_s` | `30` | 1–300 | timeout požadavku TTS (s) |
| `voice_settings` | `{}` | mapa jméno → číslo | posílá se do API beze změny (`stability`, `similarity_boost`, `speed` …); prázdné = nastavení hlasu v účtu; hodnotu `true`/`false` nastavit nejde |
| `data_use_vypnuto` | `false` | `true`/`false` | kontrolní bod (výše); `false` = `DATA_USE_NOT_CONFIRMED` u `rekni` i cloudového přepisu |
| `kredity` | `true` | `true`/`false` | dotaz na zůstatek kreditů po `rekni` („Status bar“) |
| `mazat_historii` | `true` | `true`/`false` | mazání položky historie po TTS („Historie ElevenLabs“) |

**`telegram`**

| Klíč | Výchozí | Hodnoty | Význam |
|---|---|---|---|
| `chat_id` | prázdné | celé číslo (i záporné), nejvýš 20 číslic | soukromý chat s botem; prázdné = Telegram vypnutý (`TELEGRAM_NOT_CONFIGURED`) |
| `timeout_s` | `20` | 1–120 | timeout volání Telegram API (s) |
| `protect_content` | `false` | `true`/`false` | zprávy a hlasovky od bota nejde přeposlat ani uložit |
| `odpovedi.max_casti` | `3` | 1–5 | text `odpovez` se dělí po odstavcích nejvýš na tolik zpráv, zbytek se zkrátí s poznámkou |
| `odpovedi.max_znaku_casti` | `4000` | 100–4096 | znaků na zprávu (4096 = limit Telegramu) |
| `retence.smazat_po_h` | `24` | 0–47 | mazání zpráv v chatu po tolika hodinách, `0` = nemazat (Telegram dovolí botu mazat jen do 48 h); viz „Odpovědi, citlivé kontexty, retence“ |
| `prijem` | – | mapa | příjem z Telegramu, tabulka níže |

**`telegram.prijem`** (cesty klíčů relativně k `telegram.prijem`)

| Klíč | Výchozí | Hodnoty | Význam |
|---|---|---|---|
| `povoleno` | `false` | `true`/`false` | hlavní vypínač; `true` vyžaduje `telegram.chat_id` a `povolene_user_id`, `chat_id` musí být mezi nimi (soukromý chat) |
| `povolene_user_id` | `[]` | 0–5 celých čísel > 0 | kdo smí psát; po každé změně (i `chat_id`) znovu `mcp-voice-telegram potvrd` |
| `registrace` | `hlas` | `[A-Za-z0-9_-]{1,64}` | jméno registrace serveru v Claude Code (`mcp__<registrace>__odpovez` …); musí souhlasit s `claude mcp add` |
| `povel` | `/telegram` | `/[a-z][a-z0-9_-]{1,31}` | slash příkaz okna |
| `long_poll_s` | `50` | 10–50 | long poll `getUpdates` (s) |
| `okno.vychozi_min` | `60` | 5–240, nejvýš `max_min` | délka okna, když povel neuvede čas |
| `okno.max_min` | `240` | 5–240 | strop délky okna v povelu. Pevný strop v kódu je 240 min, konfigurace ho jen snižuje; při snížení pod 60 sniž i `vychozi_min` a `necinnost_min` |
| `okno.necinnost_min` | `60` | 5–240, nejvýš `max_min` | zavřít okno po tolika minutách bez zprávy z Telegramu |
| `okno.vychozi_rezim` | `prace` | `cteni`, `prace`, `plny` | režim, když ho povel neuvede |
| `rezimy.cteni.nastroje_navic` | `[]` | nejvýš 100 přesných jmen (`Jmeno` nebo `mcp__server__nastroj`, bez globů), ne tvrdě zakázaný nástroj ani `zakazane_nastroje` | nástroje navíc, které se berou jako čtení: bez dotazu ve všech režimech |
| `rezimy.cteni.koreny` | `[]` | nejvýš 100 cest `~/…` nebo absolutních, ne `/` ani `~`, bez `..` | omezí `Read`, `Grep` a `Glob`: mimo kořeny je v `cteni` zamítnuto, v `prace` a `plny` dotaz; prázdné = bez omezení (chráněné cesty platí vždy) |
| `rezimy.prace.pri_dotazu` | `cekat` | `cekat`, `odmitnout` | dialog oprávnění v tahu z Telegramu ve všech režimech: `cekat` = čeká na potvrzení v terminálu nebo Remote Control, `odmitnout` = hned zamítnout; session bez Remote Control má vždy `odmitnout` |
| `rezimy.prace.bash_koreny` | `[]` | nejvýš 100 map `{cesta, pristup}`, `pristup` = `rw` nebo `ro` | adresáře navíc k pracovnímu adresáři viditelné v obalu `bwrap` v režimu `prace` (pravidla pod tabulkou) |
| `rezimy.plny.bash_izolace` | `bwrap` | `bwrap`, `zadna` | obal Bash z Telegramu v `prace` i `plny`; `zadna` nebo nefunkční `bwrap` = Bash vždy s dotazem a bez obalu |
| `rezimy.plny.bash_sit` | `false` | `true`/`false` | Bash v obalu se sítí (`prace` i `plny`); vědomé riziko, viz „Režimy a politika nástrojů“ |
| `rezimy.plny.bash_bez_dotazu` | `false` | `true`/`false` | Bash v obalu v `plny` bez dotazu (jen s ověřenou verzí Claude Code); viz háček 5 |
| `rezimy.plny.bash_domov` | výchozí seznam | `pridat`: `~/…`, ne domov, chráněná cesta ani konfigurace agenta; `odebrat_vychozi`: přesné položky výchozího seznamu | složky nástrojů z domova, v obalu jen pro čtení (`prace` i `plny`); výchozí `BASH_DOMOV_VYCHOZI` v `politika.py` |
| `rezimy.plny.bash_skryt` | `[]` | nejvýš 100 absolutních cest, ne `/`, bez `..` | další místa skrytá v obalu (`prace` i `plny`) navíc k pevným |
| `rezimy.plny.bash_koreny` | `[]` | jako u `prace` | totéž pro režim `plny` |
| `chranene_cesty` | výchozí seznam | `pridat`: cesta `~/…` nebo absolutní (ne `/` ani `~`, bez `..`), nebo vzor jména bez `/` (`*.sqlite`); `odebrat_vychozi`: přesné položky výchozího seznamu | čtení i zápis zamítnut ve všech režimech; výchozí `CHRANENE_VYCHOZI` v `politika.py`. Odebrání `<stav>` nebo `$XDG_CONFIG_HOME/mcp-voice` zpřístupní tahu z Telegramu stav a konfiguraci serveru |
| `chranene_zapis` | výchozí seznam | jako `chranene_cesty` | jen zápis; výchozí `CHRANENE_ZAPIS_VYCHOZI` v `politika.py` |
| `konfigurace_agenta.pridat` | `[]` | nejvýš 100 jmen nebo vzorů jmen bez `/` | další jména konfigurace agenta (tvrdý zákaz zápisu, např. `COPILOT.md`); výchozí nejdou odebrat |
| `zakazane_nastroje.pridat` | `[]` | nejvýš 100 přesných jmen nástrojů | tvrdé zákazy navíc k pevným |
| `zakazane_prikazy` | `["/[!/]*"]` | `pridat`: masky 1–64 znaků bez mezer a řídicích znaků; `odebrat_vychozi`: přesná shoda | tokeny zprávy nahrazené značkou `[zakázaný příkaz]`; `odebrat_vychozi: ["/[!/]*"]` = bez filtru |
| `omezit_naplanovane` | `true` | `true`/`false` | `/loop` a cron mají v otevřeném okně jen čtení; `false` = jako tah člověka |
| `pojistka_bash` | `true` | `true`/`false` | pojistka Bash v každém tahu (konec sekce „Režimy a politika nástrojů“); `false` ji vypne pro všechny session se stejným `cesty.stav`, dokud ji server s `true` při startu znovu nezapne. Jen pro ladění |
| `povolit_neoverenou_verzi` | `false` | `true`/`false` | okno se otevře i na verzi Claude Code, na které formát socketu a transkriptu nebyl ověřen (háček 6) |
| `vstup.max_znaku` | `4000` | 1–4096 | delší zpráva z Telegramu se odmítne |
| `vstup.zprav_za_minutu` | `6` | 1–30 | limit zpráv; druhé překročení okno zavře |
| `vstup.zprav_za_okno` | `100` | 1–1000 | limit zpráv za okno |
| `vstup.hlasovka_max_s` | `300`, nejvýš `stt.max_s` | 1 až menší z `stt.max_s` a 3600 | delší hlasovka se odmítne |
| `vstup.hlasovka_max_mb` | `5` | 1–20 | větší hlasovka se odmítne |
| `vstup.dekodovani_izolace` | `auto` | `auto`, `bwrap`, `zadna` | dekódování hlasovky v `bwrap` (bez sítě, se skrytým domovem a `/tmp`): `auto` = když `bwrap` funguje, `bwrap` = povinně (jinak se hlasovky odmítnou), `zadna` = bez izolace |
| `fronta.max_zprav` | `10` | 1–50 | zprávy čekající na volnou session; další se odmítne |
| `fronta.oznamit_po_s` | `120` | 10–3600 | po tolika sekundách čekání oznámí server frontu do Telegramu |
| `fronta.necinnost_tahu_s` | `90` | 15–600 | session bez změny transkriptu tak dlouho je volná pro další zprávu |
| `fronta.max_cekani_min` | `15` | 1–120 | pak se zpráva doručí i do zaneprázdněné session |
| `doruceni.potvrzeni_s` | `10` | 2–60 | doručení, které se do této doby neobjeví v transkriptu, je „nepotvrzeno“ |
| `forma.hlas`, `text`, `pc` | viz `config.yaml.example` | 0–50 frází na seznam, 2–40 znaků; po normalizaci (malá písmena, bez diakritiky) se nesmí opakovat napříč seznamy | fráze ve zprávě, které určí formu odpovědi |
| `oznamit_nazev_kontextu` | `false` | `true`/`false` | šablona „odpověď jen v terminálu“ uvede do Telegramu název citlivého kontextu (nejvýš 40 znaků) |
| `jazyk_sablon` | `cs` | `cs`, `en` | jazyk šablon do Telegramu a textů hooku |

Kořen `bash_koreny` (`cesta` absolutní nebo `~/…`, bez `..`) nesmí být `/`, domov ani jeho předek, chráněná cesta ani její předek (včetně `cesty.stav`), konfigurace agenta, `/dev`, `/proc`, `/sys`, `/run`, `/var/run`, `/boot`, nic pod nimi ani jejich předek (`/var`), ani samotné `/tmp` (podsložka smí). Kontroluje se i cíl symlinku. Kořen, který tato pravidla poruší až za běhu, server do politiky nezařadí. Podrobnosti obalu jsou v „Režimy a politika nástrojů“.

**`cil`** (pravidla výběru v „Výběr cíle“)

| Klíč | Výchozí | Hodnoty | Význam |
|---|---|---|---|
| `prah_necinnosti_s` | `60` | 5–86400 | Remote Control a neznámý zdroj: nečinnost od tohoto prahu = uživatel není u PC |
| `prah_dlouhe_necinnosti_s` | `600` | 5–86400 | zpráva z terminálu: odemčené PC, ale nečinnost od tohoto prahu = Telegram |
| `tolerance_snimku_s` | `5` | 0–120 | snímek stavu PC z hooku `UserPromptSubmit` se ke zprávě přiřadí, je-li od ní nejvýš tolik sekund |
| `okna_klienta` | `[com.anthropic.claude]` | seznam `WM_CLASS` (`[A-Za-z0-9._-]{1,128}`) | aktivní okno klienta při promptu z Remote Control = uživatel je u PC |
| `pc_pri_zamceni` | `odmitnout` | `odmitnout`, `hrat` | výslovné `cil="pc"` při zamčeném nebo neznámém stavu PC: chyba, nebo přehrát |
| `desktop` | `auto` | `auto`, `cinnamon`, `gnome`, `x11` | zdroj zámku a nečinnosti |
| `tiche_hodiny` | žádné | `[od, do]`, celé hodiny 0–23, `od` ≠ `do`, smí přes půlnoc (`[22, 7]`) | `auto` v tyto hodiny místo PC mluví do Telegramu |

**`prehravani`**, **`limity`**, **`cesty`**, kořen

| Klíč | Výchozí | Hodnoty | Význam |
|---|---|---|---|
| `prehravani.prikaz` | `paplay` | `paplay`, `pw-cat` | přehrávač na PC |
| `limity.max_znaku` | `1500` | 1–5000 | nejdelší text jednoho `rekni` (i hlasové `odpovez`), delší = `TEXT_TOO_LONG` |
| `limity.znaku_za_den` | `500` | 0–10 000 000 | denní rozpočet znaků TTS, `DAILY_LIMIT`; `0` = TTS zablokované |
| `limity.volani_za_minutu` | `6` | 1–600 | víc volání za minutu = `RATE_LIMIT` |
| `limity.duplicita_s` | `60` | 0–86400 | stejný text znovu do tolika sekund = `DUPLICATE` |
| `cesty.stav` | `~/.local/state/mcp-voice` | absolutní nebo `~/…` | stavový adresář (razítka, souhlasy, okno příjmu, `status.json`). Server, `mcp-voice-guard`, `mcp-voice-hook`, hook rozsahu (`--stav`) a `mcp-voice-status` musí používat stejný |
| `cesty.vystup_adresar` | `~/.local/share/mcp-voice/vystup` | absolutní nebo `~/…` | kam `rekni` ukládá MP3 (`ulozit`, `cil="soubor"`) |
| `instrukce_navic` | prázdné | text do 2000 znaků | připojí se k instrukcím serveru pro model |

**`stt`**

| Klíč | Výchozí | Hodnoty | Význam |
|---|---|---|---|
| `cloud_povolen` | `true` | `true`/`false` | `false` = `engine="cloud"` vrátí `CLOUD_DISABLED`; zapnout cloud jako výchozí nejde |
| `model_cesta` | `~/.local/share/mcp-voice/modely/faster-whisper-large-v3-turbo` | cesta | adresář modelu (`model.bin`), cíl `mcp-voice-model` |
| `zarizeni` | `cuda` | `cuda`, `cpu` | `cuda` při selhání zkusí `cpu` s `int8` |
| `compute_type` | `int8_float16` | `int8_float16`, `float16`, `int8`, `float32`, `int8_float32` | přesnost výpočtu modelu |
| `jazyk` | `cs` | 2–3 malá písmena, `null` = autodetekce | jazyk přepisu hlasovek z Telegramu; nástroj `prepis` má vlastní parametr `jazyk` (výchozí `cs`) |
| `hotwords` | prázdné | do 300 znaků | slova, která má přepis upřednostnit (vlastní jména) |
| `initial_prompt` | prázdné | do 500 znaků | úvodní kontext pro přepis |
| `povolene_adresare` | `["~/.claude/uploads"]` | seznam cest (absolutní nebo `~/…`) | jen odsud smí `prepis` číst (`FILE_NOT_ALLOWED`) |
| `max_mb` | `25` | 0.01–200 | větší soubor = `FILE_TOO_LARGE` |
| `max_s` | `600` | 1–7200 | delší audio = `AUDIO_TOO_LONG`; shora omezuje `vstup.hlasovka_max_s` |
| `uvolnit_po_s` | `600` | 0–86400 | uvolnit model z paměti po tolika sekundách bez přepisu, `0` = držet |
| `timeout_s` | `120` | 5–3600 | timeout jednoho přepisu, lokálního i cloudového (s) |

**`strazce`**

| Klíč | Výchozí | Hodnoty | Význam |
|---|---|---|---|
| `vyzadovat` | `true` | `true`/`false` | `rekni`, `prepis` a `odpovez` bez razítka hooku = `GUARD_MISSING`. `false` jen pro klienty bez hooků: server pak pustí volání i bez hooku (jiný klient, pád hooku) |
| `max_stari_s` | `600` | 5–3600 | nejvyšší stáří razítka (s); starší se uklízí |
| `prepis_cloud` | `dotaz` | `dotaz`, `povolit` | cloudový přepis: dotaz při každém volání, nebo posoudit jako `rekni` (čte hook) |
| `citlive_kontexty` | `[]` | nejvýš 20 kontextů | citlivé kontexty (čte hook), klíče kontextu níže |

| Klíč kontextu | Výchozí | Hodnoty |
|---|---|---|
| `nazev` | povinný | 1–40 znaků `[A-Za-z0-9 ._-]`, začíná písmenem nebo číslicí, unikátní bez ohledu na velikost písmen |
| `upozorneni` | povinný | jednořádkový text do 300 znaků, ukáže se v dialogu |
| `platnost` | `den` | `den`, `session`, `volani` (platnost souhlasu) |
| `telegram` | `souhlas` | `souhlas`, `nikdy` (do Telegramu nikdy, ani se souhlasem) |
| `telegram_platnost` | `kontext` | `kontext`, `okno` (souhlas jen do konce okna, ve kterém byl dán) |
| `telegram_dotaz_v_tahu` | `rc` | `rc` (dialog čeká na terminál nebo Remote Control), `odmitnout` |
| `signaly` | povinný | mapa s aspoň jedním neprázdným signálem; každý signál je seznam (nebo jeden text) nejvýš 100 položek bez řídicích znaků |
| `signaly.mcp`, `agenti`, `skilly` | – | glob `[A-Za-z0-9._:*?!\[\]-]{1,100}` |
| `signaly.cesty` | – | absolutní nebo `~/…`, ne `/` ani `~`, do 4096 znaků |
| `signaly.texty` | – | 3–200 znaků |
| `signaly.vstupy` | – | položky `nastroj` (glob jména nástroje do 200 znaků), `pole` (jméno nebo seznam jmen `[A-Za-z_][A-Za-z0-9_]{0,63}`), `hodnoty` a `predpony` (texty 1–200 znaků, aspoň jedno z nich) |

### Pevné limity v kódu

Konfigurace tyto meze nepřekročí, nanejvýš je zúží:

- Okno příjmu 5 až 240 minut (`okno.max_min` strop jen snižuje). Povel pod 5 minut nebo nad strop se odmítne.
- Telegram: `long_poll_s` nejvýš 50, zpráva nejvýš 4096 znaků (`vstup.max_znaku`, `odpovedi.max_znaku_casti`), odpověď nejvýš 5 zpráv, retence nejvýš 47 h, nejvýš 5 povolených uživatelů.
- Hlasovka z Telegramu nejvýš 3600 s a `stt.max_s`, nejvýš 20 MB.
- Seznamy v `telegram.prijem` nejvýš 100 položek (`forma` 50 frází na seznam), nejvýš 20 citlivých kontextů po 100 položkách na signál.
- Délky textů: `instrukce_navic` 2000, `initial_prompt` 500, `hotwords` 300, `upozorneni` 300, `nazev` kontextu 40 znaků.
- Nejde odebrat ani vypnout: tvrdé zákazy nástrojů v tahu z Telegramu (`ZAKAZANE_V_TAHU` v `politika.py`, `nastroje_navic` je nesmí obsahovat), zákaz zápisu do konfigurace agenta (`.claude/`, `CLAUDE.md`, `CLAUDE.local.md`, `.mcp.json`), chráněný zápis do balíčku, venvu a repozitáře `mcp-voice`, ochrana konfiguračního souboru z `MCP_VOICE_CONFIG` (je-li nastavená), skrytý domov a místa připojení v obalu (`/home`, `/mnt`, `/media`, `/srv`, `/net`, `/nfs`, `/afs`), systémové složky jako kořen Bash, cloud jako výchozí engine přepisu.
- Limity skenu stromu a obalu (500 000 položek, hloubka 32, 3 s, 100 000 znaků, 2 000 připojení) jsou v „Režimy a politika nástrojů“, hlídací časovač strážce (8 s) v „Strážce `mcp-voice-guard` a citlivé kontexty“.

### Tajemství (keyring, služba `mcp-voice`)

| Klíč | Obsah |
|---|---|
| `elevenlabs:api_key` | API klíč ElevenLabs (doporučeno omezit scope na Text to Speech + Speech to Text a nastavit kvótu klíče). Pro zůstatek kreditů navíc oprávnění „User: Access“ (jen čtení účtu a předplatného), pro mazání historie „History: Write“ (smí číst i mazat celou historii účtu). |
| `telegram:bot_token` | token bota pro všechno, co jde přes Telegram: `rekni` s cílem Telegram, `odpovez`, příjem z Telegramu a mazání zpráv (retence). Postup v „Telegram: první nastavení“ |

```bash
.venv/bin/keyring set mcp-voice elevenlabs:api_key
```

```bash
.venv/bin/keyring set mcp-voice telegram:bot_token
```

`keyring set` se na hodnotu zeptá a nevypisuje ji, do historie shellu se tak nedostane.

**Ověření backendu.** Server čte keyring v procesu, který spustí Claude Code, takže potřebuje Secret Service v téže relaci (proměnná `DBUS_SESSION_BUS_ADDRESS`). Kontrola ve stejném prostředí, odkud spouštíš Claude Code:

```bash
.venv/bin/python -c 'import keyring; print(keyring.get_keyring())'
```

- začíná na `keyring.backends.SecretService` (například `keyring.backends.SecretService.Keyring (priority: 5)`) = v pořádku;
- `keyring.backends.chainer.ChainerBackend` = v pořádku, keyring zkouší víc backendů za sebou (častý na KDE, kde vedle Secret Service běží KWallet);
- `keyring.backends.fail.Keyring` = backend chybí (Secret Service neběží, relace bez DBus, třeba přes SSH). Server pak vrací `KEYRING_UNAVAILABLE`, ne `SECRET_MISSING`.
- Přehled všech dostupných backendů: `.venv/bin/keyring --list-backends`.

`SECRET_MISSING` znamená, že backend funguje, ale položka ve službě `mcp-voice` chybí (překlep v klíči, jiný uživatel).

Keyring chrání před únikem přes proměnné prostředí a soubory, **není to hranice**: čte ho každý proces uživatele.
Rotace tokenu bota: `/revoke` v BotFatheru → nový `keyring set`. Bot nikdy nic nežádá ani se neptá; zpráva od něj s výzvou je podvrh.

## Napojení na Claude Code

Registrace serveru (příklad, ideálně jen v projektu, kde se má používat; spusť v adresáři projektu):

```bash
claude mcp add --scope local hlas -- /cesta/k/mcp-voice/.venv/bin/mcp-voice
```

**Jméno registrace.** Jméno `hlas` určuje jména nástrojů v Claude Code (`mcp__hlas__rekni` …). Vzor hooků i výchozí `telegram.prijem.registrace` s ním počítají. Při jiném jménu (třeba `voice`) přepiš ve vzoru hooků každé `mcp__hlas__` na `mcp__voice__` a nastav `telegram.prijem.registrace: voice`.

### Hooky: vzor a test před nasazením

Jediný vzor je `examples/settings-hooks.json`. Je to platný celek pro plnou instalaci včetně příjmu z Telegramu. Obsah klíče `hooks` vlož do `.claude/settings.json` projektu (nebo do `.claude/settings.local.json`, který se necommituje) a zástupné hodnoty nahraď absolutními cestami:

- `VENV` = adresář `.venv` mcp-voice;
- `PKG` = adresář balíčku, zjistíš ho příkazem `/cesta/k/mcp-voice/.venv/bin/python -c "import mcp_voice, os; print(os.path.dirname(mcp_voice.__file__))"` (při instalaci podle README je to `src/mcp_voice` v repozitáři);
- `STAV` = stejná cesta jako `cesty.stav` v konfiguraci (výchozí `~/.local/state/mcp-voice`). Hook rozsahu konfiguraci nečte. Jiná cesta projde tiše a okno příjmu se pak nikdy neotevře. Neexistující adresář hook ohlásí varováním na stderr.

Kolik toho registrovat:

| Použití | Položky ze vzoru |
|---|---|
| TTS a přepis, bez příjmu z Telegramu | `UserPromptSubmit` (`mcp-voice-hook`), `PreToolUse` s matcherem `mcp__hlas__.*` a `PostToolUse` s matcherem `.*` (obojí `mcp-voice-guard`) |
| navíc příjem z Telegramu | celý vzor, tedy navíc všechny položky `rozsah_hook.py` |

Položky jen vyber, jinak je neupravuj (kromě cest a jména registrace). Hook rozsahu se spouští mimo venv systémovým Pythonem s absolutní cestou, `/usr/bin/python3 -I`. Absolutní cesta znamená, že se interpret nehledá v `PATH` session (podstrčený `python3` v adresáři na začátku `PATH` se nespustí). Izolovaný režim `-I` ignoruje proměnné `PYTHON*` a uživatelské site-packages a nepřidá do `sys.path` adresář skriptu ani pracovní adresář. Interpret a moduly hooku s `onFailure: block` tak nezmění `PATH`, proměnné `PYTHON*` ani soubor v projektu (třeba `json.py`). Proměnné dynamického linkeru (`LD_PRELOAD`, `LD_LIBRARY_PATH`) tím ošetřené nejsou; kdo je umí nastavit procesu Claude Code, ovládá i Claude Code samotný. **Pokud systémový Python ≥ 3.12 neleží v `/usr/bin/python3`, uprav cestu ve všech položkách hooku rozsahu.** Nedosazuj Python z venv ani shim (pyenv, asdf), který vybírá interpret podle prostředí. Hook potřebuje jen standardní knihovnu a sourozenecké moduly načítá podle vlastní cesty. Vstupní bod `.venv/bin/mcp-voice-rozsah` dělá totéž, ale běží z venv.

**Test před vložením.** Hook s `"onFailure": "block"` při chybě (neexistující cesta, kód 2, timeout) zablokuje volání. U hooku rozsahu s matcherem `.*` to znamená **všechny nástroje** v projektu. Každý příkaz proto nejdřív spusť ručně se stejnými cestami, jaké vložíš do `settings.json`:

```bash
printf '{}' | /usr/bin/python3 -I PKG/rozsah_hook.py --udalost pre --stav STAV; echo "kód $?"
```

Očekávání: jediný řádek `kód 0`. Hláška `can't open file` a `kód 2` = špatné `PKG`, takový hook nevkládej. Hláška o neexistujícím souboru a `kód 127` = Python neleží v `/usr/bin/python3`, oprav cestu (v testu i ve všech položkách hooku rozsahu). Varování `adresář stavu … neexistuje` = `STAV` neodpovídá `cesty.stav`, nebo server ještě neběžel (po prvním startu serveru test zopakuj).

```bash
printf '{}' | VENV/bin/mcp-voice-guard; echo "kód $?"
```

Očekávání: jediný řádek `kód 0`. Hláška shellu o neexistujícím souboru (`No such file or directory`, podle jazyka systému) a `kód 127` = špatné `VENV`.

**Obnova, když hook zablokuje všechny nástroje.** Uvnitř Claude Code to opravit nejde, blokované jsou i nástroje na úpravu souborů.

1. Ukonči Claude Code.
2. V textovém editoru mimo Claude Code oprav cesty, nebo dočasně odstraň položky s `"onFailure": "block"` v `.claude/settings.json` (případně `.claude/settings.local.json`) projektu.
3. Zopakuj test výše a Claude Code spusť znovu. Změnu souboru za běhu nemusí Claude Code převzít hned, proto restart.

### Hook `UserPromptSubmit`: odkud přišla zpráva a stav PC

`mcp-voice-hook` při každém promptu zapíše do `<stav>/session-<pid claude>.json` vazbu na aktuální session a transkript a snímek stavu PC (zámek, nečinnost, třída aktivního okna `WM_CLASS`, nikdy titulek). Text promptu neukládá. Vždy skončí s kódem 0; výstup má jen u povelu `/telegram` a u zprávy z Telegramu (sekce „Příjem z Telegramu“). Bez hooku server čte session z prostředí (po `/clear` zastará) a zdroj je častěji `neznamy`. Registrace: položka `UserPromptSubmit` ve vzoru hooků.

Zdroj zprávy se čte z transkriptu (`origin.kind == "human"`, `promptSource`, `queueSkipSlashCommands`). Pole jsou nedokumentovaná; když chybí nebo mají neznámou hodnotu, zdroj je `neznamy` a auto režim pošle do Telegramu jen tehdy, když stav PC říká, že u něj uživatel není.

### Externí strážce (`PreToolUse`) a razítko

S `strazce.vyzadovat: true` (výchozí) server u `rekni` a `prepis` nic neodešle, dokud volání neprověří hook `PreToolUse`. Server tak zůstane zavřený i tam, kde hook neběží (jiný klient, jiný projekt, pád hooku). Protokol:

1. Claude Code posílá v `params._meta` pole `"claudecode/toolUseId"`, hook dostane stejnou hodnotu jako `tool_use_id`.
2. Hook po rozhodnutí „pustit“ nebo „zeptat se“ zapíše `<stav>/razitka/<tool_use_id>.json` (adresář `0700`, soubor `0600`, `O_CREAT|O_EXCL`):
   `{"v": 1, "nastroj": "rekni", "sha256": "<sha256 kanonického JSON tool_input>", "cas": <epoch s>}`,
   kde kanonický JSON = `json.dumps(tool_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` v UTF-8.
3. Při zamítnutí razítko nevzniká. Server razítko najde, hned smaže, ověří nástroj, hash a stáří (`strazce.max_stari_s`). Jinak vrátí `GUARD_MISSING` nebo `GUARD_MISMATCH`.

Testovací vektor pro vlastní strážce: `{"text": "Dobrý den, čtu zprávu.", "cil": "auto", "ulozit": false, "hlas": null}` → `99a52086c7831e14a7693de02946c837369f17f72362e3ee1edc0f3652609d16`.

Razítko umí zapsat i vlastní hook. Hotový strážce je `mcp-voice-guard` (další sekce). Hook vždy registruj s `"onFailure": "block"` a explicitním `timeout`, aby pád nebo timeout hooku volání zablokoval.

### Strážce `mcp-voice-guard` a citlivé kontexty

`mcp-voice-guard` je hook pro `PreToolUse` i `PostToolUse`. Rozhoduje takto:

| Volání | Rozhodnutí |
|---|---|
| ze subagenta (`agent_type` nebo `agent_id` ve vstupu hooku) | `deny`, razítko nevznikne: hlas smí používat jen hlavní session |
| `stav`, `zastav` | bez zásahu (nic neodchází) |
| `prepis` lokálně | razítko, bez dotazu |
| `prepis` s `engine="cloud"` | dotaz při každém volání (`strazce.prepis_cloud: dotaz`, výchozí). Při `povolit` se posuzuje jako `rekni` |
| `rekni` v session s citlivým kontextem bez platného souhlasu | dotaz (`ask`) s upozorněním kontextu |
| ostatní | razítko, bez dotazu |

**K čemu citlivé kontexty jsou.** Uživatel pracuje v Claude Code i s daty, která nemají opustit firmu (pracovní repozitář, firemní databáze, pošta). Text pro TTS odchází do ElevenLabs. Citlivý kontext je upozornění: jakmile session sáhla na něco citlivého, hlas se před odesláním zeptá. **Není to DLP filtr.** Text pro TTS se neprohledává, posuzuje se celá session. Model, který text sestavuje, může citlivý obsah převzít i ze zdroje, který žádný signál nemá.

Kontexty se definují v `config.yaml` (vzor v `config.yaml.example`). Hook čte konfiguraci při každém volání, změna platí hned. Neplatná konfigurace znamená `deny` pro `rekni` a `prepis`. Neznámý klíč (v kořeni i v každé sekci), duplicitní klíč a merge klíč YAML `<<` jsou taky chyba: překlep `citlive_kontexy`, druhý řádek `citlive_kontexty: []` nebo druhý `<<` by detekci tiše vypnul. Totéž platí pro server, který s neplatnou konfigurací nenastartuje.

```yaml
strazce:
  prepis_cloud: dotaz
  citlive_kontexty:
    - nazev: ACME
      upozorneni: "Session pracuje s daty ACME. Poslat text do ElevenLabs?"
      platnost: den
      signaly:
        mcp: ["acme-db", "acme-*"]
        agenti: ["mail-assistant"]
        skilly: ["acme"]
        cesty: ["~/work/acme"]
        texty: [".agents/skills/acme/"]
        vstupy:
          - {nastroj: "mcp__notes__*", pole: area, hodnoty: [acme]}
          - {nastroj: "mcp__notes__*", pole: [path, rel_path], predpony: ["acme/"]}
```

Signály se hledají v transkriptu session (hlavním i u všech subagentů, `<session>/subagents/*.jsonl`). Stačí jeden, aby se kontext pro session označil:

| Signál | Co se porovnává | Příklad |
|---|---|---|
| `mcp` | jméno MCP serveru u volání `mcp__<server>__<nástroj>` (glob, rozlišuje velikost písmen) | `acme-*` zachytí `mcp__acme-wiki__search` |
| `agenti` | `subagent_type` při spuštění subagenta (nástroj `Agent`/`Task`) | `mail-assistant` |
| `skilly` | skill spuštěný nástrojem `Skill` nebo slash příkazem uživatele (`/acme`), u `plugin:skill` i část před dvojtečkou | `acme` |
| `cesty` | pracovní adresář (hook i řádky transkriptu), pole `file_path`, `path`, `notebook_path` libovolného nástroje (relativní vůči cwd, i přes symlink) a zmínka cesty v příkazu `Bash` a v zadání subagenta (absolutně, `~/`, `$HOME/`, `${HOME}/`). Cesta musí být absolutní nebo začínat `~/` | `~/work/acme` zachytí `Read ~/work/acme/app/a.php`, `cd ~/work/acme`, ne `~/work/acme-other` |
| `texty` | podřetězec v příkazu `Bash` nebo v zadání subagenta (aspoň 3 znaky, rozlišuje velikost písmen) | `.agents/skills/acme/` |
| `vstupy` | pole vstupu nástroje, jehož jméno odpovídá globu `nastroj`: přesná hodnota (`hodnoty`) nebo začátek (`predpony`) | `area: acme` u `mcp__notes__search` |

**Platnost souhlasu** (`platnost`): `den` = do konce dne pro danou session (výchozí), `session` = do konce session, `volani` = ptá se při každém volání. Souhlas platí pro kontext, ne pro celou session. Když se v téže session později objeví další kontext, hook se zeptá znovu, ale jen na ten nový. Když se trefí víc kontextů najednou, dotaz jmenuje všechny i s jejich upozorněním a souhlas platí pro každý podle jeho platnosti. Změna platnosti v konfiguraci dřívější souhlas zruší.

**Jak vzniká souhlas.** `PreToolUse` vydá `ask` a uloží čekající záznam pro `tool_use_id`. Když nástroj opravdu proběhne, `PostToolUse` se stejným `tool_use_id` a session zapíše souhlas. Model souhlas nepodvrhne parametrem nástroje, protože `tool_use_id` přiděluje API. Odmítnutý dotaz souhlas nezapíše. `PostToolUseFailure` neregistruj, mohl by přijít i po zamítnutí. Souhlas s cloudovým přepisem v session bez kontextu žádný souhlas nezakládá.

**Režimy oprávnění.** Dotaz (`ask`) hook vydá jen v režimech `default`, `acceptEdits`, `plan` a `auto`, kde ho klient ukáže uživateli. V `bypassPermissions`, `dontAsk` a v neznámém nebo chybějícím režimu se nejde zeptat, takže každé volání, které by vedlo na dotaz, hook zamítne (`deny` s důvodem) a razítko nevznikne. Volání bez dotazu projdou normálně.

**Subagenti.** Hlas smí používat jen hlavní session. Subagent dostane `deny` a razítko nevznikne. Signály ze subagentů se ale počítají do kontextu session: když subagent sáhne na `acme-db`, hlavní session se před dalším `rekni` zeptá.

**Selhání.** Vlastní chyba hooku (třeba nečitelný transkript nebo hlídací časovač 8 s) vede na dotaz s důvodem (fail-to-ask), v režimu bez dotazu na `deny`. Nečitelný vstup hooku skončí kódem 2 (blokace). Pád nebo timeout procesu pokryje `onFailure: block` a razítko.

**Stav** je v `<cesty.stav>/strazce/` (adresář `0700`, soubory `0600`): `sken-<session>.json` (nalezené kontexty a offsety čtení transkriptu; čte se jen přírůstek a nález je pro session trvalý; změna signálů v konfiguraci vynutí nový průchod), `souhlasy-v2.json`, `cekajici-<tool_use_id>.json` a `statistika.json` (jen denní počty dotazů a souhlasů).

Registrace: položky `mcp-voice-guard` v `PreToolUse` (s `onFailure: block`) a `PostToolUse` ze vzoru hooků (sekce „Hooky: vzor a test před nasazením“).

**Omezení.**
- Je to pojistka proti omylu, ne bezpečnostní hranice. Každý proces pod stejným uživatelem (i příkaz `Bash`, který spustí model) může stavové soubory přepsat a souhlas nebo „čistý“ sken podvrhnout, stejně jako konfiguraci. Pomůže pravidlo `deny` pro editační nástroje nad `~/.config/mcp-voice/**` a `<cesty.stav>/**`, Bash tím ale pokrytý není.
- Detekce vidí jen to, co je v transkriptu: jména nástrojů, cesty, příkazy a zadání. Nevidí obsah výsledků nástrojů, relativní cesty v příkazech (`cd ../acme`), `Glob`/`Grep` bez cesty ani vyhledávání bez filtru.
- Klienti jiní než Claude Code hook nespustí. Server pak bez razítka nic neodešle (`GUARD_MISSING`), pokud `strazce.vyzadovat` nevypneš.
- Hook a server musí číst stejnou konfiguraci, aby razítka ležela ve stejném `cesty.stav`. Proto pro oba nastav stejné `MCP_VOICE_CONFIG`, nebo ho nenastavuj ani jednomu.

### Status bar: `mcp-voice-status`

Server po každém úspěšném `rekni` zapíše do `<cesty.stav>/status.json` (0600, atomicky) denní počet znaků a limit a na pozadí, aby odpověď ani přehrání nečekaly, jednou zavolá `GET /v1/user/subscription` (timeout 5 s, bez opakování). Z odpovědi uloží jen zůstatek kreditů, limit a čas obnovy, nikdy ID účtu, e-mail ani část klíče. Klíč k tomu potřebuje oprávnění „User: Access“ (jen čtení účtu a předplatného). Bez něj (`401`/`403`) jsou kredity `null` a `kredity_chyba: "opravneni"`, `rekni` tím nikdy neselže. Při výpadku sítě zůstane poslední známá hodnota. Zápis čeká i na mazání historie (viz níže), takže výsledek staršího dotazu se může zapsat až po novějším. Aby novější zůstatek nepřepsal, rozhoduje čas odpovědi (`kredity_cas`). Nástroj `stav` vrací poslední známé kredity (`kredity`) bez sítě a obnoví denní část souboru. Zápis čeká na zámek souboru nejvýš asi 50 ms, pak se vynechá. Při čtení se berou jen známá pole: čísla celá od 0 do 10¹², časy a den jen jako ASCII (`[0-9A-Za-z:+.-]`, nejvýš 40 znaků), takže podvržený soubor nedostane do výstupu řídicí ani bidi znaky.

`mcp-voice-status` soubor jen čte (nikdy síť) a vypíše jeden segment, třeba `🔊 193/500 · 24,6k kr` (znaky dnes / denní limit · zbývající kredity). Nový den začíná od nuly. Bez souboru nebo při chybě vypíše prázdný řádek a skončí s kódem 0. `--json` vypíše celý stav, `--stav DIR` přeskočí čtení konfigurace.

Napojení do statusline Claude Code (`~/.claude/settings.json`):

```json
{"statusLine": {"type": "command", "command": "/cesta/k/statusline.sh"}}
```

```bash
#!/bin/sh
# statusline.sh: vlastní řádek, za něj segment hlasu (prázdný, když není co ukázat)
cat > /dev/null    # JSON od Claude Code na stdin tu není potřeba
printf '%s\n' "$(/cesta/k/mcp-voice/.venv/bin/mcp-voice-status)"
```

Kdo má statusline ve vlastním skriptu a nechce spouštět další proces, může `status.json` číst přímo: pole `den`, `znaku_dnes`, `limit_den`, `kredity_zbyva` (popis formátu je v `src/mcp_voice/status.py`).

### Historie ElevenLabs

ElevenLabs ukládá text i audio každého TTS přes API do historie účtu (*History* v UI); vypnout to jde jen v režimu Zero Retention, který je pro tarif Enterprise. Server proto po každém TTS vezme ID položky z hlavičky odpovědi `history-item-id` a zavolá `DELETE /v1/history/{id}` (timeout 5 s, bez přesměrování, jen host `api.elevenlabs.io`). Položka nemusí být hned po konci streamu uložená, proto se DELETE při `404` nebo `422` jednou zopakuje po 3 s; jiné kódy se neopakují. Maže se i tehdy, když po hlavičce selže zbytek: čtení zvuku (síť, timeout, příliš dlouhá odpověď, prázdný zvuk, zrušení volání) nebo doručení (Telegram, zápis souboru), protože položka už mohla vzniknout. Na jiné položky historie server nesahá a historii nevypisuje. ID se před vložením do URL ověří (`[A-Za-z0-9_-]`, nejvýš 64 znaků), jinak se nemaže nic.

`smazano` znamená smazáno z *History* účtu. Co si ElevenLabs drží v interních logech a podle své retence, tím smazat nejde.

Klíč k tomu potřebuje oprávnění „History: Write“. Tím ale smí celou historii účtu číst i mazat, ne jen položky serveru: uniklý klíč zpřístupní všechny neodmazané položky (starší TTS i ty, které skončily `nesmazano:*`). Kdo to nechce, nechá oprávnění vypnuté a nastaví `elevenlabs.mazat_historii: false` (položky pak zůstanou a smaže je ručně v UI). Kdo oprávnění zapne, má starší položky smazat ručně, server se k nim nevrací.

Mazání běží na pozadí spolu s dotazem na kredity, odpověď `rekni` ani přehrání na něj nečekají a jeho selhání `rekni` nikdy neshodí. Odpověď `rekni` proto nese stav v okamžiku odpovědi, konečný výsledek je v `status.json` (`historie`, `historie_cas`) a v nástroji `stav` (`historie: {stav, cas}`, `mazat_historii`):

| Hodnota | Význam |
|---|---|
| `maze_se` | v odpovědi `rekni` a v `status.json`, dokud DELETE neskončí (s opakováním obvykle do ~15 s, nejhůř ~23 s): mazání běží. Drží-li se déle, výsledek se nezapsal (zámek `status.lock` držel jiný proces) nebo byl server během mazání ukončen; položka mohla zůstat |
| `smazano` | položka smazána z *History* účtu (`200`/`204`) |
| `nesmazano:opravneni` | klíč nemá „History: Write“ (`401`/`403`) |
| `nesmazano:http_<kód>` | jiný HTTP kód, u `404`/`422` i po opakování (např. `nesmazano:http_404`) |
| `nesmazano:sit` | výpadek sítě nebo timeout |
| `nesmazano:neplatne_id` | hlavička `history-item-id` neprošla kontrolou ID, DELETE se neposlal |
| `nesmazano:chyba` | neočekávaný host odpovědi nebo neznámá chyba |
| `nesmazano:vypnuto` | `elevenlabs.mazat_historii: false` |
| `neulozeno` | odpověď TTS ID položky nenesla, není co mazat |

Podrobnosti jdou na stderr jako `WARNING mcp_voice.historie: …` (HTTP kód, typ síťové chyby, u neplatného ID jen délka a třídy znaků); ID položky ani klíč nikdy. Claude Code ale stderr serveru po navázání spojení do `mcp-logs-<server>/` nemusí ukládat, spolehlivý zdroj je `stav` a `status.json`.

`status.json` drží výsledek posledního TTS, který doběhl, a to až do dalšího `rekni`. Při dvou rychle po sobě jdoucích voláních zůstane výsledek toho, jehož mazání skončilo později; `smazano` tak může zakrýt neúspěch druhého volání. Neodmazané položky (`nesmazano:*`) zůstanou v historii účtu a server se k nim nevrací.

## Telegram: první nastavení

Telegram potřebuje `rekni` s cílem Telegram (i `cil="auto"`, když nejsi u PC), `odpovez` a příjem z Telegramu.

1. V Telegramu otevři `@BotFather`, pošli `/newbot` a zadej jméno a uživatelské jméno bota (musí končit na `bot`). BotFather odpoví tokenem.
2. Token ulož do keyringu (vložíš ho na výzvu):

   ```bash
   .venv/bin/keyring set mcp-voice telegram:bot_token
   ```

3. Doporučeno: v BotFatheru `/setjoingroups` → Disable, aby bot nešel přidat do skupin.
4. Otevři chat se svým botem a pošli mu libovolnou zprávu (třeba `/start`). Bot nemůže psát uživateli, který s ním chat nezačal.
5. Zjisti `chat_id` a své `user_id`. Příkaz vezme token z keyringu a jednou zavolá `getUpdates`. Token nevypíše ani při chybě: když v keyringu chybí nebo nemá tvar `číslo:znaky`, skončí hláškou bez volání Telegramu, a u jakékoli jiné chyby vypíše jen její typ:

   ```bash
   .venv/bin/python -I -c 'import sys; sys.excepthook = lambda typ, e, tb: print("Chyba:", typ.__name__, "(text chyby se nevypisuje, mohl by obsahovat token)", file=sys.stderr); import json, keyring, re, urllib.request; t = (keyring.get_password("mcp-voice", "telegram:bot_token") or "").strip(); t or sys.exit("Token v keyringu chybí (služba mcp-voice, klíč telegram:bot_token), ulož ho podle kroku 2."); re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", t) or sys.exit("Token v keyringu nemá tvar číslo:znaky (jen A-Z, a-z, 0-9, _ a -, bez mezer), ulož ho znovu podle kroku 2."); r = json.load(urllib.request.urlopen("https://api.telegram.org/bot%s/getUpdates" % t, timeout=20)); [print("chat_id:", u["message"]["chat"]["id"], "user_id:", u["message"]["from"]["id"], "typ:", u["message"]["chat"]["type"]) for u in r["result"] if "message" in u]'
   ```

   Chyba `HTTPError` znamená, že Telegram volání odmítl: neplatný token, nebo právě běží okno příjmu z Telegramu (viz níž). `URLError` nebo `TimeoutError` je problém se sítí. U soukromého chatu (`typ: private`) jsou `chat_id` a `user_id` stejné. Prázdný výstup: pošli botovi zprávu znovu a příkaz zopakuj (Telegram drží nevyzvednuté zprávy nejvýš 24 h). Nespouštěj ho, když je otevřené okno příjmu z Telegramu, jinak by si zprávy brali navzájem.
6. Do konfigurace zapiš `telegram.chat_id: "<chat_id>"`, pro příjem z Telegramu i `telegram.prijem.povolene_user_id: [<user_id>]`.
7. Restartuj server (nová session Claude Code) a vyzkoušej: „Pošli mi do Telegramu hlasovku: zkouška.“

## Příjem z Telegramu

Volitelná funkce (výchozí vypnutá): po povelu v terminálu nebo v Remote Control server na omezenou dobu přijímá zprávy a hlasovky od jednoho uživatele z jeho soukromého chatu s botem a vkládá je do běžící session přes inbox socket Claude Code. Odpověď jde zpět do Telegramu nástrojem `odpovez`.

**Háčky napřed:**

1. Bezpečnost stojí na hooku `PreToolUse` (`rozsah_hook.py`). Auto režim Claude Code tahy ze socketu sám nezastaví, klasifikátor taky ne. Hook proto musí být registrovaný s `"onFailure": "block"` a server okno neotevře, dokud nemá důkaz, že hook v session běží.
2. Inbox socket není bezpečnostní hranice: píše do něj každý proces uživatele. Zprávu z Telegramu hook pozná podle `origin.verifiedPeerPid` a `verifiedPeerProcStart`, které Claude Code doplní podle procesu odesílatele (server), ne podle textu.
3. Model v tahu ze socketu umí sám psát do jiných session (`SendMessage`). V tahu z Telegramu jsou meziseancové a plánovací nástroje zakázané ve všech režimech. V projektech bez příjmu nastav v `.claude/settings.local.json` `"crossSessionInbound": "hold"`.
4. Chráněné cesty u Bash spolehlivě chrání jen izolace `bwrap`. Mimo `bwrap` (strom, který nejde projít, po potvrzení) hlídá Bash jen textová kontrola, kterou obfuskace obejde.
5. Obal `bwrap` dostane Bash přes `updatedInput` hooku. Claude Code výstupy více hooků `PreToolUse` neslučuje: `updatedInput` platí jen z hooku, jehož rozhodnutí vyhrálo, a ze stejně silných rozhodnutí z toho, který doběhl poslední. Jiný hook (vlastní, z pluginu nebo frontmatteru skillu), který pro Bash vrátí `allow` nebo `ask` bez `updatedInput`, tak obal může zahodit. Proto se Bash z Telegramu i v `plny` ve výchozím stavu ptá (`ask` s obalem; proti cizímu `allow` je `ask` odolný) a kontrola v `PostToolUse` okno zavře, když Bash běžel s jiným příkazem, než jaký hook obalil.
6. Formát řádku socketu a pole transkriptu nejsou dokumentované (ověřeno na Claude Code 2.1.314). Server kontroluje verzi a tvar záznamů a při odchylce okno neotevře nebo zavře.
7. Dialog oprávnění (`ask`) v tahu ze socketu jde potvrdit v terminálu nebo v Remote Control, i při zamčeném PC (ověřeno na 2.1.314). Proto je výchozí `rezimy.prace.pri_dotazu: cekat`: dialog čeká, až ho potvrdíš v RC nebo v terminálu. `odmitnout` ho rovnou zamítne. Session bez Remote Control má vždy `odmitnout` (i když se RC odpojí během okna). Dialog se nikdy nezmění na povolení a z Telegramu nejde potvrdit nic.
8. Server musí být přímý potomek procesu `claude` (kontrola socketu `pid == getppid()`): registruj přímo `.venv/bin/mcp-voice`, **nespouštěj ho přes `uv run` ani shellový obal** (`sh -c`, skript). Jinak je každé doručení „nepotvrzeno“.

### Instalace

1. Bot, token a ID podle „Telegram: první nastavení“. Konfigurace: `telegram.chat_id`, `telegram.prijem.povoleno: true`, `povolene_user_id` (tvoje user_id, `chat_id` musí být mezi nimi).
2. Allowlist potvrď v samostatném terminálu mimo Claude Code (bez TTY, z procesu `claude` nebo s jeho proměnnými prostředí se odmítne, ani přes `!`); po každé změně `povolene_user_id` nebo `chat_id` znovu:

   ```bash
   /cesta/k/mcp-voice/.venv/bin/mcp-voice-telegram potvrd
   ```

   Další podpříkazy, bez sítě: `mcp-voice-telegram stav` vypíše, jestli je příjem zapnutý a allowlist potvrzený, a stav oken. `mcp-voice-telegram zavri` zapíše žádost o zavření aktivního okna (funguje i bez TTY, třeba ze skriptu).
3. Celý vzor hooků včetně testu před vložením (sekce „Hooky: vzor a test před nasazením“). Hook rozsahu běží `/usr/bin/python3 -I` jen ze standardní knihovny a adresář stavu dostane parametrem `--stav`. Ten musí být shodný s `cesty.stav`, jinak se okno nikdy neotevře (bez `--stav` platí proměnná `MCP_VOICE_STATE_DIR`, jinak `~/.local/state/mcp-voice`). `PostToolUse` strážce má matcher `.*` (signály citlivých kontextů ve výsledcích všech nástrojů, i Bash, Agent, WebFetch a subagentů); bez otevřeného okna a bez kontextů skončí rychle (~50 ms na volání). Hook rozsahu v `PostToolUse` a `PostToolUseFailure` s matcherem `Bash` (`--udalost post`, timeout 10 s: zámek až 3 s a sken až 3 s) hlídá, že Bash z Telegramu běžel s obalem a nezměnil konfiguraci agenta ani Gitu. Vypršení tohoto hooku Claude Code nebere jako zamítnutí, kontrola by pak neproběhla.
4. Slash příkaz: `examples/claude-command-telegram.md` zkopíruj do `.claude/commands/telegram.md` projektu (`disable-model-invocation: true`, model si ho sám nespustí).
5. Status bar: `mcp-voice-status` ukáže i segment okna (`TG do 16:30 · práce`, `TG tah #4821`, `TG čeká potvrzení`, `TG fronta 2`, `TG ověřuji`).

### Povely

```text
/telegram start [ČAS] [REŽIM]     ČAS = 30m | 90min | 2h | 30 minut | 2 hodiny (5 min až 4 h, výchozí 1 h), REŽIM = cteni | prace | plny
/telegram stop
/telegram stav
```

Jen jako celý prompt, napsaný v terminálu nebo v Remote Control (ověří se v transkriptu: `origin.kind == human`, `turnOrigin == human`, `promptSource` `typed`, `queued` nebo u slash příkazu chybí). Z Telegramu povel nikdy neplatí. `start` ověřuje server, ne hook: Claude Code zapisuje záznam promptu do transkriptu až po hookách `UserPromptSubmit` (ověřeno na 2.1.314 a 2.1.316). Hook předá žádost s `prompt_id` a otiskem povelu, server počká nejvýš 60 s na začátek tahu se stejným `promptId` a ověří ho (záznam nejvýš 30 s před převzetím žádosti; starší záznam se stejným textem neplatí). Neověřený povel okno neotevře: server ho zaloguje a pošle šablonu „Okno se neotevřelo“. Okno pak otevře `odpovez` v tahu povelu (důkaz běhu hooku rozsahu), nebo server sám po 120 s s platným heartbeatem; `odpovez` před ověřením povel ověří, bez ověřeného povelu okno neotevře. Server délku a režim znovu přečte z povelu v transkriptu, žádost hooku je jen nese (nesouhlas = `povel_nesouhlasi`). `start` v otevřeném okně změní konec a režim. Okno se zavře po čase, po 60 min bez zprávy z Telegramu (klid session bez nástrojů okno nezavírá), povelem `stop`, zprávou `/stop` z Telegramu, při `/clear`, konci session, přesunu okna do jiné session, chybě Telegramu (401, 403, 409), opakovaném překročení limitu, když Bash z Telegramu běžel bez obalu `bwrap` (`bash_bez_obalu`), nebo když po něm v zapisovatelném kořeni vznikla, zmizela či se nahradila konfigurace agenta nebo Gitu, případně to nešlo ověřit (`konfigurace_agenta`). Dokud server žádost o zavření nezpracuje, hook tahu z Telegramu zamítá všechna volání. Mimo okno server `getUpdates` nevolá; při otevření zahodí backlog, při zavření frontu.

Z Telegramu jde jen `/stop` a `/stav`. Ostatní tokeny zprávy, které odpovídají maskám `zakazane_prikazy` (výchozí `/[!/]*`), se nahradí značkou `[zakázaný příkaz]` a obálka uvede jejich počet. V maskách `*` a `?` nepřekročí `/`, takže absolutní cesta („přečti /etc/hosts“) příkaz není, `/loop 5m` a `/compact` ano. Přijímá se jen text a hlasovka (`voice`, Ogg/Opus); přeposlané a speciální zprávy, fotky a soubory se odmítnou. Text se čistí (řídicí, bidi a neviditelné znaky).

### Režimy a politika nástrojů

| Režim | Bez dotazu | Dotaz (dialog) | Zamítnuto |
|---|---|---|---|
| `cteni` | čtení (`Read`, `Grep`, `Glob`, `odpovez`, `rekni`, `stav` …) | – | vše ostatní, `Grep` nad stromem s chráněnými soubory |
| `prace` (výchozí) | čtení | vše ostatní (Bash v `bwrap`, zápis, MCP, web), `Grep` nad stromem s chráněnými soubory | tvrdé zákazy, chráněné cesty a chráněný zápis |
| `plny` | vestavěné nástroje; Bash v `bwrap` jen s `bash_bez_dotazu: true` | Bash v `bwrap` (výchozí), Bash bez `bwrap`, MCP mimo `nastroje_navic`, neznámý vestavěný nástroj, `Grep` nad stromem s chráněnými soubory | tvrdé zákazy, chráněné cesty a chráněný zápis |

Tvrdé zákazy (všechny režimy): `SendMessage`, `ListAgents`, `Cron*`, `ScheduleWakeup`, `RemoteTrigger`, `Monitor`, `TaskStop`, běh na pozadí, plánovací režim a worktree, `AskUserQuestion`, cloudový přepis a **zápis do konfigurace agenta**: `.claude/` v libovolné hloubce (i `~/.claude/`), `CLAUDE.md`, `CLAUDE.local.md` a `.mcp.json` kdekoli. Zákaz platí pro `Write`, `Edit`, `MultiEdit`, `NotebookEdit`, MCP nástroje s cestou i Bash (textová kontrola a v obalu jen pro čtení), důvod v dialogu zní „zápis do konfigurace agenta … je z Telegramu zakázaný“. `konfigurace_agenta.pridat` přidá další jména nebo vzory (soubory instrukcí jiných agentů), odebrat výchozí nejde. Čtení těchto souborů se neomezuje (kromě `~/.claude`, který je chráněný celý). Chráněné cesty (čtení i zápis, všechny režimy): `~/.ssh`, `~/.gnupg`, `~/.claude`, konfigurace a stav `mcp-voice`, keyring, správci hesel, přihlášení CLI agentů a nástrojů (`~/.codex`, `~/.gemini`, `gh`, `rclone`, cloudy …), poštovní a komunikační klienti (Thunderbird, Evolution, Signal, Telegram Desktop, Slack, Discord …), profily prohlížečů, historie shellu a dalších programů (`~/.bash_history`, `.*_history`, `~/.lesshst`, `~/.viminfo`, fish), historie schránky, stav nvim (odkládací soubory), `/proc`, sockety session a vzory `.env`, `.env.*`, `*.env`, `.envrc`, `.netrc`, `.npmrc`, `*.pem`, `*.key`, `*.kdbx`, `id_rsa*`, `*credentials*`, `*secret*` … (vzory jmen bez ohledu na velikost písmen; úplný seznam `CHRANENE_VYCHOZI` v `politika.py`). Chráněný zápis navíc: `AGENTS.md`, `GEMINI.md`, `.gemini/`, `.codex/`, `.cursor/`, `.vscode/`, `.husky/`, `.pre-commit-config.yaml`, git hooky a `.git/config` libovolného repa, `.envrc`, `~/.gitconfig`, `~/.config/git`, shell rc soubory (`~/.bashrc`, `~/.bash_aliases`, `~/.profile`, `~/.zshrc`, fish …), `~/.config/systemd`, `~/.config/autostart`, `~/.config/environment.d`, `~/.local/bin`, `~/.local/share/applications`, crontab (i příkazy `crontab`, `at`, `systemd-run` v Bash). Vždy a bez možnosti odebrat: adresář balíčku `mcp-voice` (zdroj hooku), jeho venv a repozitář editovatelné instalace, odvozené za běhu. MCP nástroje se kontrolují po řetězcových parametrech: absolutní i relativní cesty, jména souborů a `file://` URL. Konfigurace seznamy rozšíří (`pridat`), výchozí položku jen vědomě odebere (`odebrat_vychozi`, přesná shoda). `rezimy.cteni.koreny` čtení volitelně omezí na zadané adresáře; prázdné = bez omezení.

`Grep` v tahu z Telegramu vrací jen jména souborů (obsah pak jde přes `Read`, který se kontroluje po souborech); obsahový výstup se zamítá, dokud není pokusem ověřené, že Claude Code vynucený `output_mode` použije. I jména souborů prozradí po bitech obsah (`^KEY=a`), proto se `Grep` nad stromem s chráněným souborem (`.env`, klíče) nebo nad stromem, který nejde projít celý (limity skenu níže), zamítne (`cteni`) nebo ptá. `Glob` s absolutním vzorem do chráněného adresáře (`~/.ssh/*`) se zamítne.

Bash v tahu z Telegramu se zabalí do `bwrap` jako sandbox: `--unshare-all` (bez sítě, IPC a PID), `--new-session`, `--die-with-parent`, `--clearenv` (jen `PATH`, `HOME`, `USER`, `LANG`, `TERM` …), celý souborový systém jen pro čtení (`--ro-bind / /`), vlastní `/dev`, `/proc`, `/tmp` a `/run` (žádný D-Bus a klíčenka, `systemd --user`, `ssh-agent`, `gpg-agent`, X11, docker, tmux ani sockety session).

- **Domov a místa připojení jsou skrytá** (`--tmpfs`): celý domov, `/home`, `/mnt`, `/media`, `/srv`, `/net`, `/nfs`, `/afs` a `rezimy.plny.bash_skryt`. Uvnitř je vidět jen pracovní adresář, kořeny `rezimy.<prace|plny>.bash_koreny` a složky nástrojů z domova jen pro čtení (`rezimy.plny.bash_domov`, výchozí `~/.local/bin`, `~/.local/lib`, `~/.cargo/bin`, `~/.rustup`, `~/.nvm`, `~/.pyenv`, `~/.local/share/uv` …; přidávej jen složky bez tajemství, chráněná jména se v nich neprohledávají). `.env`, databáze hesel ani data jiných projektů a NAS tak z obalu vidět nejsou, ani přes obfuskaci. Pracovní adresář, který je domovem, místem připojení nebo jejich předkem, obal nesestaví (Bash se ptá bez obalu).
- `bash_koreny`: seznam `{cesta, pristup: rw|ro}` pro další adresáře (jiný projekt). Výchozí prázdný = jen pracovní adresář. Kořen, který je chráněnou cestou, konfigurací agenta, nebo obsahuje chráněnou cestu či domov (`~`, `~/.config`, `/`), validace odmítne, stejně jako systémové složky `/dev`, `/proc`, `/sys`, `/run`, `/var/run`, `/boot`, vše pod nimi a jejich předky (`/var`) a samotné `/tmp` (podsložka `/tmp` smí). `~/bin` je chráněný zápis (je v `PATH`), kořen s `rw` se tedy připojí jen pro čtení. Chráněná jména a chráněný zápis platí i uvnitř kořenů.
- Uvnitř viditelných kořenů: chráněné adresáře `--tmpfs`, chráněné soubory a soubory s chráněným jménem `--ro-bind /dev/null`. Zapisovat jde jen do pracovního adresáře, kořenů `rw` a soukromého `/tmp`; v nich zůstanou jen pro čtení v libovolné hloubce konfigurace agenta, chráněný zápis (`AGENTS.md`, `.vscode/` …, u symlinku jeho cíl), soubor `.git` (worktree, submodul), závislosti (`node_modules`, `.venv`, `vendor`) a v každém gitdiru (`.git` i `.git/modules/**` submodulů) `hooks/`, `config`, `config.worktree`, `worktrees/*/config.worktree`, soubory z `[include]` a cíl `core.hooksPath` (i relativní z `~/.gitconfig`). Chybějící `hooks/` dostane prázdný `--tmpfs` jen pro čtení (na disku po něm zůstane prázdná složka, stejná, jakou vytvoří `git init`). Ostatní obsah `.git` (objekty, reference, index) zůstává zapisovatelný, aby git fungoval.
- **Přejmenování rodiče**: každá složka mezi zapisovatelným kořenem a položkou jen pro čtení se připojí sama na sebe (`--bind d d`), takže `mv`, `rm -r` ani `rmdir` rodiče (`sub/` nad `sub/.claude/`, `.git` nad `.git/hooks`) neprojdou (`EBUSY`). `mv` pak zkusí kopii a selže; kopie pod jiným jménem může zůstat, novou konfiguraci agenta ale pozná kontrola integrity. Připojení jdou od mělkých k hlubokým (jinak by pozdější rodič překryl dřívějšího potomka).
- **Kontrola integrity po běhu** (Bash z Telegramu s obalem): hook `pre` uloží k `tool_use_id` snímek zapisovatelných kořenů (typ a inode, u symlinku i cíl) všech položek se jménem konfigurace agenta nebo chráněného zápisu, souborů a složek `.git` a jejich háčků a konfigurace (`integrita-<session>-<tool_use_id>.json`). Hook `post` kořeny znovu prohledá. Nová, smazaná nebo nahrazená položka (`.claude/settings.local.json` v novém `.claude/`, `nove/CLAUDE.md`, `CLAUDE.local.md`, `.mcp.json`, `CLAUDE.md` symlink nahrazený souborem, nový `.git`) zavře okno (`konfigurace_agenta`), do Telegramu odejde šablona bez cest a v terminálu se ukáže upozornění (`systemMessage`) se seznamem cest. Do serverového logu jdou cesty taky. Chybějící snímek, chyba nebo sken nad limit = zavření (fail-closed). mtime se nesleduje: existující položky jsou v obalu jen pro čtení a Claude Code sám přepisuje `.claude/settings.local.json` (volba „neptat se znovu“). Snímek nevzniká u Bash bez obalu (dotaz s holým příkazem).
- **Prevence neexistujících jmen se nedělá**: předvytvořit `CLAUDE.md`, `.mcp.json` nebo `.claude/` jako místo připojení (`--ro-bind` prázdného souboru) by `bwrap` založil na disku v každém kořeni (prázdný `.mcp.json` je pro Claude Code chybná konfigurace, šum v `git status`) a nové podsložky (`nove/CLAUDE.md`) by to stejně nepokrylo. Pokrývá je kontrola integrity.
- Sken stromu: jeden průchod bez symlinků, vzory jmen jako jeden regulární výraz. Neprochází `.git` (submoduly v `.git/modules` jen po gitdirech, bez objektů), `node_modules`, `.venv`, `__pycache__`; `vendor` prochází (bývají v něm klíče balíčků). Limity 500 000 položek, hloubka 32 a 3 s **pro všechny kořeny jednoho volání dohromady** (timeout hooku je 5 s; kontrola integrity po běhu má vlastní 3 s); obal nejvýš 100 000 znaků (jeden argument `bash -c`) a 2 000 připojení. Co limit překročí, obal nesestaví a Bash se ptá bez obalu (fail-closed). Pomalý kořen (NAS, studená cache) tak vede k dotazu bez obalu, ne k vypršení hooku. Změřeno (teplá cache): `~/projects` 280 tisíc položek 0,44 s, obal 77 tisíc znaků (110 složek připojených na sebe, 618 jen pro čtení), kontrola po běhu 0,42 s; repozitář se 169 tisíci položkami 0,28 s. Cache podle mtime adresářů se nevyplatí: jen kontrola mtime všech adresářů stojí zhruba polovinu skenu.
- `rezimy.plny.bash_sit: true` vrátí Bash síť (`--share-net`) – **vědomé riziko**: abstraktní unixové sockety (X11, některé D-Bus) jsou pak dosažitelné.
- **Bash v `plny` se ve výchozím stavu ptá** (`ask`, v dialogu je obalený příkaz). Bez dotazu běží jen s `rezimy.plny.bash_bez_dotazu: true` a v okně s ověřenou verzí Claude Code. Zapni to jen tehdy, když žádný jiný hook `PreToolUse` (ani z pluginu nebo skillu) nevrací pro Bash `allow` nebo `ask`: takový hook obal zahodí (háček 5) a s `allow` běží holý příkaz bez dotazu. Kontrola v `PostToolUse` to pozná podle otisku obaleného příkazu a okno zavře (`bash_bez_obalu`, šablona do Telegramu); první takový příkaz ale už proběhl. Bez `bwrap` se Bash v `plny` ptá.
- Pojistka v každém tahu (i bez okna): Bash nesmí zmínit `cc-socks`, `CLAUDE_CODE_MESSAGING`, `mcp-voice-telegram` ani konfiguraci a stav `mcp-voice`.

Zbytková rizika: textová kontrola Bash je jen pojistka a zamítá i čtení konfigurace agenta (`cat .claude/settings.json`, `git diff CLAUDE.md`; `Read` funguje) a `/proc/cpuinfo`. MCP nástroje (kromě `odpovez`, `rekni`, `stav`) se zamítnou, když jednořádkový řetězec obsahuje jméno konfigurace agenta jako celou složku cesty, i ve větě („uprav moje slozka/.claude/settings.json“). Neexistující konfiguraci agenta nebo chráněný soubor (`sub/.claude/`, `CLAUDE.md`) a náhradu symlinku `CLAUDE.md` souborem jde v `bwrap` v zapisovatelném kořeni udělat obfuskovaným příkazem; obal tomu nezabrání, kontrola integrity to po běhu pozná a okno zavře (příkaz ale už proběhl, soubor na disku zůstane do ruční kontroly). Totéž nový `.git` v podsložce (`git init`), a háčky submodulu, jehož gitdir v `.git/modules` vznikne až během příkazu. Úpravy ostatních souborů v kořeni (`Makefile`, `package.json` skripty, `.gitmodules`) obal nehlídá; spustí je až člověk. Stromy nad limity skenu (velké monorepo, celý `~/projects` s mnoha chráněnými soubory) mají Bash vždy s dotazem a bez obalu. Chráněná jména uvnitř `node_modules` a `.venv` se nemaskují (kód třetích stran). Potvrzení allowlistu z procesu, který se odpojí od `claude` a smaže jeho proměnné, kontrola nepozná.

Původ tahu: `telegram` (zpráva od serveru), `clovek`, `uloha` (předání subagenta), `cizi` (jiná session nebo proces), `naplanovany` (`/loop`, cron), `nezname`. V otevřeném okně mají `cizi`, `naplanovany` (`omezit_naplanovane: true`) a `nezname` jen čtení. Zpráva doručená uprostřed tahu omezí zbytek tahu. Subagent dědí třídu tahu, ve kterém vznikl, až do `SubagentStop`. Nic bezpečnostního nezávisí na `Stop` (Esc ho nespustí). Hook čte transkript od konce nejvýš 1 MB; dlouhý tah člověka (velké výsledky, komprimace) pozná podle značky `prompt_id`, kterou hook rozsahu uloží při volání nástroje, když přečtený začátek tahu s tímto `promptId` napsal člověk (čas značky = začátek tahu). Značka neplatí, jakmile server po ní vloží zprávu z Telegramu. Shrnutí komprimace tah nezačíná.

Živost hooku rozsahu: server okno otevře jen s čerstvým heartbeatem hooku (tah povelu). Pak ho zavře jen tehdy, když transkript obsahuje volání nástroje hlavního vlákna novější, než by hook heartbeat obnovil (nástroje běží a hook ne). Klidná session bez nástrojů okno nezavírá; heartbeat obnovují i události `Stop`, `Notification` a `SubagentStart/Stop`.

### Odpovědi, citlivé kontexty, retence

- Forma odpovědi: hlasovka → hlas, text → text; fráze ve zprávě (`telegram.prijem.forma`) mají přednost, vyhrává poslední výskyt. `odpovez(forma="auto")` ji převezme. Text se dělí na nejvýš 3 zprávy, bez `parse_mode`, jako odpověď na zprávu. Tah bez `odpovez` skončí šablonou „Hotovo, odpověď je v terminálu“. Do Telegramu jinak posílá server jen pevné šablony.
- Citlivé kontexty: `odpovez` a `rekni` do Telegramu mají razítko strážce. Klíče kontextu: `telegram: souhlas | nikdy`, `telegram_platnost: kontext | okno`, `telegram_dotaz_v_tahu: rc | odmitnout`. Dialog jmenuje příjemce („Text … → Telegram (cloud, bez koncového šifrování)“). U `forma="auto"` dialog uvádí, že odpověď může jít i hlasem přes ElevenLabs. V tahu z Telegramu strážce hledá textové signály kontextů ve výsledcích všech nástrojů (i Bash, Agent, WebFetch), u subagenta podle třídy tahu, ve kterém vznikl. Text `upozorneni` piš obecně („Data odejdou mimo (ElevenLabs, Telegram)“).
- Retence: `retence.smazat_po_h` (výchozí 24, max 47, 0 = nemazat) – server maže zprávy obou stran v soukromém chatu (`deleteMessages`), dokud běží, a po startu dožene dluh do 48 h. S retencí se mažou i hlasovky běžného `rekni(cil="telegram")` mimo okno. Zprávy, které jsi poslal mimo okno, se nemažou; ze zahozeného backlogu při otevření okna se smaže jen poslední zpráva (víc `getUpdates` s `offset=-1` nevrátí).
- Transkript: u povelu a doručení server i hook čtou metadata záznamů, text jen hashují a hledají značku `[Telegram #<id>`. Nic se nevypisuje ani neloguje.

### Akceptační scénáře (ruční, kromě A1–A16 ze specifikace)

| # | Scénář | Očekávání | Automatický test |
|---|---|---|---|
| A17 | Okno otevřené, 20 min bez nástrojů, pak zpráva z Telegramu | zpráva se doručí, okno zůstane | `test_klidna_session_20_min_bez_nastroju_doruci` |
| A18 | Dlouhý tah u PC s velkými soubory v otevřeném okně (> 1 MB transkriptu) | Bash, Write a Edit se chovají jako mimo okno, ne „Omezený tah (nezname)“ | `test_s5_znacka_cloveka_a_posledni_vlozeni`, `test_dlouhy_tah_cloveka_podle_znacky_prompt_id` |
| A19 | Session s citlivým kontextem, tah z Telegramu, `Read` přes subagenta | `odpovez` vyvolá dialog souhlasu | `test_signal_z_read_subagenta_tahu_z_telegramu` |

### Provozní doporučení

Dvoufázové ověření v Telegramu (cloudové heslo) i na účtu Claude, kontrola aktivních relací Telegramu, v BotFather `/setjoingroups` vypnout, při ztrátě zařízení token bota rotovat (`/revoke`) a znovu uložit do keyringu. Okno otevírej jen na nutnou dobu a sleduj status bar.

## Bezpečnost a soukromí

- **Logy:** jen stderr, jen metadata (nástroj, cíl, důvod, počet znaků, délka, ms, kód chyby). Nikdy text, přepis, `chat_id`, `voice_id`, token ani klíč. Loggery `httpx`/`httpcore` jsou natvrdo na `WARNING` (URL Telegramu nese token), filtr maskuje známá tajemství. Chyby nástrojů nikdy neobsahují text výjimky.
- **Claude Code ukládá stderr MCP serverů trvale** do `~/.cache/claude-cli-nodejs/<projekt>/mcp-logs-<server>/`. I proto do logu nejde nic citlivého.
- **ElevenLabs:** text pro TTS (a u `engine="cloud"` celé audio) odchází do ElevenLabs. Podle zásad z 9. 10. 2026 je smí používat k tréninku, dokud v účtu nevypneš „Data use“. TTS přes API se ukládá do historie účtu (text i audio); server vytvořenou položku hned maže (viz „Historie ElevenLabs“).
- **Telegram:** cloudový chat bez koncového šifrování. Klient zná jen metody `sendVoice`, `sendMessage`, `getUpdates`, `getFile`, `deleteMessages` a host `api.telegram.org`, bez přesměrování a bez `caption`; po odeslání ověří, že zpráva skončila v soukromém chatu z konfigurace. `getUpdates` jen v otevřeném okně příjmu.
- **Přepis:** soubor jen z `stt.povolene_adresare` (`realpath` + `commonpath`, otevření `O_NOFOLLOW`, kontrola skutečné cesty deskriptoru, jen běžný soubor). Audio se načte jednou do paměti; `ffprobe` a `ffmpeg` čtou tytéž bajty z `memfd` přes protokol `fd` (playlisty a `concat` nesáhnou jinam), s limity CPU a paměti. Povolené formáty: ogg, mp3, wav, flac, m4a/mp4, webm. Dekóduje systémový `ffmpeg`, ne PyAV. Lokální selhání nikdy automaticky nepřejde do cloudu. Přepis je nedůvěryhodný vstup (`"zdroj": "prepis_hlasovky"`).
- **DBus:** jen `GetActive` a `GetIdletime` s pevným argv a timeoutem; nikdy `Lock`, `SetActive`, `SimulateUserActivity`.
- **Soubory:** `umask 077`, stavové adresáře `0700`, soubory `0600`, zápis atomicky; výstupní MP3 přes `O_CREAT|O_EXCL|O_NOFOLLOW`.

## Výběr cíle (`cil="auto"`)

| Zdroj | Pravidlo |
|---|---|
| terminál | PC zamčené → Telegram; nečinnost ≥ `prah_dlouhe_necinnosti_s` → Telegram; zámek neznámý → `TARGET_UNRESOLVED`; jinak PC |
| Remote Control | zamčené teď nebo při promptu → Telegram; okno klienta a aktivita při promptu → PC; aktivita po promptu → PC; nečinnost při promptu (bez snímku nečinnost teď) ≥ `prah_necinnosti_s` → Telegram; jinak PC |
| tah z Telegramu | vždy Telegram (okno příjmu otevřené, původ tahu podle transkriptu) |
| neznámý | zamčené nebo nečinnost ≥ `prah_necinnosti_s` → Telegram; stav PC neznámý → `TARGET_UNRESOLVED`; jinak PC |

`tiche_hodiny` přesměrují auto z PC do Telegramu. Výslovné `cil="pc"` při zamčeném nebo neznámém stavu PC vrátí `PC_LOCKED` / `PC_STATE_UNKNOWN` (přepínač `pc_pri_zamceni: hrat`). Přehrávání na PC se při zamčení utne a zbytek fronty zahodí.

## Kódy chyb

`TEXT_EMPTY`, `TEXT_TOO_LONG`, `ARGUMENT_INVALID`, `VOICE_UNKNOWN`, `MODEL_NOT_ALLOWED`, `DAILY_LIMIT`, `RATE_LIMIT`, `DUPLICATE`, `DATA_USE_NOT_CONFIRMED`, `SECRET_MISSING`, `KEYRING_UNAVAILABLE`, `ELEVENLABS_AUTH`, `ELEVENLABS_QUOTA`, `ELEVENLABS_RATE`, `ELEVENLABS_VOICE`, `ELEVENLABS_ERROR`, `TARGET_UNRESOLVED`, `PC_LOCKED`, `PC_STATE_UNKNOWN`, `PLAYER_UNAVAILABLE`, `TELEGRAM_NOT_CONFIGURED`, `TELEGRAM_ERROR`, `ENCODER_ERROR`, `OUTPUT_PATH_INVALID`, `FILE_NOT_ALLOWED`, `FILE_NOT_FOUND`, `FILE_TOO_LARGE`, `AUDIO_TOO_LONG`, `AUDIO_UNREADABLE`, `CLOUD_DISABLED`, `STT_LOCAL_UNAVAILABLE`, `STT_LOCAL_FAILED`, `GUARD_MISSING`, `GUARD_MISMATCH`, `WINDOW_CLOSED`, `NOT_TELEGRAM_TURN`, `SECRET_LIKE`, `INTERNAL_ERROR`.

## Testy

```bash
.venv/bin/pytest -q                      # bez sítě: HTTP přes httpx.MockTransport, falešná tajemství
.venv/bin/pytest -q --cov=mcp_voice
```

Na základní instalaci (varianta A) se testy, které potřebují lokální přepis, přeskočí. Test se skutečným modelem běží jen tam, kde je model stažený. `tests/test_repo.py` hlídá, že do repozitáře nepronikne osobní cesta ani token.

Test soukromých slov (jména, domény) je volitelný a seznam slov v repozitáři záměrně není. Zapneš ho proměnnou `MCP_VOICE_SOUKROMA_SLOVA` s cestou k vlastnímu souboru (jedno slovo na řádek, `#` = komentář), bez ní se přeskočí:

```bash
MCP_VOICE_SOUKROMA_SLOVA=/cesta/k/slova.txt .venv/bin/pytest -q tests/test_repo.py
```

## Licence

MIT, viz `LICENSE`. Vzory převzaté jako myšlenky (bez kódu): sekvenční fronta přehrávání a nevracení mluveného textu (blacktop/mcp-tts, MIT), parametry Ogg/Opus pro `sendVoice` (Agent2Telegram, MIT), hranice vstupních a výstupních souborů (elevenlabs-mcp, MIT).
