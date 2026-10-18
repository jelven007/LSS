# Kubernetes 部署

`base/` 是云厂商无关的兼容基线，`overlays/volcengine/` 是火山引擎
VKE 生产部署。`v0.5.1` 的生产 overlay 使用 PostgreSQL、ClickHouse、Redis、
TOS 和带本地 WAL 的 Market Worker，不再部署 Kafka、Flink、MinIO 或独立 Policy API。

该 overlay 部署到集群 `cdaus2p98m08ce3b12nn0`，生产入口为
<https://shanao.asia>。架构见
[v0.5.1 精简生产架构](../../docs/36-lean-architecture-v0.5.1.md)。

VKE overlay 包含：

- 统一 Web/API、`task-worker`、`tsp-platform` 和隔离 `tsp-compute`。
- 带 SQLite WAL 的双副本 `market-worker`，通过 PostgreSQL 租约单活。
- 工作日 16:20 参考数据同步、16:30/23:30 报告、16:35 涨停历史和 16:50 模拟盘任务。
- 使用独立 RWO 云盘保存每个 `market-collector` 副本的本地 WAL。
- Policy、TSP 控制库和 TSP 业务库共享 PostgreSQL 集群并使用独立角色。
- PostgreSQL/ClickHouse 迁移、TSP 数据库初始化和策略目录初始化 Job。
- TOS 保存 Policy 报告和原始归档；TSP 公共行情目录暂保留一个 NAS RWX 卷。
- 公网 ALB 统一转发到 `tsp-product:80`，SSE 与普通 API 共用入口。
- PDB、HPA、跨可用区/节点软分散和默认拒绝入站的 NetworkPolicy。
- 可选的 ServiceMonitor/PodMonitor。

## 前置条件

1. VKE 集群已安装 NAS CSI、ALB Ingress Controller 和 metrics-server。
2. `ssl-redirect` 要求 ALB Ingress Controller `v0.35.0` 或更高版本。
3. VKE Worker 子网可访问 rustdx 行情节点和上海地域 TOS 内网 Endpoint。
4. 已创建 CR 镜像仓库，并推送 Policy、Web Runtime 和 Compute 镜像。
5. 已开通 ALB 服务授权，并准备证书中心证书。
6. TOS 已创建 `strategy-reports` 与 `market-raw` Bucket；TSP 公共行情仍需 NAS。

火山引擎 ESSD 云盘的最小容量为 10GiB，数据卷和 Collector WAL 均不得
低于此容量。

## 配置占位符

部署 VKE overlay 前必须替换 overlay 和 Secret 模板中的所有占位符：

```bash
rg -n 'REPLACE_WITH_' \
  deploy/kubernetes/overlays/volcengine \
  deploy/kubernetes/banxia-secrets.example.yaml
```

`base/` 中仍保留云厂商无关的占位符；VKE overlay 会覆盖这些值，不需要
直接修改 Base。

主要配置位置：

- `overlays/volcengine/kustomization.yaml`：CR 地址和 Git commit 镜像标签。
- `overlays/volcengine/data-services.yaml`：PostgreSQL、ClickHouse、Redis 及 EBS 容量。
- `overlays/volcengine/runtime-config.yaml`：集群内数据服务地址。
- `overlays/volcengine/storage.yaml`：EBS `Retain` 存储类。
- `overlays/volcengine/tsp-storage.yaml`：TSP 公共行情兼容 NAS。
- `overlays/volcengine/collector-statefulset.yaml`：WAL 使用的块存储类。
- `overlays/volcengine/ingress.yaml`：新建 ALB 的子网和证书中心证书 ID。
- `banxia-secrets.example.yaml`：数据库、Redis、TOS 及 CR 凭据。

`BANXIA_OBJECT_ENDPOINT` 不带 URL scheme，VKE 默认使用
`tos-s3-cn-shanghai.ivolces.com`。密码出现在 PostgreSQL DSN 或 Redis URL 中时必须
进行 URL 编码。

## Secret

示例文件不能原样部署，也不能提交替换后的真实密钥：

```bash
cp deploy/kubernetes/banxia-secrets.example.yaml \
  deploy/kubernetes/banxia-secrets.yaml
# 编辑 banxia-secrets.yaml，替换全部 REPLACE_WITH_* 值
kubectl create namespace banxia --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f deploy/kubernetes/banxia-secrets.yaml
```

## 渲染与部署

迁移 ConfigMap 直接引用仓库根目录的 SQL 文件，因此需要显式允许
Kustomize 读取 overlay 目录之外的文件：

```bash
kubectl kustomize \
  --load-restrictor LoadRestrictionsNone \
  deploy/kubernetes/overlays/volcengine \
  > /tmp/banxia-vke.yaml

! rg -n 'REPLACE_WITH_' /tmp/banxia-vke.yaml
kubectl apply --server-side --force-conflicts --dry-run=server -f /tmp/banxia-vke.yaml
kubectl apply --server-side --force-conflicts -f /tmp/banxia-vke.yaml
```

升级前先完成 PostgreSQL 与对象迁移，具体顺序见
[精简架构发布顺序](../../docs/36-lean-architecture-v0.5.1.md#7-发布顺序)。不要在迁移
校验前删除旧 `tsp-postgres` PVC。完整清单应用后等待数据库初始化，再检查工作负载：

```bash
kubectl -n banxia wait \
  --for=condition=complete job/banxia-schema-migration --timeout=15m
kubectl -n banxia wait \
  --for=condition=complete job/tsp-database-bootstrap --timeout=15m
kubectl -n banxia wait \
  --for=condition=complete job/tsp-product-initialize --timeout=20m
kubectl -n banxia wait \
  --for=condition=complete job/banxia-catalog-bootstrap --timeout=15m
kubectl -n banxia rollout status deployment/tsp-product --timeout=10m
kubectl -n banxia rollout status deployment/tsp-compute --timeout=10m
kubectl -n banxia rollout status statefulset/market-collector --timeout=10m
kubectl -n banxia get pods,pvc,ingress
```

固定名称的 Job 不会因再次 `apply` 自动重跑。发布了新迁移或需要重新执行
初始化时，先删除对应 Job，再重新应用渲染结果：

```bash
kubectl -n banxia delete job \
  banxia-schema-migration tsp-database-bootstrap \
  tsp-product-initialize banxia-catalog-bootstrap --ignore-not-found
kubectl apply --server-side --force-conflicts -f /tmp/banxia-vke.yaml
```

PostgreSQL Job 使用 `banxia.schema_migration` 记录已完成脚本；ClickHouse
脚本使用 `IF NOT EXISTS`，可重复执行。

当前 overlay 的 Policy PostgreSQL migration ConfigMap 包含 `001` 至 `025` 和 `028`；
`026/027` 仍仅用于开发与隔离测试，不升级生产运行策略。TSP 双库迁移由
`tsp-product-initialize` 执行到 schema `v6`。

## DNS 与入口

ALB 就绪后查询公网地址，并把 `shanao.asia` 的 A 记录指向该地址：

```bash
kubectl -n banxia get albinstance banxia-alb
kubectl -n banxia get ingress banxia-api
```

入口由 ALB 终止 TLS。TSP Web、普通 API、Policy Facade 和事件流统一使用
`tsp-product:80`。域名只应解析到当前 ALB 公网地址。

## 全量重建

全量重建只替换应用 Deployment、Market Worker StatefulSet、Job 和 CronJob。
PostgreSQL、ClickHouse、Redis、PVC/PV、Secret、StorageClass、IngressClass 和
ALBInstance 不得批量删除。完整迁移、发布和回滚顺序见
[部署与运维](../../docs/06-deployment-operations.md#5-数据迁移)。

## 可选监控

只有集群已安装 Prometheus Operator CRD 时才应用：

```bash
kubectl get crd servicemonitors.monitoring.coreos.com
kubectl get crd podmonitors.monitoring.coreos.com
kubectl label namespace <PROMETHEUS_NAMESPACE> \
  banxia.io/metrics-access=true --overwrite
kubectl apply -f \
  deploy/kubernetes/overlays/volcengine/servicemonitor.yaml
```

每次生产发布后都应验证桌面端和移动端页面、rustdx 出网、数据库备份状态、
collector 主备、Pod 驱逐和 ALB 健康检查。
