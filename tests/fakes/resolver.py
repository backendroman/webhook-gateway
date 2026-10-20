from ipaddress import ip_address

from webhook_gateway.services.url_validator import IPAddress, ResolutionError


class FakeResolver:
    def __init__(self, records: dict[str, list[str]] | None = None) -> None:
        self.records: dict[str, list[str]] = dict(records or {})

    async def resolve(self, host: str) -> list[IPAddress]:
        if host not in self.records:
            raise ResolutionError(f"NXDOMAIN {host}")
        return [ip_address(a) for a in self.records[host]]
