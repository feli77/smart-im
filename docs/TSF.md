# TSF 局部上下文扩展

本分支把上下文来源改为小狼毫 TSF text store。Rime 继续负责拼音、分段、基础候选和上屏；Smart IM 只在既有候选内重排。此前 Lua 累积上屏历史的逻辑已移除。

## 一直提示未获取到局部上下文

先在仓库目录运行只读诊断：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/doctor_tsf.ps1
```

它检查正在运行的服务端、安装目录和 Windows 实际注册的两种位数 TSF DLL、Lua 版本与服务心跳，不读取正文或请求文件。仅部署 Lua 不会安装 TSF 扩展。2026-10-01 本机故障确认属于此情况：Lua 已更新、心跳正常，但服务端和两个系统 DLL 仍是原版。

本机已构建测试包 `artifacts/weasel-tsf-0.17.4-test`，含 64 位服务端、64/32 位 TSF DLL 和 SHA256 清单。适用范围是当前 x64 Windows、简体小狼毫 0.17.4；不包含或替换 `rime.dll`、插件、词库和用户配置。它是本地测试产物，不是已发布版本。

保存正在输入的内容、暂时切到其他输入法后，在 **64 位管理员 PowerShell** 中进入本仓库，执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_weasel_tsf.ps1 `
  -PackageDir artifacts/weasel-tsf-0.17.4-test `
  -BackupDir "$env:LOCALAPPDATA\SmartIM\weasel-backup-$(Get-Date -Format yyyyMMdd-HHmmss)"
```

脚本先完整备份本次涉及的文件和注册信息，再退出服务，覆盖三个原生文件，通过现有官方 `WeaselSetup.exe /s` 复制、注册两个系统 DLL，最后核对哈希和注册路径。它要求现有 `Hant=0`，会重新启用简体输入 profile；不部署词库，不从管理员会话启动服务。记下输出的备份路径。

安装完成后关闭管理员终端，在**普通 PowerShell** 中执行：

```powershell
Start-Process -FilePath 'C:\Program Files\Rime\weasel-0.17.4\WeaselServer.exe' -WindowStyle Hidden
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/doctor_tsf.ps1
Start-Process tests/manual/tsf_context.html
```

彻底退出并重新打开测试应用；浏览器也要退出全部进程后再开。已经加载旧 TSF DLL 的进程不会因重启服务而切换到新版，必要时保存工作后注销、登录。诊断成功只表示磁盘上的二进制、注册、Lua 和心跳配套，不代替实际输入验收。先在验收页预置正文中把光标放到“计划”后，输入拼音，再测试移动光标和切换两个输入框；详细通过条件见下方验收表。本次已部署的 Lua 与仓库一致，无需再部署。

若需回滚，在管理员终端使用安装时实际输出的备份路径：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_weasel_tsf.ps1 `
  -Rollback -BackupDir '实际备份路径'
```

随后同样从普通终端启动服务并重开应用。回滚只恢复本次原生文件并用原安装器登记，不导入整棵用户输入法注册树。回到原版原生组件后，新 Lua 会恢复“未获取到局部上下文”的降级行为。

## 小狼毫源码与补丁

以官方 [rime/weasel 0.17.4](https://github.com/rime/weasel/tree/0.17.4) 为基线，固定提交 `9cc96e20dc71b80876b12f689bb5863c76c2a7ed`。本地开发分支是 `codex/tsf-local-context`，补丁保存于 [patches/weasel](../patches/weasel)。本次开发没有合并、推送、注册 TSF DLL 或替换正在使用的小狼毫。

在 Smart IM 仓库根目录复现（目标目录需尚不存在）：

```powershell
git clone --branch 0.17.4 --single-branch https://github.com/rime/weasel.git artifacts/weasel-upstream
git -C artifacts/weasel-upstream worktree add ../weasel-smart-im -b codex/tsf-local-context 9cc96e20dc71b80876b12f689bb5863c76c2a7ed
$patchPath = (Resolve-Path patches/weasel/0001-tsf-local-context.patch).Path
git -C artifacts/weasel-smart-im -c core.whitespace=cr-at-eol am --keep-cr $patchPath
```

构建需要 Visual Studio 2022 C++/ATL、Windows SDK、完整 Boost 1.84.0 源码和官方 librime 1.17.0 Windows MSVC x64/x86 SDK。前端 `WeaselTSF`、`WeaselIPC` 与 `WeaselServer`/`RimeWithWeasel` 必须一起使用补丁版本，新 IPC 命令不能由原版服务端处理。

完整构建脚本会检查依赖版本、编译两种位数静态 Boost、链接前端与服务端并生成三文件测试包。`PackageDir` 必须是新目录或空目录；脚本不下载依赖、不停止服务、不安装输入法：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_weasel_tsf.ps1 `
  -WeaselRoot artifacts/weasel-smart-im `
  -BoostRoot artifacts/native-deps/boost_1_84_0 `
  -RimeSdkX64 artifacts/native-deps/librime-1.17.0/rime-33e7814-Windows-msvc-x64 `
  -RimeSdkX86 artifacts/native-deps/librime-1.17.0/rime-33e7814-Windows-msvc-x86 `
  -PackageDir artifacts/weasel-tsf-rebuilt
```

本机已安装的 `rime.dll` 自报版本 1.17.0，故构建使用 [固定 1.17.0 SDK](https://github.com/rime/librime/releases/tag/1.17.0) 的头文件和导入库；运行时保留本机现有 DLL 和 Lua 插件。首次配置 Smart IM 的机器仍需 `uv run smart-im install-rime --force` 并重新部署 Lua。

本地原生测试命令（VS C++ 工具已安装）：

```powershell
./scripts/verify_weasel_native.ps1 -WeaselRoot ./artifacts/weasel-smart-im
# 全部原生测试及修改源文件编译（需要 Boost 头文件及固定的 librime 子模块）
git -C artifacts/weasel-smart-im submodule update --init --depth 1 librime
./scripts/verify_weasel_native.ps1 -BoostRoot ./artifacts/native-deps/boost_1_84_0 -CompileSources
# 可选：只读加载已安装 DLL，验证真实 Lua 属性通知，所有写入在隔离目录
uv run python scripts/check_librime_property.py --rime-dir "C:/Program Files/Rime/weasel-0.17.4"
```

原生单元测试不等同于完整 DLL 链接、安装或真实应用验收；实际执行范围见 [验证记录](VALIDATION.md)。

## 读取与失效

键事件交给 Rime 之前，在当前 `ITfContext` 上请求同步、只读 edit session。取得有效 read cookie 后，读取 selection；存在本输入法 composition 时改以整个 composition 为替换区。克隆范围后向两侧各扩展最多 128 UTF-16 单元，用 `GetText` 读取正文，整个替换区不进入正文。不会遍历完整文档。代理对截断只发生在窗口外沿，半个代理对被丢弃，其他非法编码拒绝。

例如正文为 `我们计划[正在组合的文字]明天发布`，正文前文为 `我们计划`，后文为 `明天发布`。若 Rime 已在组合内选中 `实施`，该分段由 Lua 单独传送；拼音、当前候选以及小狼毫非 inline 模式的占位空格均不再拼入正文。选区输入时排除即将被替换的选中文字。

外部正文编辑或 selection 变化、document/context 栈变化、线程焦点变化会撤销旧上下文并清除旧推荐。排队的 TSF 编辑另绑定输入位置的 epoch，切换后丢弃旧更新；旧 composition 的清理只结束其自身对象。上下文 token 独立于文本内容，两个相同文本位置间移动也会使旧 token 失效。小狼毫自身的 composition 写入由 `InWriteSession` 识别，正常 preedit 更新不会每次生成一个新正文上下文。失败的读锁、无效范围或不支持读取的控件不复用旧值。

TSF 读取权限由应用 text store 决定，无法保证每种应用都暴露完整局部正文。受保护输入、密码/PIN/私密 input scope 等拒绝采集。此实现没有 UI Automation、剪贴板或全文读取后备方案。

## 传输协议

TSF → 小狼毫 IPC → Rime 使用单个原子属性：

```text
smart_im_context = 1<TAB>token<TAB>before_utf8hex<TAB>after_utf8hex
```

空属性为失效状态，和“成功读到两侧为空的文档”有区别。token 只含 ASCII 字母、数字、下划线或连字符，最长 64 字符。原生数据包最多 1700 WCHAR，正文每侧最多 128 UTF-16 单元；UTF-8 十六进制只承担协议转义。IPC 每个连接使用独立接收缓冲，校验正文实际长度和终止符；上下文通知使用独立连接，避免覆盖等待异步 TSF 编辑读取的按键响应。

Lua 监听 `property_update_notifier`，在属性变化时同步移除请求/响应、清空推荐并恢复原候选。新的排序请求采用 `SMARTIM2`，各字段独占一行，文本为 UTF-8 hex：

```text
SMARTIM2<TAB>RANK<TAB>session<TAB>revision<TAB>learning
context_token
pinyin
document_before
document_after
selected_prefix
candidate_1
...
```

结果头为 `SMARTIM2<TAB>RESULT<TAB>session<TAB>revision<TAB>ok|fallback<TAB>context_token`，下一行是 1-based 逗号分隔排列。Lua 同时校验 token、revision、拼音范围、Rime caret、已选前缀和候选元数据。服务仍在推理完成及发布后复查请求文件；进行中的模型调用不会抢占取消，但旧结果不能应用。

Python 接口的 `context` 为正文前文，`selected_prefix` 是已选分段，`context_after` 为正文后文。服务只做一次 `(context + selected_prefix)[-128:]`，后文单独传给模型。整句确认学习使用不含已选前缀的正文前文。无 TSF 属性时不发重排请求；`SMARTIM1` 解析仅保留旧调用与学习事件兼容性。

## 真实应用验收

可用浏览器打开仓库中的 [双输入框验收页](../tests/manual/tsf_context.html)，其中预置正文、contenteditable 和延迟程序编辑按钮。用普通权限测试文档，至少覆盖记事本、浏览器同页两个文本框，以及常用富文本编辑器。测试文字可由粘贴或打开已有文件提供，不能先用本次输入会话输入前缀代替“原有正文”场景。记录应用版本、是否 inline preedit、返回上下文和实际候选；只记录专用测试文本。

| 场景 | 操作与通过条件 |
|---|---|
| 原有正文 | 打开含 `我们计划明天发布` 的文本，将光标置于 `计划` 后输入拼音；请求前文为 `我们计划`、后文为 `明天发布` |
| 部分选词 | 先选中一个分段继续输入；正文中没有 preedit，`selected_prefix` 只含已选分段，模型短语只出现一次该分段 |
| 覆盖选区 | 选中文字再输入；原选区文本不进入任一侧正文 |
| 移动位置 | 等待或已经显示推荐时鼠标移动光标，包括前后文字相同的两个位置；旧文件、旧推荐立即失效，迟到结果不恢复 |
| 切换控件 | 同一窗口两个输入框来回切换、切换应用再返回；每次有新 token，不带回旧候选推荐 |
| 程序改文 | 保持焦点，用应用命令修改附近文字；旧上下文失效 |
| inline 切换 | 分别验证 inline preedit 开/关；预编辑和占位空格不进入正文 |
| 读不到/受保护 | 无 TSF 正文、读锁失败、密码框；上下文不可用，Rime 原始输入正常 |
| 非 BMP | 窗口边沿含 emoji；不产生残缺 UTF-8，也不跨入 composition |

TSF API 依据：[RequestEditSession](https://learn.microsoft.com/en-us/windows/win32/api/msctf/nf-msctf-itfcontext-requesteditsession)、[GetText](https://learn.microsoft.com/en-us/windows/win32/api/msctf/nf-msctf-itfrange-gettext)、[OnEndEdit](https://learn.microsoft.com/en-us/windows/win32/api/msctf/nf-msctf-itftexteditsink-onendedit)、[InWriteSession](https://learn.microsoft.com/en-us/windows/win32/api/msctf/nf-msctf-itfcontext-inwritesession)。
