# bcn-half

Polls the Barcelona Half 2027 MarketPlace page and sends a Telegram message when a bib is listed.
Stdlib-only Python, no dependencies.

## Setup

```sh
cp .env.example .env   # fill TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
docker compose up -d --build
docker compose logs -f
```

Local run: `python bot.py`. Tests: `python -m unittest`.

Seen listings are kept in memory only; a restart re-alerts on bibs still listed.
