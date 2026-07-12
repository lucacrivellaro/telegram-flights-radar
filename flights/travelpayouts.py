"""Client per la Data API di Travelpayouts/Aviasales.

Restituisce prezzi *cached* (rilevati dalle ricerche recenti degli utenti
Aviasales) per tutte le compagnie, inclusi itinerari con scali. Il numero di
scali è disponibile, gli aeroporti di scalo no: è un limite del piano gratuito.
"""

import logging
from datetime import date, datetime

import httpx

from airports import info
from flights.base import Offer

logger = logging.getLogger(__name__)

_URL = "https://api.travelpayouts.com/v2/prices/latest"


class TravelpayoutsClient:
    name = "travelpayouts"

    def __init__(self, token: str, marker: str = ""):
        self.token = token
        self.marker = marker

    def search(self, origin: str, date_from: date, date_to: date) -> list[Offer]:
        params = {
            "currency": "eur",
            "origin": origin,
            "period_type": "year",
            "limit": 1000,
            "sorting": "price",
            "one_way": "false",
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
        logger.info("Travelpayouts %s: %d prezzi in cache", origin, len(rows))

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
