"""Stock briefing messaging application."""

import logging

# HTTP client INFO messages include URLs; Telegram URLs contain the bot credential.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
