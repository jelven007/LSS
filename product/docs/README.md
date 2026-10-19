# LSS 产品文档中心

当前应用版本为 `0.5.1`。源码、远程主线、候选标签和生产部署的准确状态统一见
[版本基线](./version-baseline.md)。代码、测试和运行清单是最终事实来源。

生产环境使用 `deploy/kubernetes/` 与仓库根目录 VKE overlay：

- `tsp-product` 提供 React、FastAPI、SSE、RBAC 和内嵌 Policy。
- `tsp-task-worker` 负责研究、监控、模拟盘、通知和计划任务。
- `report-scheduler` 处理 Policy 报告队列与定时报告。
- `market-collector` 通过 rustdx 采集，并以 SQLite WAL 保证可恢复处理。
- `tsp-compute` 是无业务密钥、禁止出网的独立计算 Pod。
- PostgreSQL 三库隔离，ClickHouse 保存行情，Redis 保存实时投影，TOS 保存归档和报告。

Kafka、Flink、自建 MinIO、独立 Policy API、独立 SSE Gateway、独立 TSP PostgreSQL
和 Pod 内 DinD 已退出当前生产。完整单机 Compose 与历史多租户方案只用于兼容、
隔离验证或回溯，不代表 `shanao.asia` 当前拓扑。

## 生命周期

| 阶段 | 当前文档 | 当前状态 |
| --- | --- | --- |
| 需求 | [需求基线](./requirements.md)、[功能手册](./features.md) | v0.5.1 九项页面与生产约束已定版 |
| 方案 | [当前架构](./architecture.md)、[精简架构](../../docs/36-lean-architecture-v0.5.1.md) | 精简生产拓扑已部署 |
| 开发 | [贡献与复审指南](../CONTRIBUTING.md)、[二次开发](./secondary-development.md) | 主线 `132db77` 已推送 |
| 测试 | [测试与验收](./testing.md)、[技术验证记录](./technical-validation.md) | 自动化与基础公网烟测通过，外部服务待签收 |
| 运维 | [部署](./deployment.md)、[运维手册](./operations.md)、[配置](./configuration.md) | `shanao.asia` 在线，归档 CronJob 暂停 |
| 发布 | [发布与上线运营](./release-operations.md)、[安装记录](../../docs/releases/2026-10-19-fresh-install-v0.5.1.md) | `v0.5.1-rc.1` 已推送，尚非稳定 Release |

## 当前索引

| 领域 | 文档 |
| --- | --- |
| 产品与使用 | [功能手册](./features.md)、[操作说明书](../操作说明书.md) |
| VKE 生产 | [VKE 部署](../deploy/kubernetes/README.md)、[Policy 整合](../INTEGRATION.md)、[资源清单](../../docs/16-volcengine-resource-inventory.md) |
| 数据源 | [rustdx](./rustdx-data-source.md)、[自定义数据源](./custom-data-source.md)、[插件开发](./plugin-development.md) |
| 策略与研究 | [策略开发](./strategy.md)、[策略迭代](./strategy-iteration.md)、[研究配置](./research-configuration.md)、[因子系统](./factor-system-design.md) |
| AI | [接口协议](./ai-protocols.md)、[助手会话同步](./assistant-sessions.md) |
| 市场与交易 | [市场阶段](./market-phase.md)、[模拟盘](./paper-trading-plan.md) |
| 开放能力 | [开放平台](./open-platform-plan.md)、[Open API 示例](../examples/open-api/README.md) |
| 协作与版本 | [版本基线](./version-baseline.md)、[协作版本管理](./collaborative-versioning.md) |

## 历史资料

`phase1-implementation.md`、`phase2-implementation-plan.md`、
`multitenant-roadmap.md`、`realtime-gateway-contract.md` 及 `reports/` 记录旧单机
多租户阶段的设计和验证证据。它们不定义当前生产拓扑，遇到冲突时以版本基线、
当前架构和 VKE 清单为准。

根目录 `docs/releases/` 和带明确日期的研究报告同样保留当时事实，不追溯改写数据源、
样本或历史运行组件。

## 事实优先级

1. 可执行代码、数据库 schema、部署清单和公开 API 契约。
2. 自动化测试与固定契约快照。
3. [贡献与复审指南](../CONTRIBUTING.md)。
4. 当前需求、架构、测试和运维基线。
5. 专项设计与历史验证记录。

版本由维护者显式更新，并由 `.github/workflows/product.yml` 校验根项目、后端、
前端和 `VERSION` 一致；CI 不自动递增版本或创建标签。

## 同步规则

- 页面或业务边界变化：更新需求、功能手册和操作说明书。
- 数据口径或 Provider 变化：更新贡献指南和数据源文档。
- 拓扑、存储或安全边界变化：更新架构、部署和运维文档。
- 测试命令或质量门变化：更新测试文档。
- 版本、镜像、部署或回滚状态变化：更新版本基线和发布记录。
