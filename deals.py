"""Motore di ricerca e selezione delle offerte.

Un'offerta è considerata "affare" se almeno una di queste condizioni vale:
  1. prezzo sotto la soglia assoluta della sua fascia (Europa / extra-Europa);
  2. prezzo sotto la media storica della rotta di almeno `discount_pct` %,
     con almeno `min_history_samples` rilevazioni negli ultimi 90 giorni.

Le offerte vengono ordinate per convenienza (rapporto prezzo/riferimento) e
dedupplicate contro quelle già inviate di recente.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from airports import is_short_haul
from config import Config
from flights.base import FlightClient, Offer
from flights.ryanair import RyanairClient
from flights.travelpayouts import TravelpayoutsClient
from storage import Storage

logger = logging.getLogger(__name__)


@dataclass
class EvaluatedOffer:
    offer: Offer
    score: float
    reason: str
    route_average: float | None = None


@dataclass
class SearchResult:
    deals: list[EvaluatedOffer] = field(default_factory=list)
    total_offers: int = 0
    errors: list[str] = field(default_factory=list)


class DealEngine:
    def __init__(self, config: Config, storage: Storage):
        self.config = config
        self.storage = storage

    # --- impostazioni effettive (DB sovrascrive .env) ---------------------

    def thresholds(self) -> tuple[float, float, float]:
        return (
            self.storage.get_setting("threshold_europe", self.config.threshold_europe),
            self.storage.get_setting("threshold_extra", self.config.threshold_extra),
            self.storage.get_setting("discount_pct", self.config.discount_pct),
        )

    def destination_lists(self) -> tuple[list[str], list[str]]:
        return (
            self.storage.get_setting("whitelist", self.config.whitelist),
            self.storage.get_setting("blacklist", self.config.blacklist),
        )

    # --- ricerca -----------------------------------------------------------

    def _clients(self) -> list[FlightClient]:
        clients: list[FlightClient] = [RyanairClient()]
        if self.config.travelpayouts_token:
            clients.append(
                TravelpayoutsClient(
                    self.config.travelpayouts_token, self.config.travelpayouts_marker
                )
            )
        else:
            logger.warning(
                "TRAVELPAYOUTS_TOKEN assente: cerco solo voli Ryanair diretti"
            )
        return clients

    def search(self, mark_as_sent: bool = True) -> SearchResult:
        result = SearchResult()
        date_from = date.today() + timedelta(days=1)
        date_to = date.today() + timedelta(days=self.config.search_days_ahead)

        offers: list[Offer] = []
        for client in self._clients():
            for origin in self.config.origins:
                try:
                    found = client.search(origin, date_from, date_to)
                    offers.extend(found)
                except Exception as exc:  # noqa: BLE001 - un client rotto non ferma gli altri
                    msg = f"{client.name} da {origin}: {exc}"
                    logger.error("Ricerca fallita: %s", msg)
                    result.errors.append(msg)

        result.total_offers = len(offers)
        for offer in offers:
            self.storage.record_price(offer)

        deals = self._select_deals(offers)
        if mark_as_sent:
            for ev in deals:
                self.storage.mark_sent(ev.offer.offer_hash, ev.offer.price)
        result.deals = deals
        return result

    # --- selezione ----------------------------------------------------------

    def _select_deals(self, offers: list[Offer]) -> list[EvaluatedOffer]:
        thr_europe, thr_extra, discount_pct = self.thresholds()
        whitelist, blacklist = self.destination_lists()

        evaluated: dict[str, EvaluatedOffer] = {}
        for offer in offers:
            dest = offer.destination.upper()
            if dest in blacklist:
                continue
            if whitelist and dest not in whitelist:
                continue

            ev = self._evaluate(offer, thr_europe, thr_extra, discount_pct)
            if ev is None:
                continue
            if self._recently_sent(offer):
                continue

            # una sola offerta per destinazione: tieni la più conveniente
            current = evaluated.get(dest)
            if current is None or ev.score < current.score:
                evaluated[dest] = ev

        ranked = sorted(evaluated.values(), key=lambda e: e.score)
        return ranked[: self.config.top_n]

    def _evaluate(
        self, offer: Offer, thr_europe: float, thr_extra: float, discount_pct: float
    ) -> EvaluatedOffer | None:
        threshold = thr_europe if is_short_haul(offer.destination) else thr_extra
        avg, samples = self.storage.route_average(offer.origin, offer.destination)
        has_history = samples >= self.config.min_history_samples and avg

        reasons = []
        if offer.price <= threshold:
            reasons.append(f"sotto soglia ({threshold:.0f}€)")
        if has_history and offer.price <= avg * (1 - discount_pct / 100):
            saving = (1 - offer.price / avg) * 100
            reasons.append(f"-{saving:.0f}% vs media storica ({avg:.0f}€)")
        if not reasons:
            return None

        score = offer.price / avg if has_history else offer.price / threshold
        return EvaluatedOffer(
            offer=offer,
            score=score,
            reason=", ".join(reasons),
            route_average=avg if has_history else None,
        )

    def _recently_sent(self, offer: Offer) -> bool:
        prev = self.storage.last_sent(offer.offer_hash)
        if prev is None:
            return False
        prev_price, sent_at = prev
        if datetime.now() - sent_at > timedelta(days=self.config.resend_cooldown_days):
            return False
        # re-invia comunque se il prezzo è sceso di oltre il 10%
        return offer.price > prev_price * 0.9
