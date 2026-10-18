# TSP Web 产品 VKE 部署

本目录定义 `v0.5.0` 整合产品在火山引擎 VKE 的生产资源，并由根目录
`deploy/kubernetes/overlays/volcengine/` 统一渲染。公网入口继续使用
`https://shanao.asia`，Policy API 保留为集群内服务，通过服务端桥接提供次日计划和
当日实盘。

## 拓扑

- `tsp-postgres`：独立控制库和业务库，EBS `Retain` 持久化。
- `tsp-platform`：行情平台与公共只读版本发布，使用独立 NAS RWX 状态卷。
- `tsp-product`：API、事件 Gateway、研究、实时、通知、定时 Worker 和 Compute
  Broker 的单 Pod 生产单元。
- `dind`：Pod 内隔离 Docker daemon，只运行固定 digest 的 Compute 镜像；不挂载节点
  Docker socket，也不向计算容器注入 Kubernetes Token 或业务密钥。

DinD 容器需要 `privileged`，这是当前 Compute Broker 尚未实现 Kubernetes 原生 Runner
前的受控例外。它只能访问 Pod 内临时 Docker 数据目录和只读公共行情卷。升级为原生
Runner 后应删除此例外。

## Secret

真实密钥不得提交仓库。以 [`secrets.example.yaml`](secrets.example.yaml) 为字段契约，
在集群中创建：

- `tsp-product-admin`：仅 PostgreSQL 和初始化 Job 使用的 owner 密码、管理员 DSN。
- `tsp-product-secrets`：运行时双库 DSN、平台 Token、流票据密钥、SMTP 和 Policy
  服务端 Token。
- `banxia-registry`：已有火山引擎 CR 拉取凭据，同时只读挂入 Broker 用于拉取固定
  Compute 镜像。

SMTP 从原 `banxia-secrets` 映射，端口 `465` 使用 `ssl`，端口 `587` 使用
`starttls`。Policy Token 使用原 `BANXIA_API_TOKEN`，浏览器不可见。

## 发布

镜像标签：

```text
banxia-strategy:v0.5.0
banxia-strategy:web-v0.5.0
banxia-strategy:broker-v0.5.0
banxia-strategy:compute-v0.5.0
docker:27-dind
```

`rustdx` 源依赖需要 GitHub 读取权限。构建时通过 BuildKit Secret 传入，禁止使用
`ARG`、`ENV` 或复制凭据文件：

```bash
GITHUB_TOKEN="$(security find-internet-password -w -s github.com)" \
docker buildx build --secret id=github_token,env=GITHUB_TOKEN ...
```

推送后必须在 VKE overlay 中写入 Registry 返回的 digest，再渲染和执行服务端 dry-run：

```bash
kubectl kustomize --load-restrictor LoadRestrictionsNone \
  deploy/kubernetes/overlays/volcengine > /tmp/banxia-vke.yaml
! rg -n 'REPLACE_WITH_' /tmp/banxia-vke.yaml
kubectl apply --server-side --force-conflicts --dry-run=server -f /tmp/banxia-vke.yaml
```

部署前保留 `banxia-secrets`、`banxia-registry` 和所有 PVC，仅删除业务
Deployment/StatefulSet/Job/CronJob/Service；系统命名空间、IngressClass、ALBInstance、
PV 和 StorageClass 不在清理范围。数据服务重建并就绪后，依次等待 Policy 迁移、
TSP 初始化、平台和产品 Pod。

## 验收

```bash
kubectl -n banxia wait --for=condition=complete \
  job/banxia-schema-migration job/tsp-product-initialize --timeout=20m
kubectl -n banxia rollout status deployment/tsp-platform --timeout=20m
kubectl -n banxia rollout status deployment/tsp-product --timeout=30m
curl -fsS https://shanao.asia/health/ready
```

`/health/ready` 必须同时通过双库、公共行情版本、平台连接、指标缓存、Compute Broker 和
四类 Worker。随后验证管理员登录、九项菜单、SMTP、Policy 桥接和 SSE。
