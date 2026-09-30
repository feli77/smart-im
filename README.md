# 灵序 Smart IM

**Rime / 小狼毫的本地候选重排服务。** Rime 提供拼音、基础候选和上屏，Smart IM 使用自带的小模型与可选 SQLite 个人统计调整候选顺序。依赖安装后离线运行。

正常输入先显示 Rime 原始候选，按 **Tab** 应用后台已完成的排序，再用空格或数字选词。服务关闭、结果过期或计算失败时保持原候选。

## Windows 使用

需要 Python 3.10+、支持现代 `librime-lua` 的[小狼毫](https://github.com/rime/weasel)，以及已能正常使用的 `luna_pinyin` 词典与 OpenCC 简体转换配置。

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m smart_im install-rime
```

在小狼毫“输入法设定”中勾选 **Smart IM**，重新部署并切换到该方案，然后运行：

```powershell
.\.venv\Scripts\python.exe -m smart_im serve
```

保持终端运行。先输入并确认“我们计划”，再输入 `shishi`，稍等后按 Tab，观察“实施”的排序。没有上下文且未启用学习时保留原序。结果未就绪时会提示稍后再按 Tab。Ctrl+C 退出服务。

安装命令只写 `smart_im.schema.yaml` 和 `lua/smart_im.lua`，不修改现有方案。完整步骤见 [Rime 使用说明](docs/RIME.md)。

## 功能与数据

- 只重排前 9 个范围一致的候选，保留 Rime Candidate 对象和元数据。
- Lua 与 Python 通过有界的本地文件信箱通信；输入过程不等待模型完成。
- 上下文来自当前 Rime 会话的有限确认文本，不读取应用正文；同一应用内的控件切换仍需真机验证。
- 个人学习默认关闭。服务使用 `--learn` 且方案切换为“学习开启”时，才读取和写入个人统计。
- 专用方案关闭 Rime 用户词典学习，由 SQLite 保存确认词句及最多 8 字的上下文后缀。个人数据和临时信箱均为本地明文。
- 自带字符 MLP 与 n-gram 使用有限语料，排序收益仍需独立质量评估。

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

全局选项 `--data-dir` 指定个人数据目录，`--learn` 开启个人统计读写。`rerank --private` 可对单次请求禁用个人统计。

## 开发与验证

```sh
uv sync --extra dev
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv build
```

测试包含真实 Lua 运行时、文件信箱和 Python 服务，Rime 对象由测试模拟。Windows 小狼毫实际部署与上屏尚未验收。

项目的 schema、Lua、语料及模型权重包含在 wheel 中；第三方 `luna_pinyin` 词典需另行安装。

- [当前架构](docs/ARCHITECTURE.md)
- [模型说明](docs/MODEL_CARD.md)
- [验证记录](docs/VALIDATION.md)
