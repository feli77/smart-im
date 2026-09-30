# Rime / 小狼毫使用说明

## 安装

需要 Python 3.10+、支持现代 `librime-lua` 模块语法的小狼毫，以及 `luna_pinyin` 词典与 OpenCC 简体转换配置。先确认小狼毫的朙月拼音能正常输入；缺少词典时先通过 Rime 的方案管理安装。安装并启动 [Ollama](https://ollama.com/download/windows)，然后在项目目录执行：

```powershell
ollama pull qwen3:1.7b
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m smart_im install-rime
```

Windows 默认用户目录为 `%APPDATA%\Rime`。自定义目录使用 `install-rime --user-dir D:\RimeUser`，运行服务时也要指定相同的 `--user-dir`。

安装写入 `smart_im.schema.yaml` 和 `lua/smart_im.lua`，并创建 `smart_im_runtime` 临时信箱目录。不会修改 `default.custom.yaml`、`rime.lua` 或其他方案。同名文件内容冲突时默认拒绝覆盖；`--force` 会先保存 `.bak` 备份再更新。

在小狼毫“输入法设定”中勾选 **Smart IM**，重新部署，然后通过方案菜单切换。找不到方案或部署失败时，检查 Rime 日志中的 Lua、词典和 OpenCC 依赖。

## 输入与重排

```powershell
.\.venv\Scripts\python.exe -m smart_im serve
```

保持 Ollama 和服务终端运行，Ctrl+C 退出 Smart IM 服务。`serve` 和 `rerank` 默认调用 `qwen3:1.7b`，Python 运行时仅使用标准库，无需 API key。

| 参数 | 默认值与用途 |
|---|---|
| `--model` | `qwen3:1.7b`；指定已下载的 Ollama 模型 |
| `--ollama-url` | `http://127.0.0.1:11434`；仅支持本机回环 HTTP 地址 |
| `--model-timeout` | 10 秒；可设为 0.1–20 秒 |

首次下载模型需要网络，后续推理通过本机 API 完成。自定义 Rime 用户目录仍要传 `--user-dir`。PowerShell 启动脚本支持 `scripts/start.ps1 -Model qwen3:1.7b`。

可以先独立检查模型连接和重排结果，再测试输入法：

```powershell
.\.venv\Scripts\python.exe -m smart_im rerank 展示 战士 战事 战时 --context 铁血 --pinyin zhanshi
.\.venv\Scripts\python.exe -m smart_im rerank 反感 方案 --context 解决 --pinyin fangan
```

此前真实模型诊断的首项分别为“战士”“方案”，见 [验证记录](VALIDATION.md)。模型未下载、Ollama 未启动、超时或返回无效索引时，诊断命令保留原序并返回非零退出码；输入法服务保留 Rime 原候选。服务不自动下载模型。Qwen 只选择最佳候选提升到首位，其余保持原序；启用学习时个人统计仍可调整。它不生成新的候选词。

1. 输入拼音，先显示 Rime 基础候选。
2. 停下输入，模型返回后自动更新候选。推荐词移到首位并高亮，候选栏显示“★ AI 推荐”，无需按 Tab。计算期间仍显示原候选。
3. 用空格或数字按 Rime 原有方式上屏。
4. 删改拼音、移动组合输入光标或改变候选后，旧结果失效。

无上下文且未学习时保持原序，不标记为 AI 推荐；成功返回也不要求顺序变化。用户移开首项高亮后清除推荐提示，不抢回选择。Tab 交给 Rime 原有逻辑。

主动刷新针对 Windows 小狼毫。服务完成推理后会检查前台窗口、焦点和最近输入；若已经切换应用或继续操作，不会唤醒旧窗口。结果仍可在后续候选重建时读取。普通权限服务无法向管理员权限应用发送刷新通知，因此建议先在普通权限编辑器验收。其他 Rime 前端也可在候选重建时应用结果，但停下输入时不会主动刷新。

前 9 项混有不同拼音范围时，只重排首候选同范围的子集，其他范围候选留在原槽位；不足两项、超出限制或写入失败会提示原因。

从旧版升级时运行 `install-rime --force`，然后在小狼毫菜单**重新部署**并重启 Smart IM 服务。安装会备份本项目旧文件；仅修改仓库或重启服务不会替换已安装的 Lua。

### GPU 与延迟排查

运行 `ollama ps` 查看 `PROCESSOR`；`100% GPU` 表示模型已全部加载到 GPU。Smart IM 没有额外 GPU 开关，设备由 Ollama 管理，参见[硬件支持](https://docs.ollama.com/gpu)。此前本机 RTX 5060 Laptop 8 GB 已确认 Qwen3 1.7B 使用 GPU，记录见 [验证记录](VALIDATION.md)。

冷启动可先执行 `ollama run qwen3:1.7b`，输入一句测试后用 `/bye` 退出交互，保持 Ollama 后台运行。服务请求使用 `think: false` 和 `keep_alive: "10m"`，但首次加载仍可能耗时。调大超时只会延长等待上限；连续输入等待请求稳定 80 ms 后才计算，已经开始的推理不会被抢占，过期结果不应用。

## 个人学习与管理

学习默认关闭。开启时需同时使用服务参数和方案开关：

```powershell
.\.venv\Scripts\python.exe -m smart_im --learn serve
```

然后在方案菜单切换为“学习开启”。任一开关关闭时，该请求不读取或写入个人统计。专用方案始终关闭 Rime 用户词典学习。

只有确认上屏才记录。Windows 默认统计库为 `%LOCALAPPDATA%\SmartIM\learning.sqlite3`，保存确认词句、频次及最多 8 字的上下文后缀。全局选项 `--data-dir` 可指定其他数据目录，查询和清空时应使用同一目录。

```powershell
.\.venv\Scripts\python.exe -m smart_im stats
# 先 Ctrl+C 停止服务，再清空个人学习数据
.\.venv\Scripts\python.exe -m smart_im reset --yes
```

`stats` 可在学习关闭时查看已有记录数量，不会把这些记录用于排序。SQLite 和临时信箱均为本地明文；信箱含有限候选与会话上下文。正常退出会移除临时协议文件，异常退出后下次启动清理过期文件。服务不保证异常退出前每条可选学习事件都已保存。

## 当前边界

- 只增强前 9 项中与首候选拼音范围一致的子集；Windows 小狼毫自动应用仍匹配当前输入的后台结果。
- 上下文包含当前 Rime 会话的已上屏文本，以及当前候选前已选但尚未上屏的连续分段；无上下文且无学习证据时保留原序。无法保证识别同一应用内全部鼠标移动或控件切换，需要时可关闭方案 AI 和学习开关。
- 文件信箱存在轮询和文件系统开销；基础输入不等待模型，服务不可用时保留原候选。
- 已有真实 Qwen 诊断及原生 Rime 自动刷新验证见 [验证记录](VALIDATION.md)，仍需重新验收更新后的小狼毫完整输入链路。

## Windows 人工验收

在普通权限记事本中依次验证：原方案正常输入 → 安装并切换 Smart IM → 无服务时正常输入 → 启动 Ollama 和 Smart IM 服务 → 确认上下文并输入同音拼音 → 不按任何键等待自动重排及高亮 → 数字或空格选词 → 修改拼音后旧结果失效 → 停止服务后继续输入。

继续验证计算中直接选词、Tab 原行为、部分选词、连续输入、学习双开关、重启保留统计、清空数据、多应用及同一应用内控件切换；切换或取消后旧响应不得改变当前选词或上屏。记录小狼毫与 Lua 版本、部署日志和实际响应时间。

卸载时先切回其他方案并停止服务，再删除本项目两个资源和 `smart_im_runtime`，重新部署。个人 SQLite 数据可按需清空。
