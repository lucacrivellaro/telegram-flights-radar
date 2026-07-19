"""Job giornaliero: alle DAILY_TIME una sola ricerca sull'unione degli
aeroporti di tutti gli utenti attivi, poi un messaggio personalizzato a testa."""

import asyncio
import logging
from datetime import time
from zoneinfo import ZoneInfo

from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes

from config import Config
from deals import DealEngine
from formatter import build_message

logger = logging.getLogger(__name__)


async def _daily_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    app = context.application
    config: Config = app.bot_data["config"]
    engine: DealEngine = app.bot_data["engine"]
    logger.info("Job giornaliero avviato")
    try:
        users = engine.storage.list_users("active")
        if not users:
            logger.info("Nessun utente attivo: job saltato")
            return

        # le API si interrogano una volta sola per aeroporto distinto,
        # qualunque sia il numero di utenti
        prefs_by_user = {u["chat_id"]: engine.prefs_for(u["chat_id"]) for u in users}
        all_origins = sorted({o for p in prefs_by_user.values() for o in p.origins})
        logger.info(
            "Ricerca per %d utenti su %s", len(users), ", ".join(all_origins)
        )
        offers, errors = await asyncio.to_thread(engine.fetch_offers, all_origins)

        for chat_id, prefs in prefs_by_user.items():
            try:
                result = await asyncio.to_thread(
                    engine.select_for_user, prefs, offers, errors
                )
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=build_message(result),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception:
                # un invio fallito (es. utente che ha bloccato il bot)
                # non deve fermare gli altri
                logger.exception("Invio giornaliero fallito per l'utente %s", chat_id)
            await asyncio.sleep(0.5)  # margine sul rate limit Telegram
    except Exception:
        logger.exception("Job giornaliero fallito")
        # meglio un messaggio d'errore che il silenzio
        try:
            await context.bot.send_message(
                chat_id=config.admin_chat_id,
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
