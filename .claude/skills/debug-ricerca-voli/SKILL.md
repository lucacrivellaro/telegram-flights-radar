---
name: debug-ricerca-voli
description: Diagnostica perché il bot non ha trovato/inviato offerte in un dato giorno. Usare quando l'utente segnala "nessuna offerta", "il bot non ha inviato niente", offerte mancanti o sospette, o errori nella ricerca voli.
---

# Debug ricerca voli

Segui i passi in ordine: sono ordinati per probabilità della causa. Fermati al
primo che spiega il sintomo e riporta la diagnosi prima di proporre fix.

## 1. Riproduci subito

```bash
source .venv/bin/activate && python search_once.py
```

Guarda: `Tariffe analizzate` (0 → problema API, passo 3), `Offerte selezionate`
(0 con tariffe > 0 → problema filtri, passo 2), `Errori` (elenca client falliti
con messaggio).

## 2. Filtri: soglie e liste EFFETTIVE (il DB sovrascrive il .env!)

```bash
sqlite3 data/flights.db "SELECT * FROM settings;"
```

- Soglie o whitelist/blacklist qui dentro vincono sul `.env` (impostate via
  `/soglia` e `/destinazioni`). Whitelist non vuota = SOLO quelle destinazioni.
- Dedup: un'offerta già inviata non viene ripetuta per `RESEND_COOLDOWN_DAYS`
  giorni (salvo calo prezzo >10%):
  ```bash
  sqlite3 data/flights.db "SELECT offer_hash, price, sent_at FROM sent_offers ORDER BY sent_at DESC LIMIT 20;"
  ```
- Media storica: attiva solo con ≥ MIN_HISTORY_SAMPLES rilevazioni sulla rotta:
  ```bash
  sqlite3 data/flights.db "SELECT origin, destination, COUNT(*), AVG(price) FROM price_history GROUP BY 1,2 ORDER BY 3 DESC LIMIT 20;"
  ```

## 3. API a mano (isola il client rotto)

Ryanair (nessuna chiave; se fallisce qui, è l'API o un blocco, non il nostro codice):

```bash
curl -s -A "Mozilla/5.0" "https://services-api.ryanair.com/farfnd/v4/oneWayFares?departureAirportIataCode=BGY&outboundDepartureDateFrom=$(date -v+1d +%F)&outboundDepartureDateTo=$(date -v+30d +%F)&currency=EUR" | head -c 500
```

Travelpayouts (token dal `.env`; ricorda: senza token il bot è solo-Ryanair, è
un comportamento previsto, vedi log "TRAVELPAYOUTS_TOKEN assente"):

```bash
curl -s -H "X-Access-Token: $TRAVELPAYOUTS_TOKEN" "https://api.travelpayouts.com/v2/prices/latest?currency=eur&origin=BGY&period_type=year&limit=5" | head -c 500
```

- HTTP 4xx/5xx o HTML al posto di JSON → API cambiata/bloccata: confronta la
  risposta con il parsing in `flights/ryanair.py` / `flights/travelpayouts.py`.
- Travelpayouts `"success": false` → token errato/scaduto.
- Travelpayouts risponde ma il bot scarta tutto → controlla il filtro date
  (`depart_date` deve cadere tra domani e SEARCH_DAYS_AHEAD) e `actual=true`.

## 4. Scheduler (il messaggio delle 08:00 non è arrivato)

- Il processo era vivo a quell'ora? Cerca nei log: `Job giornaliero avviato`,
  `Ricerca giornaliera pianificata alle`.
- `TIMEZONE`/`DAILY_TIME` nel `.env` corretti? In Docker la tz del container è
  irrilevante: fa fede `TIMEZONE`.
- Anche a zero offerte il bot DEVE inviare un messaggio ("nessuna offerta sotto
  soglia"): se non è arrivato nulla, il processo era morto o Telegram ha
  fallito → cerca `Job giornaliero fallito` / `Impossibile inviare`.

## 5. Dopo il fix

Verifica sempre con `python search_once.py` e riporta all'utente causa e
soluzione. Se hai scoperto una limitazione nuova (rate limit, campo API
cambiato), aggiorna la sezione "Stato noto" di CLAUDE.md.
