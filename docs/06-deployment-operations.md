# 部署与运维方案

## 1. 生产基线

`v0.5.1` 使用火山引擎 VKE，生产入口为 <https://shanao.asia>。系统只提供行情研究、
策略筛选、盘中监控和报告，不连接券商账户，不自动下单。

权威架构见 [v0.5.1 精简生产架构](36-lean-architecture-v0.5.1.md)，部署清单见
[`deploy/kubernetes/`](../deploy/kubernetes/README.md)。

## 2. 工作负载

| 工作负载 | 副本 | 职责 |
| --- | ---: | --- |
| `tsp-product` | HPA 2～4 | React、统一 API、账户、多租户、SSE |
| `tsp-task-worker` | 1 | 独立监督研究、实时规则、通知与定时任务 |
| `report-scheduler` | 1 | Policy 页面刷新、历史生成、定时报告 |
| `tsp-platform` | 1 | 公共行情版本和平台配置 |
| `tsp-compute` | 1 | 隔离研究与回测计算 |
| `market-collector` | 2 | `market-worker`，通过 PostgreSQL 租约单活 |
| `market-reference-sync` | 1 | 证券、板块和参考数据 |
| 数据与模拟盘 CronJob | 按计划 | 行情历史、板块资金、模拟盘对账 |

生产清单不再部署独立 Policy API、SSE Gateway、Kafka、Flink、MinIO、独立 TSP
PostgreSQL或Pod内DinD。

## 3. 数据与存储

| 服务 | 责任 | 持久化 |
| --- | --- | --- |
| PostgreSQL | Policy、账户、租户、策略、状态、任务和审计 | EBS `Retain` |
| ClickHouse | 盘口、分钟线、特征和历史行情 | EBS `Retain` |
| Redis | 最新行情、决策和 SSE 投影 | AOF，可重建 |
| 火山引擎 TOS | 原始文件、报告和研究归档 | Bucket 版本控制 |
| TSP 公共状态 | 公共行情版本和平台文件 | NAS RWX，过渡保留 |
| Market Worker WAL | 尚未完成全链路持久化的事件 | 每 Pod 独立 EBS |

PostgreSQL 使用三个数据库：

- `banxia`：Policy 业务。
- `tsp_control`：身份、租户和路由。
- `tsp_data_production`：租户业务。

TSP 控制库和业务库必须使用不同运行角色，两个角色均禁止 `SUPERUSER`、
`BYPASSRLS`、`CREATEDB` 和角色继承。

## 4. 本次全新安装

2026-10-19 用户明确要求将线上业务容器环境作为新项目安装。本次不执行旧库备份、
旧 TSP 数据迁移或旧对象恢复，替代第 5 节的保留数据升级流程：

1. 构建最新主线对应的 `linux/amd64` 三个镜像，推送 CR 并固定 digest。
2. 停止旧工作负载和 CronJob，清空 `banxia` 应用数据卷及业务 NAS 目录，删除旧应用
   Secret；仅在已授权项目范围内操作。
3. 保留 VKE 系统、镜像拉取认证、域名证书和 ALB 安装通道；这些不承载旧业务数据。
4. 重新生成 PostgreSQL、ClickHouse、Redis、两个 TSP 运行角色及平台 Token。
   TOS AK/SK 需由云端授权提供，随机字符串不能代替云存储凭据。
5. 应用空库清单，完成 Policy Schema、TSP 双库角色、schema v6、首次管理员及其工作
   空间初始化。`bootstrap-full-workspace --cell-id production` 可幂等重跑，不覆盖
   密码和后续租户选择。
6. 验证公共行情发布标记、独立 Task Worker、Compute、统一 API、HTTPS 和登录。
   全新环境没有历史报告，页面必须展示真实空态。
7. SMTP 未配置时保持禁用；上线记录明确说明邮件注册及密码找回尚未验收。

TOS/SMTP 使用交互式 `scripts/configure_external_services.py` 更新指定 Secret 键，
密钥不回显，不导出原有 Secret。日终 `market-archive` CronJob 默认停用，待有效
TOS 凭据、含新入口的 Policy 镜像和首个有数据交易日完成验证后启用。
配置、对象读回及指定收件人验证命令见 [TOS/SMTP 验收](38-tos-smtp-acceptance.md)。

初始化 Job 的 `HOME` 与 `NUMBA_CACHE_DIR` 必须指向可写 `/tmp`，避免非 root 环境
中策略模块加载失败。首次行情同步可以继续在后台运行；公共快照只读取已有缓存，
数据未就绪时返回真实空态，不让 Web 启动排队等待全量同步。
制品完成文件 Manifest 校验后，还需验证首页引用的 JS/CSS/图标存在且公网可获取。

本次没有旧数据恢复点。应用回滚仅能使用与新建数据库兼容的制品，不应引用已删除的
旧 PVC 或未完成备份。正式业务运行后的备份策略仍按第 9 节执行。

## 4.1 保留数据升级前置

1. 镜像通过测试、依赖扫描和构建清单校验。
2. Policy、Web Runtime、Compute 镜像使用相同语义版本并固定 Registry digest。
3. TOS 已创建 `strategy-reports` 和 `market-raw` Bucket。
4. PostgreSQL、ClickHouse 和旧 TSP PostgreSQL 已完成逻辑备份。
5. 所有 PVC 的 StorageClass 回收策略为 `Retain`。
6. 暂停 CronJob，并确认没有正在运行的报告、回填或模拟盘任务。
7. 升级时先暂停并停用旧 `report-worker-1630/2330`，再启动常驻 `report-scheduler`。
   Kubernetes apply 不会自动删除已移出清单的旧对象；不能仅凭渲染结果认为退役完成。
   旧 API 侧车在滚动升级结束后必须全部退出，再核对 Task Worker 仅一组进程。

## 5. 保留数据升级的迁移流程

本节仅适用于需要保存业务数据的后续升级，不用于本次全新安装。

### 5.1 PostgreSQL

首次从 `v0.5.0` 升级：

1. 先按第 6 节生成 `/tmp/banxia-vke.yaml`，但不要整包应用。
2. 在 `tsp-product-admin` 中配置新旧四条管理员 DSN，并更新两个运行角色密码。
3. 只应用 `tsp-database-bootstrap`，在共享 PostgreSQL 中创建两个 TSP 数据库和角色：

```bash
kubectl apply -f /tmp/banxia-vke.yaml -l app=tsp-database-bootstrap
kubectl -n banxia wait \
  --for=condition=complete job/tsp-database-bootstrap --timeout=15m
```

4. 暂停旧 TSP 应用，确认旧数据库没有新写入。
5. 执行显式迁移：

```bash
kubectl apply -f product/deploy/kubernetes/migrate-legacy-postgres.yaml
kubectl -n banxia wait \
  --for=condition=complete job/tsp-legacy-postgres-migration --timeout=30m
```

6. 对比用户、租户、文档、任务、策略和审计表数量。
7. 整包应用 `/tmp/banxia-vke.yaml`，由 `tsp-product-initialize` 升级 Schema 并恢复服务。
8. 完成登录和业务抽样后，旧 `tsp-postgres` 只缩容，不删除 PVC。

### 5.2 对象存储

使用火山引擎数据迁移服务或 S3 兼容工具将 MinIO Bucket 迁移到 TOS。迁移完成后按对象数、
总字节数和 SHA-256 抽样校验，再更新：

- `BANXIA_OBJECT_ENDPOINT`
- `BANXIA_OBJECT_ACCESS_KEY`
- `BANXIA_OBJECT_SECRET_KEY`
- `BANXIA_OBJECT_REPORT_BUCKET`
- `BANXIA_OBJECT_MARKET_BUCKET`

上海地域 VKE 默认使用内网 S3 Endpoint
`tos-s3-cn-shanghai.ivolces.com`。

## 6. 发布

```bash
kubectl kustomize \
  --load-restrictor LoadRestrictionsNone \
  deploy/kubernetes/overlays/volcengine \
  > /tmp/banxia-vke.yaml

! rg -n 'REPLACE_WITH_' /tmp/banxia-vke.yaml
! rg -n 'privileged: true|name: kafka|name: minio|name: flink-' /tmp/banxia-vke.yaml
kubectl apply --server-side --force-conflicts --dry-run=server -f /tmp/banxia-vke.yaml
kubectl apply --server-side --force-conflicts -f /tmp/banxia-vke.yaml
```

等待初始化和发布：

```bash
kubectl -n banxia wait \
  --for=condition=complete job/banxia-schema-migration --timeout=15m
kubectl -n banxia wait \
  --for=condition=complete job/tsp-database-bootstrap --timeout=15m
kubectl -n banxia wait \
  --for=condition=complete job/tsp-product-initialize --timeout=20m
kubectl -n banxia rollout status deployment/tsp-platform --timeout=20m
kubectl -n banxia rollout status deployment/tsp-compute --timeout=20m
kubectl -n banxia rollout status deployment/tsp-product --timeout=30m
kubectl -n banxia rollout status deployment/tsp-task-worker --timeout=20m
kubectl -n banxia rollout status deployment/report-scheduler --timeout=20m
kubectl -n banxia rollout status statefulset/market-collector --timeout=20m
```

固定名称的 Job 需要重跑时，先删除对应 Job，再重新应用清单。

Kustomize 中的删除补丁只会把旧对象排除出新渲染结果，不会删除集群中已经存在的对象。
新链路完成切换验收后，应立即显式删除旧工作负载和 Service，避免继续写旧链路：

```bash
kubectl -n banxia delete deployment \
  api market-sink strategy-engine outbox-relay projection-worker \
  flink-jobmanager flink-taskmanager --ignore-not-found
kubectl -n banxia delete statefulset \
  kafka minio tsp-postgres --ignore-not-found
kubectl -n banxia delete service \
  api kafka minio tsp-postgres flink-jobmanager --ignore-not-found
kubectl -n banxia delete job \
  banxia-kafka-bootstrap banxia-minio-bootstrap flink-feature-job-v1 \
  --ignore-not-found
kubectl -n banxia delete hpa api --ignore-not-found
kubectl -n banxia delete pdb api strategy-engine --ignore-not-found
kubectl -n banxia delete networkpolicy api-ingress --ignore-not-found
```

上述命令不得扩展到 PVC、PV、Secret、StorageClass 或 ALB。旧数据卷和 Bucket 保留至少
五个交易日，只在备份、迁移核对和回滚观察期全部完成后单独审批回收。

## 7. 健康检查

| 服务 | 就绪条件 |
| --- | --- |
| `tsp-product` | 双库、公共行情、平台连接、Worker 心跳、Compute |
| `market-worker` | 进程心跳、PostgreSQL 租约、WAL、ClickHouse、Redis |
| `tsp-compute` | TCP 服务存活；Pod 无 Secret、无出网 |
| 报告 Worker | PostgreSQL、ClickHouse、TOS |

公网验收：

```bash
curl -fsS https://shanao.asia/health/ready
```

同时验证登录、九项菜单、次日计划、当日实盘、SSE、SMTP、回测和移动端布局。

## 8. 监控与告警

必须监控：

- Market Worker 主节点、心跳、采集成功时间、候选覆盖率和行情 P95 延迟。
- WAL 待处理数量、持久化失败数和最近错误。
- PostgreSQL 连接、事务延迟、Outbox 积压和最老任务年龄。
- ClickHouse 写入延迟和分区容量。
- Redis 命中率、内存和投影重建状态。
- TSP API P50/P95/P99、错误率、SSE 连接数。
- Task Worker 四类心跳。
- Compute 并发、超时、取消和资源使用。

P0 条件包括：交易时段行情停止、PostgreSQL 不可写、策略状态无法提交或过期行情仍参与判断。

## 9. 备份与恢复

- PostgreSQL：每日全量、持续 WAL 归档、保留 35 天、每月恢复演练。
- ClickHouse：每日增量到 TOS，每周抽样恢复交易日分区。
- TOS：开启版本控制；报告与审计 Bucket 开启对象锁或等价防篡改能力。
- Redis：不作为权威数据，不设置业务 RPO；故障后从 PostgreSQL 和 ClickHouse 重建。
- Market Worker WAL：保留旧主 EBS，确认全部事件处理后再回收。

目标：

| 场景 | RPO | RTO |
| --- | --- | --- |
| 单应用 Pod 故障 | 0 | 小于 60 秒 |
| Redis 故障 | 0 | 小于 10 分钟 |
| PostgreSQL 主节点故障 | 同步复制窗口内 | 小于 5 分钟 |
| 单可用区故障 | 小于 1 分钟 | 小于 15 分钟 |

## 10. 回滚

1. 保留旧镜像 digest、旧 TSP PostgreSQL PVC 和 MinIO 数据卷。
2. 新版本异常时回滚镜像和 Kustomize 提交，不反向覆盖数据库。
3. 若已切换 TSP 数据库，只允许从发布前逻辑备份恢复到隔离实例后再切换 DSN。
4. Market Worker 回滚到 Kafka/Flink 链路前，必须先停止新 Worker并核对WAL和Outbox。
5. 未完成数据校验前禁止删除任何旧 PVC、Bucket 或数据库。

## 11. 安全

- 外部只开放 ALB 443，数据库和内部 Service 不暴露公网。
- Cookie 使用 `HttpOnly + Secure + SameSite`，写操作校验 CSRF。
- Secret 不进入镜像、ConfigMap、日志或浏览器。
- Compute Pod 无业务 Secret、无出网、只读根文件系统和公共行情卷。
- 审计记录包含租户、用户、请求 ID、版本、变更前后值和时间。
- 行情超过时效阈值时必须 fail closed，禁止继续输出有效入场判断。
