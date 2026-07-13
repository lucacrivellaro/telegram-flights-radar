"""Modello comune delle offerte e interfaccia dei client voli."""

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass
class Offer:
    origin: str
    destination: str
    dest_city: str
    dest_country: str
    price: float
    depart_date: date | None
    return_date: date | None
    airline: str
    stops: int
    duration_minutes: int | None
    link: str
    source: str
    currency: str = "EUR"

    @property
    def direct(self) -> bool:
        return self.stops == 0

    @property
    def one_way(self) -> bool:
        return self.return_date is None

    @property
    def trip_type(self) -> str:
        return "one_way" if self.one_way else "round_trip"

    @property
    def nights(self) -> int | None:
        if self.depart_date and self.return_date:
            return (self.return_date - self.depart_date).days
        return None

    @property
    def offer_hash(self) -> str:
        """Identità dell'offerta ai fini dedup (il prezzo è escluso di proposito:
        così un ribasso sulla stessa offerta può essere re-inviato)."""
        key = "|".join([
            self.origin,
            self.destination,
            self.depart_date.isoformat() if self.depart_date else "",
            self.return_date.isoformat() if self.return_date else "",
            str(self.stops),
        ])
        return hashlib.md5(key.encode()).hexdigest()


class FlightClient(Protocol):
    name: str

    def search(self, origin: str, date_from: date, date_to: date) -> list[Offer]:
        """Cerca le tariffe più economiche di sola andata da `origin` verso
        qualsiasi destinazione con partenza nell'intervallo dato."""
        ...

    def search_round_trip(
        self,
        origin: str,
        date_from: date,
        date_to: date,
        min_nights: int,
        max_nights: int,
    ) -> list[Offer]:
        """Cerca le combinazioni andata/ritorno più economiche da `origin`
        (prezzo totale) con partenza nell'intervallo dato e soggiorno
        compreso tra min_nights e max_nights notti."""
        ...
