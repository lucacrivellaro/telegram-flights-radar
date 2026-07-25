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


def _fmt_nights(n: int) -> str:
    return f"{n} nott{'e' if n == 1 else 'i'}"


def _fmt_multi(index: int, ev: EvaluatedOffer) -> str:
    """Un itinerario a tappe: una riga per tratta, con sosta e link propri.

    Ogni tratta è un biglietto separato, quindi ogni riga ha il suo link."""
    o = ev.offer
    tappe = o.stopovers
    titolo = " → ".join(escape(leg.dest_city) for leg in tappe)
    giorni = (o.return_date - o.depart_date).days if o.return_date else None

    lines = [
        f"<b>{index}. {escape(o.origin)} → {titolo} → {escape(o.origin)}</b>"
        f" — <b>{o.price:.2f} €</b>",
        f"   🗺 {len(tappe)} tappe · {len(o.legs)} voli · "
        f"{_fmt_date(o.depart_date)} → {_fmt_date(o.return_date)}"
        + (f" · {giorni} giorni" if giorni else ""),
    ]
    for n, leg in enumerate(o.legs, start=1):
        sosta = f" · 🛏 {_fmt_nights(leg.nights)}" if leg.nights else " · 🏠 rientro"
        lines.append(
            f"   {n}. <a href=\"{leg.link}\">{_fmt_date(leg.depart_date)}</a> "
            f"{escape(leg.origin)} → {escape(leg.dest_city)} ({escape(leg.destination)})"
            f" · {leg.price:.0f} €{sosta}"
        )
    lines.append(f"   💡 {escape(ev.reason)}")
    return "\n".join(lines)


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

    blocks: list[str] = []
    if result.deals:
        blocks += [_fmt_offer(i, ev) for i, ev in enumerate(result.deals, start=1)]
    elif not result.multi_deals:
        body = "Oggi nessuna offerta sotto soglia. 😴"
        if result.total_offers:
            body += f"\n(analizzate {result.total_offers} tariffe)"
        blocks.append(body)

    if result.multi_deals:
        blocks.append("🧭 <b>Viaggi a tappe</b>")
        start = len(result.deals) + 1
        blocks += [
            _fmt_multi(i, ev)
            for i, ev in enumerate(result.multi_deals, start=start)
        ]
    elif result.multi_required:
        # con tappe obbligatorie attive una sezione vuota sembrerebbe un guasto:
        # meglio dire che il vincolo non è stato soddisfatto
        tappe = ", ".join(escape(c) for c in result.multi_required)
        via = (
            f"da nessuna di queste città (<b>{tappe}</b>)"
            if len(result.multi_required) > 1
            else f"da <b>{tappe}</b>"
        )
        blocks.append(
            f"🧭 <b>Viaggi a tappe</b>\nNessun itinerario che passi {via} "
            "sotto soglia oggi.\nPuoi allentare il vincolo con /tappe remove, "
            "oppure alzare la soglia con /soglia multi."
        )

    if result.deals or result.multi_deals:
        blocks.append(f"<i>{result.total_offers} tariffe analizzate.</i>")

    parts = [header, "", "\n\n".join(blocks)]
    if result.errors:
        errs = "\n".join(f"• {escape(e)}" for e in result.errors)
        parts += ["", f"⚠️ <b>Problemi durante la ricerca:</b>\n{errs}"]
    return "\n".join(parts)
