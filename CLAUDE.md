# telegram-flights-radar

Bot Telegram **multi-utente** che ogni giorno (DAILY_TIME, default 08:00
Europe/Rome) cerca voli economici verso destinazioni flessibili e invia a ogni
iscritto le migliori N offerte **andata/ritorno, solo voli diretti**, con link
di prenotazione, più una sezione di **viaggi a tappe** (itinerari multi-città
composti dal bot, con tappe extra-Europa). Ogni utente ha **due liste** di
aeroporti di partenza — corto raggio (default **VRN, BGY**) e lungo raggio per
extra-Europa e viaggi a tappe (default **VCE, BGY, MXP**) — più soglie e liste
destinazioni personali; l'iscrizione (/start) va approvata dall'admin
(`TELEGRAM_CHAT_ID`) con /approva.

## Stack

- Python 3.14, venv in `.venv/`
- `python-telegram-bot[job-queue]` (async; lo scheduler è la JobQueue → APScheduler)
- `httpx` (chiamate API, sync, eseguite in `asyncio.to_thread` dal bot)
- SQLite via stdlib `sqlite3` (`data/flights.db`), `airportsdata` per IATA→città/paese
- API voli: **Ryanair fare finder** (non ufficiale, no key, prezzi live, solo
diretti) + **Travelpayouts Data API v2** (token gratuito, prezzi cached
multi-compagnia con n. scali). Amadeus/Kiwi/Skyscanner NON usabili
(dismessa / chiusa a nuovi utenti / solo partner).

## Mappa repo


| File                                             | Responsabilità                                                              |
| ------------------------------------------------ | --------------------------------------------------------------------------- |
| `config.py`                                      | `Config.from_env()`: tutto il `.env`, nessun altro file legge env           |
| `airports.py`                                    | IATA → (città, paese) + `is_short_haul()` per la fascia soglia              |
| `flights/base.py`                                | dataclass `Offer` (con `offer_hash` per dedup) + `Leg` (tratta multitratta) + protocol `FlightClient` |
| `flights/ryanair.py`, `flights/travelpayouts.py` | client API, uno per fonte. Travelpayouts ha due ricerche A/R: `search_round_trip()` (tante mete, la più economica per meta, quindi con scalo sul lungo raggio) e `search_round_trip_direct()` (solo voli diretti, `direct=true`) |
| `flights/multitrip.py`                           | `MultiTripBuilder`: compone itinerari a tappe concatenando tratte singole (beam search) — NON è un `FlightClient` |
| `deals.py`                                       | `DealEngine`: `fetch_offers()` (API, per aeroporto) + `select_for_user()` (soglie/liste/dedup per utente via `UserPrefs`) |
| `storage.py`                                     | SQLite: `price_history` (globale), `sent_offers` (per chat), `users`, `user_settings` |
| `formatter.py`                                   | messaggi Telegram in HTML, date/testi in italiano                           |
| `bot.py`                                         | comandi utente `/oggi /aeroporti /destinazioni /tappe /soglia /stop /help`, admin `/utenti /approva /rifiuta` + `run_search_and_send` |
| `scheduler.py`                                   | `schedule_daily()`: un fetch sull'unione degli aeroporti degli utenti attivi, poi un messaggio a testa |
| `main.py`                                        | entry point produzione · `search_once.py` test una tantum                   |


## Convenzioni

- Logger per modulo (`logging.getLogger(__name__)`), messaggi log e testi utente in italiano.
- Un client API che fallisce NON blocca gli altri: eccezione catturata in
`DealEngine.search()`, accumulata in `result.errors` e mostrata nel messaggio.
Mai fallire in silenzio: anche con zero offerte si invia un messaggio.
- Parsing risposte API sempre difensivo (`.get()`, campi mancanti → skip riga).
- Nuova fonte voli: nuovo file in `flights/` che implementa `FlightClient`,
registrarlo in `DealEngine._clients()`.
- Nuova regola "offerta": in `DealEngine._evaluate()`; deve aggiungere una
stringa a `reasons` (finisce nel messaggio) e definire il suo `score` (più
basso = migliore). I multitratta hanno il loro `_evaluate_multi()`: soglia sul
prezzo *totale*, lista e posti separati (`multi_top_n`), non competono con i
voli singoli.
- **Multitratta**: un `Offer` con `legs` valorizzato è un itinerario a tappe
(`is_multi`, `trip_type == "multi_city"`, `stopovers` = tappe con sosta). Le
tratte sono biglietti separati: prezzo = somma, un link per tratta. Il
`MultiTripBuilder` non implementa `FlightClient` (interfaccia diversa) e va
registrato in `DealEngine._multi_builder()`, non in `_clients()`.
- **Due liste di aeroporti** per utente: `origins` (corto raggio) e
`intl_origins` (lungo raggio). `DealEngine._origin_allowed()` instrada ogni
offerta sulla lista giusta in base a `is_short_haul(destination)`; i
multitratta partono solo dagli `intl_origins`. `UserPrefs.all_origins` è
l'unione, usata per interrogare le API una volta sola.
- Il bot invia **solo A/R**: non esistono più soglie/ricerche di sola andata
(rimosse il 2026-07-25 su richiesta, erano codice morto con `SEARCH_ONE_WAY`
a false). `FlightClient` espone quindi solo `search_round_trip()`. I comandi
`/soglia europa|extra` puntano alle chiavi DB `threshold_europe_rt`/
`threshold_extra_rt`: i nomi delle *chiavi* non sono cambiati, quindi le
preferenze già salvate restano valide.
- ⚠️ **Le impostazioni nella tabella `user_settings` del DB sovrascrivono il
`.env`** (aeroporti, soglie, whitelist, blacklist — modificate via comandi
bot, scoped per `chat_id`; il `.env` è solo il default per i nuovi utenti).
Se una soglia sembra ignorata, controllare lì prima di toccare il codice.
- `/oggi` **non** marca le offerte come inviate (`run_search_and_send` passa
`mark_as_sent=False`): è una ricerca a richiesta, e marcarla brucerebbe le
offerte per `resend_cooldown_days`, rendendo ogni /oggi successivo più povero
del precedente. Solo l'invio giornaliero (`select_for_user` dallo scheduler)
marca. Conseguenza voluta: un'offerta vista con /oggi può ricomparire nel
messaggio del giorno dopo.
- Le API si interrogano una volta per aeroporto distinto (unione fra tutti
gli utenti attivi), mai una volta per utente: `fetch_offers()` è condivisa,
`select_for_user()` è la parte personalizzata.

## Env vars

Obbligatorie: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (chat dell'admin, che
approva le iscrizioni; i valori di ricerca del `.env` sono i default per ogni
nuovo utente). Consigliata:
`TRAVELPAYOUTS_TOKEN` (senza → solo Ryanair diretti). Opzionali:
`TRAVELPAYOUTS_MARKER`, `ORIGIN_AIRPORTS` (corto raggio),
`INTL_ORIGIN_AIRPORTS` (lungo raggio: extra-Europa A/R e multitratta),
`SEARCH_DAYS_AHEAD`, `LONGHAUL_START_DAYS` (sposta avanti la finestra del
lungo raggio),
`DESTINATIONS_WHITELIST/BLACKLIST`,
`PRICE_THRESHOLD_EUROPE_RT/EXTRA_RT` (soglie A/R, prezzo totale),
`PRICE_THRESHOLD_MULTI` (soglia sul totale di un itinerario a tappe),
`TOP_N_EXTRA` (posti riservati al lungo raggio),
`DIRECT_ONLY` (corto raggio, default true) e `DIRECT_ONLY_EXTRA` (lungo
raggio, default false),
`MULTI_ENABLED/TOP_N/MIN_STOPS/MAX_STOPS/MIN_STAY_NIGHTS/MAX_STAY_NIGHTS/`
`MAX_TRIP_DAYS/DIRECT_ONLY/EXTRA_EUROPE_ONLY/BEAM_WIDTH/CANDIDATES/`
`MAX_API_CALLS` (multitratta),
`MIN/MAX_TRIP_NIGHTS` (range soggiorno A/R, default 3-10),
`DISCOUNT_THRESHOLD_PCT`, `MIN_HISTORY_SAMPLES`, `TOP_N`,
`RESEND_COOLDOWN_DAYS`, `DAILY_TIME`, `TIMEZONE`, `DB_PATH`.
Tutte documentate con commenti in `.env.example`.

## Comandi

```bash
source .venv/bin/activate
python search_once.py            # ricerca di test, stampa a terminale (non marca come inviate)
python search_once.py --send     # come sopra + invio Telegram (marca come inviate)
python main.py                   # bot completo con scheduler
docker build -t flights-radar . && docker run --env-file .env -v flights_data:/app/data flights-radar
```

Non ci sono test automatici: la verifica è `search_once.py` (Ryanair funziona
senza alcuna chiave, quindi il test è sempre eseguibile).

## Regole per Claude Code

1. **Mai committare segreti o `.env`** (già in `.gitignore`; vale anche per
  valori incollati in log/README).
2. **Non modificare la logica di soglia/convenienza** (`deals.py::_evaluate`,
  `thresholds`) senza spiegare esplicitamente il motivo all'utente.
3. **Ogni modifica alla logica di ricerca** (client in `flights/`, `DealEngine`)
  va verificata con `python search_once.py` (o `/oggi` sul bot) prima di
   considerarla completata.
4. **Schema DB retrocompatibile**: `price_history` è la base delle medie
  storiche — non rinominare/eliminare colonne senza una migrazione che
   preservi i dati esistenti. Aggiunte: solo `ALTER TABLE ADD COLUMN` idempotente.

## Stato noto / limitazioni (aggiornare nel tempo)

- Multi-utente (2026-07): tabelle `users` (pending/active/stopped/blocked) e
`user_settings`; `sent_offers` migrata a PK `(chat_id, offer_hash)` con i dati
pre-esistenti attribuiti all'admin, così come le vecchie `settings` globali
(la tabella `settings` resta nello schema ma è vuota/legacy). `price_history`
resta condivisa fra tutti gli utenti.

- `TRAVELPAYOUTS_TOKEN` configurato in `.env` → il bot usa anche Travelpayouts
(scali, altre compagnie), non solo Ryanair diretti.
- L'API gratuita Travelpayouts espone *numero* scali e durata totale ma NON gli
aeroporti di scalo/tempi di attesa (serve API a pagamento tipo Duffel/SerpApi).
- Multitratta (2026-07-25): nessuna API gratuita vende itinerari multi-city, il
bot li **compone**. Due endpoint con ruoli distinti: `/v2/prices/latest`
(`period_type=month`, 200-500 rotte per città = scelta dei candidati, le sue
date NON sono vincolanti) e `/aviasales/v3/grouped_prices`
(`group_by=departure_at` + `direct=true`, prezzo giorno per giorno di una rotta
= aggancio della tappa successiva). `v3/prices_for_dates` con la sola origine è
inutilizzabile per la scoperta: ritorna 30 righe / 8 destinazioni.
- Il collo di bottiglia del multitratta è il **volo di rientro**, non il budget
API: da un aeroporto piccolo le catene più economiche finiscono in città che
non hanno un diretto verso casa. Per questo `build()` calcola `_home_routes` e
riserva metà dei posti candidati alle città collegate a casa.
- Multitratta **solo extra-Europa** (`multi_extra_europe_only`, 2026-07-25):
con `multi_direct_only=True` la resa crollava (VCE 0 itinerari, MXP 3 a
713-945€), quindi il default è passato a **False** — sulle rotte
intercontinentali gli scali sono la norma. Con gli scali ammessi: VCE 3
itinerari, MXP da 503€, ~120 chiamate API e ~18s per aeroporto. Prezzi reali
360-550€: da qui `PRICE_THRESHOLD_MULTI=700`.
- **Due finestre temporali** (`longhaul_start_days=60`, 2026-07-25): Europa
`oggi+1 → +45gg`, lungo raggio la stessa ampiezza spostata avanti di 60
giorni. Vale solo per `search_round_trip_direct()` e il multitratta: le
extra-Europa singole vengono di fatto solo da lì, perché quelle con scalo le
scarta `_vuole_diretto()`. Misurato spostando la finestra: le A/R dirette
extra-Europa migliorano molto (+0gg → 9 offerte, min 583€; +60gg → 26, min
313€; +90gg → 25, mediana migliore), i viaggi a tappe invece **peggiorano**
(18 → 12 → 11 itinerari) perché la cache Travelpayouts è più densa sulle date
vicine. 60 è il compromesso. Il calo a +120/+150 è il periodo natalizio, si
sposta col passare dei giorni. Non è per utente: cambia cosa si chiede alle
API, quindi ogni valore distinto sarebbe una ricerca in più (come
`required_sets`).
- **Quota per fascia** (`_apply_quota`, `TOP_N_EXTRA=2`): senza, le offerte
extra-Europa A/R non entrerebbero quasi mai nelle TOP_N, perché lo score è
`prezzo/soglia` della fascia e un A/R Europa a 43€ (0.61) batte Miami a 507€
(0.92). Non si risolve ritoccando le soglie: abbassarle peggiora lo score. I
posti non riempiti da una fascia passano all'altra, così la quota garantisce
senza sprecare. Composizione del messaggio: 6 Europa + 2 extra + 2 multitratta.
- ⚠️ I posti extra-Europa richiedono voli **diretti** (`_vuole_diretto()`), e
questo funziona solo grazie a `TravelpayoutsClient.search_round_trip_direct()`:
`/v2/prices/latest` ritorna una riga per destinazione, la *più economica*, che
intercontinentale è sempre con scalo — delle 45 tariffe extra-Europa trovate
così, **zero** erano dirette. Le dirette esistono ma emergono solo con
`/aviasales/v3/prices_for_dates` + `direct=true` (1 chiamata per aeroporto e
mese, ~550 tariffe A/R dirette per giro). Se un giorno i posti 7-8 tornano
vuoti, guardare lì prima che alle soglie.
- `PRICE_THRESHOLD_EXTRA_RT=900` non è generosità: dopo i filtri finestra +
3-10 notti, le A/R dirette extra-Europa disponibili erano 9, da 583€ (Sharjah)
a 1594€ (Tokyo). A 550€ ne passavano **zero**, a 900€ ne passano 5 — bacino
sufficiente per 2 posti. La soglia tocca solo questa fascia (`_evaluate` la
usa solo se `not is_short_haul`).
- `airports.same_metro()` evita tappe a due passi da casa (MIL partendo da BGY):
il confronto sui nomi non basta, MIL="Milano" ma MXP="Milan". Serve anche a
soddisfare una tappa obbligatoria "NYC" con un itinerario via JFK.
- **Tappe obbligatorie** (`/tappe`, `multi_required`, 2026-07-25): il vincolo
guida la beam search, NON è un filtro sui risultati — filtrare a posteriori
darebbe sempre zero, perché la ricerca insegue le catene più economiche. La
prima tappa è forzata a una delle obbligatorie (partire altrove le farebbe
scartare da `_prune`, che ordina per prezzo: New York non compete con Tirana).
Siccome il vincolo entra nella ricerca, serve **una build per ogni insieme di
tappe distinto** fra gli utenti attivi (`fetch_offers(required_sets=...)`):
utenti con lo stesso vincolo condividono la ricerca.
- Più tappe obbligatorie valgono in **OR**, ne basta una (`_satisfies`). Non è
solo semantica: `build()` fa **una beam search indipendente per città** e
scala `_budget` di conseguenza. Con un beam unico condiviso le catene verso la
meta più economica scacciavano le altre e aggiungere una città *riduceva* i
risultati — l'opposto di quello che un OR promette. Le cache di scoperta e
calendari sono condivise fra le sotto-ricerche, quindi il costo reale non si
moltiplica per intero (BGY con 3 città: 184 chiamate invece di 64×3).
- Con tappe obbligatorie il rientro va **verificato** prima di allungare la
catena (`_with_return_home`): `_home_routes` dice che casa vola verso quella
città, ma è la cache dei prezzi minimi (una data sola) e il calendario del
rientro su una finestra di 4 giorni è spesso vuoto. Da NYC le mete più
economiche sono Orlando e Dallas, che verso MXP non hanno rientro; Miami e
Boston sì, ma stanno più in basso nella lista per prezzo. Resa reale misurata:
`NYC` → BGY 1 itinerario (874€, via Orlando/Los Angeles/Las Vegas), MXP 0;
`DXB` → MXP 1 (430€, via Riyadh/Jeddah), BGY 0; `BKK` → nessuno. Vincolare il
percorso taglia molto la resa e alza i prezzi: 874€ sta sopra
`PRICE_THRESHOLD_MULTI=700`, quindi senza alzare la soglia l'utente vede solo
il messaggio "nessun itinerario che passi da NYC" (che è voluto: una sezione
vuota sembrerebbe un guasto).
- L'API Ryanair è non ufficiale: nessun rate limit documentato, può cambiare o
bloccare senza preavviso (User-Agent browser già impostato nel client).
- La media storica per rotta diventa attendibile solo dopo `MIN_HISTORY_SAMPLES`
(5) rilevazioni: nei primi giorni lavorano solo le soglie assolute. Lo storico
è separato per `trip_type` (one_way / round_trip): dopo la migrazione le
rilevazioni A/R ripartono quasi da zero anche se il DB ha già dati one-way.
- Ricerca A/R: Ryanair usa `farfnd/v4/roundTripFares` con `durationFrom/To`
(parametro non documentato → il range notti viene sempre rifiltrato client-side);
Travelpayouts usa lo stesso `/v2/prices/latest` con `one_way=false`. Lo storico
`price_history` conserva ancora righe `trip_type='one_way'` di quando la
ricerca sola andata esisteva: sono inerti, non vengono più né scritte né lette.
- Deploy in corso su Railway (scelto per l'hosting): repo pushato su GitHub
(`lucacrivellaro/telegram-flights-radar`, privato) con auto-deploy su push.
Lato repo è tutto pronto: Dockerfile, `.dockerignore`, procedura completa nel
README ("Deploy su Railway"). Il bot è un worker in polling: **nessun
healthcheck path** da configurare su Railway. **Da completare ancora** (solo
pannello Railway): creazione progetto dal repo, variabili d'ambiente e volume
persistente su `/app/data` (senza il volume si perde lo storico prezzi ad
ogni deploy).

