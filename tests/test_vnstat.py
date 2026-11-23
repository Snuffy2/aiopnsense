"""Tests for `aiopnsense.vnstat`."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp
import pytest

from aiopnsense import (
    OPNsenseClient,
    OPNsenseConnectionError,
    OPNsenseError,
    OPNsensePrivilegeMissing,
    OPNsenseTimeoutError,
)
from tests.conftest import install_fake_router, make_mock_session_client

ClientType = Callable[..., OPNsenseClient]
HOURLY_PATH = "/api/vnstat/service/hourly"
DAILY_PATH = "/api/vnstat/service/daily"
MONTHLY_PATH = "/api/vnstat/service/monthly"
YEARLY_PATH = "/api/vnstat/service/yearly"
SYSTEM_TIME_PATH = "/api/diagnostics/system/system_time"
UNAVAILABLE_RESPONSES = [
    pytest.param(404, id="missing-endpoint"),
    pytest.param(403, id="permission-denied"),
    pytest.param(500, id="server-error"),
    pytest.param(TimeoutError("timed out"), id="timeout"),
    pytest.param(aiohttp.ClientConnectionError("down"), id="connection-error"),
]
THROW_RESPONSES = [
    pytest.param(403, OPNsensePrivilegeMissing, id="permission-denied"),
    pytest.param(500, OPNsenseConnectionError, id="server-error"),
    pytest.param(TimeoutError("timed out"), OPNsenseTimeoutError, id="timeout"),
]


@pytest.mark.asyncio
async def test_parse_vnstat_payload_hourly_with_split_day_rows(make_client: ClientType) -> None:
    """Hourly vnStat payloads should combine date rows with time rows.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        payload = {
            "response": """
 igc0 / hourly

         hour        rx      |     tx      |    total    |   avg. rate
     03/07/26
         18:00      1.00 GiB |  512.00 MiB |    1.50 GiB |    2.00 Mbit/s
         19:00      2.00 GiB |  256.00 MiB |    2.25 GiB |    3.00 Mbit/s
"""
        }
        parsed = client._parse_vnstat_payload(payload, expected_period="hourly")
        rows = parsed["interfaces"]["igc0"]
        assert len(rows) == 2
        assert rows[0]["label"] == "03/07/26 18:00"
        assert rows[0]["day"] == "03/07/26"
        assert rows[0]["hour"] == "18:00"
        assert rows[1]["label"] == "03/07/26 19:00"
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_parse_vnstat_month_label_apostrophe_format(make_client: ClientType) -> None:
    """Monthly vnStat labels like ``Apr '25`` should parse as year/month.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        assert client._parse_month_label("Apr '25") == (2025, 4)
        assert client._parse_month_label("March '26") == (2026, 3)
        assert client._parse_month_label("2026-03") == (2026, 3)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_vnstat_metrics_yearly_parsing(make_client: ClientType) -> None:
    """get_vnstat_metrics should parse yearly vnStat payload rows with one request.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client,
        session,
        routes={
            ("GET", YEARLY_PATH): {
                "response": """
 igc0  /  yearly

         year        rx      |     tx      |    total    |   avg. rate
     ------------------------+-------------+-------------+---------------
          2023     35.84 TiB |   27.41 TiB |   63.25 TiB |   17.64 Mbit/s
          2024     36.88 TiB |   46.79 TiB |   83.68 TiB |   23.28 Mbit/s
          2025     35.72 TiB |   27.26 TiB |   62.99 TiB |   17.57 Mbit/s
          2026      7.74 TiB |    2.36 TiB |   10.09 TiB |   14.15 Mbit/s
     ------------------------+-------------+-------------+---------------
     estimated     38.88 TiB |   11.85 TiB |   50.73 TiB |
"""
            }
        },
    )
    try:
        parsed = await client.get_vnstat_metrics("yearly")
        rows = parsed["interfaces"]["igc0"]

        tib = 1024**4
        assert parsed["period"] == "yearly"
        assert len(rows) == 4
        assert rows[0]["label"] == "2023"
        assert rows[0]["total_bytes"] == round(63.25 * tib)
        assert rows[3]["label"] == "2026"
        assert rows[3]["avg_rate_bits_per_second"] == 14150000
        assert router.count("GET", YEARLY_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.parametrize("period", ["hourly", "daily", "monthly", "yearly"])
@pytest.mark.asyncio
async def test_get_vnstat_metrics_returns_rows_for_each_period(
    make_client: ClientType, period: str
) -> None:
    """get_vnstat_metrics should return parsed rows for every supported period.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        period (str): Requested vnStat period.
    """
    path = f"/api/vnstat/service/{period}"
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client,
        session,
        routes={
            (
                "GET",
                path,
            ): {
                "response": f"igc0 / {period}\n 03/07/26    1.00 GiB | 1.00 GiB | 2.00 GiB | 1.00 Mbit/s"
            }
        },
    )
    try:
        parsed = await client.get_vnstat_metrics(period)

        assert parsed["period"] == period
        assert len(parsed["interfaces"]["igc0"]) == 1
        assert router.count("GET", path) == 1
    finally:
        await client.async_close()


@pytest.mark.parametrize("route_value", UNAVAILABLE_RESPONSES)
@pytest.mark.asyncio
async def test_get_vnstat_metrics_returns_empty_when_endpoint_unavailable(
    make_client: ClientType, route_value: Any
) -> None:
    """get_vnstat_metrics returns an empty mapping for absent or failing endpoints.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        route_value (Any): Response the Router gives for the hourly endpoint.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", HOURLY_PATH): route_value})
    try:
        assert await client.get_vnstat_metrics("hourly") == {}
        assert router.count("GET", HOURLY_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_vnstat_metrics_unsupported_period_sends_no_request(
    make_client: ClientType,
) -> None:
    """get_vnstat_metrics should return empty data for unsupported periods without a request.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session)
    try:
        assert await client.get_vnstat_metrics("weekly") == {}
        assert router.count() == 0
    finally:
        await client.async_close()


@pytest.mark.parametrize("route_value, expected_error", THROW_RESPONSES)
@pytest.mark.asyncio
async def test_get_vnstat_metrics_raises_in_throw_mode(
    make_client: ClientType, route_value: Any, expected_error: type[OPNsenseError]
) -> None:
    """get_vnstat_metrics raises a public error for permission and transient failures.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        route_value (Any): Response the Router gives for the hourly endpoint.
        expected_error (type[OPNsenseError]): Public exception type expected from the call.
    """
    client, session = make_mock_session_client(make_client)
    install_fake_router(client, session, routes={("GET", HOURLY_PATH): route_value})
    client._throw_errors = True
    try:
        with pytest.raises(expected_error):
            await client.get_vnstat_metrics("hourly")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_vnstat_metrics_missing_endpoint_does_not_raise_in_throw_mode(
    make_client: ClientType,
) -> None:
    """A missing vnStat endpoint (404) is an empty result, not an error, even in throw mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    install_fake_router(client, session, routes={("GET", HOURLY_PATH): 404})
    client._throw_errors = True
    try:
        assert await client.get_vnstat_metrics("hourly") == {}
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_vnstat_summary_from_hourly_daily_monthly(make_client: ClientType) -> None:
    """get_vnstat should produce per-interface summary fields used by sensors.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    try:
        # Keep payload dates aligned with mocked OPNsense system time to avoid
        # day-boundary/timezone flakiness in CI.
        now = datetime(2000, 1, 15, 12, 0, 0, tzinfo=UTC)
        prev_hour = now - timedelta(hours=1)
        this_month = now.date().replace(day=1)
        prev_month = this_month - timedelta(days=1)
        rollover_date_header = (
            f"     {now.strftime('%m/%d/%y')}\n" if now.date() != prev_hour.date() else ""
        )

        hourly_payload = {
            "response": f"""
 igc0 / hourly

         hour        rx      |     tx      |    total    |   avg. rate
     {prev_hour.strftime("%m/%d/%y")}
         {prev_hour.strftime("%H:%M")}      1.00 GiB |    1.00 GiB |    2.00 GiB |    4.00 Mbit/s
{rollover_date_header}         {now.strftime("%H:%M")}      2.00 GiB |    1.00 GiB |    3.00 GiB |    6.00 Mbit/s
 igc1 / hourly

         hour        rx      |     tx      |    total    |   avg. rate
     {prev_hour.strftime("%m/%d/%y")}
         {prev_hour.strftime("%H:%M")}      512.00 MiB |  512.00 MiB |    1.00 GiB |    2.00 Mbit/s
{rollover_date_header}         {now.strftime("%H:%M")}      1.00 GiB |  512.00 MiB |    1.50 GiB |    3.00 Mbit/s
"""
        }
        daily_payload = {
            "response": f"""
 igc0 / daily

          day        rx      |     tx      |    total    |   avg. rate
      {(now.date() - timedelta(days=1)).strftime("%m/%d/%y")}      1.00 GiB |    1.00 GiB |    2.00 GiB |    1.00 Mbit/s
      {now.strftime("%m/%d/%y")}      2.00 GiB |    2.00 GiB |    4.00 GiB |    2.00 Mbit/s
 igc1 / daily

          day        rx      |     tx      |    total    |   avg. rate
      {(now.date() - timedelta(days=1)).strftime("%m/%d/%y")}    512.00 MiB |  512.00 MiB |    1.00 GiB |    0.50 Mbit/s
      {now.strftime("%m/%d/%y")}      1.00 GiB |  512.00 MiB |    1.50 GiB |    0.75 Mbit/s
"""
        }
        monthly_payload = {
            "response": f"""
 igc0 / monthly

        month        rx      |     tx      |    total    |   avg. rate
       {prev_month.strftime("%b '%y")}      3.00 GiB |    3.00 GiB |    6.00 GiB |    1.00 Mbit/s
       {this_month.strftime("%b '%y")}      4.00 GiB |    4.00 GiB |    8.00 GiB |    2.00 Mbit/s
 igc1 / monthly

        month        rx      |     tx      |    total    |   avg. rate
       {prev_month.strftime("%b '%y")}      1.00 GiB |    1.00 GiB |    2.00 GiB |    1.00 Mbit/s
       {this_month.strftime("%b '%y")}      1.50 GiB |    1.50 GiB |    3.00 GiB |    2.00 Mbit/s
"""
        }

        router = install_fake_router(
            client,
            session,
            routes={
                ("GET", HOURLY_PATH): hourly_payload,
                ("GET", DAILY_PATH): daily_payload,
                ("GET", MONTHLY_PATH): monthly_payload,
                ("GET", SYSTEM_TIME_PATH): {"datetime": "2000-01-15 12:00:00 EST"},
            },
        )
        vnstat = await client.get_vnstat()

        gib = 1024**3
        assert vnstat["interface_count"] == 2
        igc0_metrics = vnstat["interfaces"]["igc0"]["metrics"]
        assert igc0_metrics["vnstat_today"]["total_bytes"] == 4 * gib
        assert igc0_metrics["vnstat_today"]["rx_bytes"] == 2 * gib
        assert igc0_metrics["vnstat_today"]["tx_bytes"] == 2 * gib
        assert igc0_metrics["vnstat_this_month"]["total_bytes"] == 8 * gib
        assert igc0_metrics["vnstat_yesterday"]["total_bytes"] == 2 * gib
        assert igc0_metrics["vnstat_last_month"]["total_bytes"] == 6 * gib
        assert igc0_metrics["vnstat_last_hour"]["total_bytes"] == 3 * gib

        igc1_metrics = vnstat["interfaces"]["igc1"]["metrics"]
        assert igc1_metrics["vnstat_today"]["total_bytes"] == int(1.5 * gib)
        assert igc1_metrics["vnstat_today"]["rx_bytes"] == 1 * gib
        assert igc1_metrics["vnstat_today"]["tx_bytes"] == int(0.5 * gib)
        assert igc1_metrics["vnstat_this_month"]["total_bytes"] == 3 * gib
        assert igc1_metrics["vnstat_yesterday"]["total_bytes"] == 1 * gib
        assert igc1_metrics["vnstat_last_month"]["total_bytes"] == 2 * gib
        assert igc1_metrics["vnstat_last_hour"]["total_bytes"] == int(1.5 * gib)
        for path in (HOURLY_PATH, DAILY_PATH, MONTHLY_PATH):
            assert router.count("GET", path) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_vnstat_uses_systemtime_endpoint_path(make_client: ClientType) -> None:
    """get_vnstat should query the supported system-time endpoint and tolerate bad values.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client,
        session,
        routes={
            ("GET", HOURLY_PATH): {"response": ""},
            ("GET", DAILY_PATH): {"response": ""},
            ("GET", MONTHLY_PATH): {"response": ""},
            ("GET", SYSTEM_TIME_PATH): {"datetime": "invalid"},
        },
    )
    try:
        assert await client.get_vnstat() == {"interfaces": {}, "interface_count": 0}
        assert router.count("GET", SYSTEM_TIME_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.parametrize("route_value", UNAVAILABLE_RESPONSES)
@pytest.mark.asyncio
async def test_get_vnstat_returns_empty_when_hourly_endpoint_unavailable(
    make_client: ClientType, route_value: Any
) -> None:
    """get_vnstat returns the empty summary and stops after one request when hourly fails.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        route_value (Any): Response the Router gives for the hourly endpoint.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", HOURLY_PATH): route_value})
    try:
        assert await client.get_vnstat() == {"interfaces": {}, "interface_count": 0}
        assert router.count() == 1
        assert router.count("GET", HOURLY_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.parametrize("failing_path", [DAILY_PATH, MONTHLY_PATH])
@pytest.mark.parametrize("route_value", UNAVAILABLE_RESPONSES)
@pytest.mark.asyncio
async def test_get_vnstat_keeps_hourly_rows_when_other_period_unavailable(
    make_client: ClientType, failing_path: str, route_value: Any
) -> None:
    """A failing daily or monthly endpoint only drops that period from the summary.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        failing_path (str): Daily or monthly endpoint that fails.
        route_value (Any): Response the Router gives for the failing endpoint.
    """
    routes: dict[tuple[str, str], Any] = {
        ("GET", HOURLY_PATH): {
            "response": "igc0 / hourly\n 03/07/26\n 18:00  1.00 GiB | 1.00 GiB | 2.00 GiB | 4.00 Mbit/s"
        },
        ("GET", DAILY_PATH): {"response": ""},
        ("GET", MONTHLY_PATH): {"response": ""},
        ("GET", SYSTEM_TIME_PATH): {"datetime": "2000-01-15 12:00:00 EST"},
    }
    routes[("GET", failing_path)] = route_value
    client, session = make_mock_session_client(make_client)
    install_fake_router(client, session, routes=routes)
    try:
        vnstat = await client.get_vnstat()

        assert vnstat["interface_count"] == 1
        interface = vnstat["interfaces"]["igc0"]
        assert len(interface["hourly"]) == 1
        assert interface["daily"] == []
        assert interface["monthly"] == []
    finally:
        await client.async_close()


@pytest.mark.parametrize("route_value, expected_error", THROW_RESPONSES)
@pytest.mark.asyncio
async def test_get_vnstat_raises_in_throw_mode(
    make_client: ClientType, route_value: Any, expected_error: type[OPNsenseError]
) -> None:
    """get_vnstat raises a public error for permission and transient failures.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        route_value (Any): Response the Router gives for the hourly endpoint.
        expected_error (type[OPNsenseError]): Public exception type expected from the call.
    """
    client, session = make_mock_session_client(make_client)
    install_fake_router(client, session, routes={("GET", HOURLY_PATH): route_value})
    client._throw_errors = True
    try:
        with pytest.raises(expected_error):
            await client.get_vnstat()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_vnstat_missing_endpoint_does_not_raise_in_throw_mode(
    make_client: ClientType,
) -> None:
    """A missing vnStat plugin (404) is an empty summary, not an error, even in throw mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    install_fake_router(client, session, routes={("GET", HOURLY_PATH): 404})
    client._throw_errors = True
    try:
        assert await client.get_vnstat() == {"interfaces": {}, "interface_count": 0}
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_parse_vnstat_payload_and_helpers_edge_cases(make_client: ClientType) -> None:
    """VnStat payload/helper methods should handle malformed and fallback scenarios.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        parsed_empty = client._parse_vnstat_payload({"response": 123}, expected_period="hourly")
        assert parsed_empty["interfaces"] == {}

        parsed_mismatch = client._parse_vnstat_payload(
            {"response": "igc0 / daily\n03/07/26    1.00 GiB | 1.00 GiB | 2.00 GiB | 1.00 Mbit/s"},
            expected_period="hourly",
        )
        assert parsed_mismatch["period"] == "daily"

        assert client._parse_vnstat_row("estimated      2.41 TiB | 676.07 GiB | 3.07 TiB |") is None
        assert client._parse_vnstat_row("nonsense row") is None
        assert client._to_bytes("3.14", "BAD") is None
        assert client._to_bits_per_second("3.14", "BAD") is None

        assert client._collect_vnstat_interfaces(
            {"interfaces": {"igc0": []}}, {"interfaces": {1: []}}
        ) == ["igc0"]
        assert client._interface_rows({"interfaces": {"igc0": [{"label": "x"}]}}, "igc0") == [
            {"label": "x"}
        ]
        assert client._interface_rows({"interfaces": []}, "igc0") == []

        tz = UTC
        today = datetime.now(tz=tz).date()
        yesterday = today - timedelta(days=1)
        assert (
            client._pick_daily_row(
                [
                    {"label": yesterday.strftime("%m/%d/%y")},
                    {"label": today.strftime("%m/%d/%y")},
                ],
                0,
                tz,
            )
            is not None
        )
        assert client._pick_daily_row([{"label": "bad"}], 0, tz) == {"label": "bad"}
        assert client._pick_daily_row([{"label": "bad0"}, {"label": "bad1"}], 1, tz) == {
            "label": "bad0"
        }
        assert client._pick_daily_row([{"label": "bad"}], 2, tz) is None

        this_month = datetime.now(tz=tz).strftime("%b '%y")
        assert client._pick_monthly_row([{"label": this_month}], 0, tz) == {"label": this_month}
        assert client._pick_monthly_row([{"label": "bad0"}, {"label": "bad1"}], 1, tz) == {
            "label": "bad0"
        }
        now = datetime.now(tz=tz)
        previous_year = now.year - 1
        previous_december = f"Dec '{previous_year % 100:02d}"
        assert client._pick_monthly_row([{"label": previous_december}], now.month, tz) == {
            "label": previous_december
        }

        now_hour = datetime.now(tz=tz).replace(minute=0, second=0, microsecond=0)
        prev_hour = now_hour - timedelta(hours=1)
        rows = [
            {"label": prev_hour.strftime("%m/%d/%y %H:%M"), "total_bytes": 1},
            {"label": now_hour.strftime("%m/%d/%y %H:%M"), "total_bytes": 2},
        ]
        selected = client._pick_last_hour_row(rows, tz)
        assert selected == rows[0]
        assert client._parse_hourly_label(now_hour.strftime("%m/%d/%y %H:%M"), tz) is not None
        assert client._parse_hourly_label("bad", tz) is None
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_vnstat_metric_values_none_and_valid(make_client: ClientType) -> None:
    """_metric_values should return valid mapping only when all fields are ints.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        assert client._metric_values(None) is None
        assert client._metric_values({"total_bytes": 1, "rx_bytes": 2, "tx_bytes": 3}) == {
            "total_bytes": 1,
            "rx_bytes": 2,
            "tx_bytes": 3,
        }
        assert client._metric_values({"total_bytes": 1, "rx_bytes": None, "tx_bytes": 3}) is None
    finally:
        await client.async_close()
