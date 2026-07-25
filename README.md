# ✈️ Telegram Flights Radar

Bot Telegram **multi-utente** che ogni giorno cerca voli economici verso
destinazioni flessibili e invia a ogni iscritto le migliori offerte, con link
di prenotazione. Ogni utente sceglie i propri aeroporti di partenza (default
**Verona VRN** e **Bergamo BGY**), soglie di prezzo e destinazioni; le nuove
iscrizioni vanno approvate dall'admin.

## Come funziona

- **Ryanair fare finder** (API pubblica non ufficiale, senza chiave): prezzi
  live dei voli diretti Ryanair — copre benissimo VRN e BGY.
- **Travelpayouts/Aviasales Data API** (gratuita, con token): prezzi in cache
  di tutte le compagnie, inclusi itinerari con 1-2 scali. Nota: l'API gratuita
  indica il *numero* di scali e la durata totale, ma non gli aeroporti di scalo.
- Il bot cerca **solo offerte andata/ritorno** (soggiorni da `MIN_TRIP_NIGHTS`
  a `MAX_TRIP_NIGHTS` notti, default 3-10): tutte le soglie sono quindi sul
  prezzo totale della combinazione, non a tratta.
- **Due liste di aeroporti di partenza**: `ORIGIN_AIRPORTS` (default
  `VRN,BGY`) per le mete europee/corto raggio e `INTL_ORIGIN_AIRPORTS`
  (default `VCE,BGY,MXP`) per il lungo raggio — offerte extra-Europa e viaggi
  a tappe. Sono separate perché uno scalo regionale non ha voli
  intercontinentali: cercarli da lì è solo rumore. Entrambe personalizzabili
  per utente con `/aeroporti` e `/aeroporti intl`.
- Un'offerta è segnalata se il prezzo è **sotto la soglia assoluta** della sua
  fascia (Europa / extra-Europa, con soglie separate per sola andata e A/R)
  **oppure** sotto la media storica della rotta di almeno il
  `DISCOUNT_THRESHOLD_PCT`% (la media si costruisce da sola nel database
  SQLite, giorno dopo giorno, separatamente per sola andata e A/R).
- Vengono proposti **solo voli diretti**, su entrambe le fasce
  (`DIRECT_ONLY`, `DIRECT_ONLY_EXTRA`). Sul lungo raggio questo richiede una
  ricerca dedicata (`search_round_trip_direct()`, endpoint
  `v3/prices_for_dates` con `direct=true`): la ricerca normale restituisce
  solo la tariffa più economica per destinazione, che intercontinentale è
  sempre con scalo, quindi le dirette non emergerebbero mai. Costano di più —
  da qui la soglia extra-Europa alta.
- Il messaggio ha una **composizione garantita**: `TOP_N - TOP_N_EXTRA` offerte
  europee + `TOP_N_EXTRA` extra-Europa (default 6 + 2), più i viaggi a tappe.
  Senza la quota il lungo raggio non comparirebbe quasi mai: lo score è
  `prezzo/soglia` della fascia, e un A/R europeo a 43€ (0.61) batte sempre
  Miami a 507€ (0.92). I posti che una fascia non riempie passano all'altra.
- **🧭 Viaggi a tappe** (multitratta): in fondo al messaggio il bot aggiunge
  fino a `MULTI_TOP_N` itinerari a più tappe — da un aeroporto di lungo raggio
  → 2-4 città intermedie **fuori dall'Europa** con 2-5 notti di sosta ciascuna
  → rientro, entro `MULTI_MAX_TRIP_DAYS` giorni.
  Nessuna API gratuita vende itinerari multi-city, quindi il bot li **compone**
  concatenando biglietti di sola andata: ogni tratta ha il suo link e si
  prenota a parte, il prezzo mostrato è la somma. Hanno una soglia dedicata sul
  totale (`PRICE_THRESHOLD_MULTI`), perché le soglie per volo singolo non sono
  applicabili a 3-5 tratte sommate. Richiede `TRAVELPAYOUTS_TOKEN`.
- Le offerte già inviate a un utente non gli vengono ripetute per
  `RESEND_COOLDOWN_DAYS` giorni, a meno che il prezzo non cali di oltre il 10%.
- **Multi-utente**: chi scrive `/start` al bot entra in lista d'attesa;
  l'admin (la chat di `TELEGRAM_CHAT_ID`) riceve la richiesta e approva con
  `/approva`. Le API vengono interrogate **una sola volta per aeroporto
  distinto** al giorno, qualunque sia il numero di iscritti: il costo API
  cresce con gli aeroporti configurati, non con gli utenti.

> Perché non Amadeus o Kiwi Tequila? Amadeus ha dismesso il portale
> Self-Service il 17/07/2026; Kiwi Tequila non accetta nuove registrazioni.
> Skyscanner ha un'API solo per partner commerciali.

## Setup

### 1. Token del bot Telegram

1. Su Telegram scrivi a [@BotFather](https://t.me/BotFather) → `/newbot`.
2. Scegli nome e username: ricevi il **token** (formato `123456:ABC-...`).
3. Scrivi un messaggio qualsiasi al tuo nuovo bot (serve ad aprire la chat).
4. Recupera il **chat ID**: apri
   `https://api.telegram.org/bot<TOKEN>/getUpdates` nel browser e leggi
   `message.chat.id`.

### 2. Token Travelpayouts (consigliato)

1. Registrati gratis su [travelpayouts.com](https://www.travelpayouts.com/).
2. Nel pannello: **Profilo → API token**. Copia anche il **marker** affiliato
   se vuoi link tracciati.
3. Senza questo token il bot funziona lo stesso, ma vede solo voli Ryanair
   diretti.

### 3. Configurazione

```bash
cp .env.example .env
# apri .env e compila almeno TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID
```

Tutte le opzioni (aeroporti, soglie, orario, whitelist/blacklist, ecc.) sono
documentate in [`.env.example`](.env.example).

### 4. Avvio in locale

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# test una tantum, senza aspettare lo scheduler:
python search_once.py          # stampa a terminale
python search_once.py --send   # invia anche su Telegram

# avvio del bot vero e proprio (scheduler + comandi):
python main.py
```

## Comandi del bot

Tutte le impostazioni (aeroporti, soglie, liste) sono **personali**: ogni
utente modifica solo le proprie.

| Comando | Effetto |
|---|---|
| `/start` | Richiede l'iscrizione (o riattiva le notifiche dopo /stop) |
| `/stop` | Sospende le notifiche giornaliere |
| `/oggi` | Ricerca immediata e invio offerte. Non marca le offerte come inviate: si può rilanciare senza impoverire il messaggio giornaliero |
| `/aeroporti` | Mostra le due liste di aeroporti di partenza |
| `/aeroporti add TRN` | Aggiunge un aeroporto alla lista Europa |
| `/aeroporti remove BGY` | Rimuove un aeroporto (almeno uno deve restare) |
| `/aeroporti intl add FCO` | Aggiunge un aeroporto alla lista lungo raggio |
| `/aeroporti intl remove VCE` | Rimuove un aeroporto dal lungo raggio |
| `/aeroporti reset` | Riporta entrambe le liste ai default del `.env` |
| `/destinazioni` | Mostra whitelist/blacklist |
| `/destinazioni add LIS` | Aggiunge LIS alla whitelist (vuota = tutte) |
| `/destinazioni remove LIS` | Rimuove dalla whitelist |
| `/destinazioni block TIA` | Esclude una destinazione |
| `/destinazioni unblock TIA` | Riammette una destinazione |
| `/destinazioni reset` | Torna ai valori del `.env` |
| `/soglia` | Mostra le soglie attuali |
| `/soglia europa 70` | Soglia Europa, totale A/R in € |
| `/soglia extra 550` | Soglia extra-Europa, totale A/R in € |
| `/soglia multi 700` | Soglia sul totale di un viaggio a tappe in € |
| `/soglia sconto 30` | Sconto % minimo vs media storica |
| `/help` | Guida |

Comandi riservati all'admin: `/utenti` (elenco iscritti), `/approva CHAT_ID`,
`/rifiuta CHAT_ID`.

Le modifiche fatte via bot sono salvate in SQLite (per utente) e
sopravvivono ai riavvii.

## Deploy

### Docker

```bash
docker build -t flights-radar .
docker run -d --name flights-radar \
  --env-file .env \
  -v flights_data:/app/data \
  --restart unless-stopped \
  flights-radar
```

### VPS (systemd)

```ini
# /etc/systemd/system/flights-radar.service
[Unit]
Description=Telegram Flights Radar
After=network-online.target

[Service]
WorkingDirectory=/opt/flights-radar
ExecStart=/opt/flights-radar/.venv/bin/python main.py
Restart=always
User=flights

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now flights-radar
```

### Deploy su Railway

Il bot è un **worker** (long polling Telegram + scheduler interno): non espone
porte HTTP e non gli serve un dominio pubblico. Su Railway l'healthcheck HTTP è
opzionale (parte solo se configuri un *healthcheck path*): **lascia vuoto il
campo healthcheck** nelle impostazioni del servizio e il deploy va a buon fine
senza endpoint web.

#### Variabili d'ambiente

Da impostare nel pannello Railway (**Service → Variables**), mai caricando il
file `.env`:

| Variabile | Descrizione |
|---|---|
| `TELEGRAM_BOT_TOKEN` | **Obbligatoria.** Token del bot da @BotFather. |
| `TELEGRAM_CHAT_ID` | **Obbligatoria.** ID della chat dell'admin: riceve e approva le richieste di iscrizione. |
| `TRAVELPAYOUTS_TOKEN` | Consigliata. Token API Travelpayouts: abilita scali e altre compagnie (senza → solo Ryanair diretti). |
| `TRAVELPAYOUTS_MARKER` | Opzionale. Marker affiliato per i deep-link Aviasales. |
| `DB_PATH` | Path del database SQLite. Su Railway: `/app/data/flights.db` (è anche il default dell'immagine Docker). |
| `DAILY_TIME` | Orario dell'invio giornaliero, formato `HH:MM` (default `08:00`). |
| `TIMEZONE` | Fuso orario dello scheduler (default `Europe/Rome`). |
| `ORIGIN_AIRPORTS` | Aeroporti per le mete europee, CSV di codici IATA (default `VRN,BGY`). |
| `INTL_ORIGIN_AIRPORTS` | Aeroporti per il lungo raggio — extra-Europa e viaggi a tappe (default `VCE,BGY,MXP`). |
| `SEARCH_DAYS_AHEAD` | Finestra di ricerca: da domani a N giorni avanti (default `45`). |
| `DESTINATIONS_WHITELIST` | Destinazioni ammesse, CSV IATA (vuota = tutte). |
| `DESTINATIONS_BLACKLIST` | Destinazioni escluse, CSV IATA. |
| `MIN_TRIP_NIGHTS` | Notti minime di soggiorno (default `3`). |
| `MAX_TRIP_NIGHTS` | Notti massime di soggiorno (default `10`). |
| `PRICE_THRESHOLD_EUROPE_RT` | Soglia assoluta in € per le mete europee, A/R totale (default `70`). |
| `PRICE_THRESHOLD_EXTRA_RT` | Soglia assoluta in € per le mete extra-Europa, A/R totale (default `900`: vale su voli diretti intercontinentali, sotto i 550€ esistono solo tariffe con scalo). Non tocca le mete europee. |
| `DISCOUNT_THRESHOLD_PCT` | Sconto % minimo rispetto alla media storica della rotta (default `30`). |
| `MIN_HISTORY_SAMPLES` | Rilevazioni minime prima di fidarsi della media storica (default `5`). |
| `TOP_N` | Numero massimo di offerte nel messaggio giornaliero (default `8`). |
| `TOP_N_EXTRA` | Quanti dei `TOP_N` posti sono riservati alle mete extra-Europa (default `2`, quindi 6 Europa + 2 extra). |
| `DIRECT_ONLY` | `true` per proporre solo voli diretti verso Europa/corto raggio (default `true`). |
| `DIRECT_ONLY_EXTRA` | Lo stesso per il lungo raggio (default `true`): attiva la ricerca dedicata delle dirette intercontinentali, +1 chiamata API per aeroporto e mese. |
| `RESEND_COOLDOWN_DAYS` | Giorni prima di re-inviare la stessa offerta, salvo cali >10% (default `3`). |
| `MULTI_ENABLED` | Abilita la sezione viaggi a tappe (default `true`; serve `TRAVELPAYOUTS_TOKEN`). |
| `MULTI_TOP_N` | Itinerari a tappe aggiunti in fondo al messaggio, oltre a `TOP_N` (default `2`). |
| `PRICE_THRESHOLD_MULTI` | Soglia in € sul **totale** dell'itinerario a tappe (default `700`). |
| `MULTI_MIN_STOPS` / `MULTI_MAX_STOPS` | Città intermedie per itinerario, casa esclusa (default `2`-`4`). |
| `MULTI_MIN_STAY_NIGHTS` / `MULTI_MAX_STAY_NIGHTS` | Notti di sosta in ogni città (default `2`-`5`). |
| `MULTI_MAX_TRIP_DAYS` | Durata massima dell'intero viaggio in giorni (default `20`). |
| `MULTI_EXTRA_EUROPE_ONLY` | `true` per accettare come tappe solo mete fuori dall'Europa (default `true`). |
| `MULTI_DIRECT_ONLY` | `true` per accettare solo tratte dirette fra le tappe (default `false`: sulle rotte intercontinentali azzererebbe la resa). |
| `MULTI_BEAM_WIDTH` / `MULTI_CANDIDATES` | Ampiezza della ricerca a tappe: più alti = più itinerari, più chiamate API (default `4` / `6`). |
| `MULTI_MAX_API_CALLS` | Tetto di chiamate API per aeroporto nella ricerca a tappe (default `250`, ~140 in un giro tipico). |

Le variabili opzionali non impostate usano i default qui sopra (gli stessi di
`.env.example`). Aeroporti, soglie e liste sono i **default per ogni nuovo
utente**: le personalizzazioni fatte via comandi bot vengono salvate in SQLite
per utente e **sovrascrivono** questi valori.

#### Passi

1. **Crea il progetto**: dashboard Railway → *New Project* → *Deploy from
   GitHub repo* → seleziona `telegram-flights-radar`. Railway rileva il
   `Dockerfile` e lo usa automaticamente; da qui in poi ogni push su `main` fa
   un auto-deploy.
2. **Imposta le variabili** nella tab *Variables* del servizio (vedi tabella).
   Almeno `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`, più
   `DB_PATH=/app/data/flights.db`.
3. **Crea il volume persistente**: click destro sul servizio (o *⌘K* →
   *Create Volume*) → *Attach volume* → mount path **`/app/data`** (la
   directory che contiene il file indicato da `DB_PATH`). Senza volume il
   filesystem è effimero e lo storico prezzi si azzera a ogni deploy.
4. **Deploy**: parte da solo dopo il collegamento del repo; altrimenti
   *Deployments → Deploy*. Non impostare healthcheck path né generare domini.

#### Verifica post-deploy

Apri i log del servizio (*Deployments → View logs*) e controlla che compaiano:

- la riga dello scheduler: `Ricerca giornaliera pianificata alle 08:00
  (Europe/Rome)` (o l'orario/timezone configurati);
- il polling Telegram attivo (`Application started` di python-telegram-bot),
  senza traceback.

Poi manda **`/oggi`** al bot: nei log vedi partire la ricerca e in chat arriva
il messaggio con le offerte (o "nessuna offerta", ma un messaggio arriva
sempre).

#### Redeploy senza perdere lo storico

Il volume Railway è agganciato al servizio, non al singolo deploy: push su
`main` o *Redeploy* dal pannello **non** lo ricreano, quindi
`price_history`, `sent_offers` e le impostazioni via bot sopravvivono. Per
verificarlo dopo un redeploy: `/soglia` deve mostrare le soglie personalizzate
via bot (se ne avevi impostate) e nei log la ricerca successiva usa le medie
storiche accumulate. Attenzione solo a: **non scollegare/eliminare il volume**,
non cambiare il mount path e non cambiare `DB_PATH` verso un path fuori dal
volume — sono le uniche operazioni che azzerano i dati.

### Render (alternativa)

Su Render il tipo giusto è *Background Worker*, ma sul piano free i worker sono
a pagamento — Railway o un piccolo VPS sono più adatti.

## Struttura del progetto

```
config.py        # lettura .env
airports.py      # città/paese/fascia per codice IATA
storage.py       # SQLite: storico prezzi, dedup invii, impostazioni
flights/
  base.py        # modelli Offer/Leg + interfaccia client
  ryanair.py     # client Ryanair fare finder
  travelpayouts.py  # client Travelpayouts/Aviasales
  multitrip.py   # costruzione itinerari a tappe (beam search su tratte singole)
deals.py         # logica "è un affare?" + ranking + dedup
formatter.py     # formattazione messaggi Telegram (HTML)
bot.py           # comandi /oggi /destinazioni /soglia /help
scheduler.py     # job giornaliero (JobQueue di python-telegram-bot)
main.py          # entry point
search_once.py   # test una tantum
```
