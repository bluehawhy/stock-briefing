import json
from dataclasses import replace
from datetime import date

import httpx
import pytest
from cryptography.fernet import Fernet

from app.models import DataError
from app.providers import KisProvider, Vault
from app.storage import Store


@pytest.fixture
def keyed(settings, tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    key = private / "master.key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    result = replace(
        settings,
        db_path=tmp_path / "data" / "stock-briefing.db",
        master_key_file=str(key),
        kis_app_key="test-key",
        kis_app_secret="test-secret",
    )
    return result, Store(result.db_path)


def test_vault_encryption_and_permissions(keyed):
    settings, store = keyed
    vault = Vault(settings)
    encrypted = vault.seal({"secret": "test-value"})
    assert "test-value" not in json.dumps(encrypted)
    assert vault.open(encrypted) == {"secret": "test-value"}
    from pathlib import Path

    Path(settings.master_key_file).chmod(0o644)
    with pytest.raises(DataError, match="권한"):
        Vault(settings)


def test_kis_adjusted_daily_paging_and_encrypted_token_reuse(keyed, monkeypatch):
    settings, store = keyed
    monkeypatch.setattr("app.providers.time.sleep", lambda _: None)
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(
                200, json={"access_token": "private-access-token", "expires_in": 86400}
            )
        assert request.headers["tr_id"] == "FHKST03010100"
        assert request.url.params["FID_ORG_ADJ_PRC"] == "0"
        endpoint = request.url.params["FID_INPUT_DATE_2"]
        days = (
            ["20261006", "20261005"]
            if endpoint == "20261006"
            else ["20261002", "20261001"]
        )
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "output2": [
                    {
                        "stck_bsop_date": d,
                        "stck_oprc": "100",
                        "stck_hgpr": "110",
                        "stck_lwpr": "90",
                        "stck_clpr": "105",
                        "acml_vol": "1000",
                    }
                    for d in days
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = KisProvider(settings, store, client=client)
        bars = provider.daily_bars("005930", date(2026, 10, 1), date(2026, 10, 6))
        assert [b.day.day for b in bars] == [1, 2, 5, 6]
        second = KisProvider(settings, store, client=client)
        second.authenticate()
    assert sum(r.url.path == "/oauth2/tokenP" for r in calls) == 1
    assert "private-access-token" not in json.dumps(
        store.get("credentials", "kis-token")
    )


def test_finalized_investor_flows_and_balance_continuation(
    settings, store, monkeypatch
):
    monkeypatch.setattr("app.providers.time.sleep", lambda _: None)
    settings = replace(settings, kis_app_key="key", kis_app_secret="secret")
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("inquire-investor"):
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "output": [
                        {
                            "stck_bsop_date": "20261006",
                            "orgn_ntby_qty": "-10",
                            "frgn_ntby_qty": "20",
                        }
                    ],
                },
            )
        page_two = bool(request.url.params["CTX_AREA_NK100"])
        if not page_two:
            return httpx.Response(
                200,
                headers={"tr_cont": "M"},
                json={
                    "rt_cd": "0",
                    "output1": [{"pdno": "005930", "hldg_qty": "3"}],
                    "ctx_area_fk100": "fk",
                    "ctx_area_nk100": "nk",
                },
            )
        assert request.headers["tr_cont"] == "N"
        return httpx.Response(
            200, json={"rt_cd": "0", "output1": [{"pdno": "000660", "hldg_qty": "2"}]}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = KisProvider(settings, store, client=client)
        provider.token = "fake-token"
        flows = provider.investor_flows("005930", date(2026, 10, 1), date(2026, 10, 6))
        assert flows[0].institution == -10 and flows[0].foreign == 20
        assert provider.holdings({"number": "12345678", "product": "01"}) == {
            "005930": 3,
            "000660": 2,
        }
    assert all("order-" not in r.url.path for r in requests)


@pytest.mark.parametrize(
    "kind", ["429", "timeout", "invalid_json", "authentication", "empty_token"]
)
def test_provider_failures_are_bounded_and_do_not_echo_secrets(
    settings, store, monkeypatch, kind
):
    settings = replace(
        settings, kis_app_key="DO-NOT-EXPOSE", kis_app_secret="SECRET-VALUE"
    )
    monkeypatch.setattr("app.providers.time.sleep", lambda _: None)
    calls = []

    def handler(request):
        calls.append(request)
        if kind == "429":
            return httpx.Response(429, json={})
        if kind == "timeout":
            raise httpx.ReadTimeout("DO-NOT-EXPOSE SECRET-VALUE")
        if kind == "invalid_json":
            return httpx.Response(200, content=b"bad")
        if kind == "empty_token":
            return httpx.Response(200, json={})
        return httpx.Response(
            200, json={"rt_cd": "1", "msg1": "DO-NOT-EXPOSE SECRET-VALUE"}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = KisProvider(settings, store, client=client)
        with pytest.raises(DataError) as error:
            provider.authenticate()
    assert len(calls) <= 3
    assert "DO-NOT-EXPOSE" not in str(error.value) and "SECRET-VALUE" not in str(
        error.value
    )


def test_calendar_closures_are_explicit(settings, store, monkeypatch):
    monkeypatch.setattr("app.providers.time.sleep", lambda _: None)
    settings = replace(settings, kis_app_key="key", kis_app_secret="secret")

    def handler(request):
        assert request.headers["tr_id"] == "CTCA0903R"
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "output": [
                    {"bass_dt": "20261005", "opnd_yn": "N"},
                    {"bass_dt": "20261006", "opnd_yn": "Y"},
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = KisProvider(settings, store, client=client)
        provider.token = "fake-token"
        assert provider.calendar(date(2026, 10, 5), date(2026, 10, 6)) == {
            date(2026, 10, 5): False,
            date(2026, 10, 6): True,
        }
