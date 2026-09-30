# Rime / 小狼毫使用说明

## 安装

需要 Python 3.10+、支持现代 `librime-lua` 模块语法的小狼毫，以及已有的 `luna_pinyin` 词典与其 OpenCC 简体转换配置。先确认小狼毫自己的朙月拼音能够正常输入。如果缺少词典，先通过 Rime 的方案管理安装；本项目不自动下载这些第三方资源。asd

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m smart_im install-rime
```

Windows 默认用户目录为 `%APPDATA%\Rime`。自定义安装用 `install-rime --user-dir D:\RimeUser`；运行服务也要指定同一个 `--user-dir`。

仅安装两个文件：`smart_im.schema.yaml` 和 `lua/smart_im.lua`。

不会修改 `default.custom.yaml`、现有 `rime.lua` 或现有方案；不会替你下载/安装/注册小狼毫。若同名文件已有不同内容，默认拒绝覆盖；`--force` 会先保存 `.bak` 文件再更新。

安装后在小狼毫“输入法设定”中勾选 **Smart IM**，重新部署，再通过方案菜单切换。若无法找到该方案或部署报错，检查 Rime 日志中的 Lua 插件、词典和 OpenCC 依赖。

## 开始输入

```powershell
.\.venv\Scripts\python.exe -m smart_im serve
```

保持该终端运行，不需要管理员权限、PySide6、网络或 API key。Ctrl+C 退出服务。

1. 正常输入拼音；首先看到 Rime 基础候选。
2. 按 Tab 应用当前快照的 AI 顺序；若尚未完成，原候选保持不变，可稍后再按 Tab。
3. 用空格/数字按 Rime 原有方式上屏。只选词而不按 Tab，顺序不会被后台结果突然改变。
4. 删改拼音、改变候选或上下文后，旧结果失效。

演示：先确认“我们计划”，再输入 `shishi`，稍等后按 Tab，观察“实施”的位置。自带小模型只覆盖有限语料，排序不会在每个输入上改善；空上下文且未学习时保持原序。

## 个人学习

默认关闭。要启用，需要同时满足：

```powershell
.\.venv\Scripts\python.exe -m smart_im --learn serve
```

并在 Smart IM 的方案菜单切换为“学习开启”。服务未带 `--learn` 或方案开关关闭时，该请求不读取、不写入个人统计。Smart IM 专用方案始终关闭 Rime 用户词典学习，所以首版只有一个个人学习数据源。

确认上屏才记录，不记录未经接受的候选。统计存于 `%LOCALAPPDATA%\SmartIM\learning.sqlite3`，最多保存词句及 8 字上下文后缀。用户目录中的 `smart_im_runtime` 则是临时信箱，包含当前候选和有限上下文，不是加密存储。

清空学习：先 Ctrl+C 停止本服务，再执行 `smart-im reset --yes`。正常退出会移除本服务的临时协议文件；异常退出可能留下文件，下次启动会清理过期内容。服务不保证异常退出前的每条可选学习事件都已持久化。

## 明确的限制

- 只增强前 9 个范围一致的候选，不改写拼音，不生成新的候选词。
- 本版没有自动候选刷新、空输入续写、文内灰字或外部应用全文纠错。
- 只观察当前 Rime 会话已确认文本；无法保证识别同一应用内所有鼠标光标移动或控件切换。未知/敏感场景可关闭方案 AI 增强、关闭学习，或切回原有方案。
- 文件信箱有轮询和文件系统成本；基础输入不等待模型。可选 GGUF 很慢时心跳可能过期，此时 Lua 回退基础候选，不宣称 GGUF 达到自带微型模型延迟。
- Lua 运行时与 Python 服务的自动整合测试不等价于小狼毫真机测试。当前 Linux 环境尚未验收 Windows TSF 与实际 Rime 部署；需要按下方步骤验证。

## Windows 人工验收

在普通权限记事本中：原方案输入正常 → 安装并选择 Smart IM → 无服务输入仍正常 → 启动服务 → 输入上下文与同音拼音 → Tab 应用 → 数字选词 → 修改拼音后旧结果不生效 → 停止服务后仍能打字。再验证学习双开关、重启保持、清除数据、多应用切换及敏感场景。

卸载本适配时，先切回原方案，停服务，再删除上述两个项目文件与 `smart_im_runtime`，重新部署。个人 SQLite 数据按需另行清空。
