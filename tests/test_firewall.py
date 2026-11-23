"""Tests for `aiopnsense.firewall`."""

from collections.abc import Callable
from functools import partial
import logging
from typing import Any

import pytest

from aiopnsense import OPNsenseClient
from tests.conftest import (
    FIRMWARE_STATUS_PATH,
    FakeRouter,
    install_fake_router,
    make_mock_session_client,
)

ClientType = Callable[..., OPNsenseClient]

ROUTER_URL = "http://localhost"
FILTER_RULES_PATH = "/api/firewall/filter/download_rules"
DNAT_PATH = "/api/firewall/d_nat/search_rule"
ONE_TO_ONE_PATH = "/api/firewall/one_to_one/search_rule"
SOURCE_NAT_PATH = "/api/firewall/source_nat/search_rule"
NPT_PATH = "/api/firewall/npt/search_rule"
KILL_STATES_PATH = "/api/diagnostics/firewall/kill_states/"
NAT_GROUP_PATHS = {
    "d_nat": DNAT_PATH,
    "one_to_one": ONE_TO_ONE_PATH,
    "source_nat": SOURCE_NAT_PATH,
    "npt": NPT_PATH,
}
SOURCE_NAT_ROWS = {
    "rows": [
        {"uuid": "manual-src", "description": "Manual", "enabled": "1"},
        {"uuid": "auto-src", "description": "Automatic", "enabled": "1", "is_automatic": True},
    ]
}
DNAT_ROWS = {"rows": [{"uuid": "dnat-1", "descr": "Web", "category": "c1", "%category": "Web"}]}


def _status(version: str) -> dict[str, Any]:
    """Return the firmware status payload a Router reports for a product version.

    Args:
        version (str): Product version the Router reports.

    Returns:
        dict[str, Any]: Firmware status response body.
    """
    return {"product": {"product_version": version}}


def _firewall_client(
    make_client: ClientType,
    routes: dict[tuple[str, str], Any],
    *,
    firmware_status: Any = 404,
    throw_errors: bool = False,
) -> tuple[OPNsenseClient, FakeRouter]:
    """Build a client whose Router answers only the given routes.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        routes (dict[tuple[str, str], Any]): Router answers keyed by HTTP method and path.
        firmware_status (Any): Route value for the firmware status read. The default 404
            leaves the firmware unknown.
        throw_errors (bool): Public ``throw_errors`` setting of the client.

    Returns:
        tuple[OPNsenseClient, FakeRouter]: Client and the Router that records its requests.
    """
    client, session = make_mock_session_client(partial(make_client, throw_errors=throw_errors))
    router = install_fake_router(
        client,
        session,
        routes={("GET", FIRMWARE_STATUS_PATH): firmware_status, **routes},
        explicit_routes=True,
    )
    return client, router


async def test_get_firewall_aggregates_rest_payload(make_client: ClientType) -> None:
    """`get_firewall` aggregates the filter rules and every NAT rule collection.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _firewall_client(
        make_client,
        {
            ("GET", FILTER_RULES_PATH): "@uuid;enabled\nrule1;1\n",
            ("GET", DNAT_PATH): {"rows": [{"uuid": "dst1", "descr": "DNAT", "enabled": "1"}]},
            ("GET", ONE_TO_ONE_PATH): {"rows": [{"uuid": "oto1"}]},
            ("GET", SOURCE_NAT_PATH): {"rows": [{"uuid": "src1"}]},
            ("GET", NPT_PATH): {"rows": [{"uuid": "npt1"}]},
        },
    )

    assert await client.get_firewall() == {
        "rules": {"rule1": {"uuid": "rule1", "enabled": "1"}},
        "nat": {
            "d_nat": {"dst1": {"uuid": "dst1", "description": "DNAT", "enabled": "1"}},
            "one_to_one": {"oto1": {"uuid": "oto1"}},
            "source_nat": {"src1": {"uuid": "src1"}},
            "npt": {"npt1": {"uuid": "npt1"}},
        },
    }
    for path in (FILTER_RULES_PATH, DNAT_PATH, ONE_TO_ONE_PATH, SOURCE_NAT_PATH, NPT_PATH):
        assert router.count("GET", path) == 1


async def test_get_firewall_rules_skips_invalid_rows(make_client: ClientType) -> None:
    """Firewall rule downloads are read as text, parsing editable rows and skipping invalid ones.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _firewall_client(
        make_client,
        {
            ("GET", FILTER_RULES_PATH): (
                "@uuid;enabled;description\n"
                'rule-ok;1;"Allow; DNS"\n'
                'rule-extra;1;"Has; extra";extra\n'
                ";1;Missing UUID\n"
                "lockout-1;1;Lockout\n"
            )
        },
    )

    firewall = await client.get_firewall()

    assert firewall["rules"] == {
        "rule-ok": {"uuid": "rule-ok", "enabled": "1", "description": "Allow; DNS"},
    }
    assert router.count("GET", FILTER_RULES_PATH) == 1


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            '@uuid,enabled,tcpflags2,categories,description\nrule-2611,1,SA,web,"Allow, web"\n',
            {
                "uuid": "rule-2611",
                "enabled": "1",
                "tcpflags2": "SA",
                "categories": "web",
                "description": "Allow, web",
            },
        ),
        (
            (
                "@uuid;enabled;tcpflags2;tcpflags_any;categories;description\n"
                'rule-2612;1;SA;1;web;"Allow; web"\n'
            ),
            {
                "uuid": "rule-2612",
                "enabled": "1",
                "tcpflags2": "SA",
                "tcpflags_any": "1",
                "categories": "web",
                "description": "Allow; web",
            },
        ),
    ],
    ids=["opnsense-26.1.1", "opnsense-26.1.2-and-newer"],
)
async def test_get_firewall_rules_supports_opnsense_csv_versions(
    make_client: ClientType,
    response: str,
    expected: dict[str, str],
) -> None:
    """Firewall rule downloads support all released CSV formats.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        response (str): Firewall CSV payload for the OPNsense version case.
        expected (dict[str, str]): Parsed rule mapping expected from the payload.
    """
    client, _router = _firewall_client(make_client, {("GET", FILTER_RULES_PATH): response})

    assert (await client.get_firewall())["rules"] == {expected["uuid"]: expected}


async def test_get_nat_source_rules_labels_empty_interface_address_target(
    make_client: ClientType,
) -> None:
    """Source NAT rows with an empty target expose an interface-address label.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _router = _firewall_client(
        make_client,
        {
            ("GET", SOURCE_NAT_PATH): {
                "rows": [
                    {
                        "uuid": "src-interface-address",
                        "description": "Interface address target",
                        "enabled": "1",
                        "interface": "wan",
                        "%interface": "WAN",
                        "target": "",
                    }
                ]
            }
        },
        firmware_status=_status("26.7"),
    )

    result = (await client.get_firewall())["nat"]["source_nat"]

    assert result["src-interface-address"]["target"] == ""
    assert result["src-interface-address"]["%target"] == "WAN address"


@pytest.mark.parametrize(
    ("nat_group", "rows", "expected", "firmware"),
    [
        pytest.param(
            "d_nat",
            [
                {"uuid": "dst1", "descr": "DNAT rule", "disabled": 0},
                {"uuid": "lockout-1", "descr": "ignored", "disabled": "0"},
            ],
            {"dst1": {"uuid": "dst1", "description": "DNAT rule", "enabled": "1"}},
            "26.1.1",
            id="d_nat-disabled-flag",
        ),
        pytest.param(
            "d_nat",
            [
                {
                    "uuid": "dst1",
                    "descr": "DNAT rule",
                    "enabled": "0",
                    "category": "cat1",
                    "%category": "WAN",
                },
            ],
            {
                "dst1": {
                    "uuid": "dst1",
                    "description": "DNAT rule",
                    "enabled": "0",
                    "category": "cat1",
                    "%category": "WAN",
                    "categories": "cat1",
                    "%categories": "WAN",
                }
            },
            "26.1.4",
            id="d_nat-enabled-flag-unified-categories",
        ),
        pytest.param(
            "one_to_one",
            [{"uuid": "oto1", "description": "1:1 rule", "enabled": "1"}],
            {"oto1": {"uuid": "oto1", "description": "1:1 rule", "enabled": "1"}},
            None,
            id="one_to_one",
        ),
        pytest.param(
            "source_nat",
            [{"uuid": "src1", "description": "SNAT rule", "enabled": "0"}],
            {"src1": {"uuid": "src1", "description": "SNAT rule", "enabled": "0"}},
            None,
            id="source_nat",
        ),
        pytest.param(
            "npt",
            [{"uuid": "npt1", "description": "NPT rule", "enabled": "1"}],
            {"npt1": {"uuid": "npt1", "description": "NPT rule", "enabled": "1"}},
            None,
            id="npt",
        ),
    ],
)
async def test_nat_rule_collections_parse_rows(
    make_client: ClientType,
    nat_group: str,
    rows: list[dict[str, object]],
    expected: dict[str, dict[str, str]],
    firmware: str | None,
) -> None:
    """Each NAT rule collection is returned as a UUID-keyed mapping of its REST search rows.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        nat_group (str): Key of the NAT collection in the ``get_firewall`` result.
        rows (list[dict[str, object]]): REST search rows the Router returns.
        expected (dict[str, dict[str, str]]): Expected UUID-keyed rule mapping.
        firmware (str | None): Firmware version the Router reports, or ``None`` if unknown.
    """
    client, router = _firewall_client(
        make_client,
        {("GET", NAT_GROUP_PATHS[nat_group]): {"rows": rows}},
        firmware_status=404 if firmware is None else _status(firmware),
    )

    assert (await client.get_firewall())["nat"][nat_group] == expected
    assert router.count("GET", NAT_GROUP_PATHS[nat_group]) == 1


@pytest.mark.parametrize("throw_errors", [False, True], ids=["errors-off", "errors-on"])
async def test_get_firewall_returns_empty_collections_when_endpoints_are_unavailable(
    make_client: ClientType, throw_errors: bool
) -> None:
    """Rule collections the Router reports as 404 are empty in either error mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Public ``throw_errors`` setting of the client.
    """
    client, router = _firewall_client(make_client, {}, throw_errors=throw_errors)

    assert await client.get_firewall() == {
        "rules": {},
        "nat": {"d_nat": {}, "one_to_one": {}, "source_nat": {}, "npt": {}},
    }
    for path in (FILTER_RULES_PATH, DNAT_PATH, ONE_TO_ONE_PATH, SOURCE_NAT_PATH, NPT_PATH):
        assert router.count("GET", path) == 1


@pytest.mark.parametrize(
    ("firmware", "automatic_rows_hidden"),
    [
        pytest.param(_status("26.1.10"), False, id="below-threshold"),
        pytest.param(_status("26.1.11"), True, id="at-threshold"),
        pytest.param(_status("26.1.11_4"), True, id="at-threshold-with-hotfix"),
        pytest.param(_status("26.2"), True, id="above-threshold"),
        pytest.param({}, False, id="missing"),
        pytest.param(_status("not-a-version"), False, id="not-comparable"),
    ],
)
async def test_get_firewall_source_nat_filters_automatic_rows_by_firmware(
    make_client: ClientType, firmware: dict[str, Any], automatic_rows_hidden: bool
) -> None:
    """Source NAT hides generated rows only on known firmware that generates them.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        firmware (dict[str, Any]): Firmware status response the Router sends.
        automatic_rows_hidden (bool): Whether generated rows are expected to be hidden.
    """
    client, _router = _firewall_client(
        make_client, {("GET", SOURCE_NAT_PATH): SOURCE_NAT_ROWS}, firmware_status=firmware
    )

    firewall = await client.get_firewall()

    assert set(firewall["nat"]["source_nat"]) == (
        {"manual-src"} if automatic_rows_hidden else {"manual-src", "auto-src"}
    )


@pytest.mark.parametrize(
    ("firmware", "categories_mirrored"),
    [
        pytest.param(_status("26.1.3"), False, id="below-threshold"),
        pytest.param(_status("26.1.4"), True, id="at-threshold"),
        pytest.param(_status("26.2"), True, id="above-threshold"),
        pytest.param({}, False, id="missing"),
        pytest.param(_status("not-a-version"), False, id="not-comparable"),
    ],
)
async def test_get_firewall_dnat_mirrors_categories_by_firmware(
    make_client: ClientType, firmware: dict[str, Any], categories_mirrored: bool
) -> None:
    """Destination NAT mirrors category keys only on known unified-NAT firmware.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        firmware (dict[str, Any]): Firmware status response the Router sends.
        categories_mirrored (bool): Whether plural category keys are expected.
    """
    client, _router = _firewall_client(
        make_client, {("GET", DNAT_PATH): DNAT_ROWS}, firmware_status=firmware
    )

    rule = (await client.get_firewall())["nat"]["d_nat"]["dnat-1"]

    assert ("categories" in rule) is categories_mirrored
    assert ("%categories" in rule) is categories_mirrored


async def test_unknown_firmware_for_nat_is_logged_once_per_condition(
    make_client: ClientType, caplog: pytest.LogCaptureFixture
) -> None:
    """An uncomparable firmware version is logged once at error level, not per query.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Log capture fixture.
    """
    client, _router = _firewall_client(
        make_client,
        {("GET", SOURCE_NAT_PATH): SOURCE_NAT_ROWS, ("GET", DNAT_PATH): DNAT_ROWS},
        firmware_status=_status("not-a-version"),
    )
    caplog.set_level(logging.DEBUG, logger="aiopnsense")

    await client.get_firewall()
    await client.get_firewall()

    errors = [
        record
        for record in caplog.records
        if record.levelno == logging.ERROR and "cannot be compared" in record.getMessage()
    ]
    assert len(errors) == 2  # one per capability (source NAT, unified NAT), not per call


async def test_kill_states_returns_normalized_result(make_client: ClientType) -> None:
    """`kill_states` posts the address filter and normalizes the diagnostics response.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _firewall_client(
        make_client,
        {("POST", KILL_STATES_PATH): {"result": "ok", "dropped_states": 7}},
    )

    assert await client.kill_states("192.0.2.10") == {"success": True, "dropped_states": 7}

    assert [
        body
        for method, url, body in router.requests
        if method == "POST" and url == ROUTER_URL + KILL_STATES_PATH
    ] == [{"filter": "192.0.2.10"}]
