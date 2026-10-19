# 测试与质量策略

## 1. 质量目标

测试必须证明：

- 核心策略规则正确，时间边界明确。
- 重复、乱序、延迟和缺失数据不会产生错误入场确认。
- 服务重启、WAL/Outbox 重放和缓存丢失不会改变权威结果。
- 统一 API、Policy 门面、Compute 协议和前端契约兼容。
- 生产清单不包含已退出的组件或不安全运行方式。
- 备份、迁移、恢复、回滚和降级路径可以实际执行。

## 2. v0.5.1 自动化基线

当前候选的验证由自动化、制品、集群和公网四层组成。各组测试可能重叠，不能简单相加。
截至 `2026-10-19`：

| 范围 | 结果 |
| --- | --- |
| Policy | 394 项：369 通过、25 项按环境跳过 |
| LSS 精简架构定向 | Policy Facade、Task Worker、Compute、扩展与健康 46 项通过 |
| LSS 身份与工作空间 | 真实隔离 PostgreSQL 23 项通过 |
| Compute | 9 项通过，实际 Pod 策略描述成功 |
| LSS 前端 | 42 个测试文件、180 项通过 |
| 静态制品 | TypeScript、ESLint、Vite 构建和首页引用资源检查通过 |
| TOS/SMTP 新增源码 | 归档 8 项、配置安全 5 项、相关存储/邮件回归通过 |
| Kustomize | 可渲染、server dry-run 通过，无特权容器和已退出组件 |
| 公网 | HTTPS、登录、九项页面、SSE、桌面与 390px 窄屏通过 |
| 版本契约 | 根项目、后端、锁文件、前端和 `VERSION` 均为 `0.5.1` |

实际 TOS 写入、SMTP 收件箱、首个有数据交易日归档、真实有信号回测、容量和恢复演练
仍未通过，不能由上述结果推断。完整证据见
[安装记录](releases/2026-10-19-fresh-install-v0.5.1.md)和
[TOS/SMTP 验收](38-tos-smtp-acceptance.md)。

## 3. 本地命令

Policy：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
ruff check src tests
```

LSS 后端定向门禁：

```bash
cd product/backend
PYTHONPATH=. pytest -q \
  tests/test_policy_bridge.py \
  tests/test_extensions.py \
  tests/test_task_worker.py \
  tests/test_full_compute.py
```

前端：

```bash
cd product/frontend
pnpm test -- --run
pnpm exec tsc --noEmit
pnpm exec eslint .
pnpm build
```

生产清单：

```bash
kubectl kustomize --load-restrictor LoadRestrictionsNone \
  deploy/kubernetes/overlays/volcengine > /tmp/banxia-vke.yaml
```

## 4. 分层策略

| 层级 | 目标 | 时机 |
| --- | --- | --- |
| 静态检查 | 格式、类型、版本、依赖和配置 | 每次提交 |
| 单元测试 | 策略规则、状态机、转换器、Worker 和幂等键 | 每次提交 |
| 契约测试 | HTTP、SSE、Policy 门面、Compute、SQL 和对象配置 | 每次提交 |
| 组件测试 | PostgreSQL、Redis、ClickHouse、TOS | 每次合并 |
| 集成测试 | Market Worker 到权威存储、投影和 API | 每次合并 |
| 端到端测试 | 登录、九项菜单、报告、实盘、回测、SMTP | 发布候选 |
| 性能测试 | 1 秒采集、查询延迟、任务吞吐和资源消耗 | 发布候选 |
| 故障测试 | 租约切换、WAL/Outbox 重放、依赖中断 | 生产发布前 |
| 恢复演练 | PostgreSQL、ClickHouse、TOS、NAS 和 WAL | 定期执行 |

## 5. 核心单元测试

### 5.1 策略状态机

- 竞价、开盘、早盘、午休、午盘、收盘和非交易日。
- 开盘涨幅、09:59:59 与 10:00:00 等边界。
- 跌破昨收后的不可逆放弃。
- 昨收不一致、源时间未来、行情过期和缺失。
- 旧事件、乱序事件和重复事件。
- 不同策略、计划和版本的状态隔离。
- 特征日期、最大输入时间和质量状态准入。

### 5.2 Market Worker

- 事件先写 WAL，权威写入成功后才确认。
- ClickHouse 或 PostgreSQL 失败时 WAL 保留。
- 同一 `event_id` 重放不产生重复行情或决策。
- 同一行情对多个计划按 `plan_id + source_event_id` 独立处理。
- 特征先于报价状态判断合并，字段缺失不覆盖已有有效特征。
- Outbox 失败不回滚已提交的权威决策，并可继续重试。
- Redis 丢失不改变 PostgreSQL 权威状态。

### 5.3 Web 与多租户

- 登录、注册、找回密码、管理员和租户权限。
- 次日计划和当日实盘的默认空态及降级。
- `/api/policy/*` 参数校验、错误映射和同进程 ASGI 调用。
- 扩展资源在 FastAPI 停机时关闭。
- Task Worker 子进程异常触发容器失败。
- 数据库角色、RLS、任务认领和幂等。

### 5.4 Compute

- Unix Socket 兼容模式和生产 TCP 模式协议一致。
- 未知任务、超大输入、超大输出和追加输入被拒绝。
- 进度、心跳、结果、错误、取消和超时语义正确。
- 同一租户单任务和全局并发限制。
- 子进程只接收固定环境和只读公共行情路径。
- 生产清单不注入 Secret、不允许出网、非 root 且根文件系统只读。

## 6. 组件与集成测试

### PostgreSQL

- 三个数据库可从空集群初始化。
- LSS 两个运行角色不能跨库越权或执行 DDL。
- Schema 迁移幂等，失败不会留下半完成版本。
- Inbox、状态、事件和 Outbox 原子提交。
- 旧 LSS 双库迁移前后表数、行数和关键记录一致。

### ClickHouse

- 重复盘口最终只有一个逻辑结果。
- 分钟线按修订版本保留最新值。
- 日期和股票过滤触发分区裁剪。
- 备份可以恢复到隔离数据库并通过数量核对。

### Redis

- 旧事件不能覆盖新投影。
- TTL 正确。
- 清空后由 PostgreSQL 和 ClickHouse 重建。

### TOS

- 报告和原始文件写入后 SHA-256 一致。
- 重复任务不生成冲突对象。
- Manifest 可定位完整输入和产物。
- 保留数据升级时，从旧对象存储迁移后的对象数、总大小和抽样哈希一致。
- 日终五表归档覆盖去重、空值/精度、重复执行内容寻址、对象损坏、导出截断及源修订；
  全空交易日失败，源数据改变时不发布 Manifest。
- 配置工具只做 Secret merge patch，所有输入验证前不写集群；失败不回显密钥。
- SMTP 单元回归与真实收件箱确认分别记录，SMTP 接受请求不能代替邮件到达。

2026-10-19 的源码回归、线上只读结果和真实 TOS/SMTP 待验收项见
[TOS、SMTP 配置与归档验收](38-tos-smtp-acceptance.md)。

### 完整链路

| 编号 | 场景 | 期望 |
| --- | --- | --- |
| IT-001 | Market Worker 处理盘口 | ClickHouse、Redis 和策略状态可见同一事件 |
| IT-002 | 处理过程中 ClickHouse 失败 | WAL 保留，恢复后重放 |
| IT-003 | PostgreSQL 状态事务失败 | 不产生仅存在于 Redis 的新决策 |
| IT-004 | 状态提交后 Redis 决策投影失败 | Outbox 保留并最终重试成功 |
| IT-005 | 重放同一行情 | 不产生重复状态迁移 |
| IT-006 | Redis 清空 | 投影重建且 API 恢复 |
| IT-007 | 主 Market Worker 退出 | 备用实例获得租约，旧 WAL 卷保留 |
| IT-008 | Compute 断网或重启 | API 返回受控错误，任务不泄露凭据 |
| IT-009 | Policy 依赖故障 | 页面保留结构化空态，不触发全局登出 |
| IT-010 | 同一日报重复执行 | 文件、对象和数据库保持幂等 |

## 7. 端到端验收

发布候选至少验证：

1. HTTPS、健康检查、注册、登录、退出和找回密码。
2. 首页、自选、策略、次日计划、当日实盘、复盘、回测、个股分析和设置。
3. 桌面侧栏展开/收缩与 `390x844` 移动布局。
4. Policy 报告查询、异步刷新、监控快照和事件流。
5. Market Worker 的租约、采集、WAL、决策与告警。
6. Task Worker 四类任务均有心跳并能处理一条真实任务。
7. Compute 的回测、进度、取消和无出网验证。
8. SMTP 注册验证码和找回密码。
9. TOS 报告下载与内容哈希。
10. PostgreSQL、ClickHouse、TOS、NAS 和 Market Worker WAL 恢复演练。

## 8. 性能与稳定性

首期边界为不超过 200 只动态监控股票。候选环境需要记录：

- 采集周期、端到端 P50/P95/P99 和丢失率。
- WAL/Outbox 最老记录年龄和恢复耗时。
- PostgreSQL 事务延迟、连接池占用和锁等待。
- ClickHouse 写入批次、Parts 数量和查询延迟。
- Redis 命中率、内存和重建时间。
- API P95/P99、SSE 连接数和错误率。
- Task Worker 队列年龄、Compute 并发、429 和任务耗时。

持续 30 分钟压测不允许出现未解释的进程退出、WAL 单调增长、Outbox 停滞、数据库连接耗尽
或页面显示过期行情为有效建议。

## 9. 生产清单门禁

渲染结果必须满足：

- 仅有统一 `lss-product` 公网入口。
- 存在 `market-worker`、`task-worker` 和 `lss-compute`。
- 不包含 Kafka、Flink、MinIO、独立 Policy API、独立 Gateway 或旧 LSS PostgreSQL。
- 不包含 `privileged: true` 或宿主机 Docker Socket。
- Compute 无 Secret、禁止出网、只读根文件系统。
- 所有镜像无占位符并固定 Registry Digest。
- Secret 示例值未进入发布清单。

## 10. 兼容回归

Kafka/Flink 适配器、旧 Worker 入口和 Compose 链路已退役。历史回归与回滚使用对应
Git 提交和旧镜像；不能替代 Market Worker、共享 PostgreSQL、TOS 和 Compute TCP 验收。
全新安装必须从空库、空 NAS 和新凭据验证，不使用恢复旧数据代替首次初始化。

## 11. 首次启动与静态制品

- 首次全量同步持有独占计算槽时，股票/ETF/指数 `refresh=False` 快照读取应立即
  返回现有缓存或真实空态，不能进入同步历史计算。板块和异动仅在股票快照就绪时计算。
- 回归覆盖计算槽被占用、默认懒加载保留、已有缓存读取和全快照冷启动。
- 文件 Manifest 校验之外，执行 `product/scripts/check_static.py`，核对首页引用的
  JavaScript、CSS 和图标都存在；公网必须实际请求这些资源，首页 HTTP 200 不够。
- 数据初始化 Job 在非 root 环境中使用 `/tmp` HOME 与 Numba 缓存，确认内置策略
  目录初始化数量；不能只按 Job Complete 判断策略目录已正确创建。
- `test_full_identity.py` 使用真实隔离 PostgreSQL 验证管理员工作空间幂等初始化和
  向导跨 API 实例持久化；`onboarding-api.test.ts` 验证平台设置合并账户标记。
- 公网用新浏览器会话完成向导后刷新九项页面，在桌面与 390px 窄屏确认真实业务标题，
  不将向导标题当作页面通过。SSE 验证真实租户授权、200 与首帧。
