"""Ricerca una tantum per verificare che tutto funzioni, senza scheduler.

    python search_once.py          # stampa i risultati a terminale
    python search_once.py --send   # li invia anche alla chat Telegram
"""

import argparse
import asyncio
import logging

from config import Config
from deals import DealEngine
from formatter import build_message
from storage import Storage

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--send", action="store_true", help="invia il risultato alla chat Telegram"
    )
    args = parser.parse_args()

    config = Config.from_env()
    storage = Storage(config.db_path, admin_chat_id=config.admin_chat_id)
    engine = DealEngine(config, storage)

    # la ricerca di test usa le preferenze dell'admin (o i default del .env
    # se TELEGRAM_CHAT_ID non è configurato); in modalità test non marchiamo
    # le offerte come inviate, così il messaggio giornaliero non ne risente
    result = engine.search_for_user(config.admin_chat_id, mark_as_sent=args.send)
    message = build_message(result)

    print("\n" + "=" * 60)
    print(f"Tariffe analizzate: {result.total_offers}")
    print(f"Offerte selezionate: {len(result.deals)}")
    print(f"Errori: {result.errors or 'nessuno'}")
    print("=" * 60)
    print(message)

    if args.send:
        config.require_telegram()
        from telegram import Bot
        from telegram.constants import ParseMode

        async def send() -> None:
            async with Bot(config.telegram_token) as bot:
                await bot.send_message(
                    chat_id=config.admin_chat_id,
                    text=message,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )

        asyncio.run(send())
        print("\n✅ Messaggio inviato su Telegram")


if __name__ == "__main__":
    main()
