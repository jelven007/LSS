"""告警触发记录 API — 查询与删除真实业务告警。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

from app.api import contracts
from app.multitenancy.documents import private_storage
from app.services import alert_store

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _data_dir(request: Request) -> Path:
    return private_storage(request) or request.app.state.repo.store.data_dir


@router.get("", response_model=contracts.AlertsResponse)
def list_alerts(
    request: Request,
    # 上限取存储侧保留策略 (alert_store.MAX_DAYS / MAX_RECORDS): 超出也没有可返回的记录。
    # 无下限时 days=-1 会把 cutoff 推到未来直接返回空, limit=-1 会走负数切片静默丢掉最旧一条。
    days: int = Query(alert_store.MAX_DAYS, ge=1, le=alert_store.MAX_DAYS),
    limit: int = Query(alert_store.MAX_RECORDS, ge=1, le=alert_store.MAX_RECORDS),
    source: str | None = None,
    type: str | None = None,
    ext_columns: str | None = None,
):
    """查询触发记录 (时间倒序)。

    ext_columns: 逗号分隔的 "configId.fieldName", 传入后按 symbol 富化行业/概念等 ext 字段,
    每条记录附带 {configId}__{fieldName} 键 (与 watchlist/screener 一致)。
    """
    events = alert_store.list_recent(
        _data_dir(request), days=days, limit=limit, source=source, type=type,
    )
    if ext_columns and events:
        try:
            from app.api.screener import _load_ext_value_maps, _rows_with_ext
            repo = request.app.state.repo
            value_maps = _load_ext_value_maps(repo, ext_columns)
            if value_maps:
                events = _rows_with_ext(events, value_maps)
        except Exception:  # noqa: BLE001
            pass
    total = alert_store.count(_data_dir(request))
    return {"alerts": events, "total": total}


@router.delete("")
def clear_alerts(request: Request):
    """清空全部触发记录。"""
    n = alert_store.clear(_data_dir(request))
    return {"ok": True, "cleared": n}


@router.delete("/{ts}")
def delete_alert(ts: int, request: Request):
    """删除单条触发记录 (按 ts 毫秒时间戳)。"""
    deleted = alert_store.delete_one(_data_dir(request), ts)
    if not deleted:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"ok": True}
