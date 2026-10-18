# TSP 产品移植与 Policy 整合

## 目标与版本

`v0.5.0` 以 TSP `0.4.8` 为产品主干，保留完整 Web 产品和账户体系；Policy 继续提供
次日计划和当日实盘的权威数据。Web 是唯一客户端，Android/APK 已删除。

整合源码位于 [`product/`](../product/)，上游基线与数据归属见
[`product/INTEGRATION.md`](../product/INTEGRATION.md)。

## 产品菜单

| 顺序 | 菜单 | 实现来源 | 路由 |
| --- | --- | --- | --- |
| 1 | 首页 | TSP Dashboard | `/` |
| 2 | 自选 | TSP Watchlist | `/watchlist` |
| 3 | 策略 | TSP Screener | `/screener` |
| 4 | 次日计划 | Policy 专用页 | `/next-plan` |
| 5 | 当日实盘 | Policy 专用页 | `/live-trading` |
| 6 | 复盘 | TSP Review | `/review` |
| 7 | 回测 | TSP Backtest | `/backtest` |
| 8 | 个股分析 | TSP StockAnalysis | `/stock-analysis` |
| 9 | 设置 | TSP Settings | `/settings` |

桌面侧栏每次进入默认完整展开，可切换为图标栏；移动端使用抽屉并为菜单按钮预留顶部
空间。菜单不再提供完全隐藏的第三态。

## 开发实现

### 前端

- `product/frontend/src/components/Layout.tsx`：九项菜单、两态侧栏和移动端壳层。
- `product/frontend/src/router.tsx`：`/next-plan`、`/live-trading` 与账户守卫。
- `product/frontend/src/pages/NextPlan.tsx`：日期、报告汇总、候选计划和刷新任务。
- `product/frontend/src/pages/LiveTrading.tsx`：实时状态、候选监控、最后有效快照。
- 页面始终先渲染标题、操作区、状态区和空态；Policy 依赖只负责补充数据。

### 后端

- `product/backend/app/custom/policy_bridge.py`：固定白名单、参数校验和服务端 Token。
- `product/backend/app/multitenancy/`：双库多租户、RBAC、事件 Gateway 和 Worker。
- `product/backend/app/cell/admin.py`：首次缺失时创建 `admin/Admin123`，重启不重置。
- `product/backend/app/services/email_adapter.py`：`AUTH_SMTP_*` 与设置页 SMTP 回退。

Policy 上游 `401/403` 转为 `502`，避免前端把 Policy 鉴权失败误判为 TSP 会话失效。
Policy 只读调用启用静默错误，由页面内非阻塞状态承接。

## 数据边界

```text
浏览器
  -> TSP Cookie / RBAC / Tenant
  -> /api/policy/*
  -> TSP Policy Bridge
  -> http://api.banxia.svc.cluster.local/api/v1/*
  -> Policy PostgreSQL / Redis / ClickHouse
```

| 数据 | 权威系统 |
| --- | --- |
| 用户、租户、角色、会话、找回密码 | TSP PostgreSQL |
| 自选、TSP 策略、复盘、回测、个股分析、设置 | TSP PostgreSQL / TSP 状态卷 |
| 次日计划、候选、策略版本 | Policy PostgreSQL |
| 盘中行情和决策投影 | Policy ClickHouse / Redis / PostgreSQL |

## VKE 部署

根 Kustomize overlay 同时部署 Policy 与 TSP：

- Policy 原有数据服务和应用工作负载使用当前主线镜像重建。
- TSP 使用独立 PostgreSQL EBS 与 NAS 状态卷。
- `shanao.asia` 根路径转发到 TSP，Policy API 只保留集群内 Service。
- Compute Broker 使用 Pod 内 DinD 过渡方案，不挂载宿主 Docker socket。

清单和运行手册：

- [`product/deploy/kubernetes/README.md`](../product/deploy/kubernetes/README.md)
- [`deploy/kubernetes/README.md`](../deploy/kubernetes/README.md)
- [`docs/06-deployment-operations.md`](06-deployment-operations.md)

## 测试与验收

- TSP 前端：43 个文件、200 项测试，ESLint、TypeScript 和生产构建通过。
- Policy Bridge：11 项测试。
- Policy：完整 Python 测试与基础设施布局测试。
- Kubernetes：Kustomize 无占位符、服务端 dry-run、Rollout 和 Job 验收。
- 浏览器：管理员登录、九项菜单、移动端无重叠、依赖失败降级、无 Token 泄露。

发布记录见 [`releases/2026-10-18-web-v0.5.0.md`](releases/2026-10-18-web-v0.5.0.md)。
