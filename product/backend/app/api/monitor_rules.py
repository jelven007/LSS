"""监控规则 API 路由 — HTTP 请求 → 调用 monitor_rules 模块 → 同步引擎内存态。

只做胶水: 校验 → 持久化 → 失效引擎内存态。不含评估逻辑。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.multitenancy.documents import private_storage
from app.strategy import monitor_rules
from app.strategy.intraday_signals import INTRADAY_SIGNAL_LABELS, uses_intraday_signals

router = APIRouter(prefix="/api/monitor-rules", tags=["monitor-rules"])


def _data_dir(request: Request) -> Path:
    return private_storage(request) or request.app.state.repo.store.data_dir


def _groups(request: Request):
    from app.api.watchlist import _service

    return _service(request).list_groups()


def _monitor_engine(request: Request):
    if private_storage(request) is not None:
        from app.strategy.monitor import MonitorRuleEngine
        engine = MonitorRuleEngine()
        engine.set_rules(monitor_rules.load_all(_data_dir(request)))
        return engine
    return getattr(request.app.state, "monitor_engine", None)


def _reconcile_index_asset_type(rule: dict, repo) -> dict:
    """纠正误存为 stock 的指数/ETF 规则 (asset_type → index/etf)。

    个股弹窗加监控 / 点位提醒等入口未传 asset_type, 指数/ETF symbol 的规则被存成
    stock, 导致监控中心显示「个股」、引擎在股票轮评估 (指数/ETF symbol 永不命中)。
    仅当规则全部 symbols 都 resolve 为同一非 stock 类型时纠正 (混合池不动)。
    """
    if rule.get("asset_type", "stock") != "stock" or rule.get("scope") != "symbols":
        return rule
    symbols = [s for s in rule.get("symbols", []) if s]
    if not symbols:
        return rule
    try:
        types = {repo.resolve_asset_type(s) for s in symbols}
    except Exception:
        return rule
    if len(types) == 1:
        only = next(iter(types))
        if only in ("index", "etf"):
            rule["asset_type"] = only
    return rule


def _sync_engine(request: Request) -> None:
    """保存/删除后,把最新规则集 reload 到引擎内存态。"""
    if private_storage(request) is not None:
        return
    engine = getattr(request.app.state, "monitor_engine", None)
    if engine is not None:
        repo = request.app.state.repo
        rules = [
            _reconcile_index_asset_type(r, repo)
            for r in monitor_rules.load_all(_data_dir(request))
        ]
        engine.set_rules(rules)


# ── Pydantic 模型 ───────────────────────────────────────
class ConditionModel(BaseModel):
    field: str
    op: str            # truth | > >= < <= == !=
    value: float | None = None   # op 非 truth 时必填


class SectorTargetModel(BaseModel):
    key: str
    kind: str
    name: str
    symbol: str | None = None
    source_id: str | None = None
    field: str | None = None
    source_field: str | None = None
    value: str | None = None
    level: int | None = None
    available: bool = True
    member_count: int = 0


class RuleModel(BaseModel):
    id: str
    name: str
    enabled: bool = True
    type: str          # strategy | signal | price | market | sector | abnormal
    asset_type: str = "stock"   # stock | etf (etf: strategy 型走 ETF 历史加载器)
    scope: str = "symbols"   # symbols | all | sector | watchlist_group
    symbols: list[str] = []
    # watchlist_group 作用域: 绑定的自选分组 id (成员动态解析, 增删自选自动生效)
    group_id: str | None = None
    sector: str | None = None
    sector_kind: str | None = None  # index | concept | industry
    sector_targets: list[SectorTargetModel] = []
    sector_trigger: str = "change_pct"  # change_pct | momentum
    threshold_pct: float = 1.0
    window_minutes: int = 5
    strategy_id: str | None = None
    direction: str = "entry"  # entry | exit | both | (sector/ladder/abnormal: up|down|both)
    notify_events: list[str] | None = None
    score_min: float | None = None
    score_max: float | None = None
    conditions: list[ConditionModel] = []
    logic: str = "and"        # and | or
    cooldown_seconds: int = 3600
    # date 类型 (日期提醒): 纯日历窗口, 无 conditions
    remind_date: str | None = None   # YYYY-MM-DD
    lead_days: int = 0               # 提前 N 天进入提醒窗口
    severity: str = "info"    # info | warn | critical
    webhook_url: str = ""     # Webhook 推送地址 (推送到 QMT 等外部软件, 待定)
    webhook_enabled: bool = False  # 兼容老规则 (已由 webhook_channels 取代, 仅做向后兼容读)
    webhook_channels: list[str] = []  # 合法值: feishu | wecom | custom | email
    message: str = ""
    # abnormal 专属 (异动边缘监控): any | 3d | 10d | 30d
    abnormal_window: str = "any"
    # ladder 专属 (连板梯队封单监控)
    metric: str = "sealed_vol"   # sealed_vol=封单量(手) | sealed_amount=封单额(元)
    threshold: float = 0         # 封单 <= 此值时报警 (原始单位: 量=手, 额=元)
    # volume_delta 专属 (轮询放量监控): 相邻两次全市场快照的成交量增量
    threshold_volume: float = 9000   # 单轮增量 >= 此值(手)时报警
    threshold_amount: float = 1e6    # metric=amount 时: 单轮增量 >= 此值(元)时报警
    # 基础过滤 (与策略 basic_filter 语义对齐): 值为 null 表示不过滤
    basic_filter: dict = {}


# ── 字段选项 ─────────────────────────────────────────────
@router.get("/options")
def get_options(request: Request):
    """返回可选字段、信号列、运算符、枚举,供前端表单使用。"""
    from app.indicators.pipeline import ENRICHED_COLUMNS
    from app.services.kline_sync import intraday_monitor_support
    from app.strategy.custom_signals import ALLOWED_FIELDS, load_all as load_csg

    # 阈值字段 (带中文标签)
    threshold_fields = [
        {"key": f, "label": ENRICHED_COLUMNS.get(f, f)}
        for f in sorted(ALLOWED_FIELDS)
    ]
    # 内置信号列 (布尔, 用于 op=truth)
    builtin_signals = [
        {"key": k, "label": v}
        for k, v in ENRICHED_COLUMNS.items()
        if k.startswith("signal_")
    ]
    builtin_signals.extend(
        {"key": key, "label": label}
        for key, label in INTRADAY_SIGNAL_LABELS.items()
    )
    # 自定义信号列 (csg_)
    custom_sigs = []
    try:
        for cs in load_csg(_data_dir(request)):
            if cs.get("enabled") is not False:
                custom_sigs.append({
                    "key": f"csg_{cs['id']}",
                    "label": cs.get("name", cs["id"]),
                })
    except Exception:
        pass

    sector_service = getattr(request.app.state, "sector_monitor_service", None)
    sector_targets = sector_service.list_targets() if sector_service is not None else {
        "index": [], "concept": [], "industry": [],
    }
    return {
        "threshold_fields": threshold_fields,
        "builtin_signals": builtin_signals,
        "custom_signals": custom_sigs,
        "operators": [">", ">=", "<", "<=", "==", "!="],
        "types": [
            {"key": "signal", "label": "信号"},
            {"key": "price", "label": "价格/涨跌"},
            {"key": "market", "label": "市场异动"},
            {"key": "strategy", "label": "策略监控"},
            {"key": "abnormal", "label": "异动监控"},
            {"key": "sector", "label": "板块监控"},
            {"key": "volume_delta", "label": "轮询放量"},
            {"key": "date", "label": "日期提醒"},
        ],
        "scopes": [
            {"key": "symbols", "label": "指定标的"},
            {"key": "watchlist_group", "label": "自选分组"},
            {"key": "all", "label": "全市场"},
            {"key": "sector", "label": "板块"},
        ],
        "logics": [
            {"key": "and", "label": "全部满足 (AND)"},
            {"key": "or", "label": "任一满足 (OR)"},
        ],
        "severities": [
            {"key": "info", "label": "普通"},
            {"key": "warn", "label": "警告"},
            {"key": "critical", "label": "重要"},
        ],
        "directions": [
            {"key": "entry", "label": "入场"},
            {"key": "exit", "label": "出场"},
            {"key": "both", "label": "出入都报"},
        ],
        "intraday_signal_support": intraday_monitor_support(
            getattr(request.app.state, "capabilities", None),
        ),
        "sector_targets": sector_targets,
    }


# ── 列表 ───────────────────────────────────────────────
@router.get("")
def list_rules(request: Request):
    repo = request.app.state.repo
    rules = [
        _reconcile_index_asset_type(r, repo)
        for r in monitor_rules.load_all(_data_dir(request))
    ]
    from app.services.kline_sync import intraday_monitor_support

    support = intraday_monitor_support(getattr(request.app.state, "capabilities", None))
    intraday_rules = [
        rule for rule in rules
        if rule.get("enabled", True) and uses_intraday_signals(rule)
    ]
    pooled_symbols = {
        str(symbol)
        for rule in intraday_rules
        for symbol in rule.get("symbols", [])
        if symbol
    }
    runtime_warning = ""
    if intraday_rules and not support["available"]:
        runtime_warning = str(support["reason"])
    elif len(pooled_symbols) > int(support["max_symbols"]):
        runtime_warning = (
            f"分时监听标的池已超限: {len(pooled_symbols)}/{support['max_symbols']}"
        )
    if runtime_warning:
        for rule in intraday_rules:
            rule["runtime_warning"] = runtime_warning
    sector_service = getattr(request.app.state, "sector_monitor_service", None)
    if sector_service is not None:
        for rule in rules:
            if rule.get("type") != "sector":
                continue
            missing = sector_service.missing_target_keys(rule.get("sector_targets", []))
            unavailable = sector_service.unavailable_target_keys(rule.get("sector_targets", []))
            if missing:
                rule["runtime_warning"] = "部分板块数据已不存在, 请重新选择监控对象"
            elif unavailable:
                rule["runtime_warning"] = "所选指数未加入实时指数池, 请先在实时监控设置中启用"
    # 分组作用域规则: 绑定的分组被删除 → 标注运行时警告 (引擎侧已 fail-closed 跳过)
    group_rules = [rule for rule in rules if rule.get("scope") == "watchlist_group"]
    if group_rules:
        try:
            existing_ids = {g["id"] for g in _groups(request)}
            for rule in group_rules:
                if rule.get("group_id") not in existing_ids:
                    rule["runtime_warning"] = "绑定的自选分组已删除, 规则已暂停监控, 编辑可重新选择"
        except Exception:  # noqa: BLE001
            pass
    # 按 created_at 倒序
    rules.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return {"rules": rules}


# ── 新建 / 更新 ────────────────────────────────────────
@router.post("")
def save_rule(req: RuleModel, request: Request):
    rule = monitor_rules.normalize(req.model_dump())
    rule = _reconcile_index_asset_type(rule, request.app.state.repo)
    # 连板梯队封单监控 (type=ladder) 依赖五档盘口数据, 需 Pro+ (DEPTH5_BATCH 能力)。
    # 无能力时拒绝创建, 避免规则存了却永远无法触发。
    if rule.get("type") == "ladder":
        from app.tickflow.capabilities import Cap
        capset = getattr(request.app.state, "capabilities", None)
        if capset is None or not capset.has(Cap.DEPTH5_BATCH):
            raise HTTPException(
                status_code=403,
                detail="封单监控需要 Pro+ 套餐 (批量五档能力),请升级后在「设置」页配置",
            )
    if rule.get("type") == "strategy":
        from app.api.strategy import _get_engine
        from app.strategy.engine import StrategyDataContext

        strategy_engine = _get_engine(request)
        if strategy_engine is None:
            raise HTTPException(status_code=503, detail="策略引擎未初始化")
        try:
            strategy = strategy_engine.get(str(rule.get("strategy_id")))
            strategy_engine.validate_context(
                strategy,
                StrategyDataContext(
                    asset_type=str(rule.get("asset_type") or "stock"),
                    timeframe="1d",
                    as_of=date.today(),
                ),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    # 编辑现有规则时, 保留原 created_at (避免按时间排序时位置跳动)
    existing = monitor_rules.load_one(_data_dir(request), rule["id"])
    # 批次派生规则由「持仓提醒」页托管, 监控中心只读 (启停/改/删均回持仓页)
    if existing and existing.get("lot_id"):
        raise HTTPException(status_code=409, detail="该规则由「持仓提醒」页托管, 请在持仓提醒页修改")
    if existing and existing.get("created_at"):
        rule["created_at"] = existing["created_at"]
    try:
        monitor_rules.validate(rule)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if rule.get("scope") == "watchlist_group":
        # 绑定的分组必须存在 (strategy 层校验形状, 存在性在本层校验)
        group_id = str(rule.get("group_id") or "")
        try:
            group_ids = {g["id"] for g in _groups(request)}
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"自选分组读取失败: {e}") from e
        if group_id not in group_ids:
            raise HTTPException(status_code=400, detail="自选分组不存在或已被删除, 请重新选择")
    if rule.get("type") == "sector":
        sector_service = getattr(request.app.state, "sector_monitor_service", None)
        if sector_service is None:
            raise HTTPException(status_code=503, detail="板块监控服务未初始化")
        targets = rule.get("sector_targets", [])
        if sector_service.missing_target_keys(targets):
            raise HTTPException(status_code=400, detail="所选板块数据已变化, 请重新选择")
        if sector_service.unavailable_target_keys(targets):
            raise HTTPException(status_code=400, detail="所选指数未加入实时指数池, 请先在实时监控设置中启用")
    if rule.get("enabled", True) and uses_intraday_signals(rule):
        from app.services.kline_sync import intraday_monitor_support

        support = intraday_monitor_support(getattr(request.app.state, "capabilities", None))
        if not support["available"]:
            raise HTTPException(status_code=403, detail=str(support["reason"]))
        symbols = set(str(symbol) for symbol in rule.get("symbols", []) if symbol)
        for saved in monitor_rules.load_all(_data_dir(request)):
            if (
                saved.get("id") != rule.get("id")
                and saved.get("enabled", True)
                and uses_intraday_signals(saved)
            ):
                symbols.update(str(symbol) for symbol in saved.get("symbols", []) if symbol)
        max_symbols = int(support["max_symbols"])
        if len(symbols) > max_symbols:
            raise HTTPException(
                status_code=400,
                detail=f"当前分时数据能力最多监听 {max_symbols} 只标的,当前规则合计 {len(symbols)} 只",
            )
    monitor_rules.save_one(_data_dir(request), rule)
    _sync_engine(request)
    return {"ok": True, "rule": rule}


# ── 删除 ───────────────────────────────────────────────
@router.delete("/{rule_id}")
def delete_rule(rule_id: str, request: Request):
    if not monitor_rules.ID_RE.match(rule_id):
        raise HTTPException(status_code=400, detail="规则 id 非法")
    # 批次派生规则由「持仓提醒」页托管, 删除需在持仓页操作 (级联清理派生规则)
    existing = monitor_rules.load_one(_data_dir(request), rule_id)
    if existing and existing.get("lot_id"):
        raise HTTPException(status_code=409, detail="该规则由「持仓提醒」页托管, 请在持仓提醒页删除批次")
    deleted = monitor_rules.delete_one(_data_dir(request), rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="规则不存在")
    _sync_engine(request)
    return {"ok": True}
