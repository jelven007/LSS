# 发布与上线运营

本文定义 LSS 从候选提交、构建、部署到观察和回滚的流程。测试入口见
[测试与验收](./testing.md)，生产操作见[运维手册](./operations.md)。

当前 `v0.5.1` 已部署候选，源码归档标签为 `v0.5.1-rc.1`。HTTPS、登录、九项页面
和 SSE 已通过；TOS、SMTP、首个有数据归档、真实有信号回测、容量和恢复演练未签收，
因此尚不创建稳定标签或稳定 Release。

## 1. 发布原则

- 提交、语义版本、Tag、镜像和生产状态分别记录。
- CI 通过、镜像构建成功和生产可用是不同结论。
- 生产镜像固定 Registry digest，不依赖 `latest`。
- 配置、schema、数据迁移和应用发布分别评估。
- 先准备恢复点，再执行会写入生产数据的升级。
- 只声明已验证能力，未完成项必须列入发布记录。

## 2. 当前自动化边界

唯一产品工作流是 `.github/workflows/product.yml`。它执行 Policy 测试、前端测试与
构建、统一 API 定向检查、候选 Compose/Dockerfile 检查、版本一致性和 VKE 渲染。

该工作流不会：

- 自动递增版本或修改主线。
- 自动创建 Git Tag。
- 自动构建、推送或提升生产镜像。
- 自动生成桌面安装包或 GitHub Release。
- 自动部署、灰度、备份或回滚生产。

这些操作由发布负责人显式执行，并在发布记录中保存命令结果、SHA 和 digest。

## 3. 发布产物

| 产物 | 必须记录 |
| --- | --- |
| 源码 | 完整 commit SHA、工作区状态、远程主线 |
| 版本 | 根项目、产品后端、前端、锁文件和 `VERSION` 一致 |
| 容器 | 镜像标签、平台、Registry digest、源码 Manifest |
| VKE 清单 | 完整渲染文件、无占位符、server dry-run 结果 |
| 数据 | schema 版本、迁移或全新安装结论、备份/恢复点 |
| 验证 | 自动化结果、公网烟测、外部服务和未完成项 |
| Tag | annotated Tag、对象 SHA、peeled commit |

## 4. 候选流程

1. 确认范围、验收条件、兼容影响和回滚条件。
2. 同步需求、架构、开发、测试、部署和运维文档。
3. 执行本地测试、`git diff --check` 和文档链接检查。
4. 将目标提交推送到 `main`，等待 `product.yml` 通过。
5. 从同一提交构建 amd64 镜像，生成文件 Manifest 并推送 Registry。
6. 将 VKE overlay 固定到返回的 digest，执行 Kustomize 和服务端 dry-run。
7. 在候选环境部署，完成健康、登录、九项页面、SSE 和受影响业务链路。
8. 创建并推送 annotated 候选标签，核对远端 peeled commit。
9. 记录已通过、失败、跳过和未执行项目。

当前发布不自动完成上述第 5～8 步。

## 5. 放行清单

### 代码与文档

- [ ] 目标提交已复审，无未解决 P0/P1。
- [ ] 版本字段一致，当前分支和远程状态明确。
- [ ] 需求、架构、测试、部署、运维与用户说明已同步。
- [ ] 配置示例不含真实凭据。
- [ ] API、数据、Provider 和 schema 变化有兼容结论。

### 自动化与制品

- [ ] `product.yml` 所有适用 Job 通过。
- [ ] 镜像 Manifest 的 commit、版本和文件哈希一致。
- [ ] Kustomize 无占位符、特权容器和退役组件。
- [ ] 生产使用固定 digest。
- [ ] 数据迁移、备份和回滚步骤已验证。

### 业务验收

- [ ] HTTPS、健康、登录、退出、九项页面和 SSE 通过。
- [ ] Policy 报告、任务、Market Worker 和 Compute 至少各完成一条真实链路。
- [ ] TOS 对象写入、读回和 SHA-256 校验通过。
- [ ] SMTP 测试邮件在真实收件箱到达。
- [ ] 有数据交易日归档和 Manifest 完整性通过。
- [ ] 容量和恢复演练满足发布目标。

缺少外部凭据时可以保留候选，但不能把相关能力标记为通过。

## 6. 生产发布

```bash
kubectl kustomize --load-restrictor LoadRestrictionsNone \
  deploy/kubernetes/overlays/volcengine > /tmp/banxia-vke.yaml
! rg -n 'REPLACE_WITH_' /tmp/banxia-vke.yaml
kubectl apply --server-side --force-conflicts \
  --dry-run=server -f /tmp/banxia-vke.yaml
```

发布顺序：

1. 记录当前镜像 digest、配置摘要、schema 和恢复点。
2. 暂停会冲突的调度或写入者。
3. 应用数据库初始化或迁移 Job，并核对终态。
4. 发布 Platform、Market Worker、Compute、Task Worker 和 Report Scheduler。
5. 发布统一 Web/API，等待滚动完成。
6. 验证 `/health/live`、`/health/ready`、登录、九项页面和 SSE。
7. 逐项恢复调度；未签收的 `market-archive` 继续暂停。
8. 进入至少覆盖受影响交易时段的观察期。

## 7. 停止与回滚

以下情况阻断或立即停止推广：

- 健康检查持续失败或工作负载启动循环。
- 身份、租户或 RBAC 越权。
- 金融口径错误、未来函数或错误有效信号。
- PostgreSQL/ClickHouse 权威写入失败但 Redis 仍显示新状态。
- WAL/Outbox 持续积压，任务无法进入终态。
- 凭据泄露、Compute 获得出网或业务 Secret。
- 数据迁移不可核对或恢复点不可用。

仅代码不兼容且数据格式未变化时，可回滚上一固定 digest。发生不兼容写入时，先停止
所有写入者，再恢复与旧镜像匹配的 PostgreSQL、ClickHouse、TOS/NAS 和 WAL 恢复点。
回滚后重新执行健康、登录、数据范围、任务、SSE 和核心业务烟测。

## 8. 发布记录

每次发布至少记录：

```text
版本与提交:
Tag 与 peeled commit:
镜像标签与 digest:
schema 与迁移:
配置变化:
备份/恢复点:
自动化结果:
人工验收:
未完成项:
观察指标:
回滚条件:
```

生产故障按 P0/P1/P2/P3 分级，修复必须补最小复现、自动化回归、根因和文档影响。
