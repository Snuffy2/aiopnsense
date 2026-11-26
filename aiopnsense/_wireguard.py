"""Assemble configured WireGuard inventories and apply live link observations."""

from collections import defaultdict
from collections.abc import Hashable, Iterator, MutableMapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any

from .helpers import api_value_matches, timestamp_to_datetime, try_to_int


@dataclass(frozen=True, slots=True)
class _WireGuardLinkKey:
    """Identify a live link by Server interface and Client public key."""

    interface: str | None
    public_key: Any

    def __hash__(self) -> int:
        """Hash the link while retaining equality for malformed JSON public keys.

        Returns:
            int: Link hash, using only the interface for an unhashable public key.
        """
        if isinstance(self.public_key, Hashable):
            return hash((self.interface, self.public_key))
        return hash(self.interface)


type _WireGuardLinkTarget = tuple[MutableMapping[str, Any], MutableMapping[str, Any], str]


def _wireguard_key_hash(public_key: Any) -> str | None:
    """Derive the identity of a WireGuard Server or Client.

    Args:
        public_key (Any): Public key as reported by the Router.

    Returns:
        str | None: First 16 lowercase hex characters of its SHA-256, or
            ``None`` when the public key is missing or malformed.
    """
    if isinstance(public_key, str) and public_key:
        return sha256(public_key.encode()).hexdigest()[:16]
    return None


def _selected_options(options: Any) -> Iterator[tuple[str, Any]]:
    """Read selected options in the order reported by the Router.

    Args:
        options (Any): Router field containing a mapping of options.

    Yields:
        tuple[str, Any]: Option identifier and label or value.
    """
    if not isinstance(options, MutableMapping):
        return
    for uuid, option in options.items():
        if (
            isinstance(option, MutableMapping)
            and api_value_matches(option.get("selected"), "1")
            and option.get("value")
        ):
            yield uuid, option["value"]


def _configured_identity(uuid: str, settings: MutableMapping[str, Any]) -> dict[str, Any]:
    """Read the fields common to configured WireGuard Servers and Clients.

    Args:
        uuid (str): Configured Server or Client identifier.
        settings (MutableMapping[str, Any]): Settings for that Server or Client.

    Returns:
        dict[str, Any]: Identity and enabled state.
    """
    public_key = settings.get("pubkey")
    return {
        "uuid": uuid,
        "name": settings.get("name"),
        "pubkey": public_key,
        "key_hash": _wireguard_key_hash(public_key),
        "enabled": api_value_matches(settings.get("enabled"), "1"),
    }


def _assemble_configured_wireguard(
    summary: list[Any],
    server_settings: MutableMapping[str, Any],
    client_settings: MutableMapping[str, Any],
) -> tuple[dict[str, MutableMapping[str, Any]], dict[str, MutableMapping[str, Any]]]:
    """Build the configured inventories and enrich their selected links from the summary.

    Args:
        summary (list[Any]): WireGuard summary rows from the Router.
        server_settings (MutableMapping[str, Any]): Server settings keyed by UUID.
        client_settings (MutableMapping[str, Any]): Client settings keyed by UUID.

    Returns:
        tuple[dict[str, MutableMapping[str, Any]], dict[str, MutableMapping[str, Any]]]:
            Servers and Clients keyed by UUID, with their directional links and totals.
    """
    servers: dict[str, MutableMapping[str, Any]] = {}
    clients: dict[str, MutableMapping[str, Any]] = {}
    links: defaultdict[_WireGuardLinkKey, list[_WireGuardLinkTarget]] = defaultdict(list)
    for uuid, settings in server_settings.items():
        interface = f"wg{settings.get('instance', '')}"
        server: MutableMapping[str, Any] = {
            **_configured_identity(uuid, settings),
            "interface": interface,
            "dns_servers": [settings["peer_dns"]] if settings.get("peer_dns") else [],
            "tunnel_addresses": [
                value for _, value in _selected_options(settings.get("tunneladdress"))
            ],
            "clients": [],
            "connected_clients": 0,
            "total_bytes_recv": 0,
            "total_bytes_sent": 0,
        }
        servers[uuid] = server
        for client_uuid, label in _selected_options(settings.get("peers")):
            public_key = client_settings.get(client_uuid, {}).get("pubkey")
            link = {"name": label, "uuid": client_uuid, "pubkey": public_key, "connected": False}
            server["clients"].append(link)
            links[_WireGuardLinkKey(interface, public_key)].append(
                (link, server, "connected_clients")
            )

    for uuid, settings in client_settings.items():
        client: dict[str, Any] = {
            **_configured_identity(uuid, settings),
            "tunnel_addresses": [
                value for _, value in _selected_options(settings.get("tunneladdress"))
            ],
            "servers": [],
            "connected_servers": 0,
            "total_bytes_recv": 0,
            "total_bytes_sent": 0,
        }
        clients[uuid] = client
        for server_uuid, label in _selected_options(settings.get("servers")):
            link = {"name": label, "uuid": server_uuid, "connected": False}
            if (referenced_server := servers.get(server_uuid)) is not None:
                link.update(
                    name=referenced_server["name"],
                    pubkey=referenced_server["pubkey"],
                    interface=referenced_server["interface"],
                    tunnel_addresses=referenced_server["tunnel_addresses"],
                )
            client["servers"].append(link)
            links[_WireGuardLinkKey(link.get("interface"), client["pubkey"])].append(
                (link, client, "connected_servers")
            )

    for row in summary:
        if not isinstance(row, MutableMapping):
            continue
        if row.get("type") == "interface":
            for server in servers.values():
                if server["pubkey"] == row.get("public-key"):
                    server["status"] = row.get("status")
        elif row.get("type") == "peer":
            _apply_wireguard_observation(row, links)
    return servers, clients


def _apply_wireguard_observation(
    row: MutableMapping[str, Any],
    links: MutableMapping[_WireGuardLinkKey, list[_WireGuardLinkTarget]],
) -> None:
    """Apply a live observation to every matching link and its parent totals.

    Args:
        row (MutableMapping[str, Any]): WireGuard summary peer row.
        links (MutableMapping[_WireGuardLinkKey, list[_WireGuardLinkTarget]]):
            Link records, their parents and their counters grouped by relationship key.
    """
    public_key = row.get("public-key", "-")
    interface = row.get("if", "-")
    endpoint = row.get("endpoint")
    received = try_to_int(row.get("transfer-rx", 0), 0) or 0
    sent = try_to_int(row.get("transfer-tx", 0), 0) or 0
    handshake = timestamp_to_datetime(try_to_int(row.get("latest-handshake", 0), 0))
    connected = handshake is not None and datetime.now().astimezone() - handshake <= timedelta(
        minutes=3
    )

    # Configured interfaces are strings, or absent for an incomplete reference.
    if interface is not None and not isinstance(interface, str):
        return
    for link, parent, counter in links.get(_WireGuardLinkKey(interface, public_key), []):
        if endpoint and endpoint != "(none)":
            link["endpoint"] = endpoint
        link.update(
            bytes_recv=received, bytes_sent=sent, latest_handshake=handshake, connected=connected
        )
        parent["total_bytes_recv"] += received
        parent["total_bytes_sent"] += sent
        if connected:
            parent[counter] += 1
            if parent.get("latest_handshake") is None or parent["latest_handshake"] < handshake:
                parent["latest_handshake"] = handshake
