# LSS v0.5.1 产品架构

本文描述当前生产架构。完整系统图、运行参数和扩展阈值见
[精简生产架构](../../docs/36-lean-architecture-v0.5.1.md)，实际部署状态见
[版本基线](./version-baseline.md)。

历史 Gateway、四个独立 LSS Worker、独立 LSS PostgreSQL、Policy HTTP Bridge 和
Pod 内 DinD 已退出生产。完整单机 Compose 和相关阶段报告只用于兼容与历史回溯。

## 1. 总体拓扑

```text
浏览器
  -> ALB / HTTPS
  -> lss-product：React + FastAPI + SSE + RBAC
       -> 内嵌 Policy ASGI
       -> PostgreSQL / ClickHouse / Redis

rustdx -> market-collector -> SQLite WAL
                           -> ClickHouse / PostgreSQL / Redis

PostgreSQL -> lss-task-worker -> lss-compute
PostgreSQL -> report-scheduler -> TOS
ClickHouse -> market-archive   -> TOS
```

核心原则：

1. 浏览器只访问统一域名和统一 API。
2. Policy 使用进程内 ASGI Transport，不传递服务间 Token。
3. API 与任务消费者独立部署，Web 扩容不复制后台任务。
4. 实时行情先进入 SQLite WAL，再进行至少一次处理和幂等写入。
5. PostgreSQL、ClickHouse 和 TOS 保存权威事实，Redis 只保存可重建投影。
6. Compute 无业务密钥、禁止出网，只读公共行情版本。
7. 数据缺失、过期或依赖异常时停止有效判断，不输出伪成功。

## 2. 生产工作负载

| 工作负载 | 副本 | 职责 |
| --- | ---: | --- |
| `lss-product` | HPA 2～4 | React、统一 API、账户、租户、SSE 和 Policy 门面 |
| `lss-task-worker` | 1 | 研究、监控、模拟盘、通知和计划任务 |
| `report-scheduler` | 1 | Policy 报告队列、历史生成、定时报告和补跑 |
| `lss-platform` | 1 | 公共行情版本、平台配置和只读行情服务 |
| `lss-compute` | 1 | 回测、优化和研究计算 |
| `market-collector` | 2，租约单活 | rustdx 采集、WAL、特征、决策和投影 |
| `market-reference-sync` | 1 | 证券、板块、行业和交易日参考数据 |
| `intraday-monitor` | 1 | 盘中状态、规则评估和告警指标 |
| `market-archive` | CronJob，暂停 | ClickHouse 五类行情的 TOS 日终归档 |

归档候选镜像已部署并通过模块烟测。有效 TOS 凭据、首个有数据交易日和对象读回完成
验收前，`market-archive` 必须保持 `suspend: true`。

## 3. 数据职责

| 数据域 | 权威存储 |
| --- | --- |
| Policy 策略、计划、任务、审计 | PostgreSQL `banxia` |
| 身份、租户、角色、会话 | PostgreSQL `lss_control` |
| 自选、LSS 策略、回测、复盘、设置 | PostgreSQL `lss_data_production` |
| 盘口、分钟线、特征、历史行情 | ClickHouse |
| 最新行情、决策和 SSE 投影 | Redis |
| 报告、研究资产、原始归档 | 火山引擎 TOS |
| 未完成处理的实时事件 | Market Worker 本地 SQLite WAL / EBS |
| 公共行情版本与平台状态 | NAS RWX |

三个 PostgreSQL 数据库共用集群，但数据库与运行角色隔离。管理员 DSN 仅进入离线
初始化 Job；API 和 Compute 不获得管理员凭据。

## 4. 请求与任务

- `/api/*` 由 `lss-product` 统一完成 Cookie 认证、RBAC、限流和响应。
- `/api/policy/*` 在统一权限层校验后转入同进程 Policy `/api/v1/*`。
- SSE 使用短期票据；私有事件先持久化，再通过通知和游标补扫恢复。
- API 只登记持久任务，Task Worker 与 Report Scheduler 负责领取、租约和终态。
- Task Worker 通过集群内固定协议调用 Compute。
- Compute 读取指定公共行情版本，私有中间结果只写临时目录。

## 5. 实时链路

1. rustdx 数据在 Provider 边界完成代码、单位、日期和时区标准化。
2. 规范化事件先写入当前 Market Worker 的 SQLite WAL。
3. 原始行情幂等写入 ClickHouse，并更新 Redis 行情投影。
4. 确定性实时特征写入 ClickHouse 和 Redis。
5. 每个有效计划在 PostgreSQL 事务内写 Inbox、决策状态、事件和 Outbox。
6. Relay 将 Outbox 更新到 Redis 决策投影。
7. 权威写入完成后确认对应 WAL 记录。

WAL 重放依赖稳定 `event_id` 和数据库唯一键去重。Redis 故障不回滚权威状态，恢复后
从 PostgreSQL、ClickHouse 和 Outbox 重建。

## 6. 产品代码边界

| 层 | 主要位置 | 职责 |
| --- | --- | --- |
| Web/API | `backend/app/api/`、`backend/app/main.py` | 认证、参数校验、HTTP/SSE 和依赖组装 |
| Policy 门面 | `backend/app/custom/policy_bridge.py` | 固定路径校验和进程内 ASGI 调用 |
| 多租户运行时 | `backend/app/multitenancy/` | 租户、任务、平台、Compute 和 Worker |
| 业务服务 | `backend/app/services/` | 数据、策略、回测、监控、模拟盘和通知 |
| Provider | `backend/app/data_providers/`、`backend/app/plugins/` | 能力声明、路由和标准化 |
| 前端 | `frontend/src/` | 九项页面、查询缓存、API 类型和交互 |

API 层不实现供应商协议或批量金融计算；前端不复制后端能力注册表和金融口径。

## 7. 独立运行兼容

`MULTITENANCY_ENABLED=false` 的根目录 Compose、Dev 和桌面模式仍是单进程模块化
应用，使用 `DATA_DIR` 下的 Parquet、DuckDB、SQLite 和 JSON。该模式必须保持单个
Uvicorn worker、单个数据写入者，不能通过增加副本共享进程内事件、调度或缓存状态。

完整单机多租户 Compose 保留作隔离验证和回滚工具，不代表 VKE 当前组件划分。

## 8. 故障与安全

| 故障 | 预期行为 |
| --- | --- |
| rustdx 节点失败 | 切换备用节点，记录缺口和恢复时间 |
| Market Worker 重启 | 重放本 Pod WAL，旧主卷保留待核销 |
| PostgreSQL 不可写 | 停止新决策，不允许只写 Redis |
| ClickHouse 不可写 | 保留 WAL，历史查询和归档降级 |
| Redis 丢失 | 权威状态不丢失，重建投影 |
| TOS 不可用 | 报告或归档失败，不发布成功 Manifest |
| Compute 异常 | 任务进入失败或中断状态，不扩大权限 |
| SSE 断连 | 客户端重读 REST 权威状态并重新换票 |

公网只开放 ALB 443。数据库、缓存、平台和 Compute 只在集群内访问。应用容器非 root，
不挂载宿主 Docker Socket；Compute 不注入数据库、SMTP、TOS 或平台 Secret，
NetworkPolicy 拒绝其出站连接。

## 9. 当前验证边界

已验证：固定 digest 部署、HTTPS、登录、九项菜单、SSE、空库初始化、管理员工作空间、
冷快照、Compute 实际执行和归档模块烟测。

待完成：TOS 对象实写/读回、SMTP 真实投递、首个有数据交易日归档、完整行情覆盖、
生产真实有信号回测、容量测试和恢复演练。
