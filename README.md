# 灵序 Smart IM

**Rime / 小狼毫的本地候选重排服务。** Rime 提供拼音、基础候选和上屏，Smart IM 支持通过本机 Ollama 使用 Qwen 等开源模型，并结合可选 SQLite 个人统计调整候选顺序。自带 tiny 模型仅用于演示。

正常输入先显示 Rime 原始候选；在 Windows 小狼毫中，模型返回后自动更新候选，推荐词移到首位并高亮，候选栏显示 **★ AI 推荐**，再用空格或数字选词。无需按 Tab。服务关闭、结果过期或计算失败时保持原候选。

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

保持终端运行。先输入并确认“我们计划”，再输入 `shishi`，停下输入，观察“实施”自动移到首位并高亮。没有上下文且未启用学习时保留原序，也不标记为 AI 推荐。计算期间仍可按当前候选选词。Ctrl+C 退出服务。

## 使用本地 Qwen

安装并启动 [Ollama](https://ollama.com/download/windows)，下载模型后运行：

```powershell
ollama pull qwen3:1.7b
# 可先打开模型并输入一句话预热，输入 /bye 退出交互，Ollama 后台继续运行
ollama run qwen3:1.7b
.\.venv\Scripts\python.exe -m smart_im serve --backend ollama --model qwen3:1.7b
```

Ollama 后端默认使用 `qwen3:1.7b`，也可显式指定 `qwen3:0.6b`。首次下载需要网络；推理只调用本机 Ollama。模型比较“上下文 + 候选”的完整短语，只选择最佳索引；将该项提升到首位，其余保持 Rime 原序，关闭 Qwen3 思考模式。启用个人学习后，个人统计仍可进一步调整顺序。服务打印正在使用的模型；不加 `--backend ollama` 仍使用演示模型。

已在 RTX 5060 Laptop 上确认 Ollama 使用 GPU，并用真实权重复测“铁血 → 战士”“解决 → 方案”。0.6B 的整组排序判断不可靠；更小的兼容选项有 `qwen2.5:0.5b`，但本项目未验证其质量。小样本对比、耗时与局限见 [验证记录](docs/VALIDATION.md)，GPU 检查和延迟排查见 [Rime 使用说明](docs/RIME.md#gpu-与延迟排查)。

升级已有安装时，需要更新 Lua 并在小狼毫中**重新部署**，只重启 Python 不会更新已安装的适配器：

```powershell
.\.venv\Scripts\python.exe -m smart_im install-rime --force
```

此命令先备份本项目旧文件。更多模型参数、故障提示及自定义用户目录见 [Rime 使用说明](docs/RIME.md)。

安装命令只写 `smart_im.schema.yaml` 和 `lua/smart_im.lua`，不修改现有方案。完整步骤见 [Rime 使用说明](docs/RIME.md)。

## 功能与数据

- 在前 9 项中只重排与首候选拼音范围一致的候选，其他范围固定原位，保留 Rime Candidate 对象和元数据。
- Lua 与 Python 通过有界的本地文件信箱通信；输入过程不等待模型完成。
- Windows 服务在结果写入后通知小狼毫刷新，刷新前检查前台窗口、焦点和输入状态；切换应用或继续操作后不唤醒旧窗口。其他前端在下次候选重建时读取已完成结果。
- 上下文来自当前 Rime 会话的有限上屏文本及候选前已选的分段，不读取应用正文；同一应用内的控件切换仍需真机验证。
- 个人学习默认关闭。服务使用 `--learn` 且方案切换为“学习开启”时，才读取和写入个人统计。
- 专用方案关闭 Rime 用户词典学习，由 SQLite 保存确认词句及最多 8 字的上下文后缀。个人数据和临时信箱均为本地明文。
- Qwen 后端无需自己训练模型；自带字符 MLP 与 n-gram 使用有限语料，两者的排序收益都需独立质量评估。

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

开发依赖包含 git-cliff，版本由 `uv.lock` 固定。使用 `uv run git-cliff --config cliff.toml --output CHANGELOG.md` 从提交历史更新变更记录；没有版本标签的修改归入 `Unreleased`。协作、提交与发布流程见 [AGENTS.md](AGENTS.md)。

自动测试包含真实 Lua 运行时、文件信箱、Python 服务、模拟 Windows API 及 Ollama HTTP 接口。另已用本机 Rime DLL 的隔离会话验证原生刷新、高亮和取消行为，见 [验证记录](docs/VALIDATION.md)。`uv run python scripts/evaluate_ollama.py --model qwen3:1.7b --repeat 2` 可复测本机模型；更新 Lua、重新部署并重启服务后，仍需验收小狼毫完整输入链路。

项目的 schema、Lua、语料及模型权重包含在 wheel 中；第三方 `luna_pinyin` 词典需另行安装。

- [当前架构](docs/ARCHITECTURE.md)
- [模型说明](docs/MODEL_CARD.md)
- [验证记录](docs/VALIDATION.md)
