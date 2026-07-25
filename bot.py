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

/oggi — cerca subito le offerte (è una prova: non toglie nulla al
messaggio di domani, puoi rilanciarlo quante volte vuoi)
/aeroporti — i tuoi aeroporti di partenza
/aeroporti add|remove XXX — gestisci quelli per l'Europa (es. VRN)
/aeroporti intl add|remove XXX — gestisci quelli per il lungo raggio
/aeroporti reset — torna ai default (Europa {default_origins} · lungo raggio {default_intl})
/destinazioni — le tue whitelist e blacklist
/destinazioni add|remove XXX — gestisci la whitelist (vuota = tutte)
/destinazioni block|unblock XXX — gestisci la blacklist
/destinazioni reset — torna ai valori di default
/tappe — tappe obbligatorie dei viaggi a tappe
/tappe add|remove NYC — imponi (o togli) una città sul percorso
  (più città = basta passare da una qualsiasi)
/tappe reset — nessun vincolo di percorso
/soglia — le tue soglie di prezzo
/soglia europa|extra|multi|sconto N — imposta un parametro
/stop — sospendi le notifiche giornaliere
/help — questo messaggio

Ogni giorno alle {daily_time} ricevi le migliori offerte
<b>andata/ritorno</b> con soggiorni di {min_nights}-{max_nights} notti,
<b>solo voli diretti</b>: {n_europa} verso l'Europa e {n_extra} extra-Europa,
più {multi_top_n} viaggi a tappe. I {n_extra} posti extra-Europa sono
riservati — senza, il lungo raggio non entrerebbe mai in classifica perché
costa sempre più di un volo europeo. Se una fascia non ha abbastanza offerte
i suoi posti passano all'altra. Tutte le impostazioni sono personali.

<b>✈️ Due liste di aeroporti</b>
• <b>Europa</b> ({origins}) — da qui partono le offerte verso Europa e
corto raggio
• <b>Lungo raggio</b> ({intl_origins}) — da qui partono le offerte
extra-Europa e i viaggi a tappe, perché uno scalo regionale non ha voli
intercontinentali

<b>🧭 Viaggi a tappe</b>
In fondo al messaggio trovi fino a {multi_top_n} itinerari a più tappe:
partenza da un aeroporto di lungo raggio, {multi_min_stops}-{multi_max_stops}
città intermedie <b>fuori dall'Europa</b> e rientro, per un viaggio di
{multi_min_trip_days}-{multi_max_trip_days} giorni. Ogni sosta dura almeno
{multi_min_stay} notti e non ha un tetto proprio: può arrivare a coprire
tutto il viaggio, purché resti nei {multi_max_trip_days} giorni.
Sono biglietti di sola andata concatenati: ogni tratta ha il suo link e si
prenota a parte, quindi il prezzo mostrato è la somma delle tratte. Il
criterio è la soglia <b>multi</b>, che vale sull'intero itinerario e non
sul singolo volo.

Con <b>/tappe add NYC</b> imponi una città sul percorso: da lì in poi ogni
itinerario proposto ci passerà. È diverso da /destinazioni, che è una
whitelist sui voli singoli — qui stai vincolando il percorso, non
scegliendo la meta. Se ne aggiungi più di una valgono in <b>OR</b>: basta
che l'itinerario ne tocchi una, quindi ogni città in più allarga le
possibilità invece di restringerle. Vincolare alza il prezzo, quindi
potresti dover alzare anche /soglia multi.

<b>Come funzionano le soglie</b>
Un'offerta viene segnalata se il prezzo totale A/R è sotto la soglia
assoluta OPPURE se costa almeno "sconto"% in meno della media storica di
quella rotta (serve un minimo di rilevazioni accumulate nei giorni
precedenti, quindi all'inizio conta solo la soglia assoluta). Basta una
delle due condizioni, non entrambe.

• <b>europa</b> — soglia (€) per l'A/R verso Europa/corto raggio (prezzo
totale del viaggio, non a tratta)
• <b>extra</b> — soglia (€) per l'A/R verso destinazioni extra-Europa/lungo
raggio (le destinazioni sconosciute usano la soglia più bassa "europa",
per prudenza)
• <b>multi</b> — soglia (€) sul <i>totale</i> di un viaggio a tappe (somma di
tutte le tratte). Le altre soglie non si applicano ai viaggi a tappe
• <b>sconto</b> — sconto minimo (%) rispetto al prezzo medio storico della
rotta per considerare l'offerta un affare, indipendentemente dalla soglia
assoluta

Esempi: /soglia europa 70 · /soglia extra 550 · /soglia multi 700 ·
/soglia sconto 30"""

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
    app.add_handler(CommandHandler("tappe", cmd_tappe))
    app.add_handler(CommandHandler("soglia", cmd_soglia))
    app.add_handler(CommandHandler("utenti", cmd_utenti))
    app.add_handler(CommandHandler("approva", cmd_approva))
    app.add_handler(CommandHandler("rifiuta", cmd_rifiuta))
    return app


async def _on_startup(app: Application) -> None:
    logger.info("Bot avviato")


async def run_search_and_send(
    app: Application, chat_id: str | int, mark_as_sent: bool = False
) -> None:
    """Esegue la ricerca per l'utente (bloccante, in thread) e invia il risultato.

    `mark_as_sent=False` di default perché questa è la strada di /oggi, che è
    una ricerca a richiesta: marcarle brucerebbe le offerte per
    `resend_cooldown_days` giorni, e due /oggi di fila darebbero risultati via
    via più poveri. L'invio giornaliero passa invece da `select_for_user()` e
    continua a marcare, che è l'unico dedup che serve davvero."""
    engine: DealEngine = app.bot_data["engine"]
    result = await asyncio.to_thread(engine.search_for_user, chat_id, mark_as_sent)
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
        intl_origins=", ".join(prefs.intl_origins),
        default_origins=", ".join(config.origins),
        default_intl=", ".join(config.intl_origins),
        min_nights=config.min_trip_nights,
        max_nights=config.max_trip_nights,
        n_europa=config.top_n - config.top_n_extra,
        n_extra=config.top_n_extra,
        multi_top_n=config.multi_top_n,
        multi_min_stops=config.multi_min_stops,
        multi_max_stops=config.multi_max_stops,
        multi_min_stay=config.multi_min_stay,
        multi_min_trip_days=config.multi_min_trip_days,
        multi_max_trip_days=config.multi_max_trip_days,
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

    def elenco(codes: list[str]) -> str:
        return "\n".join(
            f"• {code} — {escape(airports.info(code)[0])}" for code in codes
        )

    if not args:
        prefs = engine.prefs_for(chat_id)
        await update.message.reply_html(
            "<b>Europa / corto raggio</b>\n" + elenco(prefs.origins) +
            "\n\n<b>Lungo raggio</b> (extra-Europa e viaggi a tappe)\n"
            + elenco(prefs.intl_origins) +
            "\n\nUsa: /aeroporti add|remove CODICE_IATA per la prima lista,\n"
            "/aeroporti intl add|remove CODICE_IATA per la seconda,\n"
            "/aeroporti reset per tornare ai default di entrambe"
        )
        return

    # "/aeroporti intl ..." agisce sulla lista lungo raggio, altrimenti su quella europea
    intl = args[0].lower() == "intl"
    if intl:
        args = args[1:]
    if not args:
        await update.message.reply_text(
            "Uso: /aeroporti intl add|remove CODICE_IATA (es. /aeroporti intl add FCO)"
        )
        return

    key = "intl_origins" if intl else "origins"
    lista = "lungo raggio" if intl else "Europa"
    action = args[0].lower()
    codes = [c for c in args[1:] if len(c) == 3 and c.isalpha()]

    if action == "reset":
        # il reset senza "intl" riporta ai default entrambe le liste
        storage.delete_user_setting(chat_id, key)
        if not intl:
            storage.delete_user_setting(chat_id, "intl_origins")
            await update.message.reply_text(
                f"✅ Aeroporti riportati ai default — Europa: "
                f"{', '.join(config.origins)} · lungo raggio: "
                f"{', '.join(config.intl_origins)}"
            )
            return
        await update.message.reply_text(
            f"✅ Aeroporti {lista} riportati ai default: "
            f"{', '.join(config.intl_origins)}"
        )
        return

    if action not in {"add", "remove"} or not codes:
        await update.message.reply_text(
            "Uso: /aeroporti [intl] add|remove CODICE_IATA (es. /aeroporti add MXP)"
        )
        return

    unknown = [c for c in codes if not airports.is_known(c)]
    if unknown:
        await update.message.reply_text(
            f"❌ Codici IATA sconosciuti: {', '.join(unknown)}"
        )
        return

    prefs = engine.prefs_for(chat_id)
    origins = list(prefs.intl_origins if intl else prefs.origins)
    if action == "add":
        origins = sorted(set(origins) | set(codes))
    else:
        origins = [c for c in origins if c not in codes]
        if not origins:
            await update.message.reply_text(
                f"❌ Deve rimanere almeno un aeroporto nella lista {lista}."
            )
            return

    storage.set_user_setting(chat_id, key, origins)
    await update.message.reply_text(
        f"✅ Fatto! Aeroporti {lista}: {', '.join(origins)}"
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


async def cmd_tappe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tappe obbligatorie dei viaggi a tappe: ogni itinerario deve toccarle.

    Separato da /destinazioni di proposito: quella è una whitelist sui voli
    singoli ("proponimi solo queste mete"), questa è un vincolo di percorso
    ("qualunque itinerario, ma deve passare di qui")."""
    if not await _require_active(update, context):
        return
    config: Config = context.bot_data["config"]
    engine: DealEngine = context.bot_data["engine"]
    storage = engine.storage
    chat_id = update.effective_chat.id
    args = [a.upper() for a in context.args]
    prefs = engine.prefs_for(chat_id)

    if not args:
        if prefs.multi_required:
            elenco = "\n".join(
                f"• {c} — {escape(airports.info(c)[0])}" for c in prefs.multi_required
            )
            testo = (
                f"<b>Tappe obbligatorie</b>\n{elenco}\n\n"
                + (
                    "Ogni viaggio a tappe proposto passerà da <b>almeno una</b> "
                    "di queste città (non da tutte): più ne aggiungi, più "
                    "possibilità ci sono."
                    if len(prefs.multi_required) > 1
                    else "Ogni viaggio a tappe proposto passerà da questa città."
                )
            )
        else:
            testo = (
                "<b>Tappe obbligatorie</b>\nNessuna: gli itinerari possono "
                "passare ovunque."
            )
        await update.message.reply_html(
            testo + "\n\nUsa: /tappe add NYC · /tappe remove NYC · /tappe reset"
        )
        return

    action = args[0].lower()
    codes = [c for c in args[1:] if len(c) == 3 and c.isalpha()]

    if action == "reset":
        storage.delete_user_setting(chat_id, "multi_required")
        await update.message.reply_text(
            "✅ Vincolo rimosso: gli itinerari possono passare ovunque."
        )
        return

    if action not in {"add", "remove"} or not codes:
        await update.message.reply_text(
            "Uso: /tappe add|remove CODICE_IATA (es. /tappe add NYC)"
        )
        return

    unknown = [c for c in codes if not airports.is_known(c)]
    if unknown:
        await update.message.reply_text(
            f"❌ Codici IATA sconosciuti: {', '.join(unknown)}"
        )
        return

    required = list(prefs.multi_required)
    if action == "add":
        required = sorted(set(required) | set(codes))
    else:
        required = [c for c in required if c not in codes]

    storage.set_user_setting(chat_id, "multi_required", required)
    if not required:
        await update.message.reply_text(
            "✅ Nessuna tappa obbligatoria: gli itinerari possono passare ovunque."
        )
        return

    dettaglio = ", ".join(
        f"{c} ({escape(airports.info(c)[0])})" for c in required
    )
    avvisi = []
    if len(required) > 1:
        avvisi.append(
            "ℹ️ Basta che un itinerario ne tocchi <b>una</b>, non tutte."
        )
    bloccate = [c for c in required if c in prefs.blacklist]
    if bloccate:
        avvisi.append(
            f"⚠️ {', '.join(bloccate)} è anche in blacklist (/destinazioni): "
            "da lì non uscirà mai un itinerario."
        )
    avvisi.append(
        "ℹ️ Vincolare il percorso alza il prezzo: se la sezione resta vuota, "
        f"alza la soglia con /soglia multi (ora {prefs.threshold_multi:.0f}€)."
    )
    await update.message.reply_html(
        f"✅ Tappe obbligatorie: {dettaglio}\n\n" + "\n".join(avvisi)
    )


async def cmd_soglia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_active(update, context):
        return
    engine: DealEngine = context.bot_data["engine"]
    chat_id = update.effective_chat.id
    args = context.args

    usage = "Uso: /soglia europa|extra|multi|sconto NUMERO"

    if not args:
        prefs = engine.prefs_for(chat_id)
        await update.message.reply_html(
            f"<b>Le tue soglie</b> (prezzo totale A/R)\n"
            f"• Europa/corto raggio: {prefs.threshold_europe_rt:.0f} €\n"
            f"• Extra-Europa/lungo raggio: {prefs.threshold_extra_rt:.0f} €\n"
            f"• Viaggio a tappe, totale itinerario: {prefs.threshold_multi:.0f} €\n"
            f"• Sconto minimo vs media storica: {prefs.discount_pct:.0f}%\n\n"
            "Modifica con: /soglia europa 70 · /soglia extra 550 · "
            "/soglia multi 700 · /soglia sconto 30"
        )
        return

    if len(args) != 2:
        await update.message.reply_text(usage)
        return

    # le offerte inviate sono solo A/R, quindi "europa"/"extra" sono le soglie
    # sul totale A/R; i vecchi nomi _ar restano accettati per abitudine
    key_map = {
        "europa": "threshold_europe_rt",
        "extra": "threshold_extra_rt",
        "europa_ar": "threshold_europe_rt",
        "extra_ar": "threshold_extra_rt",
        "multi": "threshold_multi",
        "sconto": "discount_pct",
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
