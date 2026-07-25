"""Modello comune delle offerte e interfaccia dei client voli."""

import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol


@dataclass
class Leg:
    """Una tratta di un itinerario multitratta.

    `nights` sono le notti passate a `destination` prima di ripartire: 0 sulla
    tratta finale, che riporta a casa. Ogni tratta è un biglietto a sé (le API
    gratuite non vendono itinerari multi-city), quindi ha il suo `link`."""

    origin: str
    destination: str
    dest_city: str
    dest_country: str
    price: float
    depart_date: date
    airline: str
    stops: int
    duration_minutes: int | None
    link: str
    nights: int = 0


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
    # valorizzato solo per gli itinerari multitratta (vedi flights/multitrip.py):
    # `price` è la somma delle tratte, `destination` è l'ultima tappa raggiunta
    legs: list[Leg] = field(default_factory=list)

    @property
    def direct(self) -> bool:
        return self.stops == 0

    @property
    def is_multi(self) -> bool:
        return bool(self.legs)

    @property
    def one_way(self) -> bool:
        return self.return_date is None

    @property
    def trip_type(self) -> str:
        if self.is_multi:
            return "multi_city"
        return "one_way" if self.one_way else "round_trip"

    @property
    def nights(self) -> int | None:
        if self.depart_date and self.return_date:
            return (self.return_date - self.depart_date).days
        return None

    @property
    def stopovers(self) -> list[Leg]:
        """Le tappe intermedie con sosta (esclusa la tratta di rientro)."""
        return [leg for leg in self.legs if leg.nights > 0]

    @property
    def offer_hash(self) -> str:
        """Identità dell'offerta ai fini dedup (il prezzo è escluso di proposito:
        così un ribasso sulla stessa offerta può essere re-inviato)."""
        if self.is_multi:
            key = "multi|" + "|".join(
                f"{leg.origin}>{leg.destination}@{leg.depart_date.isoformat()}"
                for leg in self.legs
            )
        else:
            key = "|".join([
                self.origin,
                self.destination,
                self.depart_date.isoformat() if self.depart_date else "",
                self.return_date.isoformat() if self.return_date else "",
                str(self.stops),
            ])
        return hashlib.md5(key.encode()).hexdigest()


class FlightClient(Protocol):
    """Il bot invia solo offerte andata/ritorno, quindi questa è l'unica
    ricerca richiesta a un client. Gli itinerari a tappe sono costruiti a
    parte da `flights/multitrip.py`, che non implementa questo protocollo."""

    name: str

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
