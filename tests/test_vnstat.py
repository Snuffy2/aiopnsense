"""Tests for `aiopnsense.vnstat`."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import aiohttp
import pytest

from aiopnsense import OPNsenseClient
from tests.conftest import make_mock_session_client, make_router_client

ClientType = Callable[..., OPNsenseClient]

VNSTAT_HOURLY_PATH = "/api/vnstat/service/hourly"
VNSTAT_DAILY_PATH = "/api/vnstat/service/daily"
VNSTAT_MONTHLY_PATH = "/api/vnstat/service/monthly"
VNSTAT_YEARLY_PATH = "/api/vnstat/service/yearly"
SYSTEM_TIME_PATH = "/api/diagnostics/system/system_time"


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
    """get_vnstat_metrics should parse yearly vnStat payload rows.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(
        make_client,
        routes={
            ("GET", VNSTAT_YEARLY_PATH): {
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

    parsed = await client.get_vnstat_metrics("yearly")
    rows = parsed["interfaces"]["igc0"]

    tib = 1024**4
    assert parsed["period"] == "yearly"
    assert len(rows) == 4
    assert rows[0]["label"] == "2023"
    assert rows[0]["total_bytes"] == round(63.25 * tib)
    assert rows[3]["label"] == "2026"
    assert rows[3]["avg_rate_bits_per_second"] == 14150000
    assert router.count("GET", VNSTAT_YEARLY_PATH) == 1


@pytest.mark.asyncio
async def test_get_vnstat_metrics_unsupported_period_or_endpoint_missing(
    make_client: ClientType,
) -> None:
    """get_vnstat_metrics should return empty data for unsupported/missing endpoints.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(make_client)

    assert await client.get_vnstat_metrics("hourly") == {}
    assert router.count("GET", VNSTAT_HOURLY_PATH) == 1
    assert router.count() == 1

    assert await client.get_vnstat_metrics("weekly") == {}
    assert router.count() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("hourly_unavailable", [False, True])
async def test_get_vnstat_summary_from_hourly_daily_monthly(
    make_client: ClientType, hourly_unavailable: bool
) -> None:
    """Daily and monthly summaries survive a transient hourly read failure.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        hourly_unavailable (bool): Whether the hourly request fails transiently.
    """
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

        client, router = make_router_client(
            make_client,
            routes={
                ("GET", VNSTAT_HOURLY_PATH): (
                    aiohttp.ClientConnectionError("Hourly read failed")
                    if hourly_unavailable
                    else hourly_payload
                ),
                ("GET", VNSTAT_DAILY_PATH): daily_payload,
                ("GET", VNSTAT_MONTHLY_PATH): monthly_payload,
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
        assert igc0_metrics["vnstat_last_hour"]["total_bytes"] == (
            None if hourly_unavailable else 3 * gib
        )

        igc1_metrics = vnstat["interfaces"]["igc1"]["metrics"]
        assert igc1_metrics["vnstat_today"]["total_bytes"] == int(1.5 * gib)
        assert igc1_metrics["vnstat_today"]["rx_bytes"] == 1 * gib
        assert igc1_metrics["vnstat_today"]["tx_bytes"] == int(0.5 * gib)
        assert igc1_metrics["vnstat_this_month"]["total_bytes"] == 3 * gib
        assert igc1_metrics["vnstat_yesterday"]["total_bytes"] == 1 * gib
        assert igc1_metrics["vnstat_last_month"]["total_bytes"] == 2 * gib
        assert igc1_metrics["vnstat_last_hour"]["total_bytes"] == (
            None if hourly_unavailable else int(1.5 * gib)
        )
        assert router.count("GET", VNSTAT_DAILY_PATH) == 1
        assert router.count("GET", VNSTAT_MONTHLY_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_vnstat_uses_systemtime_endpoint_path(make_client: ClientType) -> None:
    """get_vnstat should query the supported system-time endpoint.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(
        make_client,
        routes={
            ("GET", VNSTAT_HOURLY_PATH): {},
            ("GET", SYSTEM_TIME_PATH): {"datetime": "invalid"},
        },
    )

    await client.get_vnstat()

    assert router.count("GET", SYSTEM_TIME_PATH) == 1


@pytest.mark.asyncio
async def test_get_vnstat_skips_calls_when_endpoint_missing(make_client: ClientType) -> None:
    """get_vnstat should return empty payload and skip API calls when endpoint is absent.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(make_client)

    vnstat = await client.get_vnstat()

    assert vnstat == {"interfaces": {}, "interface_count": 0}
    assert router.count("GET", VNSTAT_HOURLY_PATH) == 1
    assert router.count() == 1


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
