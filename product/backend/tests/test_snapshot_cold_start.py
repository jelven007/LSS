"""Cold snapshot reads must not wait for the initial-sync compute reservation."""
import threading
from types import SimpleNamespace

import polars as pl

from app.services.heavy_job_limiter import shared_heavy_job_limiter
from app.tickflow.repository import KlineRepository


def cold_repo():
    repo = object.__new__(KlineRepository)
    repo._enriched_cache = None
    repo._enriched_cache_date = None
    repo._enriched_warming = False
    repo._etf_enriched_cache = None
    repo._etf_enriched_cache_date = None
    repo._index_enriched_cache = None
    repo._index_enriched_cache_date = None
    return repo


def test_stock_cache_read_returns_during_exclusive_sync():
    repo = cold_repo()
    repo._refresh_enriched_impl = lambda: "empty"
    done = threading.Event()
    result = []

    def read():
        result.append(repo.get_enriched_latest_asset("stock", refresh=False))
        done.set()

    with shared_heavy_job_limiter.slot("exclusive"):
        reader = threading.Thread(target=read)
        reader.start()
        completed = done.wait(1)
    reader.join(5)
    assert completed, "snapshot read waited for initial-sync capacity"
    assert result[0][0].is_empty() and result[0][1] is None


def test_default_stock_read_keeps_lazy_refresh_and_cached_data():
    repo = cold_repo()
    frame = pl.DataFrame({"symbol": ["600000.SH"]})

    def refresh():
        repo._enriched_cache = frame

    repo._refresh_enriched = refresh
    assert repo.get_enriched_latest_asset("stock")[0] is frame
    assert repo.get_enriched_latest_asset("stock", refresh=False)[0] is frame


def test_cold_full_snapshot_does_not_recompute_sector_or_abnormal(monkeypatch, tmp_path):
    from app.cell.platform_market import full_snapshot

    repo = cold_repo()

    def forbidden(*args, **kwargs):
        raise AssertionError("cold public read attempted computation")

    repo._refresh_enriched = forbidden
    repo.get_name_map = lambda: {}
    repo.store = SimpleNamespace(data_dir=tmp_path)
    service = SimpleNamespace(
        status=lambda: {"is_trading_hours": True},
        get_index_quotes=lambda: pl.DataFrame(),
    )
    sector = SimpleNamespace(list_targets=forbidden, build_snapshots=forbidden)
    state = SimpleNamespace(repo=repo, quote_service=service, sector_monitor_service=sector)
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    monkeypatch.setenv("TSP_PLATFORM_MARKET_TOKEN", "test-snapshot-token")
    snapshot = full_snapshot(request, "Bearer test-snapshot-token")
    assert snapshot["sectors"] == {} and snapshot["abnormal"] == []
    assert snapshot["days"] == {"stock": None, "etf": None, "index": None}
