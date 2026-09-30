# 仓库协作规范

本文件适用于整个仓库。

## 开发与提交

- 所有文件修改均在 worktree 的工作分支中完成，包括代码、文档、版本号和变更日志；不要直接在 `main` 分支上编辑或提交文件。
- 只有用户明确要求合并时，才可将工作分支合并到 `main`。完成开发任务本身不代表获得合并或发布授权。
- 有文件变更的任务完成时，先完成必要检查，再提交本任务的修改；不要将无关修改混入提交。
- 提交信息遵循 Conventional Commits：`<type>(<scope>): <description>`，其中 scope 可省略。例如 `fix(rime): handle mixed candidate ranges`、`docs: document release workflow`。
- 单次工作流中，可在重要修改完成并通过相关检查后创建阶段提交，保留便于回滚的节点，无需等到整个任务结束才提交。

## 合并与发布

用户明确要求合并到 `main` 后，按以下顺序完成发布：

1. 确认发布版本与云端远程仓库；若版本、远程或相关取舍不明确，先向用户确认，不自行假定，也不默默跳过发布步骤。
2. 在 worktree 中调用 git-cliff 更新 `CHANGELOG.md`，完成所需版本修改及检查，并提交这些变更。
3. 将已提交的工作分支合并到 `main`，为最终的 `main` 提交创建 `vX.Y.Z` 格式的附注标签（annotated tag）。
4. 将 `main` 和本次版本标签推送到已确认的云端远程仓库。遇到失败应报告具体阻塞，不能将未完成的发布视为成功。

`vX.Y.Z` 仅为格式占位符，实际版本需依据已确认的发布决定。

## 设计与决策

- 遵循 Less is More：优先选择满足当前需求的简单架构和实现，避免无必要的抽象、重复逻辑、预留扩展和冗余设计。
- 遇到不确定且需要作出取舍的问题，先向用户说明待决事项并确认，再执行依赖该决定的工作；可继续不受其影响的部分。

## 常用命令

安装开发依赖（包含 git-cliff）：

```powershell
uv sync --extra dev
```

生成变更日志：

```powershell
uv run git-cliff --config cliff.toml --output CHANGELOG.md
```

发布时，使用已确认的实际版本替换占位符：

```powershell
uv run git-cliff --config cliff.toml --tag vX.Y.Z --output CHANGELOG.md
```
