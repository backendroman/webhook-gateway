"""Real uvicorn on 127.0.0.1 pretending to be a subscriber"""

import asyncio
import contextlib
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import uvicorn
from starlette.types import Receive, Scope, Send


@dataclass(frozen=True, slots=True)
class Received:
    headers: dict[str, str]
    body: bytes


@dataclass
class Behavior:
    status: int = 200
    fail_status: int = 500
    fail_times: int = 0
    hang: bool = False
    retry_after: str | None = None
    response_body: bytes = b"ok"


@dataclass
class FakeReceiver:
    url: str = ""
    behavior: Behavior = field(default_factory=Behavior)
    requests: list[Received] = field(default_factory=list)
    _release: asyncio.Event = field(default_factory=asyncio.Event)

    def always(self, status: int, body: bytes = b"ok") -> None:
        self.behavior = Behavior(status=status, response_body=body)

    def fail_then(self, fail_status: int, times: int, then: int = 200) -> None:
        self.behavior = Behavior(status=then, fail_status=fail_status, fail_times=times)

    def hang(self) -> None:
        self.behavior = Behavior(hang=True)

    def retry_after(self, status: int, value: str) -> None:
        self.behavior = Behavior(status=status, retry_after=value)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        self.requests.append(Received(headers=headers, body=body))

        b = self.behavior
        if b.hang:
            await self._release.wait()
            return
        status = b.status
        if b.fail_times > 0:
            b.fail_times -= 1
            status = b.fail_status
        out_headers = [(b"content-type", b"text/plain")]
        if b.retry_after is not None:
            out_headers.append((b"retry-after", b.retry_after.encode()))
        await send({"type": "http.response.start", "status": status, "headers": out_headers})
        await send({"type": "http.response.body", "body": b.response_body})


@asynccontextmanager
async def run_receiver() -> AsyncIterator[FakeReceiver]:
    receiver = FakeReceiver()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(receiver, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    # don't steal ctrl+c from pytest
    server.capture_signals = contextlib.nullcontext  # type: ignore[method-assign,assignment]
    sock.listen()  # listen before uvicorn starts, so there's no race in tests
    task = asyncio.create_task(server.serve(sockets=[sock]))
    receiver.url = f"http://127.0.0.1:{port}/hook"
    try:
        yield receiver
    finally:
        receiver._release.set()
        server.should_exit = True
        await task
        sock.close()
