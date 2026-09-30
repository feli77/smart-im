# 灵序 Smart IM

**Rime / 小狼毫的本地候选重排服务。** Rime 提供拼音、基础候选和上屏，Smart IM 通过本机 Ollama 调用 Qwen，结合可选 SQLite 个人统计调整候选顺序。默认模型为 `qwen3:1.7b`，Python 运行时仅使用标准库。

正常输入先显示 Rime 原始候选；在 Windows 小狼毫中，模型返回后自动更新候选，推荐词移到首位并高亮，候选栏显示 **★ AI 推荐**，再用空格或数字选词。无需按 Tab。服务关闭、结果过期或计算失败时保持原候选。

## Windows 使用

需要 Python 3.10+、支持现代 `librime-lua` 的[小狼毫](https://github.com/rime/weasel)，以及已能正常使用的 `luna_pinyin` 词典与 OpenCC 简体转换配置。安装并启动 [Ollama](https://ollama.com/download/windows)，然后在项目目录执行：

```powershell
ollama pull qwen3:1.7b
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m smart_im install-rime
```

在小狼毫“输入法设定”中勾选 **Smart IM**，重新部署并切换到该方案，然后运行：

```powershell
.\.venv\Scripts\python.exe -m smart_im serve
```

保持 Ollama 和服务终端运行，Ctrl+C 退出 Smart IM 服务。首次下载模型需要网络，推理只调用本机 Ollama。服务不会自动下载模型。

先输入并确认“铁血”，再输入 `zhanshi`，停下输入，观察“战士”的推荐结果。计算期间仍可按当前候选选词。没有上下文且未启用学习时保留原序，也不标记为 AI 推荐；模型判断可能出错。已有真实模型诊断见 [验证记录](docs/VALIDATION.md)。

升级已有安装时，需要更新 Lua 并在小狼毫中**重新部署**，只重启 Python 不会更新已安装的适配器：

```powershell
.\.venv\Scripts\python.exe -m smart_im install-rime --force
```

此命令先备份本项目旧文件。安装只管理 `smart_im.schema.yaml` 和 `lua/smart_im.lua`，不修改现有方案。模型参数、故障排查及自定义用户目录见 [Rime 使用说明](docs/RIME.md)。

## 功能与数据

- 在前 9 项中只重排与首候选拼音范围一致的候选，其他范围固定原位，保留 Rime Candidate 对象和元数据。
- Lua 与 Python 通过有界的本地文件信箱通信；输入过程不等待模型完成。
- Windows 服务在结果写入后通知小狼毫刷新，刷新前检查前台窗口、焦点和输入状态；切换应用或继续操作后不唤醒旧窗口。其他前端在下次候选重建时读取已完成结果。
- 上下文来自当前 Rime 会话的有限上屏文本及候选前已选的分段，不读取应用正文；同一应用内的控件切换仍需真机验证。
- 个人学习默认关闭。服务使用 `--learn` 且方案切换为“学习开启”时，才读取和写入个人统计。
- 专用方案关闭 Rime 用户词典学习，由 SQLite 保存确认词句及最多 8 字的上下文后缀。个人数据和临时信箱均为本地明文。
- Qwen 选择最佳候选并提升到首位，其余保持原序；启用个人学习后，个人统计还可调整顺序。

## 命令

| 命令 | 用途 |
|---|---|
| `install-rime` | 安装独立方案与 Lua 适配；支持 `--user-dir` 和 `--force` |
| `serve` | 运行信箱服务；支持 `--user-dir` 或 `--runtime-dir` |
| `rerank` | 对指定候选重排，输出零基索引排列 |
| `stats` | 查看已有个人统计的数量 |
| `reset --yes` | 清空个人学习数据；先停止服务 |

```powershell
.\.venv\Scripts\python.exe -m smart_im rerank 实时 事实 实施 --pinyin shishi --context 我们计划
.\.venv\Scripts\python.exe -m smart_im --learn serve
.\.venv\Scripts\python.exe -m smart_im stats
```

`serve` 和 `rerank` 默认调用 `qwen3:1.7b`，支持 `--model`、`--ollama-url`、`--model-timeout`。全局选项 `--data-dir` 指定个人数据目录，`--learn` 开启个人统计读写；`rerank --private` 可对单次请求禁用个人统计。

## 开发与验证

```sh
uv sync --extra dev
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv lock --check
uv build
```

开发依赖包含 git-cliff，版本由 `uv.lock` 固定。使用 `uv run git-cliff --config cliff.toml --output CHANGELOG.md` 从提交历史更新变更记录；没有版本标签的修改归入 `Unreleased`。协作、提交与发布流程见 [AGENTS.md](AGENTS.md)。

自动测试使用真实 Lua 运行时、文件信箱、模拟 Windows API 及本地 HTTP 响应，不要求安装或下载 Qwen。`uv run python scripts/evaluate_ollama.py --model qwen3:1.7b --repeat 2` 可单独复测已安装的本机模型。此前原生 Rime 刷新验证及完整输入链路的剩余验收见 [验证记录](docs/VALIDATION.md)。

wheel 包含 Python 代码、schema 和 Lua；Qwen 模型通过 Ollama 单独下载，`luna_pinyin` 词典另行安装。

- [当前架构](docs/ARCHITECTURE.md)
- [模型说明](docs/MODEL_CARD.md)
- [验证记录](docs/VALIDATION.md)
