"""Client per il fare finder Ryanair (API non ufficiale, senza chiave).

Copre solo voli diretti Ryanair, ma con prezzi live: ideale per VRN e BGY.
"""

import logging
from datetime import date, datetime, timedelta

import httpx

from airports import info
from flights.base import Offer

logger = logging.getLogger(__name__)

_URL = "https://services-api.ryanair.com/farfnd/v4/oneWayFares"
_URL_RT = "https://services-api.ryanair.com/farfnd/v4/roundTripFares"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


class RyanairClient:
    name = "ryanair"

    def search(self, origin: str, date_from: date, date_to: date) -> list[Offer]:
        params = {
            "departureAirportIataCode": origin,
            "outboundDepartureDateFrom": date_from.isoformat(),
            "outboundDepartureDateTo": date_to.isoformat(),
            "currency": "EUR",
            "market": "it-it",
        }
        resp = httpx.get(_URL, params=params, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        fares = resp.json().get("fares", [])
        logger.info("Ryanair %s: %d tariffe trovate", origin, len(fares))

        offers = []
        for fare in fares:
            outbound = fare.get("outbound") or {}
            price = (outbound.get("price") or {}).get("value")
            arrival = outbound.get("arrivalAirport") or {}
            dest = arrival.get("iataCode")
            if price is None or not dest:
                continue
            depart = _parse_date(outbound.get("departureDate"))
            city, country = info(dest)
            # Il dataset locale può mancare: la risposta Ryanair è più ricca.
            api_city = (arrival.get("city") or {}).get("name")
            offers.append(
                Offer(
                    origin=origin,
                    destination=dest,
                    dest_city=api_city or city,
                    dest_country=country,
                    price=float(price),
                    depart_date=depart,
                    return_date=None,
                    airline="Ryanair",
                    stops=0,
                    duration_minutes=_duration(outbound),
                    link=_booking_link(origin, dest, depart),
                    source=self.name,
                )
            )
        return offers

    def search_round_trip(
        self,
        origin: str,
        date_from: date,
        date_to: date,
        min_nights: int,
        max_nights: int,
    ) -> list[Offer]:
        params = {
            "departureAirportIataCode": origin,
            "outboundDepartureDateFrom": date_from.isoformat(),
            "outboundDepartureDateTo": date_to.isoformat(),
            "inboundDepartureDateFrom": (date_from + timedelta(days=min_nights)).isoformat(),
            "inboundDepartureDateTo": (date_to + timedelta(days=max_nights)).isoformat(),
            "durationFrom": min_nights,
            "durationTo": max_nights,
            "currency": "EUR",
            "market": "it-it",
        }
        resp = httpx.get(_URL_RT, params=params, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        fares = resp.json().get("fares", [])
        logger.info("Ryanair A/R %s: %d tariffe trovate", origin, len(fares))

        offers = []
        for fare in fares:
            outbound = fare.get("outbound") or {}
            inbound = fare.get("inbound") or {}
            price = ((fare.get("summary") or {}).get("price") or {}).get("value")
            arrival = outbound.get("arrivalAirport") or {}
            dest = arrival.get("iataCode")
            if price is None or not dest:
                continue
            depart = _parse_date(outbound.get("departureDate"))
            ret = _parse_date(inbound.get("departureDate"))
            if depart is None or ret is None:
                continue
            # durationFrom/To è un parametro non documentato: rifiltra sempre
            nights = (ret - depart).days
            if not (min_nights <= nights <= max_nights):
                continue
            city, country = info(dest)
            api_city = (arrival.get("city") or {}).get("name")
            dur_out, dur_in = _duration(outbound), _duration(inbound)
            offers.append(
                Offer(
                    origin=origin,
                    destination=dest,
                    dest_city=api_city or city,
                    dest_country=country,
                    price=float(price),
                    depart_date=depart,
                    return_date=ret,
                    airline="Ryanair",
                    stops=0,
                    duration_minutes=dur_out + dur_in if dur_out and dur_in else None,
                    link=_booking_link(origin, dest, depart, ret),
                    source=self.name,
                )
            )
        return offers


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _duration(outbound: dict) -> int | None:
    try:
        dep = datetime.fromisoformat(outbound["departureDate"])
        arr = datetime.fromisoformat(outbound["arrivalDate"])
        return int((arr - dep).total_seconds() // 60)
    except (KeyError, ValueError, TypeError):
        return None


def _booking_link(
    origin: str, dest: str, depart: date | None, ret: date | None = None
) -> str:
    date_out = depart.isoformat() if depart else ""
    url = (
        "https://www.ryanair.com/it/it/trip/flights/select"
        f"?adults=1&teens=0&children=0&infants=0&isReturn={'true' if ret else 'false'}"
        f"&dateOut={date_out}&originIata={origin}&destinationIata={dest}"
    )
    if ret:
        url += f"&dateIn={ret.isoformat()}"
    return url
