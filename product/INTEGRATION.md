# TSP 产品整合说明

## v0.5.1 已部署候选

Policy 已安装到 TSP Runtime 镜像并通过 ASGI Transport 在同一进程调用；
`/api/policy/*` 契约保持不变。生产清单不再部署独立 Policy API、独立 Gateway、
独立 TSP PostgreSQL 或特权 DinD。完整说明见
[`v0.5.1 精简架构`](../docs/36-lean-architecture-v0.5.1.md)。

候选已部署到 <https://shanao.asia>，HTTPS、登录、九项页面和 SSE 已验收。
TOS 实写、SMTP、首个有数据行情归档、真实有信号回测、容量和恢复演练仍待签收。

## 基线

- 当前仓库：`https://github.com/jelven007/LSS`
- 当前主线：`132db778c03b5e4d724afd510c63d269c7764211`
- TSP 初始导入来源：`jelven007/TSP@bb8ea7045b613f189159e7ebd84162567ffb09e5`
- 整合产品版本：`0.5.1`
- 许可证：MIT，原许可证保留在 `product/LICENSE`

`product/` 保留 TSP 的 React/FastAPI 产品、完整业务模块、测试和服务端部署定义。
参考仓库中的截图、桌面打包产物、Git 元数据和 GitHub 配置未导入。

## 产品菜单

主导航固定为：

1. 首页
2. 自选
3. 策略
4. 次日计划
5. 当日实盘
6. 复盘
7. 回测
8. 个股分析
9. 设置

首页直接使用 TSP 市场看板。自选、策略、复盘、回测、个股分析、设置以及注册、登录、
找回密码使用 TSP 实现。TSP 其他业务页面和后端能力仍保留，但不进入主导航。

## Policy 内嵌业务域

次日计划和当日实盘继续由 Policy 领域负责计算和持久化：

```text
浏览器
  -> TSP 会话认证与租户权限
  -> /api/policy/*
  -> 同进程 Policy facade
  -> httpx ASGI Transport
  -> 内嵌 Policy /api/v1/*
  -> PostgreSQL / Redis / ClickHouse
```

生产链路不再创建或传递 Policy 服务间 Token。门面只开放次日计划、报告任务和盘中监控
所需的固定路径；日期、股票代码和任务 ID 在调用前校验。读取需要有效 TSP 会话，报告刷新还
需要 `data:manage` 权限。

运行配置：

```dotenv
POLICY_SERVICE_URL=embedded://policy
POLICY_SERVICE_TIMEOUT_SECONDS=30
```

独立 HTTP URL 和服务端 Token 仅保留用于非生产兼容测试。

## 数据归属

| 数据 | 权威系统 |
| --- | --- |
| 用户、租户、角色、会话、找回密码 | 共享 PostgreSQL 集群的 `tsp_control` |
| 自选、TSP 策略、复盘、回测、个股分析、用户设置 | 共享 PostgreSQL 集群的 `tsp_data_production` |
| Policy 次日计划、候选和策略版本 | 共享 PostgreSQL 集群的 `banxia` |
| Policy 盘中行情和决策投影 | Policy ClickHouse / Redis / PostgreSQL |
| Policy 行情采集 | 精简 `market-worker` 的 rustdx 长连接 |
| TSP 公共行情发布 | `tsp-platform`，按不可变版本发布 |
| 报告与行情归档 | 火山引擎 TOS；有效凭据配置前不视为可用 |

## 生产工作负载

- `tsp-product`：统一 React/FastAPI、SSE、RBAC 和 Policy 门面，HPA 2～4 副本。
- `tsp-task-worker`：研究、实时规则、模拟盘、通知和计划任务。
- `report-scheduler`：Policy 报告刷新、历史生成和补跑。
- `market-collector`：rustdx、SQLite WAL、幂等写入和实时投影，双副本租约单活。
- `tsp-compute`：独立无密钥计算 Pod，禁止出网，只读公共行情。
- `market-archive`：日终 TOS 归档 CronJob，当前保持暂停。

清单、Secret 契约和验收见
[`deploy/kubernetes/README.md`](deploy/kubernetes/README.md)。
