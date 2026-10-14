# rustdx 全面替代 mootdx 可行性与实施分析

日期：2026-10-15。目标：用 [jelven007/rustdx](https://github.com/jelven007/rustdx) **完全取代** mootdx，作为沪深行情的唯一数据源。本文给出能力对齐、架构障碍、集成方案选型与分阶段实施计划。

## 结论先行

- rustdx **库层能力完整**，可覆盖当前系统对 mootdx 的全部调用（日 K、分钟线、实时报价、证券列表、板块、财务），能力上不是障碍。
- 唯一且根本的障碍是**语言桥**：rustdx 是纯 Rust 库（crate `jelven-rustdx-complete`，`[lib] rustdx_complete`），**无 Python 绑定（无 pyo3/maturin/cdylib）、无 HTTP/server 模式**；其 CLI 只有 `day`、`east`、`help` 三个子命令，不暴露分钟/实时/板块。
- 因此"全面替代"必须先给 rustdx 增加一个 Python 可调用入口，然后把本项目的数据访问层从 mootdx 切到该入口。推荐**在 rustdx 侧新增常驻 HTTP/JSON 服务（`rustdx serve`）**，Python 侧用一个保持现有接口的 `RustdxProvider` 作为 HTTP 客户端。
- 这是一个跨 Rust/Python 两仓库的中型工程，须分阶段灰度，并以**分钟线驱动的龙头模拟盘 T+1 结算**作为正确性验收红线。

## 当前对 mootdx 的依赖面（替换范围）

### 构造点：22 处 `MootdxProvider()`

分布在生产服务与研究脚本：

- 生产运行时：[report_worker.py](../src/banxia_strategy/application/report_worker.py)、[reference_sync.py](../src/banxia_strategy/application/reference_sync.py)、[limit_up_history_sync.py](../src/banxia_strategy/application/limit_up_history_sync.py)、[intraday.py](../src/banxia_strategy/intraday.py)、[service.py](../src/banxia_strategy/service.py)、[api.py](../src/banxia_strategy/api.py)、[cli.py](../src/banxia_strategy/cli.py)、[execution_analysis.py](../src/banxia_strategy/execution_analysis.py)。
- 批处理：[stock_history_backfill.py](../src/banxia_strategy/stock_history_backfill.py)、[board_capital_backfill.py](../src/banxia_strategy/board_capital_backfill.py)。
- 研究脚本：`scripts/` 下 5 个，加 [monthly_target_research.py](../src/banxia_strategy/monthly_target_research.py)、[research_data.py](../src/banxia_strategy/research_data.py)。

抽象良好：所有构造都走同一个类，且支持注入 `client_factory`，大多数调用方只依赖 `provider.<method>()`。这意味着**只要新建一个同接口的 `RustdxProvider`，切换面可收敛到构造点与一个配置开关**。

### Provider 对外的 17 个方法（必须等价实现）

`trading_dates`、`securities`、`security`、`stock_blocks`、`stock_block_symbols`、`reference_snapshot`、`quote_snapshots`、`company_profile`、`company_section`、`history_bars`、`kline_bars`、`historical_minutes`、`limit_up_history`、`limit_up_pool`、`broken_board_pool`、`strategy_pool`，以及属性 `source_name`。

### 底层打到 mootdx client 的 10 类调用

| mootdx 调用 | 用途 | rustdx 库对应 |
| --- | --- | --- |
| `client.index(symbol=000001, freq=9)` | 交易日历 | `trade_date_a` crate / `Client::index_bars` |
| `client.stock_count` + `client.client.get_security_list` | 全市场证券列表 | `Client::stocks` |
| `client.quotes(symbol=[...])` | 盘中实时五档报价 | `Client::quotes` |
| `client.minutes(symbol, date)` | 分钟线（240 根） | `Client::minute` / `history_minute` |
| `client.bars(symbol, frequency)` | 日/周/月 K | `Client::k` / `bars` / `k_adjusted` |
| `client.finance` / `client.xdxr` / `client.F10C` | 财务 / 除权除息 / F10 分类 | `Client::finance` / `xdxr` / `f10_categories` |
| `client.client.get_block_info_meta/get_block_info` | 板块文件（block\*.dat） | `Client::block("block_gn.dat")` |

**全部有对应**。rustdx 走的也是通达信 TCP 行情协议，并额外提供复权、技术指标、严格校验与缓存——能力只增不减。

## 根本障碍：语言与进程边界

- rustdx 没有 Python 包，`import rustdx` 不存在；本项目是 Python。
- rustdx CLI（`day`/`east`）只能导出**日线文件与东财收盘快照**，**无法**从命令行取分钟线或盘中实时报价。
- 当前系统对 mootdx 的依赖是**在线实时**的：
  - `quote_snapshots()`：盘中监控、盘中计划确认；
  - `historical_minutes()`：T+1 入场/退出的分钟级证明，**龙头模拟盘每一笔结算都依赖它**；
  - `reference_snapshot()`/板块：龙头题材第一顺位判定。

因此，"用 rustdx CLI 直接替换"不成立；必须打通 Rust→Python 的实时调用通道。

## 集成方案选型

### 方案一（推荐）：rustdx 新增 `serve` HTTP/JSON 服务 + Python `RustdxProvider` 作为 HTTP 客户端

- Rust 侧：在 `rustdx-cmd` 增加 `serve` 子命令，把 `Client::{quotes,minute,k,stocks,block,finance,xdxr}` 暴露为 JSON 端点，复用其连接池与缓存；常驻、支持并发与心跳。
- Python 侧：实现 `RustdxProvider`，方法签名与 `MootdxProvider` 完全一致，内部改为 HTTP 调用；用 `BANXIA_DATA_PROVIDER=rustdx|mootdx` 选择。
- 优点：进程隔离、与现有 K8s 部署模型一致（可作为一个 Deployment/Sidecar）、Python 侧改动集中、天然支持灰度与回退。
- 代价：需在 rustdx 增量开发 server 层并容器化；多一个服务进程。

### 方案二：pyo3/maturin 给 rustdx 做 Python 原生绑定

- Rust 侧加 `cdylib` + pyo3，maturin 构建 wheel，`import rustdx_complete` 直接用。
- 优点：无额外进程，调用延迟最低。
- 代价：改 rustdx crate 结构并维护 wheel 的多平台构建（本项目生产镜像为 linux/amd64）；Rust 对象到 Python 的数据编解码需逐方法封装；灰度/回退不如独立服务灵活。

### 方案三：仅用 CLI（`day`/`east`）+ 批处理

- 只能覆盖日线/收盘快照批处理，**无法覆盖分钟与实时**，不满足"全面替代"。仅列为对照，排除。

**选型建议：方案一**。它用最小的 Python 侧改动实现真正的全替换，契合现有微服务部署，且能以配置开关安全灰度。方案二可作为后续性能优化。

## 分阶段实施计划（方案一）

1. **接口冻结**：以现有 17 个方法 + 返回结构为契约，补"provider 契约测试"（用假数据断言字段），为双实现共用。
2. **rustdx serve（Rust 侧）**：实现 `quotes/minute/k/stocks/block/finance/xdxr` + 健康检查的 JSON 端点；沿用连接池、缓存、复权与严格校验；输出字段对齐当前 Python 所需键名。
3. **容器化与部署**：rustdx serve 打镜像，作为 `banxia` 命名空间内一个 Deployment（内部 Service）；配置超时、并发、心跳、资源限额与就绪探针。
4. **RustdxProvider（Python 侧）**：同接口 HTTP 客户端，做字段映射、分钟线 240 根规整、交易日历构造（含下一交易日推导）、板块/题材装配，保证与 mootdx 版行为一致。
5. **灰度切换**：引入 `BANXIA_DATA_PROVIDER`，先切只读/批处理链路（历史回填、涨停历史、参考同步），再切报告与盘中监控，最后切模拟盘结算。每步可独立回退到 mootdx。
6. **正确性红线验收**：
   - 分钟线：对同一交易日同一标的，rustdx 与 mootdx 的 240 根收盘序列逐根一致（容差内）；
   - 龙头模拟盘：T+1 入场/退出结算结果与基线一致；
   - 交易日历、证券列表、板块题材第一顺位判定一致。
7. **收尾**：全部链路稳定后，移除 `mootdx` 依赖（`pyproject.toml`）、删除/归档 `MootdxProvider`，更新全部文档与部署清单；保留一个发布周期的回退能力。

## 风险与约束

- **实时性与稳定性**：盘中 `quotes` 是强实时路径，serve 的连接池、超时与心跳必须经压测；上线初期保留 mootdx 回退开关。
- **字段语义对齐**：复权口径、`vol/volume` 单位、板块文件版本（`block_gn.dat` 等）需逐一核对，避免题材归属漂移。
- **时点历史限制不变**：概念归属与流通股本仍可能来自较新快照，换源不会凭空获得无前视历史；回测口径的既有限制继续适用。
- **双仓库协同**：rustdx 为独立仓库（作者张路 <jelven@126.com>），serve 层的开发/发布需与本项目版本对齐，建立联合发布清单。
- **T+1 红线不可破**：切换过程中严禁出现分钟线缺失即"当日成交"的假设；缺数据必须按可重试处理，维持现有保守语义。

## 落地切换点清单（Python 侧）

- 统一入口：新增 `make_provider()` 工厂，依据 `BANXIA_DATA_PROVIDER` 返回 `RustdxProvider` 或 `MootdxProvider`，替换 22 处直接构造。
- 运行时注入：`report_worker`、`reference_sync`、`limit_up_history_sync`、`intraday`、`service`、`api` 的 `provider or ...` 默认值改为工厂。
- 批处理与脚本：`stock_history_backfill`、`board_capital_backfill` 及 `scripts/*` 显式走工厂，便于单独灰度。
- 配置与部署：`platform.yaml` 增加 `BANXIA_DATA_PROVIDER` 与 rustdx serve 的地址/超时；新增 rustdx serve 的 Deployment/Service。
