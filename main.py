"""Entry point: avvia bot Telegram + scheduler giornaliero."""

import logging

from bot import build_application
from config import Config
from deals import DealEngine
from scheduler import schedule_daily
from storage import Storage

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    config = Config.from_env()
    config.require_telegram()

    storage = Storage(config.db_path)
    engine = DealEngine(config, storage)
    app = build_application(config, engine)
    schedule_daily(app, config)

    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
