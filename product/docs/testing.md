# LSS 测试与验收

本文定义产品测试入口和 v0.5.1 放行边界。系统级策略见
[测试与质量策略](../../docs/05-testing.md)，功能验收依据
[需求基线](./requirements.md)，生产状态依据[版本基线](./version-baseline.md)。

## 1. 当前证据

截至 2026-10-19：

| 范围 | 结果 |
| --- | --- |
| Policy | 394 项：369 通过、25 项按环境跳过 |
| TSP 精简架构 | Policy Facade、Task Worker、Compute、扩展和健康定向 46 项通过 |
| 身份与工作空间 | 真实隔离 PostgreSQL 23 项通过 |
| Compute | 9 项通过，生产 Pod 实际策略描述成功 |
| 前端 | 42 个测试文件、180 项通过 |
| 静态产物 | TypeScript、ESLint、Vite 构建和静态资源引用检查通过 |
| 部署 | Kustomize 渲染、服务端 dry-run、固定 digest 和安全约束通过 |
| 公网 | HTTPS、登录、九项页面、SSE、桌面与 390px 窄屏通过 |

这些测试组存在重叠，不能相加为总测试数。TOS 实际写入/读回、SMTP 收件箱、
首个有数据交易日归档、真实有信号回测、容量与恢复演练尚未签收。

完整证据见[全新安装记录](../../docs/releases/2026-10-19-fresh-install-v0.5.1.md)和
[TOS/SMTP 验收](../../docs/38-tos-smtp-acceptance.md)。

## 2. CI 实际门禁

仓库只有 `.github/workflows/product.yml` 作为当前产品工作流：

| Job | 实际检查 |
| --- | --- |
| `policy` | 安装根项目测试与存储依赖，运行 `python -m unittest discover -s tests -v` |
| `frontend` | pnpm 锁定安装、`pnpm test`、`pnpm build` |
| `unified-api` | Policy 门面、多租户访问、初始化、Compute 和 Task Worker 定向 Ruff/pytest |
| `candidate-deploy` | 完整单机 Compose 配置、Nginx 配置和三个 Dockerfile 构建检查 |
| `version-and-kubernetes` | 七处版本一致性、VKE Kustomize 渲染及退役/特权组件检查 |

CI 不自动递增版本、创建标签、发布镜像或执行生产部署。发布负责人必须单独固定镜像
digest、执行服务端 dry-run 和记录上线证据。

## 3. 本地标准命令

Policy：

```bash
python -m pip install -e ".[test,storage]"
PYTHONPATH=src python -m unittest discover -s tests -v
```

TSP 后端：

```bash
cd product/backend
uv sync --extra dev --extra cell --frozen
uv run --frozen --no-sync pytest tests -q
uv run --frozen --no-sync ruff check app tests
```

改动统一 API 或后台任务时，至少执行：

```bash
cd product/backend
PYTHONPATH=. pytest -q \
  tests/test_policy_bridge.py \
  tests/test_task_worker.py \
  tests/test_full_compute.py \
  tests/test_extensions.py
```

前端：

```bash
cd product/frontend
corepack enable
corepack prepare pnpm@9.10.0 --activate
pnpm install --frozen-lockfile
pnpm test
pnpm lint
pnpm build
```

生产清单：

```bash
kubectl kustomize --load-restrictor LoadRestrictionsNone \
  deploy/kubernetes/overlays/volcengine > /tmp/banxia-vke.yaml
test -s /tmp/banxia-vke.yaml
! rg -n 'REPLACE_WITH_|privileged: true|name: (kafka|minio)' /tmp/banxia-vke.yaml
```

有目标集群权限时再执行：

```bash
kubectl apply --server-side --force-conflicts \
  --dry-run=server -f /tmp/banxia-vke.yaml
```

## 4. 风险矩阵

| 改动 | 最低验证 |
| --- | --- |
| Provider、字段、单位、日期 | Provider 契约、空数据、异常、真实受控烟测 |
| 策略、因子、回测 | 防未来函数、T+1、费用、滑点、涨跌停和固定样本 |
| Policy 门面 | 路径白名单、参数校验、权限、错误映射和 ASGI 调用 |
| 任务与 Worker | 认领、租约、幂等、取消、重启和终态 |
| Compute | 协议、超限、超时、并发、无密钥和禁止出网 |
| 数据发布 | 临时结果、完整性、原子发布、generation 和缓存失效 |
| 前端 | 加载/空态/错误态、认证、九项路由、窄屏和构建 |
| 部署清单 | Kustomize、digest、Secret、NetworkPolicy 和非 root |
| 备份或迁移 | 隔离恢复、数据核对和回滚演练 |

## 5. 金融正确性

- 交易日和北京时间不能依赖服务器本地时区。
- 历史数据只能在当时可见时间后参与计算。
- 日线信号不能使用同日未知收盘价成交。
- 分钟回测缺少完整分钟数据时必须拒绝伪精确结果。
- 比例、百分数、金额、股数和手数在 Provider 边界显式转换。
- 原始价格、复权价格和涨跌停判断价格不能混用。
- Provider 缺能力、空结果或失败时不得静默跨源补齐。

## 6. 发布候选验收

1. 镜像固定到 digest，版本字段和源码提交一致。
2. 空库初始化、三库角色、schema v6 和管理员工作空间通过。
3. `/health/live` 与 `/health/ready` 符合预期。
4. 登录、退出、九项页面、刷新和 SSE 通过。
5. 次日计划、当日实盘、报告、任务和 Compute 至少各完成一条真实链路。
6. TOS 对象可写入、读回并校验 SHA-256。
7. SMTP 测试邮件在真实收件箱到达。
8. Market Worker 完成有数据交易日采集，归档 Manifest 可复核。
9. PostgreSQL、ClickHouse、TOS、NAS 和 WAL 完成隔离恢复演练。
10. 容量测试记录延迟、错误率、队列年龄和资源上限。

当前 `v0.5.1-rc.1` 只完成第 1～4 项及部分 Compute 验证，因此不得标记为稳定 Release。

## 7. 缺陷放行

| 级别 | 定义 | 候选处理 |
| --- | --- | --- |
| P0 | 数据错误、越权、凭据泄露、不可恢复损坏 | 阻断 |
| P1 | 核心流程不可用、错误交易口径、任务无法恢复 | 阻断 |
| P2 | 有明确规避方式的次要功能问题 | 记录负责人和期限后评估 |
| P3 | 不影响正确性的体验问题 | 可进入后续迭代 |

测试记录至少包含 commit、镜像 digest、环境、命令、结果、跳过项、失败项和证据位置。
