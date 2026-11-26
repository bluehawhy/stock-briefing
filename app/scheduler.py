"""API-owned daily briefing, sharing delivery guards with the manual CLI."""
import asyncio
from datetime import datetime, time
import logging
import os
from zoneinfo import ZoneInfo

from app.briefings import BriefingEngine
from app.logs import log_python_exception
from app.telegram import send_briefing

LOG = logging.getLogger(__name__)
KST = ZoneInfo('Asia/Seoul')


def run_daily(settings, store):
    store.init()
    # Keep a PostgreSQL session lock for the entire analysis + network send.
    # Unlike an expiring lease it cannot expire during a long analysis.
    with store.connect() as guard:
        if store.database_options is not None and not guard.execute(
            'SELECT pg_try_advisory_lock(734901, 3)'
        ).fetchone()[0]:
            return 'skipped: daily briefing already running'
        owner = store.acquire_lease('daily-worker', 7200)
        if not owner:
            return 'skipped: daily worker already running'
        try:
            day = datetime.now(KST).date()
            if not settings.telegram_allowed_user_ids or not settings.telegram_bot_token:
                raise RuntimeError('Scheduled Telegram configuration is incomplete')
            outcomes = []
            for user_id in sorted(settings.telegram_allowed_user_ids):
                if not store.telegram_chat(user_id):
                    outcomes.append(user_id + ': no Telegram chat')
                    continue
                if not store.get_for_user('settings', 'notifications', user_id, {'enabled': True})['enabled']:
                    outcomes.append(user_id + ': notifications disabled')
                    continue
                BriefingEngine(settings, store, user_id=user_id).generate()
                # Crossing midnight must not send a different day's saved briefing.
                if datetime.now(KST).date() != day:
                    outcomes.append(user_id + ': day changed during generation')
                    continue
                outcomes.append(user_id + ': ' + asyncio.run(send_briefing(settings, store, user_id)))
            return '; '.join(outcomes) if outcomes else 'no users ready for delivery'
        finally:
            store.release_lease('daily-worker', owner)


async def daily_schedule(settings, store, stop):
    if not LOG.handlers:
        LOG.addHandler(logging.StreamHandler())
    due = time(8, 30)
    LOG.info('Daily scheduler started: timezone=Asia/Seoul time=08:30')
    last_result = None
    while not stop.is_set():
        now = datetime.now(KST)
        if now.time() >= due:
            try:
                # DB calls and market collection never block the ASGI loop.
                result = await asyncio.to_thread(run_daily, settings, store)
                label = (now.date(), result)
                if label != last_result:
                    LOG.info('Daily scheduler result: day=%s result=%s', now.date(), result)
                    last_result = label
            except Exception as exc:
                # Generation failures may retry; send failures reserve delivery and
                # become review_required, preventing blind duplicate sends.
                log_python_exception('daily_scheduler', exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except TimeoutError:
            pass
