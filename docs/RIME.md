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

1. 输入拼音，先显示 Rime 基础候选。
2. 按 Tab 应用当前快照已完成的排序。未完成时保留原序，提示稍后再按 Tab。
3. 用空格或数字按 Rime 原有方式上屏。
4. 删改拼音、移动组合输入光标或改变候选后，旧结果失效。

示例：先确认“我们计划”，再输入 `shishi`，稍等后按 Tab，观察“实施”的位置。自带模型覆盖有限语料，并非每次输入都会改序；无上下文且未学习时保持原序。

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

- 只增强前 9 个范围一致的候选；后台结果须通过 Tab 应用。
- 只观察当前 Rime 会话的已确认文本，无法保证识别同一应用内全部鼠标移动或控件切换。需要时可关闭方案 AI 和学习开关。
- 文件信箱存在轮询和文件系统开销；基础输入不等待模型，服务不可用时保留原候选。
- 自动化测试模拟 Rime 对象，尚未完成真实小狼毫部署与上屏验收。

## Windows 人工验收

在普通权限记事本中依次验证：原方案正常输入 → 安装并切换 Smart IM → 无服务时正常输入 → 启动服务 → 连续确认上下文并输入同音拼音 → Tab 重排 → 数字选词 → 修改拼音后旧结果失效 → 停止服务后继续输入。

继续验证部分选词、连续输入、学习双开关、重启保留统计、清空数据、多应用及同一应用内控件切换。记录小狼毫与 Lua 版本、部署日志和实际响应时间。

卸载时先切回其他方案并停止服务，再删除本项目两个资源和 `smart_im_runtime`，重新部署。个人 SQLite 数据可按需清空。
