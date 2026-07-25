"""Configurazione: legge tutto da variabili d'ambiente / file .env."""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _csv(value: str) -> list[str]:
    return [x.strip().upper() for x in value.split(",") if x.strip()]


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "si", "sì"}


@dataclass
class Config:
    telegram_token: str
    # chat dell'admin: riceve le richieste di iscrizione e può approvarle;
    # i valori qui sotto (origins, soglie, liste) sono i DEFAULT per ogni
    # utente, sovrascrivibili a testa nella tabella user_settings del DB.
    admin_chat_id: str
    travelpayouts_token: str
    travelpayouts_marker: str
    # aeroporti per le mete europee/corto raggio
    origins: list[str] = field(default_factory=lambda: ["VRN", "BGY"])
    # aeroporti per il lungo raggio: extra-Europa A/R e viaggi a tappe. Sono
    # separati perché uno scalo regionale (VRN) non ha voli intercontinentali
    intl_origins: list[str] = field(default_factory=lambda: ["VCE", "BGY", "MXP"])
    daily_time: str = "08:00"
    timezone: str = "Europe/Rome"
    # soglie sul prezzo totale A/R (le offerte inviate sono solo andata/ritorno)
    threshold_europe_rt: float = 70.0
    # alta perché vale solo sull'extra-Europa e solo su voli DIRETTI: sotto i
    # 550€ le uniche tariffe intercontinentali sono quelle con scalo
    threshold_extra_rt: float = 900.0
    min_trip_nights: int = 3
    max_trip_nights: int = 10
    discount_pct: float = 30.0
    min_history_samples: int = 5
    top_n: int = 8
    # posti delle TOP_N riservati alle mete extra-Europa (il resto va
    # all'Europa): senza quota il lungo raggio non entrerebbe mai in classifica
    top_n_extra: int = 2
    # "solo voli diretti" su entrambe le fasce. Sul lungo raggio richiede la
    # ricerca dedicata `search_round_trip_direct()`: le tariffe cached normali
    # sono sempre con scalo
    direct_only: bool = True
    direct_only_extra: bool = True
    # --- itinerari multitratta (sezione a parte nel messaggio) ---------------
    multi_enabled: bool = True
    multi_top_n: int = 2
    threshold_multi: float = 700.0
    multi_min_stops: int = 2
    multi_max_stops: int = 4
    multi_min_stay: int = 2
    multi_max_stay: int = 5
    multi_max_trip_days: int = 20
    # sulle rotte intercontinentali pretendere tratte dirette azzera la resa
    # (VCE: 0 itinerari con i diretti, 3 ammettendo gli scali)
    multi_direct_only: bool = False
    multi_extra_europe_only: bool = True
    multi_beam_width: int = 4
    multi_candidates: int = 6
    multi_max_api_calls: int = 250
    search_days_ahead: int = 45
    # Da quanti giorni da oggi parte la finestra del LUNGO RAGGIO (extra-Europa
    # A/R e viaggi a tappe): larga quanto `search_days_ahead`, solo spostata in
    # avanti. 0 = come l'Europa. Misurato sui dati veri: a +60 le A/R dirette
    # extra-Europa passano da 9 a 26 e il minimo da 583€ a 313€, mentre i
    # viaggi a tappe calano da 18 a 12 itinerari (la cache Travelpayouts è più
    # densa sulle date vicine) — 60 è il compromesso, i 2 posti giornalieri
    # restano ampiamente coperti da entrambi.
    # Non è personalizzabile per utente di proposito: cambia *cosa si chiede
    # alle API*, quindi ogni valore distinto sarebbe una ricerca in più.
    longhaul_start_days: int = 60
    whitelist: list[str] = field(default_factory=list)
    blacklist: list[str] = field(default_factory=list)
    # tappe obbligatorie di default per i viaggi a tappe (vuota = nessun vincolo)
    multi_required: list[str] = field(default_factory=list)
    db_path: str = "data/flights.db"
    resend_cooldown_days: int = 3

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            admin_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            travelpayouts_token=os.getenv("TRAVELPAYOUTS_TOKEN", ""),
            travelpayouts_marker=os.getenv("TRAVELPAYOUTS_MARKER", ""),
            origins=_csv(os.getenv("ORIGIN_AIRPORTS", "VRN,BGY")),
            intl_origins=_csv(os.getenv("INTL_ORIGIN_AIRPORTS", "VCE,BGY,MXP")),
            daily_time=os.getenv("DAILY_TIME", "08:00"),
            timezone=os.getenv("TIMEZONE", "Europe/Rome"),
            threshold_europe_rt=float(os.getenv("PRICE_THRESHOLD_EUROPE_RT", "70")),
            threshold_extra_rt=float(os.getenv("PRICE_THRESHOLD_EXTRA_RT", "900")),
            min_trip_nights=int(os.getenv("MIN_TRIP_NIGHTS", "3")),
            max_trip_nights=int(os.getenv("MAX_TRIP_NIGHTS", "10")),
            discount_pct=float(os.getenv("DISCOUNT_THRESHOLD_PCT", "30")),
            min_history_samples=int(os.getenv("MIN_HISTORY_SAMPLES", "5")),
            top_n=int(os.getenv("TOP_N", "8")),
            top_n_extra=int(os.getenv("TOP_N_EXTRA", "2")),
            direct_only=_bool(os.getenv("DIRECT_ONLY", "true")),
            direct_only_extra=_bool(os.getenv("DIRECT_ONLY_EXTRA", "true")),
            multi_enabled=_bool(os.getenv("MULTI_ENABLED", "true")),
            multi_top_n=int(os.getenv("MULTI_TOP_N", "2")),
            threshold_multi=float(os.getenv("PRICE_THRESHOLD_MULTI", "700")),
            multi_min_stops=int(os.getenv("MULTI_MIN_STOPS", "2")),
            multi_max_stops=int(os.getenv("MULTI_MAX_STOPS", "4")),
            multi_min_stay=int(os.getenv("MULTI_MIN_STAY_NIGHTS", "2")),
            multi_max_stay=int(os.getenv("MULTI_MAX_STAY_NIGHTS", "5")),
            multi_max_trip_days=int(os.getenv("MULTI_MAX_TRIP_DAYS", "20")),
            multi_direct_only=_bool(os.getenv("MULTI_DIRECT_ONLY", "false")),
            multi_extra_europe_only=_bool(
                os.getenv("MULTI_EXTRA_EUROPE_ONLY", "true")
            ),
            multi_beam_width=int(os.getenv("MULTI_BEAM_WIDTH", "4")),
            multi_candidates=int(os.getenv("MULTI_CANDIDATES", "6")),
            multi_max_api_calls=int(os.getenv("MULTI_MAX_API_CALLS", "250")),
            search_days_ahead=int(os.getenv("SEARCH_DAYS_AHEAD", "45")),
            longhaul_start_days=int(os.getenv("LONGHAUL_START_DAYS", "60")),
            whitelist=_csv(os.getenv("DESTINATIONS_WHITELIST", "")),
            blacklist=_csv(os.getenv("DESTINATIONS_BLACKLIST", "")),
            multi_required=_csv(os.getenv("MULTI_REQUIRED_STOPS", "")),
            db_path=os.getenv("DB_PATH", "data/flights.db"),
            resend_cooldown_days=int(os.getenv("RESEND_COOLDOWN_DAYS", "3")),
        )

    def require_telegram(self) -> None:
        if not self.telegram_token or not self.admin_chat_id:
            raise SystemExit(
                "TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID (chat dell'admin) sono "
                "obbligatori: compila il file .env (vedi .env.example)."
            )
