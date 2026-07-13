# ✈️ Telegram Flights Radar

Bot Telegram che ogni giorno cerca voli economici in partenza da **Verona (VRN)**
e **Bergamo (BGY)** verso destinazioni flessibili e invia in chat le migliori
offerte, con link di prenotazione.

## Come funziona

- **Ryanair fare finder** (API pubblica non ufficiale, senza chiave): prezzi
  live dei voli diretti Ryanair — copre benissimo VRN e BGY.
- **Travelpayouts/Aviasales Data API** (gratuita, con token): prezzi in cache
  di tutte le compagnie, inclusi itinerari con 1-2 scali. Nota: l'API gratuita
  indica il *numero* di scali e la durata totale, ma non gli aeroporti di scalo.
- Il bot cerca sia **sola andata** sia **andata/ritorno** (soggiorni da
  `MIN_TRIP_NIGHTS` a `MAX_TRIP_NIGHTS` notti, default 3-10): per gli A/R conta
  il prezzo totale della combinazione, e la stessa destinazione può comparire
  nel messaggio con entrambe le tipologie.
- Un'offerta è segnalata se il prezzo è **sotto la soglia assoluta** della sua
  fascia (Europa / extra-Europa, con soglie separate per sola andata e A/R)
  **oppure** sotto la media storica della rotta di almeno il
  `DISCOUNT_THRESHOLD_PCT`% (la media si costruisce da sola nel database
  SQLite, giorno dopo giorno, separatamente per sola andata e A/R).
- Le offerte già inviate non vengono ripetute per `RESEND_COOLDOWN_DAYS` giorni,
  a meno che il prezzo non cali di oltre il 10%.

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

| Comando | Effetto |
|---|---|
| `/oggi` | Ricerca immediata e invio offerte |
| `/destinazioni` | Mostra whitelist/blacklist |
| `/destinazioni add LIS` | Aggiunge LIS alla whitelist (vuota = tutte) |
| `/destinazioni remove LIS` | Rimuove dalla whitelist |
| `/destinazioni block TIA` | Esclude una destinazione |
| `/destinazioni unblock TIA` | Riammette una destinazione |
| `/destinazioni reset` | Torna ai valori del `.env` |
| `/soglia` | Mostra le soglie attuali |
| `/soglia europa 45` | Soglia assoluta Europa sola andata in € |
| `/soglia europa_ar 70` | Soglia assoluta Europa andata/ritorno in € |
| `/soglia extra 250` | Soglia assoluta extra-Europa sola andata in € |
| `/soglia extra_ar 500` | Soglia assoluta extra-Europa andata/ritorno in € |
| `/soglia sconto 30` | Sconto % minimo vs media storica |
| `/soglia peso_ar 0.75` | Peso delle A/R nel ranking (1 = neutro, <1 = favorite) |
| `/help` | Guida |

Le modifiche fatte via bot sono salvate in SQLite e sopravvivono ai riavvii.

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
| `TELEGRAM_CHAT_ID` | **Obbligatoria.** ID della chat che riceve le offerte. |
| `TRAVELPAYOUTS_TOKEN` | Consigliata. Token API Travelpayouts: abilita scali e altre compagnie (senza → solo Ryanair diretti). |
| `TRAVELPAYOUTS_MARKER` | Opzionale. Marker affiliato per i deep-link Aviasales. |
| `DB_PATH` | Path del database SQLite. Su Railway: `/app/data/flights.db` (è anche il default dell'immagine Docker). |
| `DAILY_TIME` | Orario dell'invio giornaliero, formato `HH:MM` (default `08:00`). |
| `TIMEZONE` | Fuso orario dello scheduler (default `Europe/Rome`). |
| `ORIGIN_AIRPORTS` | Aeroporti di partenza, CSV di codici IATA (default `VRN,BGY`). |
| `SEARCH_DAYS_AHEAD` | Finestra di ricerca: da domani a N giorni avanti (default `45`). |
| `DESTINATIONS_WHITELIST` | Destinazioni ammesse, CSV IATA (vuota = tutte). |
| `DESTINATIONS_BLACKLIST` | Destinazioni escluse, CSV IATA. |
| `MIN_TRIP_NIGHTS` | Notti minime di soggiorno per le ricerche A/R (default `3`). |
| `MAX_TRIP_NIGHTS` | Notti massime di soggiorno per le ricerche A/R (default `10`). |
| `PRICE_THRESHOLD_EUROPE` | Soglia assoluta in € per le mete europee, sola andata (default `40`). |
| `PRICE_THRESHOLD_EXTRA` | Soglia assoluta in € per le mete extra-Europa, sola andata (default `300`). |
| `PRICE_THRESHOLD_EUROPE_RT` | Soglia assoluta in € per le mete europee, A/R totale (default `70`). |
| `PRICE_THRESHOLD_EXTRA_RT` | Soglia assoluta in € per le mete extra-Europa, A/R totale (default `550`). |
| `RT_SCORE_WEIGHT` | Peso delle A/R nel ranking: 1 = neutro, <1 = favorite (default `0.75`). |
| `DISCOUNT_THRESHOLD_PCT` | Sconto % minimo rispetto alla media storica della rotta (default `30`). |
| `MIN_HISTORY_SAMPLES` | Rilevazioni minime prima di fidarsi della media storica (default `5`). |
| `TOP_N` | Numero massimo di offerte nel messaggio giornaliero (default `8`). |
| `RESEND_COOLDOWN_DAYS` | Giorni prima di re-inviare la stessa offerta, salvo cali >10% (default `3`). |

Le variabili opzionali non impostate usano i default qui sopra (gli stessi di
`.env.example`). Ricorda che soglie e liste modificate via comandi bot vengono
salvate in SQLite e **sovrascrivono** questi valori.

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
  base.py        # modello Offer + interfaccia client
  ryanair.py     # client Ryanair fare finder
  travelpayouts.py  # client Travelpayouts/Aviasales
deals.py         # logica "è un affare?" + ranking + dedup
formatter.py     # formattazione messaggi Telegram (HTML)
bot.py           # comandi /oggi /destinazioni /soglia /help
scheduler.py     # job giornaliero (JobQueue di python-telegram-bot)
main.py          # entry point
search_once.py   # test una tantum
```
