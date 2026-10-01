# 小狼毫 TSF 扩展补丁

- 上游：<https://github.com/rime/weasel>
- 基线：`0.17.4` / `9cc96e20dc71b80876b12f689bb5863c76c2a7ed`
- 实现提交：`a89a78e8d44af3bb793c17cab045cd437f35f0c8`
- 本地工作分支：`codex/tsf-local-context`
- 补丁：`0001-tsf-local-context.patch`，包含 TSF、IPC、Rime 属性桥接和原生测试。

这是配套前端/服务端源码修改，不是可直接安装的二进制包。应用方法、协议及验收见 [TSF 扩展](../../docs/TSF.md)。上游文件包含 CRLF，使用 `git am --keep-cr` 应用；本仓库用 `.gitattributes` 保留补丁字节。

重新导出已检查并提交的小狼毫分支时，使用二进制安全的重定向，保留换行。可通过 Python `subprocess.run(..., stdout=file)` 将 `git format-patch -1 --stdout --no-signature` 写入该文件，不要经 PowerShell 文本管道重新编码。

已在固定基线的独立 worktree 使用 `git am --keep-cr` 复验，得到的 Git tree 与实现分支完全相同。
