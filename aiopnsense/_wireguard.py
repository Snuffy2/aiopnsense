"""Assemble WireGuard inventories and apply live link observations."""

from collections import defaultdict
from collections.abc import Hashable, Iterable, Iterator, MutableMapping
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
            # Instances may share a public key, so the interface identifies the instance.
            for server in servers.values():
                if server["interface"] == row.get("if") and server["pubkey"] == row.get(
                    "public-key"
                ):
                    server["status"] = row.get("status")
        elif row.get("type") == "peer":
            interface = row.get("if", "-")
            targets: Iterable[_WireGuardLinkTarget] = ()
            # Configured interfaces are strings, or absent for an incomplete reference.
            if interface is None or isinstance(interface, str):
                key = _WireGuardLinkKey(interface, row.get("public-key", "-"))
                targets = links.get(key, ())
            _apply_wireguard_observation(row, targets)
    return servers, clients


def _wireguard_status_server_key(interface: str, key_hash: str) -> str:
    """Build the Status Access key of a WireGuard Server.

    Args:
        interface (str): Server interface, such as ``wg0``.
        key_hash (str): Public-key hash of the Server.

    Returns:
        str: ``<interface>:<key_hash>``, unique per instance even when public keys match.
    """
    return f"{interface}:{key_hash}"


def _assemble_status_wireguard(
    summary: list[Any],
) -> tuple[dict[str, MutableMapping[str, Any]], dict[str, MutableMapping[str, Any]]]:
    """Build WireGuard inventories and links visible in the status summary.

    Args:
        summary (list[Any]): WireGuard summary rows from the Router.

    Returns:
        tuple[dict[str, MutableMapping[str, Any]], dict[str, MutableMapping[str, Any]]]:
            Servers keyed by ``<interface>:<key_hash>``, one per instance, and Clients
            keyed by public-key hash.
    """
    servers: dict[str, MutableMapping[str, Any]] = {}
    clients: dict[str, MutableMapping[str, Any]] = {}
    servers_by_interface: dict[str, MutableMapping[str, Any]] = {}
    client_rows: list[tuple[str, MutableMapping[str, Any], MutableMapping[str, Any]]] = []
    for row in summary:
        if not isinstance(row, MutableMapping):
            continue
        public_key = row.get("public-key")
        key_hash = _wireguard_key_hash(public_key)
        interface = row.get("if")
        if key_hash is None or not isinstance(interface, str) or not interface:
            continue
        identity = {
            "uuid": None,
            "key_hash": key_hash,
            "name": row.get("name") or row.get("ifname"),
            "pubkey": public_key,
            "total_bytes_recv": 0,
            "total_bytes_sent": 0,
        }
        if row.get("type") == "interface":
            # Instances may share a public key, so each interface is its own Server.
            server_entry = servers.setdefault(
                _wireguard_status_server_key(interface, key_hash),
                {
                    **identity,
                    "interface": interface,
                    "status": row.get("status"),
                    "clients": [],
                    "connected_clients": 0,
                },
            )
            servers_by_interface[interface] = server_entry
        elif row.get("type") == "peer":
            client = clients.setdefault(
                key_hash, {**identity, "servers": [], "connected_servers": 0}
            )
            client_rows.append((interface, client, row))

    for interface, client, row in client_rows:
        server = servers_by_interface.get(interface)
        if server is None:
            continue
        server_link: MutableMapping[str, Any] = {
            "uuid": None,
            "name": client["name"],
            "pubkey": client["pubkey"],
            "connected": False,
        }
        server["clients"].append(server_link)
        client_link: MutableMapping[str, Any] = {
            "uuid": None,
            "name": server["name"],
            "pubkey": server["pubkey"],
            "interface": interface,
            "connected": False,
        }
        client["servers"].append(client_link)
        # Match the public key itself and apply this row only to its own pair of links.
        targets = (
            (server_link, server, "connected_clients"),
            (client_link, client, "connected_servers"),
        )
        _apply_wireguard_observation(row, targets if row["public-key"] == client["pubkey"] else ())
    return servers, clients


def _apply_wireguard_observation(
    row: MutableMapping[str, Any],
    targets: Iterable[_WireGuardLinkTarget],
) -> None:
    """Apply a live observation to every matching link and its parent totals.

    Args:
        row (MutableMapping[str, Any]): WireGuard summary peer row.
        targets (Iterable[_WireGuardLinkTarget]): Matching links, their parents and counters.
    """
    endpoint = row.get("endpoint")
    received = try_to_int(row.get("transfer-rx", 0), 0) or 0
    sent = try_to_int(row.get("transfer-tx", 0), 0) or 0
    handshake = timestamp_to_datetime(try_to_int(row.get("latest-handshake", 0), 0))
    connected = handshake is not None and datetime.now().astimezone() - handshake <= timedelta(
        minutes=3
    )

    for link, parent, counter in targets:
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
