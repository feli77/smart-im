# 小狼毫 TSF 扩展补丁

- 上游：<https://github.com/rime/weasel>
- 基线：`0.17.4` / `9cc96e20dc71b80876b12f689bb5863c76c2a7ed`
- 实现分支末端：`407edb7620de82b8882c91dcf810aa5c22a7f866`（包含基础实现及三项后续修复）
- 本地工作分支：`codex/tsf-local-context`
- 补丁：`0001-tsf-local-context.patch`，包含 TSF、IPC、Rime 属性桥接和原生测试。

这是配套前端/服务端源码修改。完整构建脚本、本地测试包安装和回滚、协议及验收见 [TSF 扩展](../../docs/TSF.md)。补丁是包含四个提交的 mbox，含可选输入域属性兼容修复、稳定版本资源标记和 SDK 资源头文件构建修复。上游文件包含 CRLF 和 UTF-16 资源，使用 `git am --keep-cr` 应用；本仓库用 `.gitattributes` 保留补丁字节。

重新导出已检查并提交的小狼毫分支时，使用二进制安全的重定向，保留换行。可通过 Python `subprocess.run(..., stdout=file)` 将 `git format-patch --stdout --no-signature 9cc96e20dc71b80876b12f689bb5863c76c2a7ed..HEAD` 写入该文件，不要经 PowerShell 文本管道重新编码。

已在固定基线的独立 worktree 使用 `git am --keep-cr` 复验，得到的 Git tree 与实现分支完全相同。
