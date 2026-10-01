# 小狼毫 TSF 扩展补丁

- 上游：<https://github.com/rime/weasel>
- 基线：`0.17.4` / `9cc96e20dc71b80876b12f689bb5863c76c2a7ed`
- 实现分支末端：`3427e2ed3c1be4f394e9ea15c91c9d167fab1e8b`（包含基础实现及八项后续修复、诊断与回归）
- 本地工作分支：`codex/tsf-local-context`
- 补丁：`0001-tsf-local-context.patch`，包含 TSF、IPC、Rime 属性桥接和原生测试。

这是配套前端/服务端源码修改。完整构建脚本、本地测试包安装和回滚、协议及验收见 [TSF 扩展](../../docs/TSF.md)。补丁是包含九个提交的 mbox，含可选输入域属性兼容修复、稳定版本资源标记、SDK 资源头文件构建修复和可开关的阶段诊断。真实 Windows TSF 回归覆盖没有可选 input scope 时的 `E_FAIL + VT_EMPTY` 返回，以及 Chromium 风格的 transitory 文档正文读取、组合范围排除和敏感输入域拒绝。上游文件包含 CRLF 和 UTF-16 资源，使用 `git am --keep-cr` 应用；本仓库用 `.gitattributes` 保留补丁字节。

重新导出已检查并提交的小狼毫分支时，使用二进制安全的重定向，保留换行。可通过 Python `subprocess.run(..., stdout=file)` 将 `git format-patch --stdout --no-signature 9cc96e20dc71b80876b12f689bb5863c76c2a7ed..HEAD` 写入该文件，不要经 PowerShell 文本管道重新编码。

已在固定基线的独立 worktree 使用 `git am --keep-cr` 复验，得到的 Git tree 与实现分支完全相同。
