"""Formattazione dei messaggi Telegram (HTML)."""

from datetime import date
from html import escape

from deals import EvaluatedOffer, SearchResult

_MESI = [
    "gen", "feb", "mar", "apr", "mag", "giu",
    "lug", "ago", "set", "ott", "nov", "dic",
]
_GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]


def _fmt_date(d: date | None) -> str:
    return f"{d.day} {_MESI[d.month - 1]}" if d else "?"


def _fmt_duration(minutes: int | None) -> str:
    if not minutes:
        return ""
    return f" · {minutes // 60}h{minutes % 60:02d}m totali" if minutes % 60 else f" · {minutes // 60}h totali"


def _fmt_offer(index: int, ev: EvaluatedOffer) -> str:
    o = ev.offer
    city = escape(o.dest_city)
    if o.stops == 0:
        route = "🟢 Diretto"
    else:
        route = f"🔁 {o.stops} scal{'o' if o.stops == 1 else 'i'}{_fmt_duration(o.duration_minutes)}"

    if o.return_date:
        trip = "🔄 Andata/ritorno"
        if o.nights:
            trip += f" · {o.nights} nott{'e' if o.nights == 1 else 'i'}"
        dates = f"{_fmt_date(o.depart_date)} → {_fmt_date(o.return_date)}"
    else:
        trip = "✈️ Solo andata"
        dates = _fmt_date(o.depart_date)

    lines = [
        f"<b>{index}. {o.origin} → {city} ({o.destination})</b> — <b>{o.price:.2f} €</b>",
        f"   {trip} · {dates}",
        f"   {route} · {escape(o.airline)}",
        f"   💡 {escape(ev.reason)}",
        f"   👉 <a href=\"{o.link}\">Prenota</a>",
    ]
    return "\n".join(lines)


def build_message(result: SearchResult, today: date | None = None) -> str:
    today = today or date.today()
    header = (
        f"✈️ <b>Radar voli — {_GIORNI[today.weekday()]} "
        f"{today.day} {_MESI[today.month - 1]}</b>"
    )

    if not result.deals:
        body = "Oggi nessuna offerta sotto soglia. 😴"
        if result.total_offers:
            body += f"\n(analizzate {result.total_offers} tariffe)"
    else:
        body = "\n\n".join(
            _fmt_offer(i, ev) for i, ev in enumerate(result.deals, start=1)
        )
        body += f"\n\n<i>{result.total_offers} tariffe analizzate.</i>"

    parts = [header, "", body]
    if result.errors:
        errs = "\n".join(f"• {escape(e)}" for e in result.errors)
        parts += ["", f"⚠️ <b>Problemi durante la ricerca:</b>\n{errs}"]
    return "\n".join(parts)
