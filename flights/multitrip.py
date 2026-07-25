"""Costruzione di itinerari multitratta: più tappe con sosta di più giorni.

Nessuna API gratuita vende itinerari multi-city già pronti, quindi qui
l'itinerario viene *composto*: si concatenano tratte di sola andata, ognuna
prezzata separatamente, e si somma il prezzo. Ogni tratta resta un biglietto
a sé (il link di prenotazione è per tratta, non per l'itinerario intero).

Due endpoint Travelpayouts, con ruoli diversi:

  1. `_discover()` → `/v2/prices/latest` (una chiamata per città+mese, ~200-500
     rotte): dice *quali* destinazioni sono economiche da una città. Serve solo
     a scegliere i candidati da approfondire, le sue date non sono vincolanti.
  2. `_calendar()` → `/aviasales/v3/grouped_prices` (una chiamata per rotta+mese):
     dà il prezzo giorno per giorno di una rotta precisa. È questo che permette
     di agganciare la tappa successiva alla data di arrivo della precedente,
     rispettando la sosta di N notti.

La ricerca è una beam search sulle tappe: si parte da casa, si tengono in vita
le `beam_width` catene parziali più economiche, si espandono con `candidates`
destinazioni ciascuna, e a ogni livello ≥ `min_stops` si prova a chiudere
l'anello con il volo di rientro.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import httpx

from airports import info, is_short_haul, same_metro
from flights.base import Leg, Offer

logger = logging.getLogger(__name__)

_LATEST = "https://api.travelpayouts.com/v2/prices/latest"
_GROUPED = "https://api.travelpayouts.com/aviasales/v3/grouped_prices"


@dataclass
class _State:
    """Una catena parziale: sono a `city` dal giorno `arrival`, spesi `price`."""

    city: str
    arrival: date
    price: float
    legs: list[Leg] = field(default_factory=list)
    visited: frozenset[str] = frozenset()


class MultiTripBuilder:
    name = "multitratta"

    def __init__(
        self,
        token: str,
        marker: str = "",
        *,
        min_stops: int = 2,
        max_stops: int = 4,
        min_stay: int = 2,
        max_trip_days: int = 20,
        min_trip_days: int = 0,
        beam_width: int = 4,
        candidates: int = 6,
        direct_only: bool = True,
        extra_europe_only: bool = True,
        max_api_calls: int = 150,
        max_workers: int = 6,
    ):
        self.token = token
        self.marker = marker
        self.min_stops = max(1, min_stops)
        self.max_stops = max(self.min_stops, max_stops)
        self.min_stay = min_stay
        self.max_trip_days = max_trip_days
        self.min_trip_days = min_trip_days
        self.beam_width = beam_width
        self.candidates = candidates
        self.direct_only = direct_only
        self.extra_europe_only = extra_europe_only
        self.max_api_calls = max_api_calls
        self.max_workers = max_workers
        # tetto effettivo di chiamate: `build()` lo scala col numero di tappe
        # richieste, perché ognuna è una ricerca a sé
        self._budget = max_api_calls
        # quante città sondare per trovarne alcune con un rientro reale
        self._verify_budget = 12

        self._lock = threading.Lock()
        self._discover_cache: dict[tuple[str, str], dict[str, float]] = {}
        self._calendar_cache: dict[tuple[str, str, str], dict[date, dict]] = {}
        self._calls = 0
        self._budget_hit = False
        self._home_routes: set[str] = set()

    # --- API ----------------------------------------------------------------

    def _get(self, url: str, params: dict) -> dict:
        """GET con contabilità del budget di chiamate. Ritorna {} se esaurito."""
        with self._lock:
            if self._calls >= self._budget:
                if not self._budget_hit:
                    logger.warning(
                        "Multitratta: budget di %d chiamate API esaurito, "
                        "la ricerca si ferma qui (itinerari possibili non esplorati)",
                        self._budget,
                    )
                    self._budget_hit = True
                return {}
            self._calls += 1
        resp = httpx.get(
            url,
            params=params,
            headers={"X-Access-Token": self.token, "Accept-Encoding": "gzip"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def _discover(self, city: str, months: list[str]) -> dict[str, float]:
        """Destinazioni economiche da `city` nei mesi dati → {IATA: prezzo}.

        I prezzi sono indicativi (cache Travelpayouts, date non allineate alla
        nostra sosta): servono solo a ordinare i candidati."""
        found: dict[str, float] = {}
        for month in months:
            key = (city, month)
            with self._lock:
                cached = self._discover_cache.get(key)
            if cached is None:
                try:
                    payload = self._get(
                        _LATEST,
                        {
                            "currency": "eur",
                            "origin": city,
                            "period_type": "month",
                            "beginning_of_period": month,
                            "limit": 1000,
                            "sorting": "price",
                            "one_way": "true",
                        },
                    )
                except Exception as exc:  # noqa: BLE001 - una città muta non ferma la ricerca
                    logger.warning("Multitratta: scoperta da %s fallita: %s", city, exc)
                    payload = {}
                cached = {}
                for row in payload.get("data", []) or []:
                    dest = row.get("destination")
                    price = row.get("value")
                    if not dest or price is None or dest == city:
                        continue
                    if self.direct_only and int(row.get("number_of_changes") or 0) > 0:
                        continue
                    price = float(price)
                    if dest not in cached or price < cached[dest]:
                        cached[dest] = price
                with self._lock:
                    self._discover_cache[key] = cached
            for dest, price in cached.items():
                if dest not in found or price < found[dest]:
                    found[dest] = price
        return found

    def _calendar(self, origin: str, dest: str, months: list[str]) -> dict[date, dict]:
        """Prezzo giorno per giorno della rotta → {data partenza: riga API}."""
        calendar: dict[date, dict] = {}
        for month in months:
            key = (origin, dest, month)
            with self._lock:
                cached = self._calendar_cache.get(key)
            if cached is None:
                params = {
                    "origin": origin,
                    "destination": dest,
                    "departure_at": month[:7],
                    "currency": "eur",
                    "one_way": "true",
                    "group_by": "departure_at",
                    "token": self.token,
                }
                if self.direct_only:
                    params["direct"] = "true"
                try:
                    payload = self._get(_GROUPED, params)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Multitratta: calendario %s→%s ko: %s", origin, dest, exc)
                    payload = {}
                cached = {}
                for day, row in (payload.get("data") or {}).items():
                    parsed = _parse_date(day)
                    if parsed and row.get("price") is not None:
                        cached[parsed] = row
                with self._lock:
                    self._calendar_cache[key] = cached
            calendar.update(cached)
        return calendar

    # --- costruzione tratte --------------------------------------------------

    def _best_leg(
        self, origin: str, dest: str, win_start: date, win_end: date, nights: int = 0
    ) -> Leg | None:
        """La partenza più economica sulla rotta, con data nella finestra."""
        if win_start > win_end:
            return None
        calendar = self._calendar(origin, dest, _months(win_start, win_end))
        best_day, best_row = None, None
        for day, row in calendar.items():
            if not (win_start <= day <= win_end):
                continue
            if self.direct_only and int(row.get("transfers") or 0) > 0:
                continue
            if best_row is None or row["price"] < best_row["price"]:
                best_day, best_row = day, row
        if best_row is None:
            return None
        city, country = info(dest)
        return Leg(
            origin=origin,
            destination=dest,
            dest_city=city,
            dest_country=country,
            price=float(best_row["price"]),
            depart_date=best_day,
            airline=best_row.get("airline") or "varie",
            stops=int(best_row.get("transfers") or 0),
            duration_minutes=best_row.get("duration") or None,
            link=self._booking_link(origin, dest, best_day),
            nights=nights,
        )

    def _with_return_home(
        self, cities: list[str], home: str, win: tuple[date, date], want: int
    ) -> list[str]:
        """Fra `cities`, quelle da cui risulta davvero un rientro verso casa.

        `_home_routes` non basta: dice che *casa* vola verso quella città
        (cache dei prezzi minimi, una data sola), ma il calendario del rientro
        su una finestra di pochi giorni è spesso vuoto. Da New York le mete più
        economiche sono Orlando e Dallas, che verso Milano non hanno rientro;
        Miami e Boston sì, ma stanno più in basso nella lista per prezzo.
        Senza questo controllo la catena cresce e poi non si chiude."""
        months = _months(win[0], win[1] + timedelta(days=self.max_trip_days))
        # prefetch in parallelo: i calendari finiscono in cache e le verifiche
        # qui sotto (e la chiusura vera) non ripagano la latenza di rete
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            list(pool.map(lambda c: self._calendar(c, home, months), cities))
        found = []
        for city in cities:
            if self._calendar(city, home, months):
                found.append(city)
                if len(found) >= want:
                    break
        return found

    def _acceptable(self, dest: str, visited: frozenset[str], home: str) -> bool:
        """Filtro sulle tappe candidate: mete già viste, area di casa e — se
        `extra_europe_only` — tutto ciò che è in fascia Europa/corto raggio."""
        if self.extra_europe_only and is_short_haul(dest):
            return False
        return _is_new_place(dest, visited, home)

    def _stay_window(self, state: _State, closing: bool) -> tuple[date, date]:
        """Finestra di ripartenza da una tappa: sosta min-max notti, senza
        sforare il tetto di giorni totali (lasciando spazio al rientro)."""
        first = state.legs[0].depart_date
        start = state.arrival + timedelta(days=self.min_stay)
        if closing:
            end = first + timedelta(days=self.max_trip_days)
            if self.min_trip_days:
                # il rientro non può essere prima della durata minima: si cerca
                # direttamente un volo più in là invece di scartare dopo
                start = max(start, first + timedelta(days=self.min_trip_days))
        else:
            # nessun tetto per singola sosta: la durata di una tappa è limitata
            # solo dai giorni totali. Vanno però riservate le notti minime per
            # le tappe ancora necessarie e per il rientro, altrimenti una prima
            # sosta lunghissima consuma il budget e l'anello non si chiude più
            mancanti = max(0, self.min_stops - len(state.legs))
            riserva = (mancanti + 1) * self.min_stay
            end = first + timedelta(days=self.max_trip_days - riserva)
        return start, end

    # --- beam search ----------------------------------------------------------

    def build(
        self,
        origin: str,
        date_from: date,
        date_to: date,
        required: list[str] | tuple[str, ...] = (),
    ) -> list[Offer]:
        """Itinerari multitratta che partono da `origin` e vi fanno ritorno.

        `required` sono tappe obbligatorie: ogni itinerario prodotto le tocca
        tutte. Non è un filtro applicato alla fine — sarebbe inutile, perché la
        beam search insegue le catene più economiche e a New York non
        arriverebbe mai. Il vincolo entra nella ricerca: la prima tappa è
        sempre una delle obbligatorie, e le altre vengono forzate quando i
        posti rimasti bastano appena a coprirle."""
        required = [
            r.upper() for r in required if not same_metro(r, origin)
        ]
        self._calls = 0
        self._budget_hit = False
        # ogni città richiesta è una ricerca a sé, quindi il budget cresce con
        # loro: in OR aggiungere una meta deve dare più possibilità, non meno
        self._budget = self.max_api_calls * max(1, len(required))
        # le città con un collegamento diretto da casa: le rotte sono quasi
        # sempre bidirezionali, quindi è anche l'insieme da cui si può tornare.
        # Senza questo l'anello non si chiude: le catene più economiche finiscono
        # in città che verso un aeroporto piccolo (VRN, BGY) non volano.
        self._home_routes = set(self._discover(origin, _months(date_from, date_to)))

        # una ricerca indipendente per ogni città richiesta. Con un beam solo,
        # le catene verso la meta più economica scaccerebbero le altre e
        # aggiungere una città *ridurrebbe* i risultati invece di aumentarli —
        # l'opposto di quello che ci si aspetta da un OR. Le cache di scoperta
        # e calendari sono condivise, quindi il costo reale non si moltiplica.
        itineraries: list[Offer] = []
        for vincolo in [[c] for c in required] or [[]]:
            itineraries.extend(
                self._search_chains(origin, date_from, date_to, vincolo)
            )

        itineraries.sort(key=lambda o: o.price)
        logger.info(
            "Multitratta da %s%s: %d itinerari costruiti in %d chiamate API",
            origin,
            f" (tappe obbligatorie: {', '.join(required)})" if required else "",
            len(itineraries),
            self._calls,
        )
        return _dedupe_by_route(itineraries)

    def _search_chains(
        self, origin: str, date_from: date, date_to: date, required: list[str]
    ) -> list[Offer]:
        """Una beam search completa, con al più una tappa imposta."""
        states = self._first_hops(origin, date_from, date_to, required)
        if not states:
            logger.info(
                "Multitratta da %s: nessuna prima tappa utilizzabile%s",
                origin,
                f" verso {', '.join(required)}" if required else "",
            )
            return []

        found: list[Offer] = []
        while states:
            depth = len(states[0].legs)
            if depth >= self.min_stops:
                found.extend(self._close(states, origin, required))
            if depth >= self.max_stops:
                break
            states = self._next_level(states, origin, required)
        return found

    def _first_hops(
        self, origin: str, date_from: date, date_to: date, required: list[str]
    ) -> list[_State]:
        """Livello 1: le prime tappe raggiungibili da casa.

        Con tappe obbligatorie la prima è per forza una di quelle: partire
        altrove le farebbe scartare da `_prune`, che ordina per prezzo, e un
        volo per New York non compete con uno per Tirana. Valendo in OR, questo
        basta a soddisfare il vincolo per l'intera catena."""
        if required:
            shortlist = required
        else:
            candidates = self._discover(origin, _months(date_from, date_to))
            shortlist = [
                d
                for d in sorted(candidates, key=candidates.get)
                if self._acceptable(d, frozenset(), origin)
            ][: self.candidates * 2]
        seed = _State(city=origin, arrival=date_from, price=0.0, visited=frozenset({origin}))

        states: list[_State] = []
        for dest, leg in self._parallel(
            (dest, origin, dest, date_from, date_to) for dest in shortlist
        ):
            if leg is None:
                continue
            states.append(
                _State(
                    city=dest,
                    arrival=leg.depart_date,
                    price=leg.price,
                    legs=[leg],
                    visited=seed.visited | {dest},
                )
            )
        states.sort(key=lambda s: s.price)
        if required:
            # valendo in OR, ogni città richiesta merita una catena: con il beam
            # normale (4) la meta più economica si prenderebbe tutti i posti e
            # le altre non verrebbero mai esplorate. Tetto a 6 per non far
            # esplodere il costo delle espansioni successive
            return states[: max(self.beam_width, min(len(states), 6))]
        return states[: self.beam_width]

    def _next_level(
        self, states: list[_State], home: str, required: list[str]
    ) -> list[_State]:
        """Espande ogni catena con le destinazioni candidate dalla sua tappa."""
        jobs: list[tuple] = []
        owners: dict[int, _State] = {}
        for state in states:
            win = self._stay_window(state, closing=False)
            if win[0] > win[1]:
                continue
            # nessun vincolo da propagare: le tappe obbligatorie valgono in OR
            # e `_first_hops` ne ha già messa una all'inizio di ogni catena
            candidates = self._discover(state.city, _months(*win))
            pool = [
                d
                for d in sorted(candidates, key=candidates.get)
                if self._acceptable(d, state.visited, home)
            ]
            # metà dei posti riservata alle città da cui si può tornare a
            # casa, altrimenti la catena cresce verso vicoli ciechi
            connected = [d for d in pool if d in self._home_routes]
            quota = max(1, self.candidates // 2)
            if required:
                # con una tappa imposta la catena è trascinata lontano da
                # casa: qui il rientro non si può dare per scontato, va
                # verificato o non si chiude nulla
                connected = self._with_return_home(
                    connected[: self._verify_budget], home, win, quota
                )
            shortlist = list(dict.fromkeys(connected[:quota] + pool))[
                : self.candidates
            ]
            for dest in shortlist:
                jobs.append((len(jobs), state.city, dest, win[0], win[1]))
                owners[len(jobs) - 1] = state

        grown: list[_State] = []
        for job_id, leg in self._parallel(jobs):
            if leg is None:
                continue
            parent = owners[job_id]
            nights = (leg.depart_date - parent.arrival).days
            legs = [*(_copy_legs(parent.legs)), leg]
            legs[-2].nights = nights
            grown.append(
                _State(
                    city=leg.destination,
                    arrival=leg.depart_date,
                    price=parent.price + leg.price,
                    legs=legs,
                    visited=parent.visited | {leg.destination},
                )
            )
        return _prune(grown, self.beam_width)

    def _close(
        self, states: list[_State], home: str, required: list[str]
    ) -> list[Offer]:
        """Chiude l'anello: volo di rientro a casa dall'ultima tappa."""
        jobs, owners = [], {}
        for state in states:
            if not _satisfies(state, required):
                continue  # non tocca nessuna tappa obbligatoria
            win = self._stay_window(state, closing=True)
            if win[0] > win[1]:
                continue
            jobs.append((len(jobs), state.city, home, win[0], win[1]))
            owners[len(jobs) - 1] = state

        offers: list[Offer] = []
        for job_id, leg in self._parallel(jobs):
            if leg is None:
                continue
            state = owners[job_id]
            legs = _copy_legs(state.legs)
            legs[-1].nights = (leg.depart_date - state.arrival).days
            legs.append(leg)
            offers.append(self._to_offer(home, legs))
        return offers

    def _parallel(self, jobs) -> list[tuple]:
        """Esegue in parallelo le ricerche di tratta: [(job_id, Leg|None)]."""
        jobs = list(jobs)
        if not jobs:
            return []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(self._best_leg, orig, dest, start, end): job_id
                for job_id, orig, dest, start, end in jobs
            }
            out = []
            for future, job_id in futures.items():
                try:
                    out.append((job_id, future.result()))
                except Exception as exc:  # noqa: BLE001 - una tratta ko non ferma le altre
                    logger.debug("Multitratta: tratta fallita: %s", exc)
                    out.append((job_id, None))
        return out

    def _to_offer(self, home: str, legs: list[Leg]) -> Offer:
        last_stop = legs[-2] if len(legs) >= 2 else legs[0]
        durations = [leg.duration_minutes for leg in legs]
        return Offer(
            origin=home,
            destination=last_stop.destination,
            dest_city=last_stop.dest_city,
            dest_country=last_stop.dest_country,
            price=round(sum(leg.price for leg in legs), 2),
            depart_date=legs[0].depart_date,
            return_date=legs[-1].depart_date,
            airline=", ".join(dict.fromkeys(leg.airline for leg in legs)),
            stops=sum(leg.stops for leg in legs),
            duration_minutes=sum(durations) if all(durations) else None,
            link=legs[0].link,
            source=self.name,
            legs=legs,
        )

    def _booking_link(self, origin: str, dest: str, depart: date) -> str:
        url = f"https://www.aviasales.com/search/{origin}{depart.strftime('%d%m')}{dest}1"
        if self.marker:
            url += f"?marker={self.marker}"
        return url


# --- helper ------------------------------------------------------------------


def _months(date_from: date, date_to: date) -> list[str]:
    """I primi giorni dei mesi coperti dall'intervallo, formato YYYY-MM-01."""
    months, cursor = [], date_from.replace(day=1)
    last = date_to.replace(day=1)
    while cursor <= last:
        months.append(cursor.isoformat())
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    return months


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _satisfies(state: _State, required: list[str]) -> bool:
    """Se la catena tocca almeno una delle tappe obbligatorie.

    Più tappe valgono in OR, non in AND: chiedere New York *e* Bangkok nello
    stesso viaggio non avrebbe quasi mai soluzione, mentre in OR ogni città
    aggiunta allarga le possibilità invece di restringerle.

    Il confronto è per area metropolitana: chi chiede NYC è servito da un
    itinerario che passa da JFK o EWR."""
    if not required:
        return True
    return any(
        any(same_metro(r, seen) for seen in state.visited) for r in required
    )


def _is_new_place(dest: str, visited: frozenset[str], home: str) -> bool:
    """Scarta le tappe già toccate e quelle nell'area metropolitana di casa:
    partendo da BGY una "tappa" a MIL non è un viaggio."""
    if same_metro(dest, home):
        return False
    return not any(same_metro(dest, seen) for seen in visited)


def _copy_legs(legs: list[Leg]) -> list[Leg]:
    """Copia difensiva: le catene parziali condividono il prefisso e ogni
    ramo scrive il proprio `nights` sull'ultima tappa."""
    return [Leg(**vars(leg)) for leg in legs]


def _prune(states: list[_State], width: int) -> list[_State]:
    """Tiene le catene più economiche, max 2 per tappa di provenienza: senza
    questo limite il beam si riempirebbe di varianti della stessa città."""
    per_parent: dict[str, int] = {}
    kept: list[_State] = []
    for state in sorted(states, key=lambda s: s.price):
        parent = state.legs[-1].origin
        if per_parent.get(parent, 0) >= 2:
            continue
        per_parent[parent] = per_parent.get(parent, 0) + 1
        kept.append(state)
        if len(kept) >= width:
            break
    return kept


def _dedupe_by_route(offers: list[Offer]) -> list[Offer]:
    """Un solo itinerario per *insieme* di città, il più economico.

    Volutamente sull'insieme e non sulla sequenza: A→B e B→A sono lo stesso
    viaggio agli occhi di chi legge, e proporli entrambi sprecherebbe i pochi
    posti disponibili nel messaggio."""
    seen: set[frozenset[str]] = set()
    unique = []
    for offer in sorted(offers, key=lambda o: o.price):
        route = frozenset(leg.destination for leg in offer.stopovers)
        if route in seen:
            continue
        seen.add(route)
        unique.append(offer)
    return unique
