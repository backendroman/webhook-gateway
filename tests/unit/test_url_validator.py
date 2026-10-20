from ipaddress import ip_address

import pytest

from tests.fakes.resolver import FakeResolver
from webhook_gateway.domain.errors import UnsafeUrlError
from webhook_gateway.services.url_validator import ResolutionError, SystemResolver, UrlValidator

DNS = {
    "public.example": ["93.184.216.34"],
    "dual.example": ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"],
    "rebind.example": ["93.184.216.34", "10.0.0.5"],
    "internal.example": ["192.168.1.10"],
    "localhost": ["127.0.0.1", "::1"],
    "empty.example": [],
}


@pytest.fixture
def validator() -> UrlValidator:
    return UrlValidator(FakeResolver(DNS))


@pytest.mark.parametrize(
    "url",
    [
        "https://public.example/hook",
        "http://public.example:8080/a?b=c",
        "https://dual.example/",
        "https://93.184.216.34/hook",
        "https://[2606:2800:220:1:248:1893:25c8:1946]/hook",
        "https://PUBLIC.example./hook",
    ],
)
async def test_accepts_public_urls(validator: UrlValidator, url: str) -> None:
    await validator.validate(url)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("ftp://public.example/", "scheme"),
        ("file:///etc/passwd", "scheme"),
        ("gopher://public.example/", "scheme"),
        ("public.example/hook", "scheme"),
        ("https:///nohost", "no host"),
        ("https://user:pw@public.example/", "credentials"),
        ("https://public.example:99999/", "malformed"),
        ("http://127.0.0.1/", "loopback v4"),
        ("http://127.1.2.3/", "loopback range"),
        ("http://[::1]/", "loopback v6"),
        ("http://localhost/", "localhost by DNS"),
        ("http://10.1.2.3/", "private 10/8"),
        ("http://172.16.0.1/", "private 172.16/12"),
        ("http://192.168.0.1/", "private 192.168/16"),
        ("http://169.254.169.254/latest/meta-data", "link-local / cloud metadata"),
        ("http://[fe80::1]/", "link-local v6"),
        ("http://[fc00::1]/", "unique-local v6"),
        ("http://100.64.0.1/", "CGNAT"),
        ("http://0.0.0.0/", "unspecified"),
        ("http://224.0.0.1/", "multicast"),
        ("http://[::ffff:127.0.0.1]/", "IPv4-mapped loopback"),
        ("http://[::ffff:10.0.0.1]/", "IPv4-mapped private"),
        ("http://[64:ff9b::a00:1]/", "NAT64 of 10.0.0.1"),
        ("http://[2002:a00:1::]/", "6to4 of 10.0.0.1"),
        ("http://internal.example/", "private by DNS"),
        ("http://rebind.example/", "mixed public+private records"),
        ("http://empty.example/", "no records"),
        ("http://nxdomain.example/", "unresolvable"),
    ],
)
async def test_rejects_unsafe_urls(validator: UrlValidator, url: str, reason: str) -> None:
    with pytest.raises(UnsafeUrlError):
        await validator.validate(url)


async def test_allowlist_by_hostname_skips_resolution() -> None:
    validator = UrlValidator(FakeResolver({}), allowlist=["localhost"])
    await validator.validate("http://localhost:8080/hook")


async def test_allowlist_by_cidr() -> None:
    validator = UrlValidator(FakeResolver(DNS), allowlist=["192.168.0.0/16", "127.0.0.1"])
    await validator.validate("http://internal.example/")
    await validator.validate("http://127.0.0.1:9000/")
    with pytest.raises(UnsafeUrlError):
        await validator.validate("http://10.1.2.3/")


async def test_dns_change_is_detected_on_revalidation() -> None:
    resolver = FakeResolver({"flip.example": ["93.184.216.34"]})
    validator = UrlValidator(resolver)
    await validator.validate("https://flip.example/")
    resolver.records["flip.example"] = ["127.0.0.1"]
    with pytest.raises(UnsafeUrlError):
        await validator.validate("https://flip.example/")


async def test_system_resolver_localhost() -> None:
    addrs = await SystemResolver().resolve("localhost")
    assert addrs
    assert all(a.is_loopback for a in addrs)


async def test_system_resolver_numeric() -> None:
    assert await SystemResolver().resolve("127.0.0.1") == [ip_address("127.0.0.1")]


async def test_system_resolver_bad_name() -> None:
    with pytest.raises(ResolutionError):
        await SystemResolver().resolve("x" * 300 + ".example")  # idna label too long
