# telegram-flights-radar

Bot Telegram che ogni giorno (DAILY_TIME, default 08:00 Europe/Rome) cerca voli
economici da **VRN e BGY** verso destinazioni flessibili (anche con 1-2 scali)
e invia le migliori N offerte in chat con link di prenotazione.

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
| `flights/base.py`                                | dataclass `Offer` (con `offer_hash` per dedup) + protocol `FlightClient`    |
| `flights/ryanair.py`, `flights/travelpayouts.py` | client API, uno per fonte                                                   |
| `deals.py`                                       | `DealEngine`: orchestrazione ricerca, regole "è un affare?", ranking, dedup |
| `storage.py`                                     | SQLite: `price_history`, `sent_offers`, `settings`                          |
| `formatter.py`                                   | messaggi Telegram in HTML, date/testi in italiano                           |
| `bot.py`                                         | comandi `/oggi /destinazioni /soglia /help` + `run_search_and_send`         |
| `scheduler.py`                                   | `schedule_daily()` sulla JobQueue                                           |
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
basso = migliore).
- ⚠️ **Le impostazioni nella tabella `settings` del DB sovrascrivono il `.env`**
(soglie, whitelist, blacklist — modificate via comandi bot). Se una soglia
sembra ignorata, controllare lì prima di toccare il codice.

## Env vars

Obbligatorie: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Consigliata:
`TRAVELPAYOUTS_TOKEN` (senza → solo Ryanair diretti). Opzionali:
`TRAVELPAYOUTS_MARKER`, `ORIGIN_AIRPORTS`, `SEARCH_DAYS_AHEAD`,
`DESTINATIONS_WHITELIST/BLACKLIST`, `PRICE_THRESHOLD_EUROPE/EXTRA`,
`PRICE_THRESHOLD_EUROPE_RT/EXTRA_RT` (soglie A/R, prezzo totale),
`MIN/MAX_TRIP_NIGHTS` (range soggiorno A/R, default 3-10),
`RT_SCORE_WEIGHT` (peso A/R nel ranking, default 0.75: <1 favorisce le A/R),
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

- `TRAVELPAYOUTS_TOKEN` configurato in `.env` → il bot usa anche Travelpayouts
(scali, altre compagnie), non solo Ryanair diretti.
- L'API gratuita Travelpayouts espone *numero* scali e durata totale ma NON gli
aeroporti di scalo/tempi di attesa (serve API a pagamento tipo Duffel/SerpApi).
- L'API Ryanair è non ufficiale: nessun rate limit documentato, può cambiare o
bloccare senza preavviso (User-Agent browser già impostato nel client).
- La media storica per rotta diventa attendibile solo dopo `MIN_HISTORY_SAMPLES`
(5) rilevazioni: nei primi giorni lavorano solo le soglie assolute. Lo storico
è separato per `trip_type` (one_way / round_trip): dopo la migrazione le
rilevazioni A/R ripartono quasi da zero anche se il DB ha già dati one-way.
- Ricerca A/R: Ryanair usa `farfnd/v4/roundTripFares` con `durationFrom/To`
(parametro non documentato → il range notti viene sempre rifiltrato client-side);
Travelpayouts usa lo stesso `/v2/prices/latest` con `one_way=false`. La ricerca
sola andata su Travelpayouts passa `one_way=true` (prima del 2026-07 passava
`false`, quindi lo storico pre-migrazione conteneva prezzi A/R etichettati
one-way: sistemato dal backfill su `return_date`).
- Deploy in corso su Railway (scelto per l'hosting): repo pushato su GitHub
(`lucacrivellaro/telegram-flights-radar`, privato) con auto-deploy su push.
Lato repo è tutto pronto: Dockerfile, `.dockerignore`, procedura completa nel
README ("Deploy su Railway"). Il bot è un worker in polling: **nessun
healthcheck path** da configurare su Railway. **Da completare ancora** (solo
pannello Railway): creazione progetto dal repo, variabili d'ambiente e volume
persistente su `/app/data` (senza il volume si perde lo storico prezzi ad
ogni deploy).

