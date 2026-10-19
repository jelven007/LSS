"""Desktop runtime regression tests."""

from __future__ import annotations

import importlib.metadata
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import desktop

ROOT = Path(__file__).resolve().parents[2]


class _FakeLoadedEvent:
    def __init__(self) -> None:
        self.callback = None

    def __iadd__(self, callback):
        self.callback = callback
        return self


class _FakeWindow:
    def __init__(self) -> None:
        self.events = SimpleNamespace(loaded=_FakeLoadedEvent())
        self.destroyed = False

    def evaluate_js(self, _script: str) -> dict:
        return {
            "readyState": "complete",
            "location": "http://127.0.0.1:3018/",
            "title": "LSS",
            "bodyTextLength": 42,
            "htmlLength": 512,
        }

    def destroy(self) -> None:
        self.destroyed = True


def test_smoke_plugin_validation_accepts_default_rustdx_provider() -> None:
    desktop._validate_smoke_plugins(
        [
            {
                "name": name,
                "available": True,
                "datasets": sorted(desktop._RUSTDX_DATASETS),
            } for name in ("rustdx",)
        ]
    )


def test_desktop_smoke_requires_authentication_bypass() -> None:
    source = (ROOT / "backend" / "app" / "desktop.py").read_text(encoding="utf-8")

    assert 'f"{base_url}/api/auth/status"' in source
    assert 'auth_status.get("auth_required") is not False' in source
    assert 'f"{base_url}/api/capabilities"' in source
    assert 'f"{base_url}/api/auth/login"' in source
    assert 'logger.info("DESKTOP_AUTH_BYPASS_SMOKE_TEST_OK")' in source


def test_find_free_port_skips_an_active_listener() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        occupied = listener.getsockname()[1]

        assert desktop._find_free_port(occupied, count=20) != occupied


@pytest.mark.parametrize(
    "plugin",
    [
        None,
        {"name": "rustdx", "available": False, "status": "missing"},
        {"name": "rustdx", "available": True, "datasets": ["daily"]},
    ],
)
def test_smoke_plugin_validation_rejects_incomplete_rustdx(plugin: dict | None) -> None:
    with pytest.raises(RuntimeError, match="rustdx"):
        desktop._validate_smoke_plugins(
            [] if plugin is None else [plugin]
        )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (importlib.metadata.PackageNotFoundError(), "metadata-missing"),
        (RuntimeError("broken metadata"), "metadata-error:RuntimeError"),
    ],
)
def test_distribution_metadata_errors_are_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected: str,
) -> None:
    def _missing(_name: str) -> str:
        raise error

    monkeypatch.setattr(importlib.metadata, "version", _missing)

    assert desktop._safe_distribution_version("pywebview") == expected


def test_gui_smoke_opens_window_and_validates_edgechromium(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_window = _FakeWindow()
    fake_webview = SimpleNamespace(renderer=None)

    def _create_window(*_args, **_kwargs):
        return fake_window

    def _start(**_kwargs) -> None:
        fake_webview.renderer = "edgechromium"
        assert fake_window.events.loaded.callback is not None
        fake_window.events.loaded.callback()

    def _missing_metadata(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError

    fake_webview.create_window = _create_window
    fake_webview.start = _start
    monkeypatch.setattr(importlib.metadata, "version", _missing_metadata)
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.setenv(desktop._GUI_SMOKE_TEST_ENV, "1")

    with caplog.at_level("INFO"):
        desktop._open_window("http://127.0.0.1:3018")

    assert fake_window.destroyed
    assert "DESKTOP_GUI_SMOKE_TEST_OK renderer=edgechromium" in caplog.text


@pytest.mark.parametrize(
    ("renderer", "page", "message"),
    [
        ("mshtml", {}, "renderer"),
        (
            "edgechromium",
            {
                "readyState": "complete",
                "location": "http://127.0.0.1:3018",
                "bodyTextLength": 0,
                "htmlLength": 100,
            },
            "body text",
        ),
    ],
)
def test_gui_smoke_rejects_invalid_renderer_or_blank_page(
    renderer: str,
    page: dict,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        desktop._validate_gui_smoke_page(renderer, page)


def test_gui_smoke_waits_for_react_to_render_after_loaded_event() -> None:
    blank_page = {
        "readyState": "complete",
        "location": "http://127.0.0.1:3018",
        "bodyTextLength": 0,
        "htmlLength": 100,
    }
    rendered_page = {
        **blank_page,
        "bodyTextLength": 42,
        "htmlLength": 512,
    }
    probes = iter([rendered_page])

    page = desktop._wait_for_gui_smoke_page(
        "edgechromium",
        lambda: next(probes),
        initial_page=blank_page,
        timeout=0.1,
        poll_interval=0,
    )

    assert page == rendered_page


def test_gui_smoke_rejects_a_page_that_remains_blank() -> None:
    blank_page = {
        "readyState": "complete",
        "location": "http://127.0.0.1:3018",
        "bodyTextLength": 0,
        "htmlLength": 100,
    }

    with pytest.raises(RuntimeError, match="did not render"):
        desktop._wait_for_gui_smoke_page(
            "edgechromium",
            lambda: blank_page,
            initial_page=blank_page,
            timeout=0,
            poll_interval=0,
        )
