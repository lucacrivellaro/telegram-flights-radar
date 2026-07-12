"""Job giornaliero: alle DAILY_TIME esegue la ricerca e invia il riepilogo."""

import logging
from datetime import time
from zoneinfo import ZoneInfo

from telegram.ext import Application, ContextTypes

from bot import run_search_and_send
from config import Config

logger = logging.getLogger(__name__)


async def _daily_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    logger.info("Job giornaliero avviato")
    try:
        await run_search_and_send(context.application, config.chat_id)
    except Exception:
        logger.exception("Job giornaliero fallito")
        # meglio un messaggio d'errore che il silenzio
        try:
            await context.bot.send_message(
                chat_id=config.chat_id,
                text="❌ La ricerca giornaliera è fallita. Controlla i log del bot.",
            )
        except Exception:
            logger.exception("Impossibile inviare il messaggio d'errore")


def schedule_daily(app: Application, config: Config) -> None:
    try:
        hour, minute = (int(x) for x in config.daily_time.split(":"))
    except ValueError as exc:
        raise SystemExit(f"DAILY_TIME non valido ({config.daily_time}): usa HH:MM") from exc

    when = time(hour=hour, minute=minute, tzinfo=ZoneInfo(config.timezone))
    app.job_queue.run_daily(_daily_job, time=when, name="daily_deals")
    logger.info(
        "Ricerca giornaliera pianificata alle %s (%s)", config.daily_time, config.timezone
    )
