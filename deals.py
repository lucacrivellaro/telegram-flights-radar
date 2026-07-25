"""Motore di ricerca e selezione delle offerte.

Le offerte inviate sono sempre andata/ritorno e sempre voli diretti: le soglie
sono quindi sul prezzo *totale* del viaggio. Un'offerta è "affare" se almeno
una di queste condizioni vale:
  1. prezzo sotto la soglia assoluta della sua fascia (Europa / extra-Europa);
  2. prezzo sotto la media storica della rotta di almeno `discount_pct` %,
     con almeno `min_history_samples` rilevazioni negli ultimi 90 giorni.

Ogni fascia parte dai suoi aeroporti (`_origin_allowed`): il corto raggio da
`origins`, il lungo raggio ed i viaggi a tappe da `intl_origins`.

Gli itinerari multitratta (`flights/multitrip.py`) sono una categoria a parte:
non concorrono con i voli singoli, hanno una soglia sul prezzo *totale*
dell'itinerario (`threshold_multi`) e finiscono in una sezione dedicata del
messaggio, con un proprio numero di posti (`multi_top_n`).

Multi-utente: la ricerca è divisa in due fasi. `fetch_offers()` interroga le
API una sola volta per aeroporto di partenza distinto (l'unione degli
aeroporti di tutti gli utenti attivi), poi `select_for_user()` applica a quel
pool le preferenze del singolo utente (aeroporti, soglie, liste, dedup).
Le offerte vengono ordinate per convenienza (rapporto prezzo/riferimento).
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from airports import is_short_haul, same_metro
from config import Config
from flights.base import FlightClient, Offer
from flights.multitrip import MultiTripBuilder
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
    intl_origins: list[str]
    threshold_europe_rt: float
    threshold_extra_rt: float
    discount_pct: float
    threshold_multi: float
    whitelist: list[str]
    blacklist: list[str]
    # tappe obbligatorie dei viaggi a tappe: ogni itinerario proposto le tocca
    # tutte. Lista separata da whitelist/blacklist, che valgono sui voli singoli
    multi_required: list[str]

    @property
    def all_origins(self) -> list[str]:
        return sorted(set(self.origins) | set(self.intl_origins))


@dataclass
class EvaluatedOffer:
    offer: Offer
    score: float
    reason: str
    route_average: float | None = None


@dataclass
class SearchResult:
    deals: list[EvaluatedOffer] = field(default_factory=list)
    multi_deals: list[EvaluatedOffer] = field(default_factory=list)
    total_offers: int = 0
    errors: list[str] = field(default_factory=list)
    # tappe obbligatorie attive: servono al messaggio per spiegare una sezione
    # "viaggi a tappe" vuota invece di lasciarla sparire in silenzio
    multi_required: list[str] = field(default_factory=list)


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
            intl_origins=[o.upper() for o in get("intl_origins", cfg.intl_origins)],
            threshold_europe_rt=get("threshold_europe_rt", cfg.threshold_europe_rt),
            threshold_extra_rt=get("threshold_extra_rt", cfg.threshold_extra_rt),
            discount_pct=get("discount_pct", cfg.discount_pct),
            threshold_multi=get("threshold_multi", cfg.threshold_multi),
            whitelist=[c.upper() for c in get("whitelist", cfg.whitelist)],
            blacklist=[c.upper() for c in get("blacklist", cfg.blacklist)],
            multi_required=[
                c.upper() for c in get("multi_required", cfg.multi_required)
            ],
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

    def _multi_builder(self) -> MultiTripBuilder | None:
        """Il costruttore di itinerari multitratta, se abilitato e utilizzabile.

        Serve Travelpayouts: è l'unica fonte che espone il prezzo giorno per
        giorno di una rotta arbitraria, necessario per agganciare le tappe."""
        cfg = self.config
        if not cfg.multi_enabled:
            return None
        if not cfg.travelpayouts_token:
            logger.warning(
                "Multitratta disattivato: serve TRAVELPAYOUTS_TOKEN "
                "(Ryanair da solo non copre le tratte fra città estere)"
            )
            return None
        return MultiTripBuilder(
            cfg.travelpayouts_token,
            cfg.travelpayouts_marker,
            min_stops=cfg.multi_min_stops,
            max_stops=cfg.multi_max_stops,
            min_stay=cfg.multi_min_stay,
            max_stay=cfg.multi_max_stay,
            max_trip_days=cfg.multi_max_trip_days,
            beam_width=cfg.multi_beam_width,
            candidates=cfg.multi_candidates,
            direct_only=cfg.multi_direct_only,
            extra_europe_only=cfg.multi_extra_europe_only,
            max_api_calls=cfg.multi_max_api_calls,
        )

    def fetch_offers(
        self,
        origins: list[str],
        multi_origins: list[str] | None = None,
        required_sets: list[list[str]] | None = None,
    ) -> tuple[list[Offer], list[tuple[str, str]]]:
        """Interroga le API per gli aeroporti dati e registra i prezzi.

        `multi_origins` sono gli aeroporti su cui costruire anche gli itinerari
        a tappe (i "internazionali"): è un sottoinsieme di `origins`, perché il
        lungo raggio parte solo da lì.

        `required_sets` sono i vincoli di tappa obbligatoria distinti fra gli
        utenti: serve una ricerca per ognuno, perché il vincolo guida la beam
        search e non si può applicare a posteriori. Utenti con lo stesso
        vincolo (compreso "nessuno") condividono la stessa ricerca.

        Ritorna (offerte, errori); ogni errore è (origin, messaggio) così la
        fase di selezione può mostrare a ciascun utente solo i problemi dei
        suoi aeroporti."""
        date_from = date.today() + timedelta(days=1)
        date_to = date.today() + timedelta(days=self.config.search_days_ahead)

        offers: list[Offer] = []
        errors: list[tuple[str, str]] = []
        for client in self._clients():
            for origin in origins:
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

        # Le tariffe cached sul lungo raggio sono sempre con scalo: se i posti
        # extra-Europa devono essere diretti serve una ricerca dedicata, o
        # resterebbero vuoti. Una chiamata per aeroporto e mese.
        if self.config.direct_only_extra and self.config.travelpayouts_token:
            tp = TravelpayoutsClient(
                self.config.travelpayouts_token, self.config.travelpayouts_marker
            )
            for origin in multi_origins if multi_origins is not None else origins:
                try:
                    offers.extend(
                        tp.search_round_trip_direct(
                            origin,
                            date_from,
                            date_to,
                            self.config.min_trip_nights,
                            self.config.max_trip_nights,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    msg = f"travelpayouts da {origin} (A/R diretti): {exc}"
                    logger.error("Ricerca fallita: %s", msg)
                    errors.append((origin, msg))

        builder = self._multi_builder()
        if builder is not None:
            # de-duplica i vincoli: due utenti che chiedono le stesse tappe
            # obbligatorie condividono una sola ricerca
            wanted = required_sets if required_sets is not None else [[]]
            distinct = {tuple(sorted({c.upper() for c in req})) for req in wanted}
            for origin in multi_origins if multi_origins is not None else origins:
                for req in sorted(distinct):
                    try:
                        offers.extend(
                            builder.build(origin, date_from, date_to, required=list(req))
                        )
                    except Exception as exc:  # noqa: BLE001 - il multitratta non blocca il resto
                        vincolo = f" via {', '.join(req)}" if req else ""
                        msg = f"multitratta da {origin}{vincolo}: {exc}"
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
        mine = [o for o in offers if self._origin_allowed(o, prefs)]
        result.total_offers = len(mine)
        result.errors = [
            msg for origin, msg in errors if origin in prefs.all_origins
        ]

        # voli singoli e itinerari multitratta non competono: liste separate,
        # criteri separati, posti separati nel messaggio
        deals = self._apply_quota(
            self._rank([o for o in mine if not o.is_multi], prefs),
            self.config.top_n,
            self.config.top_n_extra,
        )
        multi_deals = self._rank([o for o in mine if o.is_multi], prefs)[
            : self.config.multi_top_n
        ]
        if mark_as_sent:
            for ev in [*deals, *multi_deals]:
                self.storage.mark_sent(
                    prefs.chat_id, ev.offer.offer_hash, ev.offer.price
                )
        result.deals = deals
        result.multi_deals = multi_deals
        result.multi_required = list(prefs.multi_required)
        return result

    def search_for_user(
        self, chat_id: str | int, mark_as_sent: bool = True
    ) -> SearchResult:
        """Ricerca completa per un singolo utente (comando /oggi, test)."""
        prefs = self.prefs_for(chat_id)
        offers, errors = self.fetch_offers(
            prefs.all_origins, prefs.intl_origins, [prefs.multi_required]
        )
        return self.select_for_user(prefs, offers, errors, mark_as_sent)

    def _origin_allowed(self, offer: Offer, prefs: UserPrefs) -> bool:
        """Ogni fascia parte dai suoi aeroporti: il lungo raggio (extra-Europa
        A/R e viaggi a tappe) solo dagli internazionali, il corto raggio da
        quelli normali. Uno scalo regionale non ha voli intercontinentali,
        quindi cercarli da lì è solo rumore."""
        origin = offer.origin.upper()
        if offer.is_multi or not is_short_haul(offer.destination):
            return origin in prefs.intl_origins
        return origin in prefs.origins

    # --- selezione ----------------------------------------------------------

    def _rank(self, offers: list[Offer], prefs: UserPrefs) -> list[EvaluatedOffer]:
        """Valuta, deduplica e ordina per convenienza. Nessun taglio: i posti
        li assegna chi chiama (`_apply_quota` per i voli, `multi_top_n` per
        gli itinerari a tappe)."""
        # una sola offerta per (destinazione, tipo viaggio): la stessa meta può
        # comparire sia come solo-andata sia come andata/ritorno; per i
        # multitratta la chiave è l'intera sequenza di tappe
        evaluated: dict[tuple, EvaluatedOffer] = {}
        for offer in offers:
            if not self._passes_lists(offer, prefs):
                continue
            if not offer.is_multi and not offer.direct and self._vuole_diretto(offer):
                continue

            evaluate = self._evaluate_multi if offer.is_multi else self._evaluate
            ev = evaluate(offer, prefs)
            if ev is None:
                continue
            if self._recently_sent(prefs.chat_id, offer):
                continue

            if offer.is_multi:
                key = tuple(leg.destination for leg in offer.legs)
            else:
                key = (offer.destination.upper(), offer.trip_type)
            current = evaluated.get(key)
            if current is None or ev.score < current.score:
                evaluated[key] = ev

        return sorted(evaluated.values(), key=lambda e: e.score)

    def _apply_quota(
        self, ranked: list[EvaluatedOffer], total: int, extra_slots: int
    ) -> list[EvaluatedOffer]:
        """Riserva `extra_slots` posti alle mete extra-Europa.

        Senza quota non entrerebbero quasi mai: lo score è prezzo/soglia della
        propria fascia, e un A/R europeo a 43€ (0.61) batte sempre Miami a
        507€ (0.92). I posti che una fascia non riesce a riempire passano
        all'altra: la quota garantisce lo spazio, non lo spreca."""
        europe, extra = [], []
        for ev in ranked:
            target = europe if is_short_haul(ev.offer.destination) else extra
            target.append(ev)

        chosen = europe[: max(0, total - extra_slots)] + extra[:extra_slots]
        if len(chosen) < total:
            taken = {ev.offer.offer_hash for ev in chosen}
            chosen += [ev for ev in ranked if ev.offer.offer_hash not in taken][
                : total - len(chosen)
            ]
        return sorted(chosen, key=lambda e: e.score)

    def _vuole_diretto(self, offer: Offer) -> bool:
        """Se per questa offerta pretendiamo un volo diretto.

        Sul corto raggio sì: due scali tecnici per andare a Barcellona non
        sono un affare. Sul lungo raggio no, e non è una scelta di gusto —
        da VRN/BGY/MXP/VCE *nessuna* delle tariffe extra-Europa sotto soglia
        è diretta, quindi pretenderlo svuoterebbe i posti riservati al lungo
        raggio (`top_n_extra`) invece di renderli più selettivi."""
        if is_short_haul(offer.destination):
            return self.config.direct_only
        return self.config.direct_only_extra

    def _passes_lists(self, offer: Offer, prefs: UserPrefs) -> bool:
        """Whitelist/blacklist. Per un multitratta valgono su tutte le tappe:
        basta una tappa bloccata per scartarlo, e con la whitelist attiva
        serve almeno una tappa fra quelle richieste."""
        if offer.is_multi:
            stops = {leg.destination.upper() for leg in offer.stopovers}
            if stops & set(prefs.blacklist):
                return False
            # tappe obbligatorie: devono esserci TUTTE. Confronto per area
            # metropolitana, così "NYC" è soddisfatto da un itinerario via JFK
            for req in prefs.multi_required:
                if not any(same_metro(req, stop) for stop in stops):
                    return False
            return not prefs.whitelist or bool(stops & set(prefs.whitelist))

        dest = offer.destination.upper()
        if dest in prefs.blacklist:
            return False
        return not prefs.whitelist or dest in prefs.whitelist

    def _evaluate(self, offer: Offer, prefs: UserPrefs) -> EvaluatedOffer | None:
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

        return EvaluatedOffer(
            offer=offer,
            score=offer.price / (avg if has_history else threshold),
            reason=", ".join(reasons),
            route_average=avg if has_history else None,
        )

    def _evaluate_multi(self, offer: Offer, prefs: UserPrefs) -> EvaluatedOffer | None:
        """Come `_evaluate` ma sul prezzo *totale* dell'itinerario: le soglie
        per volo singolo non sono applicabili a 3-5 tratte sommate, quindi
        vale la soglia dedicata `multi` (o lo sconto sulla media storica della
        stessa sequenza casa → ultima tappa)."""
        threshold = prefs.threshold_multi
        avg, samples = self.storage.route_average(
            offer.origin, offer.destination, offer.trip_type
        )
        has_history = samples >= self.config.min_history_samples and avg

        reasons = []
        if offer.price <= threshold:
            reasons.append(f"sotto soglia multitratta ({threshold:.0f}€ totali)")
        if has_history and offer.price <= avg * (1 - prefs.discount_pct / 100):
            saving = (1 - offer.price / avg) * 100
            reasons.append(f"-{saving:.0f}% vs media storica ({avg:.0f}€)")
        if not reasons:
            return None

        tappe = len(offer.stopovers)
        reasons.append(f"{tappe} tappe · {offer.price / len(offer.legs):.0f}€ a tratta")
        return EvaluatedOffer(
            offer=offer,
            score=offer.price / (avg if has_history else threshold),
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
