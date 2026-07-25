"""Client per la Data API di Travelpayouts/Aviasales.

Restituisce prezzi *cached* (rilevati dalle ricerche recenti degli utenti
Aviasales) per tutte le compagnie, inclusi itinerari con scali. Il numero di
scali è disponibile, gli aeroporti di scalo no: è un limite del piano gratuito.

Due ricerche A/R con endpoint diversi, e la differenza conta:

  • `search_round_trip()` → `/v2/prices/latest`: una riga per destinazione, la
    *più economica*. Ottimo per coprire tante mete, ma sul lungo raggio la più
    economica è sempre con scalo, quindi le dirette restano invisibili.
  • `search_round_trip_direct()` → `/aviasales/v3/prices_for_dates` con
    `direct=true`: più righe per destinazione, solo voli senza scalo. È l'unico
    modo di far emergere un MXP→New York diretto.
"""

import logging
from datetime import date, datetime, timedelta

import httpx

from airports import info
from flights.base import Offer

logger = logging.getLogger(__name__)

_URL = "https://api.travelpayouts.com/v2/prices/latest"
_URL_DATES = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


class TravelpayoutsClient:
    name = "travelpayouts"

    def __init__(self, token: str, marker: str = ""):
        self.token = token
        self.marker = marker

    def search_round_trip(
        self,
        origin: str,
        date_from: date,
        date_to: date,
        min_nights: int,
        max_nights: int,
    ) -> list[Offer]:
        offers = self._search(origin, date_from, date_to, one_way=False)
        return [
            o
            for o in offers
            if o.nights is not None and min_nights <= o.nights <= max_nights
        ]

    def search_round_trip_direct(
        self,
        origin: str,
        date_from: date,
        date_to: date,
        min_nights: int,
        max_nights: int,
    ) -> list[Offer]:
        """Tariffe A/R **senza scali** da `origin`, una chiamata per mese.

        Serve per il lungo raggio: `/v2/prices/latest` mostra solo la tariffa
        più economica per destinazione, che intercontinentale è sempre con
        scalo — le dirette esistono ma non compaiono mai finché non si passa
        `direct=true`."""
        offers: list[Offer] = []
        for month in _months(date_from, date_to):
            params = {
                "origin": origin,
                "departure_at": month,
                "return_at": month,
                "currency": "eur",
                "one_way": "false",
                "direct": "true",
                "limit": 1000,
                "sorting": "price",
                "token": self.token,
            }
            resp = httpx.get(
                _URL_DATES,
                params=params,
                headers={"X-Access-Token": self.token, "Accept-Encoding": "gzip"},
                timeout=30,
            )
            resp.raise_for_status()
            payload = resp.json()
            if not payload.get("success", True):
                raise RuntimeError(f"Travelpayouts error: {payload.get('error')}")

            for row in payload.get("data", []) or []:
                depart = _parse_date(row.get("departure_at"))
                ret = _parse_date(row.get("return_at"))
                dest = row.get("destination")
                price = row.get("price")
                if depart is None or ret is None or not dest or price is None:
                    continue
                if not (date_from <= depart <= date_to):
                    continue
                nights = (ret - depart).days
                if not (min_nights <= nights <= max_nights):
                    continue
                # `direct=true` filtra l'andata: il ritorno va controllato a mano
                if int(row.get("transfers") or 0) or int(row.get("return_transfers") or 0):
                    continue
                city, country = info(dest)
                offers.append(
                    Offer(
                        origin=origin,
                        destination=dest,
                        dest_city=city,
                        dest_country=country,
                        price=float(price),
                        depart_date=depart,
                        return_date=ret,
                        airline=row.get("airline") or "varie",
                        stops=0,
                        duration_minutes=row.get("duration") or None,
                        link=self._booking_link(origin, dest, depart, ret),
                        source=self.name,
                    )
                )
        logger.info("Travelpayouts %s (A/R diretti): %d tariffe", origin, len(offers))
        return offers

    def _search(
        self, origin: str, date_from: date, date_to: date, one_way: bool
    ) -> list[Offer]:
        params = {
            "currency": "eur",
            "origin": origin,
            "period_type": "year",
            "limit": 1000,
            "sorting": "price",
            "one_way": "true" if one_way else "false",
        }
        resp = httpx.get(
            _URL,
            params=params,
            headers={"X-Access-Token": self.token, "Accept-Encoding": "gzip"},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("success", False):
            raise RuntimeError(f"Travelpayouts error: {payload.get('error')}")
        rows = payload.get("data", [])
        logger.info(
            "Travelpayouts %s (%s): %d prezzi in cache",
            origin,
            "solo andata" if one_way else "A/R",
            len(rows),
        )

        offers = []
        for row in rows:
            if not row.get("actual", True):
                continue
            depart = _parse_date(row.get("depart_date"))
            if depart is None or not (date_from <= depart <= date_to):
                continue
            dest = row.get("destination")
            price = row.get("value")
            if not dest or price is None:
                continue
            ret = _parse_date(row.get("return_date"))
            city, country = info(dest)
            stops = int(row.get("number_of_changes") or 0)
            offers.append(
                Offer(
                    origin=origin,
                    destination=dest,
                    dest_city=city,
                    dest_country=country,
                    price=float(price),
                    depart_date=depart,
                    return_date=ret,
                    airline=row.get("gate") or "varie",
                    stops=stops,
                    duration_minutes=row.get("duration") or None,
                    link=self._booking_link(origin, dest, depart, ret),
                    source=self.name,
                )
            )
        return offers

    def _booking_link(
        self, origin: str, dest: str, depart: date, ret: date | None
    ) -> str:
        # Formato deep-link Aviasales: ORIG + DDMM + DEST [+ DDMM ritorno] + n. adulti
        route = f"{origin}{depart.strftime('%d%m')}{dest}"
        if ret:
            route += ret.strftime("%d%m")
        url = f"https://www.aviasales.com/search/{route}1"
        if self.marker:
            url += f"?marker={self.marker}"
        return url


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _months(date_from: date, date_to: date) -> list[str]:
    """I mesi coperti dall'intervallo, formato YYYY-MM."""
    months, cursor = [], date_from.replace(day=1)
    last = date_to.replace(day=1)
    while cursor <= last:
        months.append(cursor.strftime("%Y-%m"))
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    return months
