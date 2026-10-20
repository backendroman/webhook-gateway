import asyncio
from collections import Counter

from webhook_gateway.domain.enums import AttemptOutcome
from webhook_gateway.worker.sender import SendResult


class CountingSender:
    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()

    async def send(self, url: str, headers: dict[str, str], body: bytes) -> SendResult:
        self.calls[headers["X-Webhook-Id"]] += 1
        await asyncio.sleep(0)  # give other workers a chance to race
        return SendResult(AttemptOutcome.SUCCESS, status_code=200, response_body="ok")


class GatedSender:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.in_progress = 0
        self.completed = 0

    async def send(self, url: str, headers: dict[str, str], body: bytes) -> SendResult:
        self.in_progress += 1
        self.started.set()
        await self.release.wait()
        self.in_progress -= 1
        self.completed += 1
        return SendResult(AttemptOutcome.SUCCESS, status_code=200, response_body="ok")
