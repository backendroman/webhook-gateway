import asyncio
import signal
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI

from webhook_gateway.api import main as api_main
from webhook_gateway.config import Settings
from webhook_gateway.worker import main as worker_main


@pytest.fixture(autouse=True)
def _no_logging_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    # keep global logging config untouched
    monkeypatch.setattr(api_main, "configure_logging", lambda *a, **kw: None)
    monkeypatch.setattr(worker_main, "configure_logging", lambda *a, **kw: None)


def test_api_main_runs_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append((app, kw)))

    api_main.main()

    [(app, kw)] = calls
    assert isinstance(app, FastAPI)
    assert kw["port"] == 8000
    assert kw["host"] == "0.0.0.0"  # noqa: S104


def test_worker_main_passes_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    got: list[Settings] = []

    async def fake_serve(settings: Settings) -> None:
        got.append(settings)

    monkeypatch.setattr(worker_main, "serve", fake_serve)
    monkeypatch.setenv("WG_WORKER_CONCURRENCY", "3")
    worker_main.main()
    assert got[0].worker_concurrency == 3


def test_worker_main_ctrl_c_is_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_serve(_: Settings) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(worker_main, "serve", fake_serve)
    worker_main.main()  # no traceback


async def test_stop_handlers_via_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.get_running_loop()
    handlers: dict[signal.Signals, Any] = {}

    def add_signal_handler(sig: signal.Signals, cb: Any, *args: Any) -> None:
        handlers[sig] = lambda: cb(*args)

    monkeypatch.setattr(loop, "add_signal_handler", add_signal_handler)
    stop = asyncio.Event()
    worker_main.install_stop_handlers(stop)

    assert {signal.SIGTERM, signal.SIGINT} <= set(handlers)
    handlers[signal.SIGTERM]()
    assert stop.is_set()
    handlers[signal.SIGINT]()  # second signal is ignored
    assert stop.is_set()


async def test_stop_handlers_fallback_to_signal_module(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.get_running_loop()

    def not_supported(*_: Any) -> None:
        raise NotImplementedError

    installed: dict[int, Any] = {}
    monkeypatch.setattr(loop, "add_signal_handler", not_supported)
    monkeypatch.setattr(signal, "signal", installed.__setitem__)
    stop = asyncio.Event()
    worker_main.install_stop_handlers(stop)

    installed[signal.SIGINT](signal.SIGINT, None)
    await asyncio.sleep(0)  # handler goes through call_soon_threadsafe
    assert stop.is_set()
