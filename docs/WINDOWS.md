# Windows 启动与验收

> 本文保留的是 v0.1 钩子浮窗演示。v0.2 日常输入主线已改为 [Rime / 小狼毫](RIME.md)，不再继续扩展此浮窗。

安装 Python 3.10+，在项目根目录打开 PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[desktop]"
.\.venv\Scripts\python.exe -m smart_im desktop
```

也可运行 `powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1 -Mode desktop`。脚本仅为本次进程指定执行策略，创建项目虚拟环境并安装依赖，不更改系统策略。依赖首次安装需要网络；安装完成后自带模型离线运行。

## 桌面体验台

编辑器内直接输入拼音，空格首选、数字 1–9 选词，Enter 提交原始拼音，Esc 取消，Tab 接受首条短语预测。Ctrl+Space 切换中英文。预测在光标位置插入；选中文本后输入会替换选择区域。

左侧“学习我的表达”默认关闭，开启后保留确认上屏的词频和短上下文统计。关闭时候选和预测均不使用个人数据，已有数据可通过清除按钮删除。此开关不等于系统密码框自动识别。

## 跨应用实验模式

```powershell
.\.venv\Scripts\python.exe -m smart_im windows
# 明确开启持久学习：
.\.venv\Scripts\python.exe -m smart_im --learn windows
```

1. 将 Windows 当前系统键盘切换为英文，打开普通权限的记事本并点击编辑区。
2. 启动程序后默认英文直通，屏幕底部显示候选浮窗，托盘显示“中”图标。
3. 按 Ctrl+Space 开启中文，输入 `nihao`，按空格确认；输入 `zhongguo`，用数字选择候选。
4. 连续确认输入后，用 Tab 接受首个续写。默认只参考本工具在当前窗口确认的文本。
5. Ctrl+Space 返回英文直通；Caps Lock 可临时输入英文。右键托盘可开关学习、清除数据和退出。

浮窗不抢输入焦点。焦点窗口变化、鼠标点击、导航键与快捷键会清空自身上下文。模型在后台推理，不在键盘钩子里运行；结果必须匹配当前组合输入版本。

## 首版边界

- 这是基于键盘钩子与 Unicode `SendInput` 的实验性浮窗，**不会出现在 Windows 系统输入法列表**。不包含 TSF 原生组合区、光标跟随候选、正式安装器与签名。
- Ctrl+Space 如果被其他应用占用，程序给出错误并清理钩子；需要用户解除冲突。当前未提供快捷键自定义。
- 首版按标准英文字母键位映射，非拉丁键盘布局、远程桌面、游戏、自绘编辑控件需要另行验证。
- 不支持密码框和管理员权限应用。进入这些场景应切回系统输入法或关闭本工具。程序不自动识别所有敏感输入框。
- 跨应用模式支持拼音拼写容错；已存在外部文本的语义纠错仅在桌面体验台提供，因为浮窗不读取外部应用文档。
- 当前开发环境是 Linux：已验证核心、Qt 无头交互与 Windows ABI/状态机测试。Windows 真机端到端验收尚未执行；仓库包含 Windows CI 配置，但不把未运行的 CI 写作通过。

## 本地数据

Windows 默认：`%LOCALAPPDATA%\SmartIM\learning.sqlite3`。可用 `--data-dir` 指定隔离目录。SQLite 为明文的本地统计库，没有静态加密；不保存原始按键流水或完整文档。数据仍含个人确认的词句，应视为个人数据。

```powershell
.\.venv\Scripts\python.exe -m smart_im stats
.\.venv\Scripts\python.exe -m smart_im reset --yes
```

`stats` 是显式的数据管理命令；关闭学习仍可查看已有记录数量，不会把其内容用于候选排序。
