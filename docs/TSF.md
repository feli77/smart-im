# TSF 局部上下文扩展

本分支把上下文来源改为小狼毫 TSF text store。Rime 继续负责拼音、分段、基础候选和上屏；Smart IM 只在既有候选内重排。此前 Lua 累积上屏历史的逻辑已移除。

## 一直提示未获取到局部上下文

先在仓库目录运行只读诊断：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/doctor_tsf.ps1
```

它分别检查正在运行的服务端、安装目录、Windows 实际注册的两种位数 TSF DLL，以及应用进程已经加载的 DLL；另核对 Lua 版本与服务心跳。应用检查只读取模块信息和 PE 文件头，不读取正文、拼音或请求文件。`stale` 表示应用仍加载旧映像，`matching` 只表示文件头标识与安装版本相符，不能证明上下文读取成功；权限不足时会报告无法检查。

2026-10-01 的首次故障是新版 Lua 配原版原生组件。用户随后已安装 `artifacts/weasel-tsf-0.17.4-test`，但仍报告所有应用缺少上下文。本轮检查发现 Code、QQ、Weixin 的进程仍加载旧 DLL，Edge 已加载当时安装的新 DLL；因此旧进程只能解释部分现象，不能把所有失败都归因于未重开应用。

随后在已加载诊断版 DLL 的 Edge（现场 PID 8784）捕获到持续的 `transitory_context`，确认代码把 `TS_SS_TRANSITORY` 直接当作不可读取条件，误拦了该应用。该标志只描述文档预期使用周期短；`TF_SS_TRANSITORY` 与它是同一常量。Chromium 的普通 text store 无条件设置这个标志，目的是限制韩文重新转换，仍提供正文 `GetText`。修复移除这项直接拒绝，保留有效读锁、焦点、范围、输入域和安全模式检查。依据：[Microsoft 标志定义](https://learn.microsoft.com/en-us/windows/win32/tsf/tf-ss--constants)、[Chromium 当前实现](https://raw.githubusercontent.com/chromium/chromium/main/ui/base/ime/win/tsf_text_store.cc)、[引入标志的提交](https://chromium.googlesource.com/chromium/src.git/+/8c9881f99aca8352ba3347c27006f59abb4f13da)。

另一项兼容修复来自隔离的真实 Windows TSF text store：未提供可选 input scope 时，属性对象的 `GetValue` 可返回 `E_FAIL` 且值为 `VT_EMPTY`。仅将此组合视为没有 scope 提示；其他失败、非空失败值以及明确的密码/PIN/私密 scope 仍拒绝读取。这是独立的兼容性缺陷，不替代上述 Edge 实机误拦证据。

本轮最终修复包 `artifacts/weasel-tsf-0.17.4-context-fix` 已完成构建，并于 2026-10-01 17:14 在本机安装及核验；含 64 位服务端、64/32 位 TSF DLL 和 SHA256 清单。适用范围是当前 x64 Windows、简体小狼毫 0.17.4；不包含或替换 `rime.dll`、插件、词库和用户配置。此前 `context-diagnostic` 包用于定位故障，不包含此项最终修复。测试包不是已发布版本。**从前版升级到新包必须新建备份并普通安装，不要把旧包的 `-Resume` 命令和旧备份套到新包上。**

保存正在输入的内容、暂时切到其他输入法后，在 **64 位管理员 PowerShell** 中进入本仓库，执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_weasel_tsf.ps1 `
  -PackageDir artifacts/weasel-tsf-0.17.4-context-fix `
  -BackupDir "$env:LOCALAPPDATA\SmartIM\weasel-backup-$(Get-Date -Format yyyyMMdd-HHmmss)"
```

脚本先完整备份本次涉及的文件和注册信息，再退出服务，更新三个安装目录文件和两个系统 DLL，最后核对哈希和注册路径。它要求现有 `Hant=0`，且两种位数的 COM 注册已指向对应系统路径。补丁没有改变 CLSID、profile、categories 或注册实现，因此更新沿用现有注册，不再调用 `WeaselSetup`。它不部署词库、不修改输入 profile、不从管理员会话启动服务。记下输出的备份路径。

脚本显示四个阶段：退出服务、更新安装目录、更新系统 DLL、校验。退出请求最多等待 10 秒，再给服务 10 秒完成保存；残留进程只有在路径、Windows 会话核对后才停止，确认全部退出后才更新。系统 DLL 先复制到同目录临时文件并校验，再将旧文件重命名、换入新版；失败时恢复旧文件。仍被应用加载的旧 DLL 可保留到重启清理。

以下是旧包安装中断时的历史恢复步骤，不用于升级新包。旧版脚本曾在 `WeaselServer.exe /q` 无限等待；之后加入超时的版本又遇到官方 `WeaselSetup /s` 超时。对**同一目标包**，若已经更新安装目录但系统 DLL 仍旧，应先确认旧安装进程已退出，再用原备份继续，不能将半安装状态另存为原始备份。以下仅对应旧包和原备份：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_weasel_tsf.ps1 `
  -Resume -PackageDir artifacts/weasel-tsf-0.17.4-test `
  -BackupDir "$env:LOCALAPPDATA\SmartIM\weasel-backup-20261001-155910"
```

`-Resume` 会检查原备份完整性、目标包和五个当前文件，只接受原版或目标版本的已知哈希；未知改动会停止，不覆盖。不要让旧、新安装脚本同时运行。阶段和错误写入原备份目录的 `install.log`；传统控制台选取文字可能暂停输出，此时按 Esc 解除，不必继续等待。

安装完成后关闭管理员终端，在**普通 PowerShell** 中执行：

```powershell
Start-Process -FilePath 'C:\Program Files\Rime\weasel-0.17.4\WeaselServer.exe' -WindowStyle Hidden
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/doctor_tsf.ps1
Start-Process tests/manual/tsf_context.html
```

彻底退出并重新打开测试应用；浏览器也要退出全部进程后再开。已经加载旧 TSF DLL 的进程不会因重启服务而切换到新版，必要时保存工作后注销、登录。再次检查 doctor 的 `[loaded TSF]` 结果，不能只看磁盘文件检查。诊断成功不代替实际输入验收。先在验收页预置正文中把光标放到“计划”后，输入拼音，再测试移动光标和切换两个输入框；详细通过条件见下方验收表。本轮已部署的 Lua 与仓库一致，无需再部署。用户已确认最终修复后使用正常，诊断也记录到读取和发送成功；这不代表所有应用及每项失效场景均已逐一人工验收，证据范围见 [验证记录](VALIDATION.md)。

### 临时开启原生读取诊断

诊断包和最终修复包默认不记录日志。需要定位失败阶段时，在普通 PowerShell 中显式开启：

```powershell
$traceDirectory = Join-Path $env:LOCALAPPDATA 'SmartIM\tsf-trace'
New-Item -ItemType Directory -Path $traceDirectory -Force | Out-Null
New-Item -ItemType File -Path (Join-Path $traceDirectory 'enabled') -Force | Out-Null
```

在重开的测试应用中输入几次拼音即可。日志位于该目录下的 `<进程号>-<线程号>.log`，每线程只保留最近 48 条固定阶段、HRESULT 和计数/长度记录，不包含正文、拼音、候选或上下文 token。文件名中的进程号可与 doctor 输出对应。读取阶段和发布阶段分别记录，便于区分 TSF 读取失败与传输失败；这些元数据不能代替请求正文的专用测试验收。

诊断会产生同步文件写入，只应短期开启。复现完成后关闭：

```powershell
Remove-Item -LiteralPath (Join-Path $env:LOCALAPPDATA 'SmartIM\tsf-trace\enabled') -ErrorAction SilentlyContinue
```

开关最多缓存一秒；删除后不再产生新日志，已有日志保留供排查。没有日志时先核对应用是否加载新包、是否使用 TSF，以及目录权限，不能据此认定读取成功。

若需回滚，在管理员终端使用安装时实际输出的备份路径：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_weasel_tsf.ps1 `
  -Rollback -BackupDir '实际备份路径'
```

随后同样从普通终端启动服务并重开应用。回滚恢复安装目录和系统目录的原生文件，沿用已核对的现有注册，不导入整棵用户输入法注册树。回到原版原生组件后，新 Lua 会恢复“未获取到局部上下文”的降级行为。

## 小狼毫源码与补丁

以官方 [rime/weasel 0.17.4](https://github.com/rime/weasel/tree/0.17.4) 为基线，固定提交 `9cc96e20dc71b80876b12f689bb5863c76c2a7ed`。独立小狼毫源码仓库的本地开发分支是 `codex/tsf-local-context`，没有向小狼毫上游合并或推送；可复现补丁保存在 Smart IM 的 [patches/weasel](../patches/weasel)，随 Smart IM 源码发布。本机 `artifacts` 下的测试二进制不随 Git 标签发布。用户已安装最终修复包，具体构建、安装及实机证据见 [验证记录](VALIDATION.md)。

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

TSF 读取权限由应用 text store 决定，无法保证每种应用都暴露完整局部正文。`TS_SS_TRANSITORY` 本身不再作为拒绝条件，但这不保证旧式 CUAS/IMM 兼容控件也提供完整周边正文。受保护输入、密码/PIN/私密 input scope 等拒绝采集。此实现没有 UI Automation、剪贴板或全文读取后备方案。

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
