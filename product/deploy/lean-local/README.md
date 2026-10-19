# 精简架构本地集成环境

Compose 项目固定为 `policy-lean-test`，Web 默认入口 `http://127.0.0.1:3061`。
它使用自己的 PostgreSQL、Redis、ClickHouse、对象目录和 WAL，不复用旧本地环境的数据库。
公共行情从一个不可变发布版本校验并复制；平台配置、用户和业务数据重新初始化。
行情与平台配置最终保存在独立 Docker 数据卷。macOS 的宿主机文件挂载存在明显的
Parquet 元数据读取开销，不能把首次导入目录当作运行时存储。

## 准备

在仓库根目录执行，`--public-release` 指向已发布的公共行情目录，不能指向包含凭据或
用户文件的可写平台数据目录：

```bash
python3 product/deploy/lean-local/prepare.py \
  --public-release /absolute/path/to/public/current \
  --state-dir "$PWD/.local/lean-test"
```

准备时会检查 Web 端口；端口被占用时使用 `--port` 选择其他入口。`3051` 在当前
机器上属于旧版前端，不能通过该端口判断新版是否部署成功。

凭据保存在被 Git 和 Docker 构建上下文排除的 `.local/lean-test/*.env`，权限为 `0600`。
已经完成的目录直接复用，不重新生成密码。文件复制中断且尚未生成凭据时，可使用
`--resume` 继续校验和复制。

先按生产 Dockerfile 构建应用镜像，或在本地依赖未改变时使用增量构建：

- 基础镜像中的 `product/backend/uv.lock`、`pyproject.toml` 和两个 native 源码目录必须
  与当前版本一致；有变化时使用完整生产构建。
- `Runtime.Dockerfile` 先验证基础镜像清单，再替换源码、重新安装 Policy、复制已验证的
  Vite 产物，最后重建并校验清单。不能使用其他提交的前端产物。
- Runtime 和 Compute 分别以 `lss-product-runtime:v0.5.1`、
  `lss-product-compute:v0.5.1` 为基底，后者保留完整回测依赖。

```bash
docker build -f product/deploy/lean-local/Runtime.Dockerfile \
  --build-context frontend-dist=product/frontend/dist \
  --build-arg BASE_IMAGE=lss-product-runtime:v0.5.1 \
  --build-arg SOURCE_COMMIT="$(git rev-parse HEAD)" \
  -t lss-product-runtime:lean-cleanup .
docker build -f product/deploy/lean-local/Runtime.Dockerfile \
  --build-context frontend-dist=product/frontend/dist \
  --build-arg BASE_IMAGE=lss-product-compute:v0.5.1 \
  --build-arg SOURCE_COMMIT="$(git rev-parse HEAD)" \
  -t lss-product-compute:lean-cleanup .
docker build -t banxia-strategy:lean-cleanup .
docker build -f product/deploy/lean-local/S3.Dockerfile \
  -t policy-s3-test:3.7.1 product/deploy/lean-local
```

## 启动与检查

构建完成后，首次启动推荐流式导入，再核对清单中全部公开文件的校验和
（本次验收版本包含 1,510 个文件，具体数量随发布版本变化）。
`seed.py` 仅用于初次导入，已初始化的环境直接启动，不覆盖运行中的数据：

```bash
python3 product/deploy/lean-local/seed.py --state-dir .local/lean-test
docker compose --env-file .local/lean-test/compose.env \
  -f product/deploy/lean-local/compose.yml config --quiet
docker compose --env-file .local/lean-test/compose.env \
  -f product/deploy/lean-local/compose.yml up -d --no-build
docker compose --env-file .local/lean-test/compose.env \
  -f product/deploy/lean-local/compose.yml ps
curl --fail http://127.0.0.1:3061/health/live
curl --fail http://127.0.0.1:3061/health/ready
python3 product/deploy/lean-local/smoke.py \
  --output .local/lean-test/smoke.json
```

`market-initialize` 在平台启动前执行一次沪深市场范围迁移，成功后由迁移标记保证
幂等；完整历史数据的首次扫描可能较慢。初始化账户与同步策略的任务另行执行，
并设置可写的 Numba 缓存目录，日志中的策略数应为 22。

初始化使用现有 LSS 离线管理员引导，首次账户为 `admin` / `Admin123`。
`bootstrap.py` 使用生产租户初始化函数建立 `lean-test`，仅在管理员未选择租户时
设置其默认租户。重复执行不重置账户密码，也不覆盖已有租户选择。
`calendar-initialize` 从 rustdx 同步交易日历，避免空数据库把所有日期判断为休市。
API 的 readiness 同时检查双库、公共行情、平台连接、指标、Compute 和四类 Worker 心跳。
完整验收还需要验证业务请求、任务结果、SSE 和持久化，单独 HTTP 200 不代表验收完成。

`smoke.py` 检查同源登录、九个业务菜单、Policy 报告与监控、SSE 心跳和票据单次使用。
首次报告完成前，监控可能返回 `active plan is unavailable`，此时检查会明确失败。
增加 `--exercise-date YYYY-MM-DD` 可提交两只股票的小范围回测及指定已收盘交易日的
报告刷新，轮询并检查任务终态；该选项会写入本测试环境的任务和结果。
首次全市场采集可能超过脚本的三分钟等待上限，超时后应按输出的任务 ID 查询，
不要反复提交相同报告。此脚本不发送邮件。

停止时使用同一 Compose 文件执行 `stop`。保留卷和 `.local/lean-test` 可恢复运行；
不要使用 `down -v` 清除验证数据。
`.local/lean-test` 中的行情是初始导入副本；运行期间新增行情和平台配置位于
`policy-lean-test_public-market`、`policy-lean-test_platform-data`、
`policy-lean-test_platform-config` 数据卷，备份时必须包含这些卷。

## 与 VKE 的差异

- Web 单副本，未配置公网 ALB、TLS 或 HPA。
- 公共行情使用本机副本，数据库也为本机独立实例。
- 旧 MinIO 社区镜像和二进制制品已不可下载；本环境使用可落盘的 S3rver 3.7.1
  验证 S3 读写，固定测试凭据为 `S3RVER` / `S3RVER`。不代表 TOS 的权限、
  生命周期、故障恢复或吞吐验证通过。生产仍使用 TOS。
- Compute 无业务凭据、非 root、根文件系统及行情只读。内部网络没有公网路由；
  API/Task Worker 同网段仍可达，完整出站拒绝由 VKE NetworkPolicy 验证。
- 未配置 SMTP，不会发出实际邮件。
