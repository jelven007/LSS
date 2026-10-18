# 精简架构本地集成验收（2026-10-19）

## 环境与范围

- 独立 Compose 项目：`policy-lean-test`；入口：`http://127.0.0.1:3061`。
- 应用版本：`0.5.1`；基础源码：`fe1d4ae`，包含 `844f4ef` 架构精简。
- 本轮补充首次部署配置及 Compute 行情路径修复；不部署云环境、不创建正式版本 Tag。
- 原有 3018、3051、soak 和 observer 环境保留；未复用或修改其数据库。
- PostgreSQL 使用 `banxia`、`tsp_control`、`tsp_data_local` 三库。
- 公开行情初始清单含 1,510 个文件，导入两份后逐文件验证 SHA-256；
  行情、平台数据、平台配置使用各自的 Docker 数据卷。

## 修复与运行证据

1. macOS 宿主机挂载读取 Parquet 元数据较慢，同一文件 schema 读取：
   bind mount 为 0.297359 秒，容器内文件为 0.002408 秒。
   切换独立数据卷后，平台 `DataStore` 初始化约 1.3 秒，原来未能完成启动。
   这是单次局部对照，不作为整体吞吐基准。
2. 初始化阶段增加 `NUMBA_CACHE_DIR=/tmp/numba`，策略同步由 0 条恢复为 22 条；
   Kubernetes 数据初始化任务同步设置此目录。
3. 初始化测试租户 `lean-test`，不重置已有密码；通过 rustdx 同步 801 个交易日，
   解决空数据库缺少租户上下文和交易日历的问题。
4. 真实回测曾失败：`[Errno 2] No such file or directory: '/market'`。
   独立 Compute 进程接收 Broker 指定的 `DATA_DIR`，执行器仍使用旧固定目录。
   修复后链接和只读数据仓库都读取指定发布目录，保留旧容器的 `/market` 默认值。
5. 报告任务 `dd344325-fc3d-46f5-92ef-01ef7b74cdc4` 领取一次并完成；
   报告日期为 `2026-10-16`，生成 3 个报告资产记录及 1 个策略计划。
6. 回测任务 `d5e82dfb61d14456b2a3aa9f6516cd19` 完成并持久化为 `succeeded`，
   返回 1 笔交易、7 个资金曲线点。使用两只公开股票、2026-10-01 至 2026-10-16，
   显式关闭本次任务的形态/量比和部分基础过滤，验证计算成功路径；
   未修改保存的策略配置。原始策略抽样返回“无买入信号”的失败状态也已正常回传。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| `/health/live` 版本与完整 readiness | 修复镜像重启后通过 |
| 22 个策略、管理员与测试租户幂等初始化 | 通过 |
| 同源登录、未登录 401、九个业务菜单 | 通过 |
| SSE 订阅心跳、票据重复使用返回 401 | 通过 |
| 统一入口首页及 JS/CSS 静态资源 | HTTP 200，资源非空 |
| Report Scheduler 消费与报告持久化 | 通过 |
| Compute 无业务凭据、UID 10001、行情只读、无 Docker socket | 通过 |
| Compute 实际回测、任务状态与结果持久化 | 通过 |
| Compute 定向测试 | 9 项通过；QA 未安装 asyncio 插件产生一个配置警告，均为同步测试 |
| 修改文件 Ruff 检查、Compose 解析、diff 空白检查 | 通过 |
| 已有目录和运行中数据卷的重复导入保护 | 两种操作均拒绝覆盖 |
| S3 对象写入/读回、Market Worker SQLite WAL | 通过 |

统一入口 10 项自动检查全部通过，证据位于私有 `.local/lean-test/smoke.json`；
回测成功证据位于 `.local/lean-test/compute-deterministic.json`。
验收脚本不保存密码、Cookie 或 SSE 票据。
平台调试挂载已从运行环境移除，调试文件保留在独立工作目录、未纳入提交。

本次运行镜像：

| 镜像 | 本地 image ID |
| --- | --- |
| `tsp-product-runtime:lean-verified` | `sha256:52673004b5ad6309164b638461ca1dc75c42000108f5fd50f66b1ad42be58b9a` |
| `tsp-product-compute:lean-verified` | `sha256:52867d0c948b7a5c72ff9a97fa3b2e216c38aa0818c72f71065093004e2ffe54` |
| `banxia-strategy:lean-cleanup` | `sha256:9c106a903dc6441336b8ae9855f0aa60e1131564b393e2f99547eb86ff582b77` |

## 限制

- 本机保留多套旧服务，容器启停及首次采集存在资源竞争；第一次历史生成超过五分钟
  未出现可见进展后切换为验证单日报告队列。完整历史回补不计入通过项。
- 浏览器自动化连接超时，尚未取得实际页面交互验收结果。
- 本地 S3rver 仅验证兼容读写；生产仍使用 TOS，其权限、生命周期和吞吐未验证。
- Compute 内部网络没有公网路由，但 API/Task Worker 同网可达；
  VKE 的完整 NetworkPolicy、TLS、ALB、HPA 未在本次验收。
- 未配置 SMTP，未发送实际邮件。
- 修复镜像为本地验收产物，清单包含文件哈希，尚未绑定正式发布提交或签名。

复现与停止命令见 [`product/deploy/lean-local/README.md`](../product/deploy/lean-local/README.md)。
