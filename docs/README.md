# 文档中心

## 当前基线

- 应用版本：`0.5.1`
- 主线提交：`132db778c03b5e4d724afd510c63d269c7764211`
- 候选标签：`v0.5.1-rc.1`
- 仓库：<https://github.com/jelven007/LSS>
- 生产入口：<https://shanao.asia>
- 当前架构：统一 React/FastAPI、内嵌 Policy、Market Worker、独立 Task Worker
  与 Compute
- 数据源：新采集统一使用 `rustdx native`
- 系统边界：研究、筛选、监控、回测、复盘和报告；不连接券商，不自动下单

源码、候选标签、镜像和生产签收是不同状态。当前生产已完成 HTTPS、登录、九项页面、
SSE 和基础就绪验收；TOS 实际写入、SMTP 投递、首个有数据交易日归档、生产真实有信号
回测及恢复演练仍待完成。

## 核心生命周期文档

| 阶段 | 文档 | 责任 |
| --- | --- | --- |
| 需求 | [需求规格](01-requirements.md) | 产品范围、硬规则、非功能要求和验收条件 |
| 方案 | [系统架构](02-architecture.md) | 当前组件、数据流、一致性和安全边界 |
| 数据 | [数据与存储](03-data-storage.md) | 数据归属、幂等键、生命周期和恢复 |
| 接口 | [接口规范](04-api.md) | 统一 API、内嵌 Policy、SSE 和错误语义 |
| 测试 | [测试策略](05-testing.md) | 自动化、集成、生产门禁和剩余风险 |
| 运维 | [部署与运维](06-deployment-operations.md) | VKE 发布、健康、备份、恢复和回滚 |
| 迭代 | [迭代计划](07-iteration-plan.md) | 当前 P0/P1/P2 工作和退出条件 |

## 当前专项文档

| 领域 | 文档 |
| --- | --- |
| 精简生产架构 | [v0.5.1 架构](36-lean-architecture-v0.5.1.md) |
| 功能退役 | [生产架构精简记录](37-production-simplification.md) |
| TOS / SMTP | [配置、归档与验收](38-tos-smtp-acceptance.md) |
| 产品整合 | [TSP 与 Policy 整合](35-tsp-product-integration.md) |
| 数据源 | [rustdx 迁移与当前契约](30-rustdx-migration.md) |
| 多策略 | [多策略管理](09-multi-strategy.md) |
| Web | [UI 标准](10-web-ui-standard.md) |
| 云资源 | [火山引擎资源清单](16-volcengine-resource-inventory.md) |

机器契约以运行时 `/api/openapi.json` 为准；仓库中的
[OpenAPI](openapi.yaml) 保存 Policy 核心接口快照。Kafka AsyncAPI 已随生产消息总线
退役删除。

## 发布与证据

| 文档 | 状态 |
| --- | --- |
| [2026-10-19 全新安装](releases/2026-10-19-fresh-install-v0.5.1.md) | 当前生产候选与验收边界 |
| [v0.5.1 精简候选](releases/2026-10-18-lean-v0.5.1.md) | 架构切换前候选记录 |
| [v0.5.0 Web 发布](releases/2026-10-18-web-v0.5.0.md) | 历史发布记录 |
| [rustdx 生产发布](releases/2026-10-15-rustdx-r1.md) | 历史数据源切换证据 |

发布记录和研究报告保留其当时的数据源、版本与结论，不追溯改写为当前状态。
历史文档中的 mootdx、Kafka、Flink 或 MinIO 只表示当时事实，不代表当前运行依赖。

## 研究档案

`08`、`12`、`14`～`25`、`27`、`29`、`32`～`34` 及 `t1-research-method.md`
是策略研究和历史验证档案。它们保留原数据窗口、来源和成交假设；研究结论不能自动
成为生产激活策略。

已删除的迁移前设计包括 mootdx 能力矩阵、mootdx 持久化目标、2026-10-14 旧架构审计、
rustdx 迁移预分析和重复的生产方案。需要追溯时使用 Git 历史，不在当前文档导航中维护
第二套事实。

## 事实源优先级

发生冲突时按以下顺序判断：

1. 可执行代码、数据库 Schema 和运行时 OpenAPI。
2. 自动化测试和固定契约快照。
3. 当前生命周期文档。
4. 专项设计与发布记录。
5. 历史研究和迁移档案。

业务规则、数据单位、API、存储归属、部署拓扑或验收状态变化时，必须同步对应生命周期
文档。源码通过、制品构建、生产部署和业务签收必须分别记录。
