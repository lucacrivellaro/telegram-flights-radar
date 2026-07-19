"""Motore di ricerca e selezione delle offerte.

Un'offerta è considerata "affare" se almeno una di queste condizioni vale:
  1. prezzo sotto la soglia assoluta della sua fascia (Europa / extra-Europa);
  2. prezzo sotto la media storica della rotta di almeno `discount_pct` %,
     con almeno `min_history_samples` rilevazioni negli ultimi 90 giorni.

Multi-utente: la ricerca è divisa in due fasi. `fetch_offers()` interroga le
API una sola volta per aeroporto di partenza distinto (l'unione degli
aeroporti di tutti gli utenti attivi), poi `select_for_user()` applica a quel
pool le preferenze del singolo utente (aeroporti, soglie, liste, dedup).
Le offerte vengono ordinate per convenienza (rapporto prezzo/riferimento).
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
class UserPrefs:
    """Preferenze effettive di un utente: user_settings del DB con fallback
    sui default del .env (Config)."""

    chat_id: str
    origins: list[str]
    threshold_europe: float
    threshold_extra: float
    threshold_europe_rt: float
    threshold_extra_rt: float
    discount_pct: float
    rt_score_weight: float
    whitelist: list[str]
    blacklist: list[str]


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

    # --- preferenze effettive (user_settings sovrascrive .env) -------------

    def prefs_for(self, chat_id: str | int) -> UserPrefs:
        cfg = self.config

        def get(key: str, default):
            return self.storage.get_user_setting(chat_id, key, default)

        return UserPrefs(
            chat_id=str(chat_id),
            origins=[o.upper() for o in get("origins", cfg.origins)],
            threshold_europe=get("threshold_europe", cfg.threshold_europe),
            threshold_extra=get("threshold_extra", cfg.threshold_extra),
            threshold_europe_rt=get("threshold_europe_rt", cfg.threshold_europe_rt),
            threshold_extra_rt=get("threshold_extra_rt", cfg.threshold_extra_rt),
            discount_pct=get("discount_pct", cfg.discount_pct),
            rt_score_weight=get("rt_score_weight", cfg.rt_score_weight),
            whitelist=[c.upper() for c in get("whitelist", cfg.whitelist)],
            blacklist=[c.upper() for c in get("blacklist", cfg.blacklist)],
        )

    # --- fase 1: ricerca (condivisa fra tutti gli utenti) -------------------

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

    def fetch_offers(
        self, origins: list[str]
    ) -> tuple[list[Offer], list[tuple[str, str]]]:
        """Interroga le API per gli aeroporti dati e registra i prezzi.

        Ritorna (offerte, errori); ogni errore è (origin, messaggio) così la
        fase di selezione può mostrare a ciascun utente solo i problemi dei
        suoi aeroporti."""
        date_from = date.today() + timedelta(days=1)
        date_to = date.today() + timedelta(days=self.config.search_days_ahead)

        offers: list[Offer] = []
        errors: list[tuple[str, str]] = []
        for client in self._clients():
            for origin in origins:
                if self.config.search_one_way:
                    try:
                        offers.extend(client.search(origin, date_from, date_to))
                    except Exception as exc:  # noqa: BLE001 - un client rotto non ferma gli altri
                        msg = f"{client.name} da {origin} (solo andata): {exc}"
                        logger.error("Ricerca fallita: %s", msg)
                        errors.append((origin, msg))
                try:
                    offers.extend(
                        client.search_round_trip(
                            origin,
                            date_from,
                            date_to,
                            self.config.min_trip_nights,
                            self.config.max_trip_nights,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    msg = f"{client.name} da {origin} (A/R): {exc}"
                    logger.error("Ricerca fallita: %s", msg)
                    errors.append((origin, msg))

        for offer in offers:
            self.storage.record_price(offer)
        return offers, errors

    # --- fase 2: selezione per utente ---------------------------------------

    def select_for_user(
        self,
        prefs: UserPrefs,
        offers: list[Offer],
        errors: list[tuple[str, str]],
        mark_as_sent: bool = True,
    ) -> SearchResult:
        result = SearchResult()
        mine = [o for o in offers if o.origin.upper() in prefs.origins]
        result.total_offers = len(mine)
        result.errors = [msg for origin, msg in errors if origin in prefs.origins]

        deals = self._select_deals(mine, prefs)
        if mark_as_sent:
            for ev in deals:
                self.storage.mark_sent(
                    prefs.chat_id, ev.offer.offer_hash, ev.offer.price
                )
        result.deals = deals
        return result

    def search_for_user(
        self, chat_id: str | int, mark_as_sent: bool = True
    ) -> SearchResult:
        """Ricerca completa per un singolo utente (comando /oggi, test)."""
        prefs = self.prefs_for(chat_id)
        offers, errors = self.fetch_offers(prefs.origins)
        return self.select_for_user(prefs, offers, errors, mark_as_sent)

    # --- selezione ----------------------------------------------------------

    def _select_deals(
        self, offers: list[Offer], prefs: UserPrefs
    ) -> list[EvaluatedOffer]:
        # una sola offerta per (destinazione, tipo viaggio): la stessa meta può
        # comparire sia come solo-andata sia come andata/ritorno
        evaluated: dict[tuple[str, str], EvaluatedOffer] = {}
        for offer in offers:
            dest = offer.destination.upper()
            if dest in prefs.blacklist:
                continue
            if prefs.whitelist and dest not in prefs.whitelist:
                continue

            ev = self._evaluate(offer, prefs)
            if ev is None:
                continue
            if self._recently_sent(prefs.chat_id, offer):
                continue

            key = (dest, offer.trip_type)
            current = evaluated.get(key)
            if current is None or ev.score < current.score:
                evaluated[key] = ev

        ranked = sorted(evaluated.values(), key=lambda e: e.score)
        return ranked[: self.config.top_n]

    def _evaluate(self, offer: Offer, prefs: UserPrefs) -> EvaluatedOffer | None:
        if offer.one_way:
            threshold = (
                prefs.threshold_europe
                if is_short_haul(offer.destination)
                else prefs.threshold_extra
            )
        else:
            threshold = (
                prefs.threshold_europe_rt
                if is_short_haul(offer.destination)
                else prefs.threshold_extra_rt
            )
        avg, samples = self.storage.route_average(
            offer.origin, offer.destination, offer.trip_type
        )
        has_history = samples >= self.config.min_history_samples and avg

        reasons = []
        if offer.price <= threshold:
            reasons.append(f"sotto soglia ({threshold:.0f}€)")
        if has_history and offer.price <= avg * (1 - prefs.discount_pct / 100):
            saving = (1 - offer.price / avg) * 100
            reasons.append(f"-{saving:.0f}% vs media storica ({avg:.0f}€)")
        if not reasons:
            return None

        score = offer.price / avg if has_history else offer.price / threshold
        if not offer.one_way:
            score *= prefs.rt_score_weight
        return EvaluatedOffer(
            offer=offer,
            score=score,
            reason=", ".join(reasons),
            route_average=avg if has_history else None,
        )

    def _recently_sent(self, chat_id: str, offer: Offer) -> bool:
        prev = self.storage.last_sent(chat_id, offer.offer_hash)
        if prev is None:
            return False
        prev_price, sent_at = prev
        if datetime.now() - sent_at > timedelta(days=self.config.resend_cooldown_days):
            return False
        # re-invia comunque se il prezzo è sceso di oltre il 10%
        return offer.price > prev_price * 0.9
