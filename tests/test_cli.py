from datetime import datetime
from zoneinfo import ZoneInfo

from app import cli


def test_daily_worker_collects_before_send_and_skips_already_recorded(
    settings, store, monkeypatch
):
    calls = []

    class Engine:
        def __init__(self, settings, shared):
            self.store = shared

        def generate(self):
            calls.append("generate")
            self.store.save_briefing(
                datetime.now(ZoneInfo("Asia/Seoul")).date(), "generated"
            )

    async def send(settings, shared):
        calls.append("send")
        day = datetime.now(ZoneInfo("Asia/Seoul")).date()
        assert shared.briefing(day)[1] == "generated"
        assert shared.reserve_delivery(day)
        shared.finish_delivery(day, "sent")
        return "sent"

    monkeypatch.setattr(cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(cli, "BriefingEngine", Engine)
    monkeypatch.setattr(cli, "send_briefing", send)
    monkeypatch.setattr("sys.argv", ["stock-briefing", "run-daily"])
    cli.main()
    cli.main()
    assert calls == ["generate", "send"]


def test_daily_worker_does_not_override_another_collector(
    settings, store, monkeypatch, capsys
):
    owner = store.acquire_lease("daily-worker")
    monkeypatch.setattr(cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr("sys.argv", ["stock-briefing", "run-daily"])
    cli.main()
    assert "already running" in capsys.readouterr().out
    assert store.briefing() is None
    store.release_lease("daily-worker", owner)
