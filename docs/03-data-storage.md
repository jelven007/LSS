# 数据模型与多模存储设计

## 1. 设计目标

- 每类数据选择与访问模式匹配的存储。
- 权威数据可恢复、可追溯、可重放。
- Redis 和进程内存均不作为事实来源。
- 高频行情与事务状态隔离。
- 所有数据集都有明确的幂等键、保留周期和恢复路径。

## 2. v0.5.1 存储职责

| 数据类型 | 权威存储 | 辅助存储 | 说明 |
| --- | --- | --- | --- |
| 未完成处理的采集事件 | SQLite WAL / EBS | 无 | 每个 Market Worker 独立，权威写入成功后确认 |
| 原始盘口、一分钟线、实时特征 | ClickHouse | Redis、TOS | 高频写入、时间范围查询、长期归档 |
| 证券目录、交易日历、板块关系 | PostgreSQL | TOS | 时点版本与原始文件归档 |
| 公司财务、除权除息、F10 | PostgreSQL / TOS | ClickHouse | 按数据形态版本化 |
| Policy 策略、计划、决策、任务、审计 | PostgreSQL `banxia` | TOS | 事务权威数据 |
| TSP 身份、租户和路由 | PostgreSQL `tsp_control` | 无 | 独立运行角色 |
| TSP 租户业务数据 | PostgreSQL `tsp_data_production` | TOS | RLS 与独立运行角色 |
| 最新行情、特征、决策、SSE 投影 | Redis | PostgreSQL、ClickHouse | 可重建 |
| 报告、研究资产、原始归档 | 火山引擎 TOS | PostgreSQL 元数据 | 内容寻址与哈希校验 |
| TSP 公共行情版本、租户产物 | NAS RWX | 发布 Manifest | Compute 只读挂载公共数据 |
| 日志、追踪、指标 | Loki、Tempo、Prometheus | TOS/长期指标存储 | 独立生命周期 |

生产不再部署 Kafka、Flink、自建 MinIO 或 Policy 报告 NAS。本地 Policy Compose
使用同一 Market Worker 处理模型，仅以 S3 兼容模拟器替代 TOS；历史流式链路只存在于
Git 历史和旧镜像中。

## 3. 事件标识与时间

所有事件使用带时区的 ISO 8601 时间，并保存：

- `source_time`：行情源时间。
- `collected_at`：采集完成时间。
- `published_at`：当前处理阶段完成时间。
- `processed_at`：下游完成时间。

展示统一转换为 `Asia/Shanghai`，禁止用服务接收时间冒充行情源时间。

盘口缺少交易所序列号时，事件标识由规范化关键字段生成：

```text
event_id = sha256(
  trade_date | symbol | source_time | price | cumulative_volume |
  cumulative_amount | bid1 | bid1_volume | ask1 | ask1_volume
)
```

其他业务唯一键：

- 分钟线：`trade_date + symbol + bar_time`
- 实时特征：`feature_version + symbol + window_end`
- 策略计划：`trade_date + strategy_version`
- 策略日：`strategy_id + trade_date`
- 当前状态：`plan_id + symbol`
- Inbox：`plan_id + source_event_id`
- 报告资产：`strategy_run_id + format + content_hash`

## 4. SQLite WAL

Market Worker 在调用任何下游前先将规范化事件写入 SQLite WAL：

1. `event_id` 唯一，重复追加不产生新记录。
2. SQLite 使用 WAL 日志模式和 `synchronous=FULL`。
3. ClickHouse、Redis 行情投影和 PostgreSQL 策略事务完成后才确认记录。
4. 决策 Redis 投影失败时由 PostgreSQL Outbox 保留并重试，不依赖原始 WAL。
5. 进程重启先重放未确认记录。
6. 达到容量上限时停止接受新事件并告警，不覆盖旧记录。

两个 `market-collector` StatefulSet Pod 各使用独立 10Gi EBS RWO 卷，通过 PostgreSQL
advisory lease 单活采集。故障切换后旧主 WAL 不会自动转移，旧卷必须保留并纳入恢复流程。

## 5. PostgreSQL

### 5.1 数据库与角色

一个 PostgreSQL 集群承载三个隔离数据库：

| 数据库 | 责任 | 运行角色 |
| --- | --- | --- |
| `banxia` | Policy 领域数据 | `banxia` |
| `tsp_control` | 身份、租户、会话、Cell 和路由 | `tsp_control_runtime` |
| `tsp_data_production` | 自选、策略、任务、模拟盘、通知和审计 | `tsp_data_runtime` |

两个 TSP 运行角色使用独立密码，禁止超级用户、`BYPASSRLS`、建库、建角色和角色继承。
建库与迁移只允许离线初始化 Job 使用管理员 DSN。

### 5.2 Policy 核心表

| 表 | 主键/唯一键 | 责任 |
| --- | --- | --- |
| `strategy_definition` | `strategy_id`、`code`、激活部分唯一索引 | 策略目录、名称、血缘和激活状态 |
| `strategy_version` | `strategy_version_id` | 不可变规则、配置和代码版本 |
| `strategy_run` | `run_id`、`trade_date + strategy_version_id` | 日报运行 |
| `strategy_plan` / `candidate` | `plan_id` / `candidate_id` | 计划与候选 |
| `decision_state` | `plan_id + symbol` | 当前权威状态 |
| `decision_event` | `decision_event_id` | 追加式迁移历史 |
| `inbox_event` | 作用域事件 ID | 输入幂等 |
| `outbox_event` | `outbox_id` | 决策投影可靠重试 |
| `job_execution` | `job_id` | 调度、重试和执行历史 |
| `report_asset` | `run_id + format + content_hash` | TOS 对象元数据 |
| `strategy_day` | `strategy_id + trade_date` | 页面与研究的每日物化聚合 |
| `research_*` | 各实验唯一键 | 研究、逐日结果和候选配置 |

一次策略事件在单个事务中插入 Inbox、锁定并更新状态、追加决策事件、写入 Outbox。
任一步失败全部回滚。不可逆状态不能因后续行情或重放而回退。

### 5.3 迁移

`tsp-database-bootstrap` 只负责创建数据库、角色和连接权限；
`tsp-product-initialize` 负责 Schema 迁移与首次身份初始化。旧 `tsp-postgres` 数据迁移由
显式 Job 完成，迁移前后必须核对表数、行数、管理员登录和关键业务数据。

## 6. ClickHouse

主要表：

- `market_quote_snapshot`：盘口快照，按交易月分区。
- `market_bar_1m`：一分钟线，按交易日、股票和分钟幂等修订。
- `market_feature_realtime`：量能、板块强度和数据质量等确定性特征。
- 历史 K 线、板块资金和研究所需宽表。

规则：

- 使用稳定事件 ID 或修订版本消除重复逻辑结果。
- 最新查询优先走 Redis，不对高频请求执行无界 `FINAL`。
- 历史查询必须包含日期和标的过滤。
- 大批量导出使用异步任务和 TOS。

## 7. Redis

| Key | TTL | 内容 |
| --- | ---: | --- |
| `quote:latest:{symbol}` | 2 天 | 最新盘口和源时间 |
| `feature:latest:{symbol}` | 2 天 | 最新实时特征 |
| `decision:latest:{plan_id}:{symbol}` | 7 天 | 当前建议和原因 |
| `stream:monitor:{trade_date}` | 2 天 | SSE 短期事件 |

Redis 故障不得阻止 PostgreSQL 状态事务。行情和特征可由 ClickHouse 重建，决策可由
PostgreSQL 状态与 Outbox 重建。

## 8. TOS

生产环境通过 S3 兼容协议访问 TOS：

```text
market-raw/
  quote/trade_date=YYYY-MM-DD/hour=HH/part-*.parquet
  bar_1m/trade_date=YYYY-MM-DD/part-*.parquet
  reference/file=<filename>/as_of_date=YYYY-MM-DD/<sha256>
  manifests/dataset=<dataset>/snapshot_id=<uuid>/manifest.json

strategy-reports/
  strategy_version=<version>/trade_date=YYYY-MM-DD/sha256=<hash>/<file>
  research/<run-id>/<hash-prefix>/<file>
```

Manifest 至少包含对象哈希、Schema 版本、生成时间、策略版本、输入范围和文件列表。
生产配置使用 `BANXIA_OBJECT_*`；旧 `BANXIA_MINIO_*` 仅为迁移期兼容变量。

当前主数据归档由 `MarketReferenceSync` 实现；已采集 ClickHouse 行情的日终归档由
`banxia_strategy.application.market_archive` 实现，五表去重后流式写入
`clickhouse/dataset=<table>/trade_date=<date>/<sha256>.json.gz`，逐对象读回校验后
发布 `scope=collected_data` 的 Manifest。空安装不会产生完整归档清单。
上方 Parquet 目录仍是目标格式，不能视为当前已落盘事实；完整采集覆盖、恢复测试
和真实 TOS 授权状态见 [验收记录](38-tos-smtp-acceptance.md)。

## 9. 生命周期

| 数据 | 在线保留 | 归档/删除 |
| --- | --- | --- |
| SQLite WAL | 仅未确认事件 | 确认后删除；异常卷按事件对账后清理 |
| ClickHouse 盘口 | 90 天 | TOS 归档按策略保留 |
| 一分钟线 | 至少 1 年 | TOS 长期归档 |
| 实时特征 | 180 天 | 按研究需要归档 |
| PostgreSQL 业务与审计 | 在线长期保存 | 数据库备份与合规归档 |
| Redis | 2～7 天 | 可随时重建 |
| 报告与研究资产 | 长期保存 | TOS 生命周期和版本控制 |
| 日志 / Trace | 30 天 / 7 天 | 到期删除或抽样归档 |

## 10. 数据质量与对账

每个交易日检查：

- 股票代码、交易所、交易日和时间顺序。
- `source_time` 与 `collected_at` 偏差。
- 分钟线 OHLC、累计成交量和成交额单调性。
- 计划昨收与行情昨收差异。
- WAL 最老事件、失败次数和磁盘占用。
- ClickHouse 输入数量与稳定事件 ID 去重结果。
- PostgreSQL Inbox、决策和 Outbox 数量与最老未发布记录。
- TOS 对象数、总大小、Manifest 与 SHA-256。
- Redis 投影时间不得早于权威状态。

严重异常必须将策略状态降级为“行情异常”，不能仅记录日志后继续判断。

## 11. 备份与恢复

- PostgreSQL：持续 WAL 归档、每日备份和时间点恢复演练。
- ClickHouse：定期备份到独立 TOS Bucket，并验证分区恢复。
- TOS：启用版本控制、生命周期和跨区域副本。
- NAS：备份公共版本 Manifest、平台配置和租户产物。
- Redis：可启用 AOF，但恢复依据仍是 PostgreSQL 与 ClickHouse。
- SQLite WAL：保留异常 Pod 的 EBS 卷，按事件 ID 完成重放或人工核销。

EBS/NAS 的 `Retain` 回收策略只能防止删除 PVC 时立即丢盘，不能替代数据库备份。

## 12. 兼容与历史边界

旧配置值 `mootdx` 会在读取边界归一化为 `rustdx`；旧 `BANXIA_MINIO_*` 变量只用于
迁移期兼容读取。新配置、新部署和新文档不得继续生成这些旧值。

Kafka Topic、DLQ、Flink Checkpoint 和 MinIO 生产定义已从当前源码、契约和部署清单
删除。历史回滚必须使用对应 Git 提交与旧镜像，不能把旧组件重新接入当前运行时。
只有达到 [精简架构扩展阈值](36-lean-architecture-v0.5.1.md#8-扩展阈值) 并完成容量验证后，
才重新设计消息总线或流式计算。
