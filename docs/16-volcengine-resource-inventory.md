# 火山引擎生产资源清单

更新时间：2026-10-19。应用版本：`0.5.1`。生产入口：<https://shanao.asia>。

本文记录当前仓库清单与本次生产验收确认的逻辑资源。云账号中的实例数量、资源 ID、
余额和计费随时间变化，必须在执行变更前从控制台或 API 实时查询，本文不再保存容易
失效的账号级快照。

## 1. 当前边界

| 项目 | 当前值 |
| --- | --- |
| VKE 集群 | `cdaus2p98m08ce3b12nn0` |
| 命名空间 | `banxia` |
| 公网入口 | ALB HTTPS 443 / `shanao.asia` |
| 主线 | `132db778c03b5e4d724afd510c63d269c7764211` |
| 候选标签 | `v0.5.1-rc.1` |
| 生产清单 | `deploy/kubernetes/overlays/volcengine/` |
| TSP 子清单 | `product/deploy/kubernetes/` |

Kafka、Flink、自建 MinIO、独立 Policy API、独立 SSE Gateway、独立 TSP PostgreSQL、
Policy 报告 NAS 和 Pod 内 DinD 不属于当前生产目标。历史资源若仍在云账号中，先核对
挂载、快照、备份和引用，再按变更流程清理，不能仅凭名称删除。

## 2. 计算资源

| 工作负载 | 类型 | 副本/调度 | 职责 |
| --- | --- | --- | --- |
| `tsp-product` | Deployment + HPA | 2～4 | React、统一 API、SSE、RBAC、内嵌 Policy |
| `tsp-task-worker` | Deployment | 1 / Recreate | 研究、监控、模拟盘、通知和计划任务 |
| `report-scheduler` | Deployment | 1 / Recreate | Policy 报告队列、定时生成和补跑 |
| `tsp-platform` | Deployment | 1 | 公共行情版本和平台服务 |
| `tsp-compute` | Deployment | 1 | 隔离回测与研究计算 |
| `market-collector` | StatefulSet | 2 / 租约单活 | rustdx 采集、SQLite WAL、特征和投影 |
| `market-reference-sync` | Deployment | 1 | 证券、板块、行业和交易日参考数据 |
| `intraday-monitor` | Deployment | 1 | 盘中指标与告警采集 |
| `market-archive` | CronJob | 周二至周六 00:10，暂停 | ClickHouse 五表 TOS 日终归档 |

`market-archive` 候选镜像与 CronJob 已部署，但在 TOS 凭据和首个有数据交易日验收前
保持 `suspend: true`。

## 3. 数据资源

| 数据域 | 当前资源 | 持久化边界 |
| --- | --- | --- |
| 事务与身份 | PostgreSQL StatefulSet | `banxia`、`tsp_control`、`tsp_data_production` 三库隔离 |
| 行情与特征 | ClickHouse StatefulSet | EBS PVC；五类行情表按交易日归档 |
| 实时投影 | Redis StatefulSet | 可从 PostgreSQL/ClickHouse 重建 |
| Market Worker 缓冲 | `market-collector` PVC | 每个 Pod 独立 SQLite WAL |
| 公共行情版本 | `tsp-product-state` NAS RWX | Platform 写入，API/Compute 只读消费 |
| 报告与原始归档 | 火山引擎 TOS | `strategy-reports`、`market-raw` 两个私有 Bucket |

PostgreSQL 三库共用集群但不共用数据库和运行角色。管理员 DSN 只供初始化与迁移 Job；
Compute 不获得数据库、SMTP、TOS 或平台管理凭据。

TOS 是生产对象存储。当前 AK/SK 尚未配置，实际对象写入、读回和 Manifest 完整性
尚未签收。TSP 公共行情 NAS 仍是运行依赖，不得当作已迁移到 TOS。

## 4. 网络与安全

- 公网流量经 ALB 终止 TLS，只转发到 `tsp-product`。
- PostgreSQL、ClickHouse、Redis、Platform 和 Compute 只暴露集群内 Service。
- Market Worker 和参考同步需要访问 rustdx；报告和归档需要访问 TOS。
- Compute NetworkPolicy 禁止出站，只允许 API/Task Worker 访问其服务端口。
- 应用容器非 root，不挂载宿主 Docker Socket，不使用 `privileged: true`。
- Registry 拉取凭据、数据库凭据、TOS AK/SK 和 SMTP 授权码保存在 Kubernetes Secret，
  不进入 ConfigMap、镜像、日志或文档。

## 5. 镜像与配置

本次已验收制品：

| 组件 | 标签 | Digest |
| --- | --- | --- |
| Policy / Market Worker | `v0.5.1` | `sha256:3f569013ce24412457b1d9c118921cd0b45157b09984b3e39775681d7e0bc789` |
| Web Runtime | `web-v0.5.1` | `sha256:cf1a2864e39678b4a5ee5cda7735494bf85771cff7a22a49d6ea4993d3a8d995` |
| Compute | `compute-v0.5.1` | `sha256:9bf4fce66903f48286812aa84dfdbcef3afcb9a7f68cc3e964e41ec5e47070f0` |
| 行情归档候选 | `policy-v0.5.1-archive-9db47bc` | `sha256:873d58fac341555873d5928bd49032f5d977b88a8bfdd40d14906de93302d57a` |

生产发布必须继续固定 digest。仓库中的 `REPLACE_WITH_*` 只允许存在于基础模板，
完整 overlay 渲染结果不得包含占位符。

## 6. 已验证状态

- 新 PostgreSQL、ClickHouse、Redis 和对应卷已创建并 Ready。
- PostgreSQL 三库、TSP 双运行角色、schema v6 和管理员工作空间初始化完成。
- Web/API 两副本、Platform、Task Worker、Report Scheduler、Compute 和
  Market Collector 已完成部署。
- HTTPS、登录、九项页面、SSE 和 `/health/ready` 通过。
- Compute 在实际 Pod 内完成策略描述。
- 行情归档模块在真实候选镜像中完成 SHA-256 烟测。

未完成：

- TOS AK/SK 配置及两个 Bucket 的对象实写/读回。
- SMTP 真实投递、注册和找回密码。
- 首个有数据交易日归档。
- 完整行情覆盖、真实有信号回测、容量和恢复演练。

## 7. 现场核对

```bash
kubectl -n banxia get deploy,statefulset,cronjob,pod,pvc
kubectl -n banxia get ingress,service,networkpolicy
kubectl -n banxia get cronjob market-archive \
  -o custom-columns=NAME:.metadata.name,SUSPEND:.spec.suspend,LAST:.status.lastScheduleTime
kubectl -n banxia get pods \
  -o custom-columns=NAME:.metadata.name,IMAGE:.spec.containers[*].image
curl -fsS https://shanao.asia/health/ready
```

渲染发布清单：

```bash
kubectl kustomize --load-restrictor LoadRestrictionsNone \
  deploy/kubernetes/overlays/volcengine > /tmp/banxia-vke.yaml
! rg -n 'REPLACE_WITH_|privileged: true|name: (kafka|minio)' /tmp/banxia-vke.yaml
```

云资源盘点还应从火山控制台核对 VKE 节点、EBS/NAS、ALB、EIP/NAT、CR、证书和
Prometheus 的实际 ID、容量、计费与到期时间。账号总资源不能直接归因到本项目。

## 8. 备份与清理

生产恢复集必须覆盖 PostgreSQL 三库、ClickHouse、TOS 对象和 Manifest、公共行情 NAS、
Market Worker WAL/PVC、镜像 digest 与受控配置。Redis 不是唯一恢复源。

删除旧资源前逐项确认：

1. 当前 Pod、PV/PVC、Ingress、Service 和 Secret 无引用。
2. 数据已经迁移或明确授权丢弃。
3. Retain 卷和云盘快照策略已处理。
4. 回滚窗口已经结束。
5. 删除动作、执行人、时间和结果进入运维记录。

当前没有完成整套生产恢复演练，也没有承诺 RTO/RPO。详细验收见
[全新安装记录](releases/2026-10-19-fresh-install-v0.5.1.md)和
[TOS/SMTP 验收](38-tos-smtp-acceptance.md)。
