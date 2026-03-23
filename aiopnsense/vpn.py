"""OpenVPN and WireGuard methods for OPNsenseClient."""

from collections.abc import MutableMapping
from datetime import datetime, timedelta
from typing import Any

from ._typing import PyOPNsenseClientProtocol
from .helpers import _LOGGER, _log_errors, timestamp_to_datetime, try_to_int


class VPNMixin(PyOPNsenseClientProtocol):
    """VPN methods for OPNsenseClient."""

    @staticmethod
    def wireguard_is_connected(past_time: datetime | None) -> bool:
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
    async def get_openvpn(self) -> MutableMapping[str, Any]:
        """Return OpenVPN information.

        Returns:
            MutableMapping[str, Any]: Normalized data returned by the related OPNsense endpoint.
        """
        # https://docs.opnsense.org/development/api/core/openvpn.html
        # https://github.com/opnsense/core/blob/master/src/opnsense/www/js/widgets/OpenVPNClients.js
        # https://github.com/opnsense/core/blob/master/src/opnsense/www/js/widgets/OpenVPNServers.js
        openvpn: dict[str, Any] = {"servers": {}, "clients": {}}

        # Fetch data
        sessions_info = await self._safe_dict_get("/api/openvpn/service/search_sessions")
        routes_info = await self._safe_dict_get("/api/openvpn/service/search_routes")
        providers_info = await self._safe_dict_get("/api/openvpn/export/providers")
        instances_info = await self._safe_dict_get("/api/openvpn/instances/search")

        await self._process_openvpn_instances(instances_info, openvpn)
        await self._process_openvpn_providers(providers_info, openvpn)
        await self._process_openvpn_sessions(sessions_info, openvpn)
        await self._process_openvpn_routes(routes_info, openvpn)
        # _LOGGER.debug(f"[get_openvpn] sessions_info: {sessions_info}")
        # _LOGGER.debug(f"[get_openvpn] routes_info: {routes_info}")
        # _LOGGER.debug(f"[get_openvpn] providers_info: {providers_info}")
        # _LOGGER.debug(f"[get_openvpn] instances_info: {instances_info}")

        await self._fetch_openvpn_server_details(openvpn)
        # _LOGGER.debug("[get_openvpn] openvpn: %s", openvpn)
        _LOGGER.debug(
            "[get_openvpn] servers: %s, clients: %s",
            len(openvpn["servers"]),
            len(openvpn["clients"]),
        )
        return openvpn

    @staticmethod
    async def _process_openvpn_instances(
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
                await VPNMixin._add_openvpn_server(instance, openvpn)
            elif role == "client" and uuid:
                openvpn["clients"][uuid] = {
                    "name": instance.get("description"),
                    "uuid": uuid,
                    "enabled": instance.get("enabled") == "1",
                }

    @staticmethod
    async def _add_openvpn_server(
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
                "enabled": instance.get("enabled") == "1",
                "dev_type": instance.get("dev_type"),
                "clients": [],
            }

    @staticmethod
    async def _process_openvpn_providers(
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
    async def _process_openvpn_sessions(
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
            await VPNMixin._update_openvpn_server_status(server, session)

    @staticmethod
    async def _update_openvpn_server_status(
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
    async def _process_openvpn_routes(
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
            details_info = await self._safe_dict_get(f"/api/openvpn/instances/get/{uuid}")
            details = (
                details_info.get("instance", {}) if isinstance(details_info, MutableMapping) else {}
            )
            if details.get("server"):
                server["tunnel_addresses"] = [details["server"]]
            server["dns_servers"] = [
                dns["value"]
                for dns in details.get("dns_servers", {}).values()
                if dns.get("selected") == 1 and dns.get("value")
            ]

    @_log_errors
    async def get_wireguard(self) -> MutableMapping[str, Any]:
        """Get the details of the WireGuard services.

        Returns:
            MutableMapping[str, Any]: Normalized data returned by the related OPNsense endpoint.
        """
        data_sources = {
            "summary_raw": "/api/wireguard/service/show",
            "clients_raw": "/api/wireguard/client/get",
            "servers_raw": "/api/wireguard/server/get",
        }
        data = {key: await self._safe_dict_get(path) for key, path in data_sources.items()}

        summary = data["summary_raw"].get("rows", [])
        client_summ = data["clients_raw"].get("client", {}).get("clients", {}).get("client", {})
        server_summ = data["servers_raw"].get("server", {}).get("servers", {}).get("server", {})

        if (
            not isinstance(summary, list)
            or not isinstance(client_summ, MutableMapping)
            or not isinstance(server_summ, MutableMapping)
        ):
            _LOGGER.debug("[get_wireguard] servers: 0, clients: 0")
            return {"servers": {}, "clients": {}}

        servers = {
            uid: await self._process_wireguard_server(uid, srv, client_summ)
            for uid, srv in server_summ.items()
            if isinstance(srv, MutableMapping)
        }
        clients = {
            uid: await self._process_wireguard_client(uid, clnt, servers)
            for uid, clnt in client_summ.items()
            if isinstance(clnt, MutableMapping)
        }

        await self._update_wireguard_status(summary, servers, clients)

        wireguard = {"servers": servers, "clients": clients}
        # _LOGGER.debug("[get_wireguard] wireguard: %s", wireguard)
        _LOGGER.debug(
            "[get_wireguard] servers: %s, clients: %s",
            len(servers),
            len(clients),
        )
        return wireguard

    @staticmethod
    async def _process_wireguard_server(
        uid: str, srv: MutableMapping[str, Any], client_summ: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        """Process a single WireGuard server entry.

        Args:
            uid (str): Unique identifier used by test fixtures.
            srv (MutableMapping[str, Any]): WireGuard server mapping entry.
            client_summ (MutableMapping[str, Any]): WireGuard client summary entry from API data.

        Returns:
            MutableMapping[str, Any]: Mapping containing normalized fields for downstream use.
        """
        return {
            "uuid": uid,
            "name": srv.get("name"),
            "pubkey": srv.get("pubkey"),
            "enabled": srv.get("enabled", "") == "1",
            "interface": f"wg{srv.get('instance', '')}",
            "dns_servers": [srv.get("peer_dns")] if srv.get("peer_dns") else [],
            "tunnel_addresses": [
                addr.get("value")
                for addr in srv.get("tunneladdress", {}).values()
                if addr.get("selected") == 1 and addr.get("value")
            ],
            "clients": [
                {
                    "name": peer.get("value"),
                    "uuid": peer_id,
                    "pubkey": client_summ.get(peer_id, {}).get("pubkey"),
                    "connected": False,
                }
                for peer_id, peer in srv.get("peers", {}).items()
                if peer.get("selected") == 1 and peer.get("value")
            ],
            "connected_clients": 0,
            "total_bytes_recv": 0,
            "total_bytes_sent": 0,
        }

    @staticmethod
    async def _process_wireguard_client(
        uid: str, clnt: MutableMapping[str, Any], servers: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        """Process a single WireGuard client entry.

        Args:
            uid (str): Unique identifier used by test fixtures.
            clnt (MutableMapping[str, Any]): WireGuard client mapping entry.
            servers (MutableMapping[str, Any]): Server mapping keyed by server identifier.

        Returns:
            MutableMapping[str, Any]: Mapping containing normalized fields for downstream use.
        """
        return {
            "uuid": uid,
            "name": clnt.get("name"),
            "pubkey": clnt.get("pubkey"),
            "enabled": clnt.get("enabled", "") == "1",
            "tunnel_addresses": [
                addr.get("value")
                for addr in clnt.get("tunneladdress", {}).values()
                if addr.get("selected") == 1 and addr.get("value")
            ],
            "servers": [
                await VPNMixin._link_wireguard_client_to_server(srv_id, servers, srv)
                for srv_id, srv in clnt.get("servers", {}).items()
                if srv.get("selected") == 1 and srv.get("value")
            ],
            "connected_servers": 0,
            "total_bytes_recv": 0,
            "total_bytes_sent": 0,
        }

    @staticmethod
    async def _link_wireguard_client_to_server(
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
    async def _update_wireguard_status(
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
                await VPNMixin._update_wireguard_peer_status(entry, servers, clients)

    @staticmethod
    async def _update_wireguard_peer_status(
        entry: MutableMapping[str, Any],
        servers: MutableMapping[str, Any],
        clients: MutableMapping[str, Any],
    ) -> None:
        """Update the WireGuard peer status for clients and servers.

        Args:
            entry (MutableMapping[str, Any]): Single lease or telemetry entry under evaluation.
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
        is_connected = VPNMixin.wireguard_is_connected(handshake_time)

        # Update servers
        for server in servers.values():
            if server.get("interface") == interface:
                for client in server.get("clients", []):
                    if client.get("pubkey") == pubkey:
                        await VPNMixin._update_wireguard_peer_details(
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
                        await VPNMixin._update_wireguard_peer_details(
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
    async def _update_wireguard_peer_details(
        peer: MutableMapping[str, Any],
        server_or_client: MutableMapping[str, Any],
        endpoint: str,
        transfer_rx: int,
        transfer_tx: int,
        handshake_time: datetime | None,
        is_connected: bool,
        connection_counter_key: str,
    ) -> None:
        """Update details of WireGuard peers.

        Args:
            peer (MutableMapping[str, Any]): WireGuard peer details payload.
            server_or_client (MutableMapping[str, Any]): VPN entity data to evaluate for state changes.
            endpoint (str): Remote endpoint string for peer connection.
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
            clients_servers (str): Mapping that links WireGuard clients to servers.
            uuid (str): Unique identifier of the target OPNsense resource.

        Returns:
            bool: True when the toggle operation completes successfully; otherwise, False.
        """
        if vpn_type == "openvpn":
            success = await self._safe_dict_post(f"/api/openvpn/instances/toggle/{uuid}")
            if not success.get("changed", False):
                return False
            reconfigure = await self._safe_dict_post("/api/openvpn/service/reconfigure")
            return reconfigure.get("result", "") == "ok"
        if vpn_type == "wireguard":
            if clients_servers == "clients":
                endpoint = f"/api/wireguard/client/toggle_client/{uuid}"
            elif clients_servers == "servers":
                endpoint = f"/api/wireguard/server/toggle_server/{uuid}"
            else:
                return False
            success = await self._safe_dict_post(endpoint)
            if not success.get("changed", False):
                return False
            reconfigure = await self._safe_dict_post("/api/wireguard/service/reconfigure")
            return reconfigure.get("result", "") == "ok"
        return False
