# 协作与版本管理

当前仓库为 <https://github.com/jelven007/LSS>，`main` 是唯一集成主线。源码、
标签、镜像和生产环境是四个独立状态，统一记录见[版本基线](./version-baseline.md)。

## 开发与合并

- 每项任务使用 `feat/*`、`fix/*`、`docs/*` 或 `chore/*` 短期分支。
- 多人并行时使用独立 clone 或 worktree，不在同一工作目录覆盖他人未提交内容。
- 共享分支只做普通 merge 或 fast-forward，不强推已共享历史。
- 删除分支前确认内容已合并，且没有未提交或未推送工作。
- 提交应聚焦；用户已有的无关改动和调试文件不得顺带提交。

合并前至少核对：

1. 需求、架构、测试和运维文档已随行为变化同步。
2. 受影响自动化测试通过。
3. 版本、配置、schema、兼容和回滚影响明确。
4. 不含凭据、生产数据、调试目录或临时产物。

## 版本规则

应用使用语义版本 `MAJOR.MINOR.PATCH`。发布负责人显式同步以下位置：

- 根项目 `pyproject.toml` 与 `src/banxia_strategy/__init__.py`。
- `product/backend/pyproject.toml`、`uv.lock` 和 `app/__init__.py`。
- `product/frontend/package.json`。
- `product/VERSION`。
- 版本基线、发布记录和用户可见说明。

`.github/workflows/product.yml` 只验证这些值一致，不自动递增版本。普通功能提交无需
改版本；进入明确发布批次时再统一调整，避免同一版本对应无法区分的发布制品。

## 当前质量门

`product.yml` 在相关 PR 和 `main` push 上执行：

- Policy unittest。
- 前端测试和生产构建。
- 统一 API、Policy 门面、Task Worker 与 Compute 定向 Ruff/pytest。
- 完整单机候选 Compose、Nginx 和 Dockerfile 构建检查。
- 版本一致性与 VKE Kustomize 渲染检查。

工作流权限是 `contents: read`，不会写回提交、创建标签、推送镜像或部署生产。

## Tag、镜像与 Release

1. 从已通过质量门的 `main` 提交构建镜像。
2. 记录源码 SHA、文件清单和 Registry digest。
3. 在隔离或候选环境完成目标验收。
4. 创建 annotated 候选标签，例如 `v0.5.1-rc.1`。
5. 推送 `main` 和明确的标签，核对远端 peeled commit。
6. 完整业务签收后才创建稳定标签或 GitHub Release。

标签不可替代制品或生产证明；同名标签不得移动。生产只引用固定 digest，不使用
`latest`。当前仓库没有自动容器发布或桌面 Release 工作流，相关产物必须按实际构建
与验收记录声明。

远程核验：

```bash
git remote get-url origin
git ls-remote origin refs/heads/main
git ls-remote origin refs/tags/v0.5.1-rc.1 \
  'refs/tags/v0.5.1-rc.1^{}'
```

远程分支保护和 ruleset 以 GitHub 实际配置为准；仓库中的模板或文档不能作为已启用
证明。

## 回滚

- 源码问题通过新提交修复或 revert，不重写已共享主线。
- 生产代码回滚固定到已知镜像 digest。
- 发生不兼容数据写入时，先停止写入者，再恢复与旧镜像匹配的备份。
- 回滚后重新验证健康、登录、数据范围、任务、SSE 和核心业务。
