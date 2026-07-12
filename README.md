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
- Un'offerta è segnalata se il prezzo è **sotto la soglia assoluta** della sua
  fascia (Europa / extra-Europa) **oppure** sotto la media storica della rotta
  di almeno il `DISCOUNT_THRESHOLD_PCT`% (la media si costruisce da sola nel
  database SQLite, giorno dopo giorno).
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
| `/soglia europa 45` | Soglia assoluta Europa in € |
| `/soglia extra 250` | Soglia assoluta extra-Europa in € |
| `/soglia sconto 30` | Sconto % minimo vs media storica |
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

### Railway / Render

- Il bot è un **worker** (long polling), non un web service: su Railway crea un
  servizio dal repo e basta; su Render scegli il tipo *Background Worker*
  (il piano free di Render sospende i web service, ma i worker sono a
  pagamento — Railway o un piccolo VPS sono più adatti).
- Imposta le variabili d'ambiente dal pannello (non caricare il `.env`).
- Monta un volume/disco persistente su `data/` per non perdere lo storico
  prezzi a ogni deploy.

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
