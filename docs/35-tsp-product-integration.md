# TSP 产品移植与 Policy 整合

## 1. 基线

- 上游仓库：`https://github.com/jelven007/TSP`
- 上游提交：`bb8ea7045b613f189159e7ebd84162567ffb09e5`
- 上游版本：`0.4.8`
- 当前整合版本：`0.5.1`
- 客户端：Web；Android/APK 已移除

TSP 提供产品壳层、账户、多租户和通用研究能力；Policy 提供次日计划、当日实盘、领域策略
和报告。`v0.5.1` 将两者打入同一个 Runtime 镜像，并由统一 FastAPI 对外服务。

## 2. 产品菜单

| 顺序 | 菜单 | 主要实现 | 路由 |
| --- | --- | --- | --- |
| 1 | 首页 | TSP Dashboard | `/` |
| 2 | 自选 | TSP Watchlist | `/watchlist` |
| 3 | 策略 | TSP Screener | `/screener` |
| 4 | 次日计划 | Policy | `/next-plan` |
| 5 | 当日实盘 | Policy | `/live-trading` |
| 6 | 复盘 | TSP Review | `/review` |
| 7 | 回测 | TSP Backtest | `/backtest` |
| 8 | 个股分析 | TSP StockAnalysis | `/stock-analysis` |
| 9 | 设置 | TSP Settings | `/settings` |

桌面侧栏默认展开，可切换为图标栏；移动端使用抽屉。Policy 依赖失败时，次日计划和当日
实盘仍先渲染页面框架、日期、状态和空态，不触发全局登出。

## 3. 统一 API

```text
浏览器
  -> TSP Cookie / RBAC / Tenant
  -> /api/policy/*
  -> Policy facade
  -> httpx ASGI Transport
  -> 内嵌 Policy /api/v1/*
  -> PostgreSQL / ClickHouse / Redis / TOS
```

- 浏览器只访问同源 API。
- `/api/policy/*` 继续执行日期、股票代码、任务 ID 和权限校验。
- 报告刷新要求 `data:manage`。
- 生产配置为 `POLICY_SERVICE_URL=embedded://policy`。
- 不再部署独立 Policy Service，也不再使用服务间 Policy Token。
- 上游 `401/403` 的兼容映射保留给独立 HTTP 测试模式。
- 内嵌 Policy 客户端随 FastAPI 扩展生命周期关闭。

## 4. 数据边界

Policy 与 TSP 共用一个 PostgreSQL 集群，但保持数据库与角色隔离：

| 数据 | 权威落点 |
| --- | --- |
| 用户、租户、角色、会话、找回密码 | `tsp_control` |
| 自选、TSP 策略、复盘、任务、通知、用户设置 | `tsp_data_production` |
| Policy 策略、次日计划、候选、决策、报告元数据 | `banxia` |
| 高频行情和实时特征 | ClickHouse |
| 最新行情、决策和 SSE 投影 | Redis |
| 报告、研究资产和原始归档 | TOS |
| 公共行情版本和租户产物 | TSP NAS 状态卷 |

TSP 运行时只获得两个最小权限 Runtime DSN；管理员 DSN 只用于初始化和迁移 Job。
Policy 数据不会复制到 TSP 表，TSP 只通过领域门面读取 Policy 权威结果。

## 5. 运行单元

| 单元 | 职责 |
| --- | --- |
| `tsp-product` API | React、账户、多租户、SSE、TSP API、Policy 门面 |
| `task-worker` | 监督 research、realtime、notifications、scheduled 四类 Worker |
| `tsp-platform` | 唯一公共行情写入者与不可变版本发布 |
| `tsp-compute` | 无密钥、禁止出网的受限计算服务 |
| `market-worker` | rustdx 采集、实时特征、Policy 状态机和 Redis 投影 |

独立 SSE Gateway、四个独立 TSP Worker、Pod 内 DinD、独立 TSP PostgreSQL 和独立
Policy API 均已退出生产清单。

## 6. Compute 边界

API 使用 `TSP_COMPUTE_ADDRESS=tsp-compute:7000` 连接 Compute。Compute：

- 只读挂载公共行情版本。
- 不注入数据库、SMTP、TOS 或平台管理 Secret。
- 禁止出站网络，根文件系统只读。
- 以非 root 用户运行，删除全部 capabilities。
- 固定任务类型、入口程序、环境和并发上限。

Unix Socket + 容器 Broker 仅保留给独立部署兼容模式，不进入 VKE 生产清单。

## 7. 发布边界

1. 先备份旧 TSP PostgreSQL、Policy PostgreSQL、ClickHouse、MinIO 和 NAS。
2. 先迁移 TSP 双库和 TOS 对象，再切换应用。
3. 旧 PVC、Secret 和镜像 Digest 在至少五个交易日观察期内保留。
4. Kustomize 发布不得删除 PostgreSQL、ClickHouse、Redis、NAS、ALB 或 StorageClass。
5. 生产镜像必须固定 Digest；源码通过不代表生产已生效。

详细迁移和回滚见
[部署与运维](06-deployment-operations.md) 与
[v0.5.1 精简生产架构](36-lean-architecture-v0.5.1.md)。

## 8. 质量基线

- TSP 前端：43 个测试文件、200 项测试。
- Policy：401 项测试通过，25 项按环境跳过。
- Policy Facade：包含 ASGI 模式、错误映射、参数校验和资源释放。
- Task Worker：监督四类后台进程，任一异常退出触发整体失败。
- Compute：Unix/TCP 协议、取消、不可用状态和隔离参数回归。
- Kustomize：无旧组件、无占位符、无特权容器和 Docker Socket。

发布候选记录见
[`releases/2026-10-18-lean-v0.5.1.md`](releases/2026-10-18-lean-v0.5.1.md)。
