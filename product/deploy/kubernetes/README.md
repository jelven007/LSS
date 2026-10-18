# TSP Web 产品 VKE 部署

本目录定义 `v0.5.1` 精简产品在火山引擎 VKE 的生产资源，并由根目录
`deploy/kubernetes/overlays/volcengine/` 统一渲染。公网入口继续使用
`https://shanao.asia`。Policy 作为内嵌 ASGI 模块提供次日计划和当日实盘，不再部署
独立 API 或网络 Bridge。

## 拓扑

- `postgres`：Policy、TSP 控制库和 TSP 业务库共享的 PostgreSQL 集群。
- `tsp-platform`：行情平台与公共只读版本发布，使用独立 NAS RWX 状态卷。
- `tsp-product`：统一 Web/API，HPA 2～4 副本滚动发布。
- `tsp-task-worker`：独立后台任务，1 副本 Recreate；API 扩容不复制任务进程。
- `report-scheduler`：根部署中的常驻 Policy 调度，处理刷新队列、历史生成和定时报告。
- `tsp-compute`：独立计算服务，无业务 Secret、禁止出网、只读公共行情。

## Secret

真实密钥不得提交仓库。以 [`secrets.example.yaml`](secrets.example.yaml) 为字段契约，
在集群中创建：

- `tsp-product-admin`：TSP 数据库运行密码、新旧数据库管理员 DSN。
- `tsp-product-secrets`：运行时双库 DSN、平台 Token、流票据密钥和 SMTP。
- `banxia-registry`：火山引擎 CR 拉取凭据。

SMTP 从原 `banxia-secrets` 映射，端口 `465` 使用 `ssl`，端口 `587` 使用
`starttls`。Policy 内嵌运行时复用 `banxia-runtime` 和 `banxia-secrets`，浏览器不可见
数据库或对象存储凭据。

## 发布

镜像标签：

```text
banxia-strategy:v0.5.1
banxia-strategy:web-v0.5.1
banxia-strategy:compute-v0.5.1
```

`rustdx` 使用 crates.io 固定版本，不需要 GitHub 凭据。构建上下文必须是仓库根目录：

```bash
docker buildx build -f product/Dockerfile \
  --build-arg SOURCE_COMMIT="$(git rev-parse HEAD)" .
```

推送后必须在 VKE overlay 中写入 Registry 返回的 digest，再渲染和执行服务端 dry-run：

```bash
kubectl kustomize --load-restrictor LoadRestrictionsNone \
  deploy/kubernetes/overlays/volcengine > /tmp/banxia-vke.yaml
! rg -n 'REPLACE_WITH_' /tmp/banxia-vke.yaml
kubectl apply --server-side --force-conflicts --dry-run=server -f /tmp/banxia-vke.yaml
```

升级已有集群时，先从完整渲染结果中只应用数据库 Bootstrap：

```bash
kubectl apply -f /tmp/banxia-vke.yaml -l app=tsp-database-bootstrap
kubectl -n banxia wait \
  --for=condition=complete job/tsp-database-bootstrap --timeout=15m
```

再按 [`migrate-legacy-postgres.yaml`](migrate-legacy-postgres.yaml) 迁移旧 TSP 双库，
完成数据核对后才能整包应用新清单。验证账户、租户和业务表后才能删除旧
`tsp-postgres` StatefulSet；旧 PVC 必须继续保留。
先暂停旧 `report-worker-1630/2330` 并等待存量任务结束，再启用报告调度。
清单删除不会自动删除集群旧对象，退役列表与回滚边界见
[精简记录](../../../docs/37-production-simplification.md)。

## 验收

```bash
kubectl -n banxia wait --for=condition=complete \
  job/banxia-schema-migration job/tsp-database-bootstrap \
  job/tsp-product-initialize --timeout=20m
kubectl -n banxia rollout status deployment/tsp-platform --timeout=20m
kubectl -n banxia rollout status deployment/tsp-compute --timeout=20m
kubectl -n banxia rollout status deployment/tsp-product --timeout=30m
kubectl -n banxia rollout status deployment/tsp-task-worker --timeout=20m
kubectl -n banxia rollout status deployment/report-scheduler --timeout=20m
kubectl -n banxia rollout status statefulset/market-collector --timeout=20m
curl -fsS https://shanao.asia/health/ready
```

`/health/ready` 必须同时通过双库、公共行情版本、平台连接、指标缓存、Compute 和
四类任务心跳。随后验证管理员登录、九项菜单、SMTP、内嵌 Policy 页面和 SSE。
Task Worker 启动检查数据库 Schema 与公共行情标记，不等待 API readiness，
避免与四类心跳检查互相等待。Compute 网络入口同时允许 API 健康探测和 Task Worker 计算。
