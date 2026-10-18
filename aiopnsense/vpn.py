"""OpenVPN, WireGuard, and IPsec methods for OPNsenseClient."""

from collections.abc import MutableMapping
from datetime import datetime, timedelta
from typing import Any

from ._typing import AiopnsenseClientProtocol
from .exceptions import OPNsenseError
from .helpers import (
    _LOGGER,
    _log_errors,
    api_value_matches,
    dict_get,
    timestamp_to_datetime,
    try_to_int,
)

OPENVPN_SESSIONS_SNAKE_ENDPOINT = "/api/openvpn/service/search_sessions"
OPENVPN_SESSIONS_CAMELCASE_ENDPOINT = "/api/openvpn/service/searchSessions"
OPENVPN_ROUTES_SNAKE_ENDPOINT = "/api/openvpn/service/search_routes"
OPENVPN_ROUTES_CAMELCASE_ENDPOINT = "/api/openvpn/service/searchRoutes"
OPENVPN_PROVIDERS_ENDPOINT = "/api/openvpn/export/providers"
OPENVPN_INSTANCES_SEARCH_ENDPOINT = "/api/openvpn/instances/search"
OPENVPN_INSTANCE_DETAILS_ENDPOINT_PREFIX = "/api/openvpn/instances/get/"
WIREGUARD_SUMMARY_ENDPOINT = "/api/wireguard/service/show"
WIREGUARD_CLIENTS_ENDPOINT = "/api/wireguard/client/get"
WIREGUARD_SERVERS_ENDPOINT = "/api/wireguard/server/get"
IPSEC_PHASE1_SESSIONS_SNAKE_ENDPOINT = "/api/ipsec/sessions/search_phase1"
IPSEC_PHASE1_SESSIONS_CAMELCASE_ENDPOINT = "/api/ipsec/sessions/searchPhase1"
IPSEC_CONNECTIONS_SNAKE_ENDPOINT = "/api/ipsec/connections/search_connection"
IPSEC_CONNECTIONS_CAMELCASE_ENDPOINT = "/api/ipsec/connections/searchConnection"
IPSEC_CHILDREN_SNAKE_ENDPOINT = "/api/ipsec/connections/search_child"
IPSEC_CHILDREN_CAMELCASE_ENDPOINT = "/api/ipsec/connections/searchChild"
IPSEC_PHASE2_SESSIONS_SNAKE_ENDPOINT = "/api/ipsec/sessions/search_phase2"
IPSEC_PHASE2_SESSIONS_CAMELCASE_ENDPOINT = "/api/ipsec/sessions/searchPhase2"
IPSEC_CHILD_SA_INSTALLED_STATE = "INSTALLED"
IPSEC_CONNECTION_TOGGLE_SNAKE_ENDPOINT_PREFIX = "/api/ipsec/connections/toggle_connection/"
IPSEC_CONNECTION_TOGGLE_CAMELCASE_ENDPOINT_PREFIX = "/api/ipsec/connections/toggleConnection/"
IPSEC_SERVICE_RECONFIGURE_ENDPOINT = "/api/ipsec/service/reconfigure"
IPSEC_CONFIGURED_VERSIONS: dict[str, str] = {"1": "IKEv1", "2": "IKEv2"}
OPENVPN_INSTANCE_TOGGLE_ENDPOINT_PREFIX = "/api/openvpn/instances/toggle/"
OPENVPN_SERVICE_RECONFIGURE_ENDPOINT = "/api/openvpn/service/reconfigure"
WIREGUARD_CLIENT_TOGGLE_SNAKE_ENDPOINT_PREFIX = "/api/wireguard/client/toggle_client/"
WIREGUARD_CLIENT_TOGGLE_CAMELCASE_ENDPOINT_PREFIX = "/api/wireguard/client/toggleClient/"
WIREGUARD_SERVER_TOGGLE_SNAKE_ENDPOINT_PREFIX = "/api/wireguard/server/toggle_server/"
WIREGUARD_SERVER_TOGGLE_CAMELCASE_ENDPOINT_PREFIX = "/api/wireguard/server/toggleServer/"
WIREGUARD_SERVICE_RECONFIGURE_ENDPOINT = "/api/wireguard/service/reconfigure"


class VPNMixin(AiopnsenseClientProtocol):
    """VPN methods for OPNsenseClient."""

    @staticmethod
    def _mapping_value(data: MutableMapping[str, Any], key: str) -> MutableMapping[str, Any]:
        """Return a nested mapping field or an empty mapping.

        Args:
            data (MutableMapping[str, Any]): Source mapping.
            key (str): Field expected to contain a nested mapping.

        Returns:
            MutableMapping[str, Any]: Nested mapping, or an empty mapping when
            the field is unavailable or malformed.
        """
        value = data.get(key, {})
        return value if isinstance(value, MutableMapping) else {}

    @staticmethod
    def _wireguard_is_connected(past_time: datetime | None) -> bool:
        """Determine whether a WireGuard session is still considered active.

        Args:
            past_time (datetime | None): Timestamp of the most recent WireGuard handshake.

        Returns:
            bool: True if a wireguard session is still considered active; otherwise, False.
        """
        if not past_time:
            return False
        return datetime.now().astimezone() - past_time <= timedelta(minutes=3)

    @_log_errors
    async def get_openvpn(self) -> MutableMapping[str, Any] | None:
        """Return OpenVPN server and client status information.

        Returns:
            MutableMapping[str, Any] | None: Mapping with ``servers`` and ``clients``
                keyed by UUID. Server entries combine instance, provider,
                session, route, tunnel-address, DNS, status, byte-counter, and
                connected-client data when available. With the default
                ``throw_errors=False``, returns ``None`` when the sessions endpoint
                is unavailable or its rows are missing or malformed, or when the
                instance or provider inventory cannot be read reliably. A known
                missing (404) or forbidden (403) inventory endpoint allows partial
                results in this mode.
                Route-derived ``clients`` and ``connected_clients`` are omitted
                when routes cannot be fetched or have missing or malformed rows,
                rather than reporting zero clients.

        Raises:
            OPNsenseError: Raised when ``self._throw_errors`` is ``True`` and a
                request or endpoint probe fails, including a forbidden (403)
                instance or provider configuration endpoint. A known missing
                (404) instance or provider endpoint still allows partial results.
                Structurally malformed required payloads return ``None`` rather
                than raising.
        """
        # https://docs.opnsense.org/development/api/core/openvpn.html
        # https://github.com/opnsense/core/blob/master/src/opnsense/www/js/widgets/OpenVPNClients.js
        # https://github.com/opnsense/core/blob/master/src/opnsense/www/js/widgets/OpenVPNServers.js
        openvpn: dict[str, Any] = {"servers": {}, "clients": {}}

        # Fetch data
        sessions_endpoint = await self._get_endpoint_path(
            snake_case_path=OPENVPN_SESSIONS_SNAKE_ENDPOINT,
            camel_case_path=OPENVPN_SESSIONS_CAMELCASE_ENDPOINT,
        )
        if await self._is_get_endpoint_available(sessions_endpoint):
            sessions_info = await self._safe_dict_get(sessions_endpoint)
        else:
            _LOGGER.debug("OpenVPN sessions endpoint unavailable")
            return None
        if not isinstance(sessions_info.get("rows"), list):
            _LOGGER.debug("[get_openvpn] sessions unavailable (malformed rows)")
            return None

        routes_endpoint = await self._get_endpoint_path(
            snake_case_path=OPENVPN_ROUTES_SNAKE_ENDPOINT,
            camel_case_path=OPENVPN_ROUTES_CAMELCASE_ENDPOINT,
        )
        if await self._is_get_endpoint_available(routes_endpoint):
            try:
                routes_info = await self._safe_dict_get(routes_endpoint)
            except OPNsenseError as err:
                if self._throw_errors:
                    raise
                _LOGGER.debug("[get_openvpn] routes unavailable. %s: %s", type(err).__name__, err)
                routes_info = {}
        else:
            _LOGGER.debug("OpenVPN routes endpoint unavailable")
            routes_info = {}

        providers_endpoint = OPENVPN_PROVIDERS_ENDPOINT
        if await self._is_get_endpoint_available(providers_endpoint):
            # Preserve the distinction between a valid empty mapping and a failed request.
            providers_info = await self._get(providers_endpoint)
            if providers_info == []:
                providers_info = {}
            if not isinstance(providers_info, MutableMapping):
                _LOGGER.debug("[get_openvpn] providers inventory unavailable")
                return None
        else:
            _LOGGER.debug("OpenVPN providers endpoint unavailable")
            if self._endpoint_availability.get(providers_endpoint) is not False:
                return None
            providers_info = {}
        if any(
            not isinstance(uuid, str) or not uuid or not isinstance(vpn_info, MutableMapping)
            for uuid, vpn_info in providers_info.items()
        ):
            _LOGGER.debug("[get_openvpn] providers inventory unavailable (malformed entry)")
            return None

        instances_endpoint = OPENVPN_INSTANCES_SEARCH_ENDPOINT
        if await self._is_get_endpoint_available(instances_endpoint):
            instances_info = await self._safe_dict_get(instances_endpoint)
            instance_rows = instances_info.get("rows")
            if not isinstance(instance_rows, list):
                _LOGGER.debug("[get_openvpn] instances inventory unavailable (malformed rows)")
                return None
            for instance in instance_rows:
                if not isinstance(instance, MutableMapping):
                    _LOGGER.debug("[get_openvpn] instances inventory unavailable (malformed row)")
                    return None
                role = instance.get("role")
                if not isinstance(role, str) or not role.strip():
                    _LOGGER.debug("[get_openvpn] instances inventory unavailable (malformed role)")
                    return None
                if role.lower() in {"server", "client"}:
                    uuid = instance.get("uuid")
                    if not isinstance(uuid, str) or not uuid:
                        _LOGGER.debug(
                            "[get_openvpn] instances inventory unavailable (malformed identity)"
                        )
                        return None
        else:
            _LOGGER.debug("OpenVPN instances endpoint unavailable")
            if self._endpoint_availability.get(instances_endpoint) is not False:
                return None
            instances_info = {}

        self._process_openvpn_instances(instances_info, openvpn)
        self._process_openvpn_providers(providers_info, openvpn)
        self._process_openvpn_sessions(sessions_info, openvpn)
        routes_available = isinstance(routes_info.get("rows"), list)
        if routes_available:
            self._process_openvpn_routes(routes_info, openvpn)
        await self._fetch_openvpn_server_details(openvpn)
        if not routes_available:
            _LOGGER.debug("[get_openvpn] routes unavailable (missing or malformed rows)")
            for server in openvpn["servers"].values():
                server.pop("clients", None)
                server.pop("connected_clients", None)
        _LOGGER.debug(
            "[get_openvpn] servers: %s, clients: %s",
            len(openvpn["servers"]),
            len(openvpn["clients"]),
        )
        return openvpn

    @staticmethod
    def _process_openvpn_instances(
        instances_info: MutableMapping[str, Any], openvpn: MutableMapping[str, Any]
    ) -> None:
        """Process OpenVPN instances into servers and clients.

        Args:
            instances_info (MutableMapping[str, Any]): Raw OpenVPN instance payload from the API.
            openvpn (MutableMapping[str, Any]): Accumulated OpenVPN data structure being populated.
        """
        for instance in instances_info.get("rows", []):
            if not isinstance(instance, MutableMapping):
                continue
            role = instance.get("role", "").lower()
            uuid = instance.get("uuid")
            if role == "server":
                VPNMixin._add_openvpn_server(instance, openvpn)
            elif role == "client" and uuid:
                openvpn["clients"][uuid] = {
                    "name": instance.get("description"),
                    "uuid": uuid,
                    "enabled": api_value_matches(instance.get("enabled"), "1"),
                }

    @staticmethod
    def _add_openvpn_server(
        instance: MutableMapping[str, Any], openvpn: MutableMapping[str, Any]
    ) -> None:
        """Add a server to the OpenVPN structure.

        Args:
            instance (MutableMapping[str, Any]): OpenVPN instance entry from the API payload.
            openvpn (MutableMapping[str, Any]): Accumulated OpenVPN data structure being populated.
        """
        uuid = instance.get("uuid")
        if not uuid:
            return
        if uuid not in openvpn["servers"]:
            openvpn["servers"][uuid] = {
                "uuid": uuid,
                "name": instance.get("description"),
                "enabled": api_value_matches(instance.get("enabled"), "1"),
                "dev_type": instance.get("dev_type"),
                "clients": [],
            }

    @staticmethod
    def _process_openvpn_providers(
        providers_info: MutableMapping[str, Any], openvpn: MutableMapping[str, Any]
    ) -> None:
        """Process OpenVPN providers.

        Args:
            providers_info (MutableMapping[str, Any]): Raw OpenVPN provider payload from the API.
            openvpn (MutableMapping[str, Any]): Accumulated OpenVPN data structure being populated.
        """
        for uuid, vpn_info in providers_info.items():
            if not uuid or not isinstance(vpn_info, MutableMapping):
                continue
            server = openvpn["servers"].setdefault(uuid, {"uuid": uuid, "clients": []})
            server.update({"name": vpn_info.get("name")})
            if vpn_info.get("hostname") and vpn_info.get("local_port"):
                server["endpoint"] = f"{vpn_info['hostname']}:{vpn_info['local_port']}"

    @staticmethod
    def _process_openvpn_sessions(
        sessions_info: MutableMapping[str, Any], openvpn: MutableMapping[str, Any]
    ) -> None:
        """Process OpenVPN sessions.

        Args:
            sessions_info (MutableMapping[str, Any]): Raw OpenVPN session payload from the API.
            openvpn (MutableMapping[str, Any]): Accumulated OpenVPN data structure being populated.
        """
        for session in sessions_info.get("rows", []):
            if not isinstance(session, MutableMapping) or "id" not in session:
                continue
            if session.get("type") != "server":
                continue
            server_id = str(session["id"]).split("_", 1)[0]
            server = openvpn["servers"].setdefault(server_id, {"uuid": server_id, "clients": []})
            if description := session.get("description"):
                server["name"] = description
            VPNMixin._update_openvpn_server_status(server, session)

    @staticmethod
    def _update_openvpn_server_status(
        server: MutableMapping[str, Any], session: MutableMapping[str, Any]
    ) -> None:
        """Update server status based on session data.

        Args:
            server (MutableMapping[str, Any]): Server entry to update.
            session (MutableMapping[str, Any]): Session entry payload retrieved from the API.
        """
        status = session.get("status")
        if not session.get("is_client", False):
            server["status"] = (
                "disabled"
                if not server.get("enabled", True)
                else "up"
                if status in {"connected", "ok"}
                else "failed"
                if status == "failed"
                else status or "down"
            )
        else:
            server.update(
                {
                    "status": "up",
                    "latest_handshake": timestamp_to_datetime(
                        session.get("connected_since__time_t_")
                    ),
                    "total_bytes_recv": try_to_int(session.get("bytes_received", 0), 0),
                    "total_bytes_sent": try_to_int(session.get("bytes_sent", 0), 0),
                }
            )

    @staticmethod
    def _process_openvpn_routes(
        routes_info: MutableMapping[str, Any], openvpn: MutableMapping[str, Any]
    ) -> None:
        """Process OpenVPN routes.

        Args:
            routes_info (MutableMapping[str, Any]): Raw OpenVPN route payload from the API.
            openvpn (MutableMapping[str, Any]): Accumulated OpenVPN data structure being populated.
        """
        for route in routes_info.get("rows", []):
            if not isinstance(route, MutableMapping):
                continue
            server_id = route.get("id")
            if server_id not in openvpn["servers"]:
                continue
            openvpn["servers"][server_id]["clients"].append(
                {
                    "name": route.get("common_name"),
                    "endpoint": route.get("real_address"),
                    "tunnel_addresses": [route.get("virtual_address")],
                    "latest_handshake": timestamp_to_datetime(route.get("last_ref__time_t_", 0)),
                }
            )

    async def _fetch_openvpn_server_details(self, openvpn: MutableMapping[str, Any]) -> None:
        """Fetch detailed server information.

        Args:
            openvpn (MutableMapping[str, Any]): Accumulated OpenVPN data structure being populated.
        """
        for uuid, server in openvpn["servers"].items():
            server.setdefault("total_bytes_sent", 0)
            server.setdefault("total_bytes_recv", 0)
            server["connected_clients"] = len(server.get("clients", []))
            details_endpoint = f"{OPENVPN_INSTANCE_DETAILS_ENDPOINT_PREFIX}{uuid}"
            if await self._is_get_endpoint_available(details_endpoint):
                details_info = await self._safe_dict_get(details_endpoint)
            else:
                _LOGGER.debug("OpenVPN instance details endpoint unavailable for uuid: %s", uuid)
                details_info = {}
            details = self._mapping_value(details_info, "instance")
            if details.get("server"):
                server["tunnel_addresses"] = [details["server"]]
            server["dns_servers"] = [
                dns["value"]
                for dns in self._mapping_value(details, "dns_servers").values()
                if isinstance(dns, MutableMapping)
                and api_value_matches(dns.get("selected"), "1")
                and dns.get("value")
            ]

    @_log_errors
    async def get_wireguard(self) -> MutableMapping[str, Any] | None:
        """Return WireGuard server and client status information.

        Returns:
            MutableMapping[str, Any] | None: Mapping with ``servers`` and ``clients``
                keyed by UUID. Entries combine configured tunnel addresses,
                peer links, enabled state, interface names, handshake status,
                byte counters, and connected peer counts where available. With the
                default ``throw_errors=False``, returns ``None`` when the
                summary endpoint is unavailable or its rows are missing or
                malformed, or either configuration inventory cannot be read
                reliably. A known missing (404) or forbidden (403) configuration
                endpoint is treated as empty in this mode. A valid empty summary
                still reports configured peers as disconnected with zero counters.
                When ``self._throw_errors`` is ``True``, request or endpoint-probe
                failures can raise ``OPNsenseError``, including a forbidden (403)
                configuration endpoint. A known missing (404) endpoint is treated
                as empty for the clients and servers configuration inventories.
                Structurally malformed required payloads return ``None`` regardless
                of error mode.
        """
        data_sources = {
            "summary_raw": WIREGUARD_SUMMARY_ENDPOINT,
            "clients_raw": WIREGUARD_CLIENTS_ENDPOINT,
            "servers_raw": WIREGUARD_SERVERS_ENDPOINT,
        }
        data: dict[str, dict[str, Any]] = {}
        for key, path in data_sources.items():
            if await self._is_get_endpoint_available(path):
                data[key] = await self._safe_dict_get(path)
            else:
                _LOGGER.debug("WireGuard endpoint unavailable: %s", path)
                if self._endpoint_availability.get(path) is not False:
                    return None
                # Only known missing or forbidden configuration may be treated as empty.
                if key == "clients_raw":
                    data[key] = {"client": {"clients": {"client": {}}}}
                elif key == "servers_raw":
                    data[key] = {"server": {"servers": {"server": {}}}}
                else:
                    return None

        summary = data["summary_raw"].get("rows")
        client_summ = dict_get(data["clients_raw"], "client.clients.client")
        server_summ = dict_get(data["servers_raw"], "server.servers.server")

        if (
            not isinstance(summary, list)
            or not isinstance(client_summ, MutableMapping)
            or not isinstance(server_summ, MutableMapping)
        ):
            _LOGGER.debug("[get_wireguard] status unavailable (malformed payload)")
            return None
        if any(not isinstance(client, MutableMapping) for client in client_summ.values()) or any(
            not isinstance(server, MutableMapping) for server in server_summ.values()
        ):
            _LOGGER.debug("[get_wireguard] configuration unavailable (malformed entry)")
            return None

        servers = {
            uid: self._process_wireguard_server(uid, srv, client_summ)
            for uid, srv in server_summ.items()
        }
        clients = {
            uid: self._process_wireguard_client(uid, clnt, servers)
            for uid, clnt in client_summ.items()
        }

        self._update_wireguard_status(summary, servers, clients)

        wireguard = {"servers": servers, "clients": clients}
        _LOGGER.debug(
            "[get_wireguard] servers: %s, clients: %s",
            len(servers),
            len(clients),
        )
        return wireguard

    @staticmethod
    def _process_wireguard_server(
        uid: str, srv: MutableMapping[str, Any], client_summ: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        """Process a single WireGuard server entry.

        Args:
            uid (str): WireGuard server UUID from the server settings payload.
            srv (MutableMapping[str, Any]): WireGuard server settings row.
            client_summ (MutableMapping[str, Any]): Client settings mapping
                used to enrich selected peer links with public keys.

        Returns:
            MutableMapping[str, Any]: Normalized server mapping with identity,
                enabled state, interface, DNS servers, tunnel addresses,
                configured client peers, and initialized connection counters.
        """
        tunnel_addresses = VPNMixin._mapping_value(srv, "tunneladdress")
        peers = VPNMixin._mapping_value(srv, "peers")
        return {
            "uuid": uid,
            "name": srv.get("name"),
            "pubkey": srv.get("pubkey"),
            "enabled": api_value_matches(srv.get("enabled"), "1"),
            "interface": f"wg{srv.get('instance', '')}",
            "dns_servers": [srv.get("peer_dns")] if srv.get("peer_dns") else [],
            "tunnel_addresses": [
                addr.get("value")
                for addr in tunnel_addresses.values()
                if isinstance(addr, MutableMapping)
                and api_value_matches(addr.get("selected"), "1")
                and addr.get("value")
            ],
            "clients": [
                {
                    "name": peer.get("value"),
                    "uuid": peer_id,
                    "pubkey": client_summ.get(peer_id, {}).get("pubkey"),
                    "connected": False,
                }
                for peer_id, peer in peers.items()
                if isinstance(peer, MutableMapping)
                and api_value_matches(peer.get("selected"), "1")
                and peer.get("value")
            ],
            "connected_clients": 0,
            "total_bytes_recv": 0,
            "total_bytes_sent": 0,
        }

    @staticmethod
    def _process_wireguard_client(
        uid: str, clnt: MutableMapping[str, Any], servers: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        """Process a single WireGuard client entry.

        Args:
            uid (str): WireGuard client UUID from the client settings payload.
            clnt (MutableMapping[str, Any]): WireGuard client settings row.
            servers (MutableMapping[str, Any]): Normalized server mapping used
                to enrich configured server links.

        Returns:
            MutableMapping[str, Any]: Normalized client mapping with identity,
                enabled state, tunnel addresses, configured server links, and
                initialized connection counters.
        """
        tunnel_addresses = VPNMixin._mapping_value(clnt, "tunneladdress")
        server_links = VPNMixin._mapping_value(clnt, "servers")
        return {
            "uuid": uid,
            "name": clnt.get("name"),
            "pubkey": clnt.get("pubkey"),
            "enabled": api_value_matches(clnt.get("enabled"), "1"),
            "tunnel_addresses": [
                addr.get("value")
                for addr in tunnel_addresses.values()
                if isinstance(addr, MutableMapping)
                and api_value_matches(addr.get("selected"), "1")
                and addr.get("value")
            ],
            "servers": [
                VPNMixin._link_wireguard_client_to_server(srv_id, servers, srv)
                for srv_id, srv in server_links.items()
                if isinstance(srv, MutableMapping)
                and api_value_matches(srv.get("selected"), "1")
                and srv.get("value")
            ],
            "connected_servers": 0,
            "total_bytes_recv": 0,
            "total_bytes_sent": 0,
        }

    @staticmethod
    def _link_wireguard_client_to_server(
        srv_id: str, servers: MutableMapping[str, Any], srv: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        """Link a WireGuard client to its corresponding server.

        Args:
            srv_id (str): Server identifier used to match related entries.
            servers (MutableMapping[str, Any]): Server mapping keyed by server identifier.
            srv (MutableMapping[str, Any]): WireGuard server mapping entry.

        Returns:
            MutableMapping[str, Any]: Mapping that describes the linked
                client-to-server relationship, including keys such as
                ``name``, ``uuid``, and ``connected``, and optionally
                ``pubkey``, ``interface``, and ``tunnel_addresses`` when the
                referenced server exists in ``servers``.
        """
        if srv_id in servers:
            server = servers[srv_id]
            return {
                "name": server.get("name"),
                "uuid": srv_id,
                "connected": False,
                "pubkey": server.get("pubkey"),
                "interface": server.get("interface"),
                "tunnel_addresses": server.get("tunnel_addresses"),
            }
        return {
            "name": srv.get("value"),
            "uuid": srv_id,
            "connected": False,
        }

    @staticmethod
    def _update_wireguard_status(
        summary: list[MutableMapping[str, Any]],
        servers: MutableMapping[str, Any],
        clients: MutableMapping[str, Any],
    ) -> None:
        """Update WireGuard server and client statuses based on the summary.

        Args:
            summary (list[MutableMapping[str, Any]]): WireGuard summary payload from the API.
            servers (MutableMapping[str, Any]): Server mapping keyed by server identifier.
            clients (MutableMapping[str, Any]): Client mapping keyed by client identifier.
        """
        for entry in summary:
            if not isinstance(entry, MutableMapping):
                continue
            if entry.get("type") == "interface":
                for server in servers.values():
                    if server.get("pubkey") == entry.get("public-key"):
                        server["status"] = entry.get("status")
            elif entry.get("type") == "peer":
                VPNMixin._update_wireguard_peer_status(entry, servers, clients)

    @staticmethod
    def _update_wireguard_peer_status(
        entry: MutableMapping[str, Any],
        servers: MutableMapping[str, Any],
        clients: MutableMapping[str, Any],
    ) -> None:
        """Update the WireGuard peer status for clients and servers.

        Args:
            entry (MutableMapping[str, Any]): WireGuard summary peer row from
                ``/api/wireguard/service/show``.
            servers (MutableMapping[str, Any]): Server mapping keyed by server identifier.
            clients (MutableMapping[str, Any]): Client mapping keyed by client identifier.
        """
        pubkey = entry.get("public-key", "-")
        interface = entry.get("if", "-")
        endpoint = entry.get("endpoint", None)
        transfer_rx: int = try_to_int(entry.get("transfer-rx", 0), 0) or 0
        transfer_tx: int = try_to_int(entry.get("transfer-tx", 0), 0) or 0
        latest_handshake = try_to_int(entry.get("latest-handshake", 0), 0)
        handshake_time = timestamp_to_datetime(latest_handshake)
        is_connected = VPNMixin._wireguard_is_connected(handshake_time)

        # Update servers
        for server in servers.values():
            if server.get("interface") == interface:
                for client in server.get("clients", []):
                    if client.get("pubkey") == pubkey:
                        VPNMixin._update_wireguard_peer_details(
                            peer=client,
                            server_or_client=server,
                            endpoint=endpoint,
                            transfer_rx=transfer_rx,
                            transfer_tx=transfer_tx,
                            handshake_time=handshake_time,
                            is_connected=is_connected,
                            connection_counter_key="connected_clients",
                        )

        # Update clients
        for client in clients.values():
            if client.get("pubkey") == pubkey:
                for server in client.get("servers", []):
                    if server.get("interface") == interface:
                        VPNMixin._update_wireguard_peer_details(
                            peer=server,
                            server_or_client=client,
                            endpoint=endpoint,
                            transfer_rx=transfer_rx,
                            transfer_tx=transfer_tx,
                            handshake_time=handshake_time,
                            is_connected=is_connected,
                            connection_counter_key="connected_servers",
                        )

    @staticmethod
    def _update_wireguard_peer_details(
        peer: MutableMapping[str, Any],
        server_or_client: MutableMapping[str, Any],
        endpoint: str,
        transfer_rx: int,
        transfer_tx: int,
        handshake_time: datetime | None,
        is_connected: bool,
        connection_counter_key: str,
    ) -> None:
        """Apply live WireGuard peer counters to a linked server or client.

        Args:
            peer (MutableMapping[str, Any]): Linked peer mapping to update.
            server_or_client (MutableMapping[str, Any]): Parent server or
                client mapping whose aggregate counters should be incremented.
            endpoint (str): Remote endpoint string reported by WireGuard.
            transfer_rx (int): Received byte counter for peer statistics.
            transfer_tx (int): Transmitted byte counter for peer statistics.
            handshake_time (datetime | None): Handshake time used by this operation.
            is_connected (bool): Connection status flag for the interface entry.
            connection_counter_key (str): Counter key used for interface connection tracking.
        """
        if endpoint and endpoint != "(none)":
            peer["endpoint"] = endpoint
        peer["bytes_recv"] = transfer_rx
        peer["bytes_sent"] = transfer_tx
        peer["latest_handshake"] = handshake_time
        peer["connected"] = is_connected

        # Update the parent (server or client) stats
        server_or_client["total_bytes_recv"] = (
            server_or_client.get("total_bytes_recv", 0) + transfer_rx
        )
        server_or_client["total_bytes_sent"] = (
            server_or_client.get("total_bytes_sent", 0) + transfer_tx
        )

        if is_connected:
            server_or_client[connection_counter_key] = (
                server_or_client.get(connection_counter_key, 0) + 1
            )
            # Update the latest handshake time if it's newer
            if (
                server_or_client.get("latest_handshake") is None
                or server_or_client["latest_handshake"] < handshake_time
            ):
                server_or_client["latest_handshake"] = handshake_time

    async def toggle_vpn_instance(self, vpn_type: str, clients_servers: str, uuid: str) -> bool:
        """Toggle the specified VPN instance on or off.

        Args:
            vpn_type (str): Vpn type used by this operation.
            clients_servers (str): WireGuard collection to toggle. Use
                ``clients`` for a client entry or ``servers`` for a server
                entry. IPsec only supports ``connections``. Ignored for OpenVPN.
            uuid (str): UUID of the VPN instance to toggle.

        Returns:
            bool: True when the toggle operation completes successfully; otherwise, False.
        """
        if vpn_type == "openvpn":
            success = await self._safe_dict_post(f"{OPENVPN_INSTANCE_TOGGLE_ENDPOINT_PREFIX}{uuid}")
            _LOGGER.debug(
                "[toggle_vpn_instance] OpenVPN instance toggle response for UUID %s: %s",
                uuid,
                success,
            )
            if not success.get("changed", False):
                _LOGGER.debug(
                    "[toggle_vpn_instance] OpenVPN instance toggle did not report a change for "
                    "UUID %s",
                    uuid,
                )
                return False
            reconfigure = await self._safe_dict_post(OPENVPN_SERVICE_RECONFIGURE_ENDPOINT)
            _LOGGER.debug(
                "[toggle_vpn_instance] OpenVPN service reconfigure response after toggling UUID "
                "%s: %s",
                uuid,
                reconfigure,
            )
            reconfigure_succeeded = reconfigure.get("result", "") == "ok"
            if not reconfigure_succeeded:
                _LOGGER.debug(
                    "[toggle_vpn_instance] OpenVPN service reconfigure did not report success "
                    "after toggling UUID %s",
                    uuid,
                )
            return reconfigure_succeeded
        if vpn_type == "wireguard":
            if clients_servers == "clients":
                endpoint = await self._get_endpoint_path(
                    snake_case_path=f"{WIREGUARD_CLIENT_TOGGLE_SNAKE_ENDPOINT_PREFIX}{uuid}",
                    camel_case_path=f"{WIREGUARD_CLIENT_TOGGLE_CAMELCASE_ENDPOINT_PREFIX}{uuid}",
                )
            elif clients_servers == "servers":
                endpoint = await self._get_endpoint_path(
                    snake_case_path=f"{WIREGUARD_SERVER_TOGGLE_SNAKE_ENDPOINT_PREFIX}{uuid}",
                    camel_case_path=f"{WIREGUARD_SERVER_TOGGLE_CAMELCASE_ENDPOINT_PREFIX}{uuid}",
                )
            else:
                return False
            success = await self._safe_dict_post(endpoint)
            if not success.get("changed", False):
                return False
            reconfigure = await self._safe_dict_post(WIREGUARD_SERVICE_RECONFIGURE_ENDPOINT)
            return reconfigure.get("result", "") == "ok"
        if vpn_type == "ipsec":
            if clients_servers != "connections":
                return False
            endpoint = await self._get_endpoint_path(
                snake_case_path=f"{IPSEC_CONNECTION_TOGGLE_SNAKE_ENDPOINT_PREFIX}{uuid}",
                camel_case_path=f"{IPSEC_CONNECTION_TOGGLE_CAMELCASE_ENDPOINT_PREFIX}{uuid}",
            )
            success = await self._safe_dict_post(endpoint)
            _LOGGER.debug(
                "[toggle_vpn_instance] IPsec connection toggle response for UUID %s: %s",
                uuid,
                success,
            )
            if not success.get("changed", False):
                return False
            reconfigure = await self._safe_dict_post(IPSEC_SERVICE_RECONFIGURE_ENDPOINT)
            _LOGGER.debug(
                "[toggle_vpn_instance] IPsec service reconfigure response after toggling UUID "
                "%s: %s",
                uuid,
                reconfigure,
            )
            return reconfigure.get("status", "") == "ok"
        return False

    @staticmethod
    def _ipsec_flag(value: Any) -> bool:
        """Interpret a boolean-like IPsec session field.

        Args:
            value (Any): Raw field value, which the IPsec sessions API reports
                as a JSON boolean, a ``"0"``/``"1"`` string, or a textual
                ``"true"``/``"false"`` value depending on the firmware version.

        Returns:
            bool: True when the field represents an enabled or true state.
        """
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "yes"}

    @staticmethod
    def _process_ipsec_connection(
        row: MutableMapping[str, Any],
    ) -> MutableMapping[str, Any] | None:
        """Normalize a single IPsec phase 1 session row.

        Args:
            row (MutableMapping[str, Any]): Raw row from the OPNsense IPsec
                ``search_phase1`` sessions API.

        Returns:
            MutableMapping[str, Any] | None: Normalized connection mapping keyed
                for the ``connections`` collection, or ``None`` when the row has
                no usable connection identifier.
        """
        uuid = row.get("ikeid") or row.get("name")
        if not isinstance(uuid, str) or not uuid:
            return None
        connected = VPNMixin._ipsec_flag(row.get("connected"))
        description = row.get("phase1desc")
        name = description if isinstance(description, str) and description else uuid
        return {
            "uuid": uuid,
            "name": name,
            "connected": connected,
            "status": "connected" if connected else "disconnected",
            "version": row.get("version"),
            "local_addrs": row.get("local-addrs"),
            "remote_addrs": row.get("remote-addrs"),
            "local_id": row.get("local-id"),
            "remote_id": row.get("remote-id"),
            "routed": VPNMixin._ipsec_flag(row.get("routed")),
            "install_time": try_to_int(row.get("install-time")) if connected else None,
            "total_bytes_recv": try_to_int(row.get("bytes-in"), 0),
            "total_bytes_sent": try_to_int(row.get("bytes-out"), 0),
            "total_packets_recv": try_to_int(row.get("packets-in"), 0),
            "total_packets_sent": try_to_int(row.get("packets-out"), 0),
        }

    @staticmethod
    def _ipsec_config_enabled(row: MutableMapping[str, Any]) -> bool | None:
        """Read the ``enabled`` field of an IPsec configuration row.

        Args:
            row (MutableMapping[str, Any]): Raw row from an OPNsense IPsec
                configuration search API.

        Returns:
            bool | None: The enabled state, or ``None`` when the field is
                missing or not a recognizable ``0``/``1`` value.
        """
        value = row.get("enabled")
        if isinstance(value, bool):
            return value
        if str(value).strip() in {"0", "1"}:
            return str(value).strip() == "1"
        return None

    @staticmethod
    def _process_ipsec_configured_connection(
        row: MutableMapping[str, Any],
    ) -> MutableMapping[str, Any] | None:
        """Normalize a configured IPsec connection that has no active session.

        Args:
            row (MutableMapping[str, Any]): Raw row from the OPNsense IPsec
                ``search_connection`` configuration API.

        Returns:
            MutableMapping[str, Any] | None: Disconnected connection mapping in
                the same shape as a session row, or ``None`` when the row has no
                usable UUID. ``version`` uses the session spelling (``IKEv1`` or
                ``IKEv2``) and is ``None`` when both versions are allowed;
                ``enabled`` is omitted when the configured value is unrecognized.
        """
        uuid = row.get("uuid")
        if not isinstance(uuid, str) or not uuid:
            return None
        description = row.get("description")
        connection: dict[str, Any] = {
            "uuid": uuid,
            "name": description if isinstance(description, str) and description else uuid,
            "connected": False,
            "status": "disconnected",
            "version": IPSEC_CONFIGURED_VERSIONS.get(str(row.get("version"))),
            "local_addrs": row.get("local_addrs"),
            "remote_addrs": row.get("remote_addrs"),
            "local_id": None,
            "remote_id": None,
            "routed": False,
            "install_time": None,
            "total_bytes_recv": 0,
            "total_bytes_sent": 0,
            "total_packets_recv": 0,
            "total_packets_sent": 0,
        }
        enabled = VPNMixin._ipsec_config_enabled(row)
        if enabled is not None:
            connection["enabled"] = enabled
        return connection

    async def _get_ipsec_configured_rows(
        self, snake_case_path: str, camel_case_path: str
    ) -> dict[str, MutableMapping[str, Any]] | None:
        """Return IPsec configuration rows keyed by UUID.

        Args:
            snake_case_path (str): Snake case search endpoint for the configuration items.
            camel_case_path (str): Camel case search endpoint for the configuration items.

        Returns:
            dict[str, MutableMapping[str, Any]] | None: Raw configuration rows
                keyed by UUID. Empty when the endpoint is known to be missing or
                not permitted for the API user. ``None`` when the configuration
                could not be read this time (transient failure or malformed
                payload).
        """
        endpoint = await self._get_endpoint_path(
            snake_case_path=snake_case_path,
            camel_case_path=camel_case_path,
        )
        if not await self._is_get_endpoint_available(endpoint):
            if self._endpoint_availability.get(endpoint) is False:
                _LOGGER.debug("IPsec configuration endpoint missing or not permitted: %s", endpoint)
                return {}
            _LOGGER.debug("IPsec configuration endpoint temporarily unavailable: %s", endpoint)
            return None
        payload = await self._safe_dict_get(endpoint)
        rows = payload.get("rows")
        if not isinstance(rows, list):
            _LOGGER.debug("[get_ipsec] configuration unavailable (malformed rows): %s", endpoint)
            return None
        configured: dict[str, MutableMapping[str, Any]] = {}
        for row in rows:
            if not isinstance(row, MutableMapping):
                _LOGGER.debug("[get_ipsec] configuration unavailable (malformed row): %s", endpoint)
                return None
            uuid = row.get("uuid")
            if not isinstance(uuid, str) or not uuid:
                _LOGGER.debug(
                    "[get_ipsec] configuration unavailable (malformed identity): %s", endpoint
                )
                return None
            configured[uuid] = row
        return configured

    @staticmethod
    def _new_ipsec_child(
        uuid: str, configured_row: MutableMapping[str, Any] | None
    ) -> dict[str, Any]:
        """Build a disconnected IPsec child (phase 2) entry.

        Args:
            uuid (str): Child identifier, the configured child UUID when known.
            configured_row (MutableMapping[str, Any] | None): Raw row from the
                OPNsense IPsec ``search_child`` configuration API, or ``None``
                for a child seen only in the sessions API.

        Returns:
            dict[str, Any]: Child mapping with zeroed counters and no installed SAs.
                ``local_ts``/``remote_ts`` are the configured traffic selectors;
                ``active_local_ts``/``active_remote_ts`` collect the selectors of
                installed SAs, which can be narrower when the peer narrows them.
        """
        row = configured_row or {}
        description = row.get("description")
        connection = row.get("connection")
        return {
            "uuid": uuid,
            "name": description if isinstance(description, str) and description else uuid,
            "connection": connection if isinstance(connection, str) and connection else None,
            "enabled": (
                VPNMixin._ipsec_config_enabled(row) if configured_row is not None else None
            ),
            "connected": False,
            "status": "disconnected",
            "installed_sas": 0,
            "local_ts": row.get("local_ts"),
            "remote_ts": row.get("remote_ts"),
            "active_local_ts": [],
            "active_remote_ts": [],
            "install_time": None,
            "total_bytes_recv": 0,
            "total_bytes_sent": 0,
            "total_packets_recv": 0,
            "total_packets_sent": 0,
        }

    @staticmethod
    def _add_ipsec_child_sa(child: dict[str, Any], sa_row: MutableMapping[str, Any]) -> None:
        """Aggregate one phase 2 security association into its child entry.

        Byte and packet counters are summed over the SAs, so they drop when an SA
        is rekeyed. ``install_time`` is the age of the oldest installed SA.

        Args:
            child (dict[str, Any]): Child entry updated in place.
            sa_row (MutableMapping[str, Any]): Raw row from the OPNsense IPsec
                ``search_phase2`` sessions API.
        """
        child["total_bytes_recv"] += try_to_int(sa_row.get("bytes-in"), 0)
        child["total_bytes_sent"] += try_to_int(sa_row.get("bytes-out"), 0)
        child["total_packets_recv"] += try_to_int(sa_row.get("packets-in"), 0)
        child["total_packets_sent"] += try_to_int(sa_row.get("packets-out"), 0)
        if sa_row.get("state") != IPSEC_CHILD_SA_INSTALLED_STATE:
            return
        child["installed_sas"] += 1
        child["connected"] = True
        child["status"] = "connected"
        for field, key in (("local-ts", "active_local_ts"), ("remote-ts", "active_remote_ts")):
            selector = sa_row.get(field)
            if isinstance(selector, str) and selector and selector not in child[key]:
                child[key].append(selector)
                child[key].sort()
        install_time = try_to_int(sa_row.get("install-time"))
        if install_time is not None and (
            child["install_time"] is None or install_time > child["install_time"]
        ):
            child["install_time"] = install_time

    async def _get_ipsec_children(
        self, connected_session_names: list[str]
    ) -> dict[str, dict[str, Any]] | None:
        """Return IPsec child (phase 2) status keyed by child UUID.

        Args:
            connected_session_names (list[str]): Phase 1 session names of the
                connected connections, used to query their phase 2 SAs.

        Returns:
            dict[str, dict[str, Any]] | None: Children from the configuration,
                plus children seen only in the sessions API, with installed SA
                counts and aggregated counters. ``None`` when the child
                configuration or any phase 2 session list could not be read this
                time, so a missing reply is never reported as a down child.

        Raises:
            OPNsenseError: Raised when a phase 2 sessions request fails, for
                example with a reply that is not valid JSON, and
                ``self._throw_errors`` is ``True``.
        """
        configured = await self._get_ipsec_configured_rows(
            IPSEC_CHILDREN_SNAKE_ENDPOINT, IPSEC_CHILDREN_CAMELCASE_ENDPOINT
        )
        if configured is None:
            return None
        children: dict[str, dict[str, Any]] = {
            uuid: self._new_ipsec_child(uuid, row) for uuid, row in configured.items()
        }
        if not connected_session_names:
            return children
        phase2_endpoint = await self._get_endpoint_path(
            snake_case_path=IPSEC_PHASE2_SESSIONS_SNAKE_ENDPOINT,
            camel_case_path=IPSEC_PHASE2_SESSIONS_CAMELCASE_ENDPOINT,
        )
        for session_name in connected_session_names:
            try:
                payload = await self._safe_dict_post(phase2_endpoint, {"id": session_name})
            except OPNsenseError as err:
                if self._throw_errors:
                    raise
                _LOGGER.debug(
                    "[get_ipsec] phase 2 sessions unavailable for %s. %s: %s",
                    session_name,
                    type(err).__name__,
                    err,
                )
                return None
            sa_rows = payload.get("rows")
            if not isinstance(sa_rows, list):
                _LOGGER.debug(
                    "[get_ipsec] phase 2 sessions unavailable for %s (malformed rows)",
                    session_name,
                )
                return None
            for sa_row in sa_rows:
                if not isinstance(sa_row, MutableMapping):
                    continue
                child_uuid = sa_row.get("name")
                if not isinstance(child_uuid, str) or not child_uuid:
                    continue
                if child_uuid not in children:
                    children[child_uuid] = self._new_ipsec_child(child_uuid, None)
                    description = sa_row.get("phase2desc")
                    if isinstance(description, str) and description:
                        children[child_uuid]["name"] = description
                    children[child_uuid]["connection"] = sa_row.get("ikeid") or None
                self._add_ipsec_child_sa(children[child_uuid], sa_row)
        return children

    @_log_errors
    async def get_ipsec(self) -> MutableMapping[str, Any] | None:
        """Return IPsec phase 1 (IKE) connection status information.

        Returns:
            MutableMapping[str, Any] | None: Mapping with ``connections`` keyed by the
                IKE connection UUID. Entries combine the connection description,
                IKE version, local and remote addresses and identities, the
                connected state, install age, routing state, and byte and packet
                counters reported by the OPNsense IPsec sessions API. ``enabled``
                comes from the IPsec connections configuration: it is ``None``
                for sessions without a configured connection (legacy tunnels, or
                an API user without access to the connections configuration) and
                the key is omitted when the configuration reports an unrecognized
                value. With the default ``throw_errors=False``, returns ``None``
                when the connections configuration cannot be read this time, so
                a partial inventory cannot be mistaken for a complete one. A known
                missing (404) or forbidden (403) configuration endpoint allows
                session-only results with ``enabled=None`` in this mode.
                Configured connections without a session, including disabled
                ones, are reported as disconnected. ``children`` holds phase 2
                status keyed by child UUID, with ``install_time`` as the age of
                the oldest installed SA. A transient child-configuration or phase 2
                read failure, or a decoded response without a valid ``rows`` list,
                omits ``children``. Invalid JSON from the child-configuration
                endpoint instead makes this method return ``None`` by default and
                raise ``OPNsenseError`` when ``self._throw_errors`` is ``True``.
                Returns ``None`` when the phase 1 sessions endpoint is unavailable
                or its rows are missing or malformed. A valid empty session list
                still reports configured connections and children as disconnected
                with zero counters.
                When ``self._throw_errors`` is ``True``, request or endpoint-probe
                failures can raise ``OPNsenseError``, including a forbidden (403)
                connections configuration endpoint. A known missing (404)
                connections configuration endpoint still allows session-only
                results. Structurally malformed phase 1 or connections
                configuration payloads return ``None`` regardless of error mode.
        """
        # https://docs.opnsense.org/development/api/core/ipsec.html
        ipsec: dict[str, Any] = {"connections": {}, "children": {}}
        sessions_endpoint = await self._get_endpoint_path(
            snake_case_path=IPSEC_PHASE1_SESSIONS_SNAKE_ENDPOINT,
            camel_case_path=IPSEC_PHASE1_SESSIONS_CAMELCASE_ENDPOINT,
        )
        if not await self._is_get_endpoint_available(sessions_endpoint):
            _LOGGER.debug("IPsec phase 1 sessions endpoint unavailable")
            return None

        sessions_info = await self._safe_dict_get(sessions_endpoint)
        rows = sessions_info.get("rows")
        if not isinstance(rows, list):
            _LOGGER.debug("[get_ipsec] phase 1 sessions unavailable (malformed rows)")
            return None

        configured = await self._get_ipsec_configured_rows(
            IPSEC_CONNECTIONS_SNAKE_ENDPOINT, IPSEC_CONNECTIONS_CAMELCASE_ENDPOINT
        )
        if configured is None:
            _LOGGER.debug("[get_ipsec] connections inventory unavailable")
            return None
        connected_session_names: list[str] = []
        for row in rows:
            if not isinstance(row, MutableMapping):
                continue
            connection = self._process_ipsec_connection(row)
            if connection is None:
                continue
            configured_row = configured.get(connection["uuid"])
            if configured_row is None:
                connection["enabled"] = None
            else:
                enabled = self._ipsec_config_enabled(configured_row)
                if enabled is not None:
                    connection["enabled"] = enabled
            ipsec["connections"][connection["uuid"]] = connection
            session_name = row.get("name")
            if connection["connected"] and isinstance(session_name, str) and session_name:
                connected_session_names.append(session_name)

        for uuid, configured_row in configured.items():
            if uuid in ipsec["connections"]:
                continue
            connection = self._process_ipsec_configured_connection(configured_row)
            if connection is not None:
                ipsec["connections"][uuid] = connection

        _LOGGER.debug("[get_ipsec] connections: %s", len(ipsec["connections"]))
        children = await self._get_ipsec_children(connected_session_names)
        if children is None:
            del ipsec["children"]
        else:
            ipsec["children"] = children
            _LOGGER.debug("[get_ipsec] children: %s", len(children))
        return ipsec
