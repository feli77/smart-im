# Rime / 小狼毫使用说明

## 安装

需要 Python 3.10+、支持现代 `librime-lua` 模块语法的小狼毫，以及 `luna_pinyin` 词典与 OpenCC 简体转换配置。先确认小狼毫的朙月拼音能正常输入；缺少词典时先通过 Rime 的方案管理安装。

```powershell
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

保持终端运行，Ctrl+C 退出。安装依赖后，服务运行无需网络或 API key。

上述命令使用自带演示模型。使用通用开源模型时，先安装并启动 [Ollama](https://ollama.com/download/windows)，然后：

```powershell
ollama pull qwen3:1.7b
ollama run qwen3:1.7b
# 完成一句测试后输入 /bye，保留 Ollama 后台运行
.\.venv\Scripts\python.exe -m smart_im serve --backend ollama --model qwen3:1.7b
```

较低资源可选 `qwen3:0.6b`。初次下载需网络；服务只支持本机回环地址，默认 `http://127.0.0.1:11434`，可用 `--ollama-url` 改端口。`--model-timeout` 为 0.1–20 秒，默认 10 秒；冷启动可先通过 `ollama run` 预热。自定义 Rime 用户目录仍要传 `--user-dir`。PowerShell 启动脚本也支持 `scripts/start.ps1 -Backend ollama -Model qwen3:1.7b`。

可以先独立检查模型连接和重排结果，再测试输入法：

```powershell
.\.venv\Scripts\python.exe -m smart_im rerank 实时 事实 实施 --context 我们计划 --pinyin shishi --backend ollama --model qwen3:1.7b
```

命令失败会返回非零退出码并保留原序。服务未开、模型未下载、超时或返回无效排列都会回退；不会自动下载或悄悄切换到 tiny。Qwen 是整组候选的提示重排，并不生成新的候选词；它的准确率与延迟尚待实测。

1. 输入拼音，先显示 Rime 基础候选。
2. 按 Tab 应用当前快照已完成的排序。未完成时保留原序，提示稍后再按 Tab。
3. 用空格或数字按 Rime 原有方式上屏。
4. 删改拼音、移动组合输入光标或改变候选后，旧结果失效。

示例：先确认“我们计划”，再输入 `shishi`，稍等后按 Tab，观察“实施”的位置。自带模型覆盖有限语料，并非每次输入都会改序；无上下文且未学习时保持原序。

已修复原先“前 9 项混有不同拼音范围时不提交请求，却一直显示计算中”的问题。现在只重排首候选同范围的子集，单字等其他范围候选留在原槽位；不足两项、超出限制或写入失败会明确提示，不再伪装成后台计算。成功处理也可能保持原序。

从旧版升级时运行 `install-rime --force`，然后在小狼毫菜单**重新部署**并重启 Smart IM 服务。安装会备份本项目旧文件；仅修改仓库或重启服务不会替换已安装的 Lua。

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

- 只增强前 9 项中与首候选拼音范围一致的子集；后台结果须通过 Tab 应用。
- 只观察当前 Rime 会话的已确认文本，无法保证识别同一应用内全部鼠标移动或控件切换。需要时可关闭方案 AI 和学习开关。
- 文件信箱存在轮询和文件系统开销；基础输入不等待模型，服务不可用时保留原候选。
- 用户已验证真实小狼毫界面与输入正常；混合候选修复和 Qwen 后端仍需重新实测。自动化测试模拟 Rime 对象和 Ollama 输出。

## Windows 人工验收

在普通权限记事本中依次验证：原方案正常输入 → 安装并切换 Smart IM → 无服务时正常输入 → 启动服务 → 连续确认上下文并输入同音拼音 → Tab 重排 → 数字选词 → 修改拼音后旧结果失效 → 停止服务后继续输入。

继续验证部分选词、连续输入、学习双开关、重启保留统计、清空数据、多应用及同一应用内控件切换。记录小狼毫与 Lua 版本、部署日志和实际响应时间。

卸载时先切回其他方案并停止服务，再删除本项目两个资源和 `smart_im_runtime`，重新部署。个人 SQLite 数据可按需清空。
