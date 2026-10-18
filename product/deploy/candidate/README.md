# 云端候选部署

> 历史方案：`v0.5.0` 起生产部署已迁移到 VKE，当前清单见
> [`../kubernetes/README.md`](../kubernetes/README.md)。本目录仅保留独立主机回滚与
> 隔离验证能力，不再代表生产拓扑。

该目录把完整 TSP 产品部署到一台独立云主机，作为 `candidate.shanao.asia` 的验收环境。
它不会修改现有 `shanao.asia`、Policy VKE 工作负载或生产数据库。

完整产品的 Compute Broker 需要在宿主机创建无网络、无凭据的临时计算容器。VKE 节点
使用 containerd，不能按当前安全模型挂载 Docker socket；在实现 Kubernetes 原生
Compute Runner 前，本独立主机方案仍使用受限宿主 Broker。生产 VKE 不挂载宿主
Docker socket，而使用 [`../kubernetes`](../kubernetes/README.md) 定义的 Pod 内 DinD
受控例外。

## 资源与边界

- 推荐独立 Linux AMD64 云主机：32 vCPU、96 GiB 内存、500 GiB 独立数据盘。
- Docker Engine 27+、Compose 2.24+；主机只开放 SSH 和候选入口端口。
- PostgreSQL、行情目录、公共版本、平台配置和 Broker socket 均使用候选专属路径。
- ALB 终止公网 TLS，只把候选域名转发到候选主机端口。
- Policy 继续由现有 VKE 提供，通过 TSP 服务端白名单桥接；Token 不下发到浏览器。
- `candidate.shanao.asia` 当前没有 DNS 记录，创建记录前不能进行公网 Web 验收。

## 构建镜像

从仓库根目录构建三个同源镜像，并推送到私有镜像仓库：

```bash
RUNTIME=registry.example.com/tsp/runtime:candidate
COMPUTE=registry.example.com/tsp/compute:candidate
BROKER=registry.example.com/tsp/broker:candidate

docker build -f product/Dockerfile \
  --build-arg BACKEND_EXTRAS=cell \
  --build-arg SOURCE_COMMIT="$(git rev-parse HEAD)" \
  -t "$RUNTIME" .
docker build -f product/deploy/full/Compute.Dockerfile \
  --build-arg RUNTIME_IMAGE="$RUNTIME" \
  --build-arg SOURCE_COMMIT="$(git rev-parse HEAD)" \
  -t "$COMPUTE" .
docker build -f product/deploy/full/Broker.Dockerfile \
  --build-arg RUNTIME_IMAGE="$RUNTIME" \
  -t "$BROKER" .
```

推送后把 `candidate.env` 中所有镜像改为 Registry 返回的不可变 digest。不能用浮动 tag
作为上线证据。

## 私密配置

1. 将本目录四个 `*.env.example` 复制到 `/etc/tsp-candidate/`，移除 `.example`。
2. 使用 `openssl rand -hex 32` 分别生成数据库密码、市场 Token、平台管理 Token、
   流票据密钥和 Policy Token；各用途不得复用。
3. `runtime.env` 与 `platform.env` 中两个内部 Token 必须逐项一致。
4. 配置真实 `AUTH_SMTP_*`，否则注册和找回密码不能验收。
5. 所有私密环境文件设为 `root:root`、权限 `0600`，不得提交 Git。

`candidate.env.example` 只包含镜像、路径和资源参数，可作为非私密模板。实际部署文件
仍建议设为 `0600`，避免泄露内部拓扑。

## 初始化

```bash
install -d -m 0750 -o 10001 -g 10001 \
  /var/lib/tsp-candidate/platform \
  /var/lib/tsp-candidate/platform-config \
  /var/lib/tsp-candidate/public \
  /run/tsp-candidate

COMPOSE="docker compose --env-file /etc/tsp-candidate/candidate.env \
  -p tsp-product-candidate \
  -f product/deploy/full/compose.yml \
  -f product/deploy/candidate/compose.yml"

$COMPOSE config --quiet
$COMPOSE up -d postgres
$COMPOSE --profile init run --rm initialize
$COMPOSE up -d platform broker
```

等待 `platform` 生成首个完整 `/var/lib/tsp-candidate/public/current` 后再启动其余角色：

```bash
$COMPOSE up -d gateway research realtime notifications scheduled api edge
$COMPOSE ps
curl -fsS http://127.0.0.1:38080/health/ready
```

初始化只在空库创建 `admin/Admin123`。入口保持回环绑定，通过 SSH 隧道首次登录，立即
修改管理员密码，并在设置中配置独立注册口令。确认 SMTP、注册和找回密码后，才把
`TSP_CANDIDATE_BIND_ADDRESS` 改为 ALB 私网可达地址；安全组只允许 ALB 访问该端口。

## 验收门槛

1. `/health/ready` 同时通过数据库、公共版本、平台、Broker 和四类 Worker 检查。
2. 注册、登录、退出、找回密码和管理员改密均使用真实 SMTP。
3. 首页、自选、策略、次日计划、当日实盘、复盘、回测、个股分析、设置九项可访问。
4. 两个独立租户验证自选、策略和任务数据隔离。
5. 次日计划与当日实盘只通过 `/api/policy/*` 访问，浏览器网络记录中无 Policy Token。
6. SSE 断线重连、报告刷新权限、下载和 390x844 移动视口通过。
7. PostgreSQL 双库备份、平台目录快照和恢复演练完成后，才允许讨论正式域名切流。

验收期间现有 Policy 入口保持运行。候选失败时停止本 Compose 项目并保留数据库与日志；
不要删除或覆盖现有 VKE 数据。
