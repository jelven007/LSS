# LSS

面向沪深市场的策略研究、选股、盘中监控、回测、复盘和报告系统。

系统不连接券商账户、不发送真实委托，所有候选、监控和回测结果仅供研究。

## 当前状态

| 项目 | 当前值 |
| --- | --- |
| 应用版本 | `0.5.1` |
| 主线提交 | `132db778c03b5e4d724afd510c63d269c7764211` |
| 候选标签 | `v0.5.1-rc.1` |
| 仓库 | <https://github.com/jelven007/LSS> |
| 生产入口 | <https://shanao.asia> |
| 数据源 | `rustdx native` |

生产候选已完成固定 digest 部署、HTTPS、登录、九项页面、静态资源、SSE、空库初始化、
管理员工作空间和基础就绪验收。以下项目仍未完成业务签收：

- 有效 TOS 凭据下的对象写入、读回和首个有数据交易日归档。
- SMTP 真实投递、注册验证码和密码找回。
- 生产真实有信号回测、容量测试和恢复演练。

具体状态见
[2026-10-19 全新安装记录](docs/releases/2026-10-19-fresh-install-v0.5.1.md)和
[TOS/SMTP 验收](docs/38-tos-smtp-acceptance.md)。

## 架构

```text
浏览器
  -> ALB / HTTPS
  -> React + FastAPI 统一入口
       -> 账户 / 多租户 / SSE
       -> 内嵌 Policy ASGI
       -> PostgreSQL / ClickHouse / Redis / TOS

rustdx native
  -> Market Worker
  -> SQLite WAL
  -> 行情持久化 / 实时特征 / 策略事务 / Redis 投影

Task Worker -> 隔离 Compute
Report Scheduler -> PostgreSQL + TOS
```

生产不再部署 Kafka、Flink、独立 Policy API、独立 SSE Gateway、独立 TSP
PostgreSQL、自建 MinIO 或 Pod 内 DinD。可靠性由 SQLite WAL、PostgreSQL
Inbox/Outbox、稳定事件 ID 和幂等写入保证。

详见：

- [系统架构](docs/02-architecture.md)
- [v0.5.1 精简生产架构](docs/36-lean-architecture-v0.5.1.md)
- [生产架构精简记录](docs/37-production-simplification.md)
- [数据与存储](docs/03-data-storage.md)

## 产品范围

统一 Web 主导航包含：

1. 首页
2. 自选
3. 策略
4. 次日计划
5. 当日实盘
6. 复盘
7. 回测
8. 个股分析
9. 设置

`product/` 提供 React/FastAPI 产品、账户、多租户和通用研究能力。Policy 领域提供
次日计划、当日实盘、策略状态和报告，通过进程内 ASGI 门面接入统一 API。

## 目录

| 路径 | 内容 |
| --- | --- |
| `src/banxia_strategy/` | Policy 领域、行情、策略、报告和归档 |
| `product/backend/` | 统一 FastAPI、多租户、任务和 Compute |
| `product/frontend/` | React 产品 |
| `deploy/kubernetes/` | VKE 生产基础清单与火山引擎 overlay |
| `deploy/compose/` | Policy 本地基础设施 |
| `product/deploy/` | 产品本地、独立主机和 VKE 部署 |
| `migrations/` | PostgreSQL 与 ClickHouse Schema |
| `tests/` | Policy 测试 |
| `product/backend/tests/` | 产品后端测试 |
| `docs/` | 当前文档、发布记录和研究档案 |

## 本地开发

### Policy

需要 Python 3.9+、Rust 1.89+ 和 Docker：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install ./native/rustdx_native
.venv/bin/pip install -e '.[storage,test]'
cp deploy/compose/.env.example deploy/compose/.env
make infra-up
```

运行报告或兼容研究服务：

```bash
.venv/bin/banxia-strategy run
.venv/bin/banxia-strategy serve
```

### 统一产品

需要 Python 3.11+、Node.js 20+、pnpm 9、uv 和 Rust：

```bash
cd product
cp .env.example .env
./dev.sh
```

默认前端为 <http://localhost:3011>，后端为 <http://localhost:3018>。
生产拓扑不能用 Dev 模式或独立单容器模式替代。

## 验证

Policy：

```bash
make test
```

产品后端：

```bash
cd product/backend
uv sync --extra dev --extra backtest --frozen
uv run --frozen --no-sync pytest tests -q
```

产品前端：

```bash
cd product/frontend
pnpm install --frozen-lockfile
pnpm test
pnpm lint
pnpm build
```

提交前执行：

```bash
git diff --check
python3 product/scripts/check_version.py --current product/VERSION
```

完整门禁见 [测试策略](docs/05-testing.md) 和
[产品测试指南](product/docs/testing.md)。

## 文档

- [文档中心](docs/README.md)
- [需求规格](docs/01-requirements.md)
- [接口规范](docs/04-api.md)
- [部署与运维](docs/06-deployment-operations.md)
- [迭代计划](docs/07-iteration-plan.md)
- [TSP 与 Policy 整合](docs/35-tsp-product-integration.md)

历史研究保留原始数据源、窗口和成交假设，不代表当前运行依赖或生产状态。
