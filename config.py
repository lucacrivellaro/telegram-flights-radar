"""Configurazione: legge tutto da variabili d'ambiente / file .env."""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _csv(value: str) -> list[str]:
    return [x.strip().upper() for x in value.split(",") if x.strip()]


@dataclass
class Config:
    telegram_token: str
    # chat dell'admin: riceve le richieste di iscrizione e può approvarle;
    # i valori qui sotto (origins, soglie, liste) sono i DEFAULT per ogni
    # utente, sovrascrivibili a testa nella tabella user_settings del DB.
    admin_chat_id: str
    travelpayouts_token: str
    travelpayouts_marker: str
    origins: list[str] = field(default_factory=lambda: ["VRN", "BGY"])
    daily_time: str = "08:00"
    timezone: str = "Europe/Rome"
    threshold_europe: float = 40.0
    threshold_extra: float = 300.0
    threshold_europe_rt: float = 70.0
    threshold_extra_rt: float = 550.0
    min_trip_nights: int = 3
    max_trip_nights: int = 10
    search_one_way: bool = False
    rt_score_weight: float = 0.75
    discount_pct: float = 30.0
    min_history_samples: int = 5
    top_n: int = 8
    search_days_ahead: int = 45
    whitelist: list[str] = field(default_factory=list)
    blacklist: list[str] = field(default_factory=list)
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
            daily_time=os.getenv("DAILY_TIME", "08:00"),
            timezone=os.getenv("TIMEZONE", "Europe/Rome"),
            threshold_europe=float(os.getenv("PRICE_THRESHOLD_EUROPE", "40")),
            threshold_extra=float(os.getenv("PRICE_THRESHOLD_EXTRA", "300")),
            threshold_europe_rt=float(os.getenv("PRICE_THRESHOLD_EUROPE_RT", "70")),
            threshold_extra_rt=float(os.getenv("PRICE_THRESHOLD_EXTRA_RT", "550")),
            min_trip_nights=int(os.getenv("MIN_TRIP_NIGHTS", "3")),
            max_trip_nights=int(os.getenv("MAX_TRIP_NIGHTS", "10")),
            search_one_way=os.getenv("SEARCH_ONE_WAY", "false").strip().lower()
            in {"1", "true", "yes", "si", "sì"},
            rt_score_weight=float(os.getenv("RT_SCORE_WEIGHT", "0.75")),
            discount_pct=float(os.getenv("DISCOUNT_THRESHOLD_PCT", "30")),
            min_history_samples=int(os.getenv("MIN_HISTORY_SAMPLES", "5")),
            top_n=int(os.getenv("TOP_N", "8")),
            search_days_ahead=int(os.getenv("SEARCH_DAYS_AHEAD", "45")),
            whitelist=_csv(os.getenv("DESTINATIONS_WHITELIST", "")),
            blacklist=_csv(os.getenv("DESTINATIONS_BLACKLIST", "")),
            db_path=os.getenv("DB_PATH", "data/flights.db"),
            resend_cooldown_days=int(os.getenv("RESEND_COOLDOWN_DAYS", "3")),
        )

    def require_telegram(self) -> None:
        if not self.telegram_token or not self.admin_chat_id:
            raise SystemExit(
                "TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID (chat dell'admin) sono "
                "obbligatori: compila il file .env (vedi .env.example)."
            )
