import asyncio
import socket
from collections.abc import Iterable
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address, ip_network
from typing import Protocol
from urllib.parse import urlsplit

from webhook_gateway.domain.errors import UnsafeUrlError

type IPAddress = IPv4Address | IPv6Address

_NAT64 = IPv6Network("64:ff9b::/96")
ALLOWED_SCHEMES = frozenset({"http", "https"})


class ResolutionError(Exception):
    pass


class Resolver(Protocol):
    async def resolve(self, host: str) -> list[IPAddress]: ...


class SystemResolver:
    async def resolve(self, host: str) -> list[IPAddress]:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError) as exc:
            raise ResolutionError(str(exc)) from exc
        # strip zone id: "fe80::1%eth0"
        return [ip_address(str(info[4][0]).split("%", 1)[0]) for info in infos]


def _embedded_ipv4(ip: IPv6Address) -> IPv4Address | None:
    # ipv4 hidden inside ipv6: mapped, 6to4, NAT64
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip in _NAT64:
        return IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def is_public_address(ip: IPAddress) -> bool:
    if isinstance(ip, IPv6Address):
        inner = _embedded_ipv4(ip)
        if inner is not None:
            return is_public_address(inner)
    return ip.is_global and not ip.is_multicast


class UrlValidator:
    def __init__(self, resolver: Resolver, allowlist: Iterable[str] = ()) -> None:
        self._resolver = resolver
        self._allowed_hosts: set[str] = set()
        self._allowed_networks: list[IPv4Network | IPv6Network] = []
        for entry in allowlist:
            try:
                self._allowed_networks.append(ip_network(entry, strict=False))
            except ValueError:
                self._allowed_hosts.add(entry.lower().rstrip("."))

    async def validate(self, url: str) -> None:
        try:
            parts = urlsplit(url)
            _ = parts.port  # ValueError on bad port
        except ValueError as exc:
            raise UnsafeUrlError("malformed URL") from exc
        if parts.scheme not in ALLOWED_SCHEMES:
            raise UnsafeUrlError("only http and https URLs are allowed")
        if parts.username is not None or parts.password is not None:
            raise UnsafeUrlError("credentials in URL are not allowed")
        host = (parts.hostname or "").rstrip(".")
        if not host:
            raise UnsafeUrlError("URL has no host")
        if host in self._allowed_hosts:
            return

        addresses = await self._addresses(host)
        if not addresses:
            raise UnsafeUrlError("host did not resolve to any address")
        # check every address, one private A record is enough to reject
        for address in addresses:
            if not self._is_allowed(address):
                raise UnsafeUrlError("host resolves to a non-public address")

    async def _addresses(self, host: str) -> list[IPAddress]:
        try:
            return [ip_address(host)]
        except ValueError:
            pass
        try:
            return await self._resolver.resolve(host)
        except ResolutionError as exc:
            raise UnsafeUrlError("host could not be resolved") from exc

    def _is_allowed(self, ip: IPAddress) -> bool:
        if any(ip in net for net in self._allowed_networks):
            return True
        return is_public_address(ip)
