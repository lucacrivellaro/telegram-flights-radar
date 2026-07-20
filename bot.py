"""Bot Telegram multi-utente: iscrizione con approvazione admin, comandi
interattivi per utente e invio del riepilogo giornaliero."""

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

import airports
from config import Config
from deals import DealEngine
from formatter import build_message

logger = logging.getLogger(__name__)

HELP_TEXT = """<b>Radar voli — comandi</b>

/oggi — cerca subito le offerte
/aeroporti — i tuoi aeroporti di partenza
/aeroporti add|remove XXX — gestiscili (codici IATA, es. VRN)
/aeroporti reset — torna ai default ({default_origins})
/destinazioni — le tue whitelist e blacklist
/destinazioni add|remove XXX — gestisci la whitelist (vuota = tutte)
/destinazioni block|unblock XXX — gestisci la blacklist
/destinazioni reset — torna ai valori di default
/soglia — le tue soglie di prezzo
/soglia europa|europa_ar|extra|extra_ar|sconto|peso_ar N — imposta un parametro
/stop — sospendi le notifiche giornaliere
/help — questo messaggio

Ogni giorno alle {daily_time} ricevi le migliori offerte
dai tuoi aeroporti ({origins}): {trip_mode} con soggiorni di
{min_nights}-{max_nights} notti. Tutte le impostazioni sono personali.

<b>Come funzionano le soglie</b>
Un'offerta viene segnalata se il prezzo è sotto la soglia assoluta
(europa/europa_ar/extra/extra_ar) OPPURE se costa almeno "sconto"% in meno
della media storica di quella rotta (serve un minimo di rilevazioni
accumulate nei giorni precedenti, quindi all'inizio conta solo la soglia
assoluta). Bastano una delle due condizioni, non entrambe.

• <b>europa</b> — soglia (€) per voli di sola andata verso Europa/corto
raggio (es. Italia, Spagna, Francia...)
• <b>extra</b> — soglia (€) per voli di sola andata verso destinazioni
extra-Europa/lungo raggio (le destinazioni sconosciute usano la soglia più
bassa "europa", per prudenza)
• <b>europa_ar</b> / <b>extra_ar</b> — le stesse due soglie ma per andata e
ritorno insieme (prezzo totale del viaggio, non a tratta)
• <b>sconto</b> — sconto minimo (%) rispetto al prezzo medio storico della
rotta per considerare l'offerta un affare, indipendentemente dalla soglia
assoluta
• <b>peso_ar</b> — non è un filtro ma un peso nella classifica: valori
&lt;1 fanno salire le offerte A/R in cima al messaggio, 1 = nessuna
preferenza tra andata semplice e A/R

{soglie_one_way_note}Esempi: /soglia europa 45 · /soglia europa_ar 70 · /soglia extra 250 ·
/soglia extra_ar 500 · /soglia sconto 30 · /soglia peso_ar 0.75"""

ADMIN_HELP_TEXT = """

<b>Comandi admin</b>
/utenti — elenco iscritti e richieste in attesa
/approva CHAT_ID — approva una richiesta
/rifiuta CHAT_ID — rifiuta/blocca un utente"""


def build_application(config: Config, engine: DealEngine) -> Application:
    app = (
        ApplicationBuilder()
        .token(config.telegram_token)
        .post_init(_on_startup)
        .build()
    )
    app.bot_data["config"] = config
    app.bot_data["engine"] = engine

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("oggi", cmd_oggi))
    app.add_handler(CommandHandler("aeroporti", cmd_aeroporti))
    app.add_handler(CommandHandler("destinazioni", cmd_destinazioni))
    app.add_handler(CommandHandler("soglia", cmd_soglia))
    app.add_handler(CommandHandler("utenti", cmd_utenti))
    app.add_handler(CommandHandler("approva", cmd_approva))
    app.add_handler(CommandHandler("rifiuta", cmd_rifiuta))
    return app


async def _on_startup(app: Application) -> None:
    logger.info("Bot avviato")


async def run_search_and_send(app: Application, chat_id: str | int) -> None:
    """Esegue la ricerca per l'utente (bloccante, in thread) e invia il risultato."""
    engine: DealEngine = app.bot_data["engine"]
    result = await asyncio.to_thread(engine.search_for_user, chat_id)
    await app.bot.send_message(
        chat_id=chat_id,
        text=build_message(result),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# --- helper accesso ----------------------------------------------------------


def _is_admin(update: Update, config: Config) -> bool:
    return str(update.effective_chat.id) == str(config.admin_chat_id)


async def _require_active(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True se l'utente è iscritto e approvato; altrimenti risponde e ritorna False."""
    engine: DealEngine = context.bot_data["engine"]
    user = engine.storage.get_user(update.effective_chat.id)
    if user and user["status"] == "active":
        return True
    if user and user["status"] == "pending":
        await update.message.reply_text(
            "⏳ La tua richiesta è in attesa di approvazione, ti avviso appena sarà accettata."
        )
    elif user and user["status"] == "stopped":
        await update.message.reply_text(
            "🔕 Notifiche sospese: usa /start per riattivarle."
        )
    else:
        await update.message.reply_text(
            "👋 Per usare il bot serve l'iscrizione: manda /start per richiederla."
        )
    return False


# --- iscrizione ---------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    engine: DealEngine = context.bot_data["engine"]
    storage = engine.storage
    chat = update.effective_chat
    tg_user = update.effective_user
    user = storage.get_user(chat.id)

    if _is_admin(update, config):
        if user is None:
            storage.add_user(chat.id, tg_user.username, tg_user.first_name, "active")
        elif user["status"] != "active":
            storage.set_user_status(chat.id, "active")
        await cmd_help(update, context)
        return

    if user is None:
        storage.add_user(chat.id, tg_user.username, tg_user.first_name)
        await update.message.reply_text(
            "✅ Richiesta di iscrizione inviata! Riceverai un messaggio "
            "appena l'admin la approva."
        )
        who = escape(tg_user.first_name or "?")
        if tg_user.username:
            who += f" (@{escape(tg_user.username)})"
        try:
            await context.bot.send_message(
                chat_id=config.admin_chat_id,
                text=(
                    f"🔔 <b>Nuova richiesta di iscrizione</b>\n{who} — id <code>{chat.id}</code>\n\n"
                    f"/approva {chat.id} oppure /rifiuta {chat.id}"
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            logger.exception("Impossibile notificare l'admin della nuova richiesta")
        return

    status = user["status"]
    if status == "active":
        await cmd_help(update, context)
    elif status == "pending":
        await update.message.reply_text(
            "⏳ La tua richiesta è già in attesa di approvazione."
        )
    elif status == "stopped":
        # era già stato approvato: riattivazione senza nuova approvazione
        storage.set_user_status(chat.id, "active")
        await update.message.reply_text(
            "🔔 Bentornato! Notifiche giornaliere riattivate."
        )
    else:  # blocked
        await update.message.reply_text("🚫 L'accesso al bot non è consentito.")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_active(update, context):
        return
    engine: DealEngine = context.bot_data["engine"]
    engine.storage.set_user_status(update.effective_chat.id, "stopped")
    await update.message.reply_text(
        "🔕 Notifiche giornaliere sospese. Usa /start quando vuoi riattivarle."
    )


# --- comandi admin -------------------------------------------------------------

_STATUS_ICONS = {"active": "✅", "pending": "⏳", "stopped": "🔕", "blocked": "🚫"}


async def cmd_utenti(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    if not _is_admin(update, config):
        return
    engine: DealEngine = context.bot_data["engine"]
    users = engine.storage.list_users()
    if not users:
        await update.message.reply_text("Nessun utente registrato.")
        return
    lines = []
    for u in users:
        who = escape(u["first_name"] or "?")
        if u["username"]:
            who += f" (@{escape(u['username'])})"
        icon = _STATUS_ICONS.get(u["status"], "❓")
        lines.append(f"{icon} {who} — <code>{u['chat_id']}</code> · {u['status']}")
    await update.message.reply_html("<b>Utenti</b>\n" + "\n".join(lines))


async def _set_status_by_admin(
    update: Update, context: ContextTypes.DEFAULT_TYPE, new_status: str,
    reply_ok: str, notify_user: str | None,
) -> None:
    config: Config = context.bot_data["config"]
    if not _is_admin(update, config):
        return
    engine: DealEngine = context.bot_data["engine"]
    if len(context.args) != 1:
        await update.message.reply_text("Uso: indica il CHAT_ID (vedi /utenti)")
        return
    chat_id = context.args[0]
    user = engine.storage.get_user(chat_id)
    if user is None:
        await update.message.reply_text(f"Nessun utente con id {chat_id}.")
        return
    engine.storage.set_user_status(chat_id, new_status)
    await update.message.reply_text(reply_ok.format(chat_id=chat_id))
    if notify_user:
        try:
            await context.bot.send_message(chat_id=chat_id, text=notify_user)
        except Exception:
            logger.exception("Impossibile notificare l'utente %s", chat_id)


async def cmd_approva(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_status_by_admin(
        update,
        context,
        "active",
        "✅ Utente {chat_id} approvato.",
        "🎉 La tua iscrizione è stata approvata! Usa /help per i comandi: "
        "da domani riceverai le offerte ogni giorno.",
    )


async def cmd_rifiuta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_status_by_admin(
        update, context, "blocked", "🚫 Utente {chat_id} bloccato.", None
    )


# --- comandi utente -------------------------------------------------------------


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_active(update, context):
        return
    config: Config = context.bot_data["config"]
    engine: DealEngine = context.bot_data["engine"]
    prefs = engine.prefs_for(update.effective_chat.id)
    text = HELP_TEXT.format(
        daily_time=config.daily_time,
        origins=", ".join(prefs.origins),
        default_origins=", ".join(config.origins),
        trip_mode=(
            "sola andata e andata/ritorno"
            if config.search_one_way
            else "andata/ritorno"
        ),
        soglie_one_way_note=(
            ""
            if config.search_one_way
            else "⚠️ La ricerca sola andata è disattivata: al momento contano "
            "solo <b>europa_ar</b> ed <b>extra_ar</b>, \"europa\" ed \"extra\" "
            "non hanno effetto.\n\n"
        ),
        min_nights=config.min_trip_nights,
        max_nights=config.max_trip_nights,
    )
    if _is_admin(update, config):
        text += ADMIN_HELP_TEXT
    await update.message.reply_html(text)


async def cmd_oggi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_active(update, context):
        return
    await update.message.reply_text("🔍 Cerco le offerte, un momento…")
    try:
        await run_search_and_send(context.application, update.effective_chat.id)
    except Exception:
        logger.exception("Errore nel comando /oggi")
        await update.message.reply_text(
            "❌ Qualcosa è andato storto durante la ricerca. Controlla i log."
        )


async def cmd_aeroporti(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_active(update, context):
        return
    config: Config = context.bot_data["config"]
    engine: DealEngine = context.bot_data["engine"]
    storage = engine.storage
    chat_id = update.effective_chat.id
    args = [a.upper() for a in context.args]

    if not args:
        origins = engine.prefs_for(chat_id).origins
        lines = [f"• {code} — {escape(airports.info(code)[0])}" for code in origins]
        await update.message.reply_html(
            "<b>I tuoi aeroporti di partenza</b>\n" + "\n".join(lines) +
            "\n\nUsa: /aeroporti add|remove CODICE_IATA, oppure reset"
        )
        return

    action = args[0].lower()
    codes = [c for c in args[1:] if len(c) == 3 and c.isalpha()]

    if action == "reset":
        storage.delete_user_setting(chat_id, "origins")
        await update.message.reply_text(
            f"✅ Aeroporti riportati ai default: {', '.join(config.origins)}"
        )
        return

    if action not in {"add", "remove"} or not codes:
        await update.message.reply_text(
            "Uso: /aeroporti add|remove CODICE_IATA (es. /aeroporti add MXP)"
        )
        return

    unknown = [c for c in codes if not airports.is_known(c)]
    if unknown:
        await update.message.reply_text(
            f"❌ Codici IATA sconosciuti: {', '.join(unknown)}"
        )
        return

    origins = list(engine.prefs_for(chat_id).origins)
    if action == "add":
        origins = sorted(set(origins) | set(codes))
    else:
        origins = [c for c in origins if c not in codes]
        if not origins:
            await update.message.reply_text(
                "❌ Deve rimanere almeno un aeroporto di partenza."
            )
            return

    storage.set_user_setting(chat_id, "origins", origins)
    await update.message.reply_text(
        f"✅ Fatto! I tuoi aeroporti: {', '.join(origins)}"
    )


async def cmd_destinazioni(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_active(update, context):
        return
    engine: DealEngine = context.bot_data["engine"]
    storage = engine.storage
    chat_id = update.effective_chat.id
    args = [a.upper() for a in context.args]

    if not args:
        prefs = engine.prefs_for(chat_id)
        wl = ", ".join(prefs.whitelist) if prefs.whitelist else "(vuota → tutte le destinazioni)"
        bl = ", ".join(prefs.blacklist) if prefs.blacklist else "(vuota)"
        await update.message.reply_html(
            f"<b>Whitelist:</b> {escape(wl)}\n<b>Blacklist:</b> {escape(bl)}\n\n"
            "Usa: /destinazioni add|remove|block|unblock CODICE_IATA, oppure reset"
        )
        return

    action = args[0].lower()
    codes = [c for c in args[1:] if len(c) == 3 and c.isalpha()]

    if action == "reset":
        storage.delete_user_setting(chat_id, "whitelist")
        storage.delete_user_setting(chat_id, "blacklist")
        await update.message.reply_text("✅ Liste riportate ai valori di default")
        return

    if action not in {"add", "remove", "block", "unblock"} or not codes:
        await update.message.reply_text(
            "Uso: /destinazioni add|remove|block|unblock CODICE_IATA (es. /destinazioni add LIS)"
        )
        return

    prefs = engine.prefs_for(chat_id)
    whitelist, blacklist = list(prefs.whitelist), list(prefs.blacklist)
    if action == "add":
        whitelist = sorted(set(whitelist) | set(codes))
    elif action == "remove":
        whitelist = [c for c in whitelist if c not in codes]
    elif action == "block":
        blacklist = sorted(set(blacklist) | set(codes))
    elif action == "unblock":
        blacklist = [c for c in blacklist if c not in codes]

    storage.set_user_setting(chat_id, "whitelist", whitelist)
    storage.set_user_setting(chat_id, "blacklist", blacklist)
    await update.message.reply_text(f"✅ Fatto: {action} {', '.join(codes)}")


async def cmd_soglia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_active(update, context):
        return
    engine: DealEngine = context.bot_data["engine"]
    chat_id = update.effective_chat.id
    args = context.args

    usage = "Uso: /soglia europa|europa_ar|extra|extra_ar|sconto|peso_ar NUMERO"

    if not args:
        prefs = engine.prefs_for(chat_id)
        await update.message.reply_html(
            f"<b>Le tue soglie</b>\n"
            f"• Europa/corto raggio, solo andata: {prefs.threshold_europe:.0f} €\n"
            f"• Europa/corto raggio, A/R: {prefs.threshold_europe_rt:.0f} €\n"
            f"• Extra-Europa, solo andata: {prefs.threshold_extra:.0f} €\n"
            f"• Extra-Europa, A/R: {prefs.threshold_extra_rt:.0f} €\n"
            f"• Sconto minimo vs media storica: {prefs.discount_pct:.0f}%\n"
            f"• Peso A/R nel ranking: {prefs.rt_score_weight:g} (1 = neutro, più basso = A/R favorite)\n\n"
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

    engine.storage.set_user_setting(chat_id, key, value)
    await update.message.reply_text(f"✅ Soglia '{args[0].lower()}' impostata a {value:g}")
