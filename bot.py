"""Bot Telegram: comandi interattivi e invio del riepilogo giornaliero."""

import asyncio
import logging
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

from config import Config
from deals import DealEngine
from formatter import build_message

logger = logging.getLogger(__name__)

HELP_TEXT = """<b>Radar voli — comandi</b>

/oggi — cerca subito le offerte
/destinazioni — mostra whitelist e blacklist
/destinazioni add|remove XXX — gestisci la whitelist (vuota = tutte)
/destinazioni block|unblock XXX — gestisci la blacklist
/destinazioni reset — torna ai valori del file .env
/soglia — mostra le soglie attuali
/soglia europa|europa_ar|extra|extra_ar|sconto N — imposta soglia (€ o %)
/soglia peso_ar N — peso delle A/R nel ranking (1 = neutro, &lt;1 = favorite)
/help — questo messaggio

Ogni giorno alle {daily_time} ricevi automaticamente le migliori offerte
da {origins}: sola andata e andata/ritorno con soggiorni di
{min_nights}-{max_nights} notti (configurabili nel file .env)."""


def build_application(config: Config, engine: DealEngine) -> Application:
    app = (
        ApplicationBuilder()
        .token(config.telegram_token)
        .post_init(_on_startup)
        .build()
    )
    app.bot_data["config"] = config
    app.bot_data["engine"] = engine

    app.add_handler(CommandHandler(["start", "help"], cmd_help))
    app.add_handler(CommandHandler("oggi", cmd_oggi))
    app.add_handler(CommandHandler("destinazioni", cmd_destinazioni))
    app.add_handler(CommandHandler("soglia", cmd_soglia))
    return app


async def _on_startup(app: Application) -> None:
    logger.info("Bot avviato")


async def run_search_and_send(app: Application, chat_id: str | int) -> None:
    """Esegue la ricerca (bloccante, in thread) e invia il risultato."""
    engine: DealEngine = app.bot_data["engine"]
    result = await asyncio.to_thread(engine.search)
    await app.bot.send_message(
        chat_id=chat_id,
        text=build_message(result),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# --- comandi ---------------------------------------------------------------


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    await update.message.reply_html(
        HELP_TEXT.format(
            daily_time=config.daily_time,
            origins=", ".join(config.origins),
            min_nights=config.min_trip_nights,
            max_nights=config.max_trip_nights,
        )
    )


async def cmd_oggi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🔍 Cerco le offerte, un momento…")
    try:
        await run_search_and_send(context.application, update.effective_chat.id)
    except Exception:
        logger.exception("Errore nel comando /oggi")
        await update.message.reply_text(
            "❌ Qualcosa è andato storto durante la ricerca. Controlla i log."
        )


async def cmd_destinazioni(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    engine: DealEngine = context.bot_data["engine"]
    storage = engine.storage
    args = [a.upper() for a in context.args]

    if not args:
        whitelist, blacklist = engine.destination_lists()
        wl = ", ".join(whitelist) if whitelist else "(vuota → tutte le destinazioni)"
        bl = ", ".join(blacklist) if blacklist else "(vuota)"
        await update.message.reply_html(
            f"<b>Whitelist:</b> {escape(wl)}\n<b>Blacklist:</b> {escape(bl)}\n\n"
            "Usa: /destinazioni add|remove|block|unblock CODICE_IATA, oppure reset"
        )
        return

    action = args[0].lower()
    codes = [c for c in args[1:] if len(c) == 3 and c.isalpha()]

    if action == "reset":
        storage.delete_setting("whitelist")
        storage.delete_setting("blacklist")
        await update.message.reply_text("✅ Liste riportate ai valori del file .env")
        return

    if action not in {"add", "remove", "block", "unblock"} or not codes:
        await update.message.reply_text(
            "Uso: /destinazioni add|remove|block|unblock CODICE_IATA (es. /destinazioni add LIS)"
        )
        return

    whitelist, blacklist = engine.destination_lists()
    whitelist, blacklist = list(whitelist), list(blacklist)
    if action == "add":
        whitelist = sorted(set(whitelist) | set(codes))
    elif action == "remove":
        whitelist = [c for c in whitelist if c not in codes]
    elif action == "block":
        blacklist = sorted(set(blacklist) | set(codes))
    elif action == "unblock":
        blacklist = [c for c in blacklist if c not in codes]

    storage.set_setting("whitelist", whitelist)
    storage.set_setting("blacklist", blacklist)
    await update.message.reply_text(f"✅ Fatto: {action} {', '.join(codes)}")


async def cmd_soglia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    engine: DealEngine = context.bot_data["engine"]
    args = context.args

    usage = "Uso: /soglia europa|europa_ar|extra|extra_ar|sconto|peso_ar NUMERO"

    if not args:
        thr_e, thr_x, thr_e_rt, thr_x_rt, disc = engine.thresholds()
        rt_weight = engine.rt_score_weight()
        await update.message.reply_html(
            f"<b>Soglie attuali</b>\n"
            f"• Europa/corto raggio, solo andata: {thr_e:.0f} €\n"
            f"• Europa/corto raggio, A/R: {thr_e_rt:.0f} €\n"
            f"• Extra-Europa, solo andata: {thr_x:.0f} €\n"
            f"• Extra-Europa, A/R: {thr_x_rt:.0f} €\n"
            f"• Sconto minimo vs media storica: {disc:.0f}%\n"
            f"• Peso A/R nel ranking: {rt_weight:g} (1 = neutro, più basso = A/R favorite)\n\n"
            "Modifica con: /soglia europa 45 · /soglia europa_ar 70 · "
            "/soglia extra 250 · /soglia extra_ar 500 · /soglia sconto 30 · "
            "/soglia peso_ar 0.75"
        )
        return

    if len(args) != 2:
        await update.message.reply_text(usage)
        return

    key_map = {
        "europa": "threshold_europe",
        "extra": "threshold_extra",
        "europa_ar": "threshold_europe_rt",
        "extra_ar": "threshold_extra_rt",
        "sconto": "discount_pct",
        "peso_ar": "rt_score_weight",
    }
    key = key_map.get(args[0].lower())
    try:
        value = float(args[1].replace(",", "."))
    except ValueError:
        value = None
    if key is None or value is None or value <= 0:
        await update.message.reply_text(usage)
        return

    engine.storage.set_setting(key, value)
    await update.message.reply_text(f"✅ Soglia '{args[0].lower()}' impostata a {value:g}")
