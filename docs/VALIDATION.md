# 验证记录

## 2026-10-01：v0.5.0 发布检查

用户确认最终修复使用正常，并确认 `v0.5.0` 发布到 `origin`（`feli77/smart-im`）。版本在 Python 包、Rime schema 和锁文件中同步更新；变更日志由 git-cliff 生成。

- Python / Lua 全量测试：**460 passed**；Ruff 和 `git diff --check` 通过。
- 原生 x64：解析器 **66 checks**、真实命名管道 **44 checks**、COM 范围与真实 Windows TSF 回归通过。最终原生生产代码的 x64/x86 完整构建及两种位数回归证据见下节。
- Windows PowerShell 5.1 安装器隔离测试：进程退出/超时 **3 场景**、续装 **41 项**、文件替换 **9 场景**通过；发布检查未再次改变系统安装或运行中的服务。
- 临时原生诊断已关闭，用户原有 README 未提交格式改动不纳入本次发布。

## 2026-10-01：Edge 实机确认 transitory 标志误拦截

17:08 前后，已将诊断包 `artifacts/weasel-tsf-0.17.4-context-diagnostic`（原生提交前缀 `4af65`）安装到本机，三个安装目录文件和两个系统 DLL 均完成校验。该次备份为 `%LOCALAPPDATA%\SmartIM\weasel-backup-context-20261001-170755`；普通权限服务启动后的现场 PID 为 2860。此记录确认诊断包安装，不等于后续最终修复包已经安装。

新 Edge 进程 PID 8784 的原生诊断连续返回 `transitory_context`，确认此进程在获取正文前被本实现的 `status.dwStaticFlags & TS_SS_TRANSITORY` 判断拦截。Windows SDK 中 `TF_STATUS` 是 `TS_STATUS` 的别名，两组 TRANSITORY 常量均为 `0x4`，不存在位值混用；错误在于将生命周期提示当作拒绝正文读取的依据。

[Microsoft 文档](https://learn.microsoft.com/en-us/windows/win32/tsf/tf-ss--constants)将其定义为预期短使用周期。[Chromium 当前源码](https://raw.githubusercontent.com/chromium/chromium/main/ui/base/ime/win/tsf_text_store.cc)的普通 `TSFTextStore::GetStatus` 无条件设置 `TS_SS_TRANSITORY | TS_SS_NOHIDDENTEXT`，邻近的 `GetText` 仍提供文档缓冲内容；[原始提交](https://chromium.googlesource.com/chromium/src.git/+/8c9881f99aca8352ba3347c27006f59abb4f13da)说明该标志用于限制韩文重新转换。因此这是具体、已观察到的误拦截，不再只是未安装或缺少可选 input scope 的推测。

最终修复移除 transitory 直接拒绝，保留安全模式、输入域、有效读锁、焦点与范围检查。`artifacts/weasel-tsf-0.17.4-context-fix` 已从干净的原生提交 `3427e2ed3c1be4f394e9ea15c91c9d167fab1e8b` 完成两种位数构建，并于 17:14 完成管理员安装和五文件/注册路径校验；备份为 `%LOCALAPPDATA%\SmartIM\weasel-backup-context-fix-20261001-171227`，随后从普通权限重新启动服务。

- x64/x86 原生解析器各 66 项、COM 范围测试和真实 Windows TSF 回归通过。新的真实 TSF 用例确认携带 `TS_SS_TRANSITORY | TS_SS_NOHIDDENTEXT` 的 store 仍能读取两侧各 14 字符的预存正文；包含已选中文及拼音的合成 replacement range 被完整排除，六类敏感 scope 仍拒绝。此用例的组合范围是合成范围，不是实际 TIP 创建的 composition。
- 诊断日志的关闭、开启、60 条只保留最后 48 条、关闭后不写入行为在 x64/x86 `/W4 /WX` 独立测试均通过。
- 九提交源码补丁在独立 worktree 回放后与实现 tree 完全一致：`f237901a569b3f2b8b00f2703e0d4b36e5d078bf`。

用户随后确认修复后使用“没问题了”。关闭临时诊断前，最新记录中同一线程有 11 次 `read_ok` 和 11 次 `publish_ok`，确认实际读取及发送链路已恢复；未采集正文、拼音或候选内容。本次用户反馈没有分别记录全部应用、组合/已选段、光标和焦点切换用例，相关精确边界仍以自动化覆盖及下方专项验收表为准，不将反馈扩大为全应用逐项验收。临时 `tsf-trace/enabled` 已移除；旧式 CUAS/IMM 兼容控件仍可能不暴露完整上下文。

## 2026-10-01：安装后仍无上下文的读取诊断

用户已完成前版 `artifacts/weasel-tsf-0.17.4-test` 安装并启动服务，仍报告 VS Code、记事本、浏览器和聊天软件都提示缺少上下文。当前磁盘上的前后端原生标记、已部署 Lua 和服务心跳检查通过，因此不能继续沿用首次排查的“原生组件未安装”结论。

- 新增应用已加载 DLL 检查：只读取模块信息和前 1024 字节 PE 文件头，以架构、时间戳和映像大小对照安装版本。现场快照中 Code、QQ、Weixin 加载旧映像，Edge 加载与当时安装版本相符的映像。此检查不是完整内存哈希，也不能证明 TSF 读取成功；受权限限制的进程显示无法检查。
- 隔离真实 Windows TSF manager / 自建 `ITextStoreACP` 复现：未提供可选 input scope 时，`GetAppProperty` 可取得属性对象，但 `GetValue` 返回 `E_FAIL`，值仍为 `VT_EMPTY`，旧实现因此拒绝读取普通正文。新实现仅将这一组合视为无 scope 提示；其余 API 失败、失败时的非空值，以及明确密码/PIN/私密 scope 继续拒绝。该探针使用合成文本，不是用户应用现场根因证明。
- 独立命名管道编译生产 `WeaselClientImpl.cpp` 与 `PipeChannel.cpp` 验证通过：真实 StartSession 正文含 `client_type=tsf`，分配的 session 编号贯穿按键与上下文消息，合成上下文及空包失效完整传送，独立上下文连接不覆盖待消费的按键/schema 响应。没有连接用户正在使用的管道。
- 真实 `rime.dll` 加载项目 Lua 的隔离合成候选探针确认：合法原子属性经按键处理仍可供 filter 使用，空属性才产生 `NO_CONTEXT`。这不包含实际应用 TSF 读取。
- 另尝试在独立后台进程激活已注册 Weasel profile，仅使用 `TF_IPPMF_FORPROCESS`，未修改系统默认、注册或取得前台焦点。profile 激活成功，但 Windows 未加载键盘 TIP，`GetForeground` 返回 `S_FALSE`，合成 TestKeyDown 均未处理。测试完成后已失活和清理，不能将该结果当作真实键事件验收。
- 新原生诊断由 `%LOCALAPPDATA%\SmartIM\tsf-trace\enabled` 显式开启，每线程最多保留 48 条固定阶段、HRESULT、计数/长度，不记录正文、拼音、候选或 token。单头文件以 MSVC `/W4 /WX` 编译通过；隔离日志测试写入 120 条后准确保留最后 48 条。诊断有同步磁盘开销，应在复现后删除 `enabled` 关闭。

该阶段生成诊断包 `artifacts/weasel-tsf-0.17.4-context-diagnostic`，随后已安装用于上述 Edge 实机排查；最终修复包为 `artifacts/weasel-tsf-0.17.4-context-fix`。从已安装前版升级时，必须使用新备份普通安装，不能把旧包的 `-Resume` 命令套到新包。步骤见 [安装与诊断](TSF.md#一直提示未获取到局部上下文)。**真实应用仍待验证：** 已有正文可读、正文/组合/已选分段不重复、移动光标及切换控件后旧推荐立即失效。当前证据不能宣称这些端到端场景已通过。

以下各节保留当时阶段的证据和未完成项；其中“尚未安装”或“待续装”不表示用户此后没有完成安装。

## 2026-10-01：注册器超时后的续装与系统 DLL 更新

后续截图确认 `WeaselSetup /s` 的 60 秒超时及进程树清理已触发，Esc 后终端显示了该错误；不是本次有限等待失效。故障后安装目录三文件为目标版本、两个系统 DLL 为原版，备份完整。没有取得管理员进程堆栈，官方 Setup 内部具体阻塞位置仍不确定。

核对补丁相对 0.17.4 的 `Register.cpp`、`Globals.cpp/.h`、`DllMain.cpp` 无变化，现有两架构 CLSID 注册路径正确。因此当前安装脚本改为沿用注册，只更新已核对路径的系统 DLL，不再执行 Setup 或 regsvr32。原始备份可用 `-Resume` 继续，未知文件哈希会拒绝；阶段与错误另写备份目录 `install.log`。

- Windows PowerShell 5.1 的续装隔离测试 **41 项通过**，覆盖初始、混合、已完成状态，未知版本、损坏备份、错误包拒绝。对本机真实原备份及五个当前文件的只读前置核验通过。
- 文件替换测试在 PS5.1 / PS7 各 **9 项通过**：先暂存并校验、已加载文件换入、幂等、错误哈希拒绝、复制和重命名失败恢复，以及新目标已被打开时的恢复。
- 独立 64 位 PS5.1 进程把原系统 DLL 复制到隔离目录，用真实 `LoadLibraryW` 加载该副本后成功替换为目标 DLL；旧映像保留到 `FreeLibrary` 后清理。系统目录原文件哈希前后相同，没有注册或激活输入法。
- 进程超时测试再次通过，3 秒超时后约 4 秒完成测试子进程及后代清理，父进程和无关进程存活。所有 PowerShell 文件语法及 `git diff --check` 通过。

该阶段结束时待用户在管理员会话执行原备份续装。用户随后已完成安装，安装后仍无上下文的进展见本页最新记录。

## 2026-10-01：安装退出进程无限等待修复

安装停在 `Installing:` 时，备份完整，三份安装文件仍与原始备份一致，两个系统 DLL 尚未替换。旧脚本阻塞在 `Start-Process WeaselServer.exe /q -Wait`，其后的 10 秒检查不能限制该调用。旧安装 PowerShell 退出后，退出助手进程仍然存在；当前权限不足以读取其内部调用栈，不能确定具体卡在管道连接、写入或读取哪一步。

安装脚本改用有限进程等待，区分退出请求、服务保存宽限和注册阶段。核对安装路径及 Windows 会话后才终止残留服务进程，复制前再次确认已退出。注册超时只清理该次启动的进程树，检查清理结果，并保留原备份。

`tests/native/test_installer_process.ps1` 仅从 AST 提取进程等待函数，不执行安装。Windows PowerShell 5.1 的真实子进程测试验证退出码 0/23；无限等待的子进程及其孙进程在 3 秒超时后的有限清理期退出，父测试进程和无关进程仍在。此测试不停止 Weasel，不修改系统 DLL 或注册表。实际管理员安装仍需重试验收。

## 2026-10-01：持续缺少上下文的部署诊断与完整原生构建

故障发生时，真实 Lua 与仓库一致、Smart IM 心跳正常，但运行中的 `WeaselServer.exe`、安装目录两种位数 TSF DLL、实际注册的 System32/SysWOW64 DLL 都没有原生上下文扩展。这解释了为什么每次输入都退回 `NO_CONTEXT`。检查只读取版本、特征标记、哈希与心跳，不读取用户正文。

- 修复可选 input scope 属性返回 `S_FALSE`/`E_NOTIMPL` 时误拒绝普通正文的问题；安全标记、明确私密 scope 和其他读取故障继续拒绝。原生 COM 测试覆盖无属性、空属性、一般 scope、密码/PIN 和失败路径。
- 完成 VS 2022 / MSVC 14.44、Windows SDK、Boost 1.84 静态库、官方 librime 1.17.0 导入库下的完整链接：64 位 `WeaselServer.exe`、64 位 `weaselx64.dll`、32 位 `weasel.dll`。修复完整链接暴露的 input-scope GUID 定义和资源头文件依赖问题。
- `scripts/build_weasel_tsf.ps1` 实际运行成功，生成 `artifacts/weasel-tsf-0.17.4-test` 与 SHA256 清单。两种位数 DLL 分别在对应位数进程中 `LoadLibrary` 成功，四个 COM 导出存在；没有调用注册或激活。服务端所导入的 Rime、WinSparkle 接口均能在本机现有 DLL 找到，运行时 DLL 不随测试包替换。
- 原生 parser **66 checks**、真实 named pipe **44 checks**、TSF COM 范围及输入域测试通过。在隔离的真实 Windows TSF manager / 自建 text store 中，支持 scope 的控件路径可进入同步 READ session，生产 helper 读取两侧正文成功，本方 READWRITE 通知可由 `InWriteSession` 识别。该探针未注册输入法，不代表真实应用键事件验收。
- 原生四提交补丁在新的 0.17.4 worktree 使用 `git am --keep-cr` 重放成功，最终 tree 与开发分支一致：`6cb49e4c3b827588543df449218f033e9bc426f3`。
- 诊断、构建和安装脚本均通过 Windows PowerShell 5.1 语法检查。安装脚本的真实包哈希、位数、特征标记检查通过；错误哈希和原版 DLL 被拒绝。非管理员执行在任何备份、停服或覆盖前拒绝，验证后原服务 PID 不变。安装和回滚代码已独立审查，尚未执行管理员安装或真实回滚。

**待实际执行：** 管理员安装测试包、普通权限启动服务、重开应用，然后验证已有正文、组合/已选段去重，以及光标/焦点变化失效。步骤见 [安装与诊断](TSF.md#一直提示未获取到局部上下文)。本轮未修改 Python/Lua 业务代码，沿用下方 460 项测试结果；原生完整链接和安装仍是两个不同阶段。

## 2026-10-01：TSF 局部上下文重构

本阶段以小狼毫官方 0.17.4 固定提交为基础开发，源码补丁与复现步骤见 [TSF 扩展](TSF.md)。此初始测试阶段未替换系统输入法、未修改实际 Rime 用户目录；此后的安装情况见上方记录。

- Python / 真实 Lua / 文件信箱全量测试 **460 passed**。新增测试覆盖原有正文直接读取接口、独立后文、已选分段只拼接一次、未知前缀拒绝、同文不同 token、切换输入框的立即失效及模型计算期间取消。现有候选对象、混合范围、个人学习和失败回退测试保留。
- Windows 原生 `ITfRange` COM 测试执行实际局部读取 helper：已有正文、整个 composition/占位空格排除、覆盖选区、两侧 128 UTF-16 单元和 region 边界、代理对截断、失败或短读回退通过。text store 由测试实现，不代表真实应用 TSF 验收。
- 原生 packet parser 与真实 Win32 named-pipe 测试通过。管道测试编译实际 `PipeChannel.cpp`，验证两连接并发隔离、完整 token/正文、缺失正文和短 header 拒绝、上下文通知不覆盖尚未消费的按键响应。
- 本机 MSVC 2022 对修改的 TSF、IPC 和 Rime 桥接源文件完成对象文件编译；这不是最终 DLL/EXE 链接或安装包验证。
- 只读加载已安装小狼毫 0.17.4 的 `rime.dll`，在每次新建的隔离目录运行 Lua 属性探针：`property_update_notifier` 可用；回调内已可读到完整新值；空属性和同值发布均触发通知。仓库提供 `scripts/check_librime_property.py` 复现，所有生成内容写入 `artifacts`。
- Ruff、格式检查、锁文件检查和 Python wheel/sdist 构建通过。此轮未重跑真实 Qwen 语义评测。

**尚未完成：** 小狼毫完整二进制链接/安装，以及记事本、浏览器多输入框、富文本编辑器的真实 TSF → Rime → 服务 → 候选窗口验收。三项重点行为已由分层自动化测试覆盖；真实应用支持与焦点通知时序仍需按 [专项验收表](TSF.md#真实应用验收) 记录，不能把模拟 text store 结果当作实机证明。

```powershell
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv lock --check
uv build
./scripts/verify_weasel_native.ps1 -BoostRoot ./artifacts/native-deps/boost_1_84_0 -CompileSources
uv run python scripts/check_librime_property.py --rime-dir "C:/Program Files/Rime/weasel-0.17.4"
```

## 2026-09-30 及更早记录

以下保留已有模型诊断和原生 Rime 验证证据；其中“上屏历史”、400 tests 和旧部署说明描述重构前版本，不代表本次 TSF 补丁已经完成实机验证。

## 自动更新与推荐高亮

- 已将 Tab 手动应用改为 Windows 服务发布结果后通知小狼毫刷新，推荐词移到首位并使用原生高亮，提示“★ AI 推荐”。用户已经移动候选高亮时不抢回选择；移开首项后清除推荐提示。输入变化、取消和模式切换会使旧结果失效。
- 真实 Lua 与文件信箱集成测试通过：服务通知后直接检查已显示候选，不依赖额外人工按键或再次调用 filter；覆盖上下文、混合拼音范围、重复候选、元数据保留、模型失败和过期结果。
- Windows API 模拟测试覆盖前台、焦点、光标、键盘布局、输入时间、按键状态和 SendInput 失败；服务测试覆盖按键尚未松开时等待、发布后再通知及通知去重。另已对本机 user32 完成只读捕获验证。
- 使用本机小狼毫 0.17.4 的 `rime.dll`，在独立临时用户目录通过真实 C API 创建会话，加载本项目 Lua 与测试候选。写回 `3,1,2` 后，原生 `Select` 事件将「世界、视界、时节」更新为「时节、世界、视界」，高亮索引为 0，原生 preedit 包含“★ AI 推荐”。重复通知不重复刷新、不提交或学习；清空组合后请求与响应被撤销。
- 原生验证没有修改实际 Rime 用户目录、重新部署或向应用发送全局按键；完整 SendInput → TSF → 应用候选窗口仍需按 [Windows 人工验收](RIME.md#windows-人工验收) 实测。

## v0.2.1 模型诊断背景

以下记录来自此前针对 Qwen3 0.6B 输入延迟和“铁血 → 展示”“解决 → 反感”的首选错误排查。当时采用 Tab 手动应用，用户确认已先上屏前缀并按 Tab，因此这两例主要通过真实模型调用排查，不能归因于未按 Tab。

## 修复与证据

- 旧提示要求小模型一次生成整个索引排列；直接传入正确上下文仍能复现误选，1.7B 旧提示也会把“解决”的首选排成“反感”。改为比较带上下文后缀的短语，仅返回最佳索引，客户端提升首项、保留其余 Rime 原序。用户确认采用此策略，Ollama 默认值统一为 1.7B。
- 服务原先每轮立即开始推理，连续按键的中间态可能占用模型。现在按实际请求内容等待 80 ms 稳定窗口，确认事件和心跳照常处理。已经开始的请求不会中断，但过期结果不会应用。
- Lua 提交、取消及关闭 AI 原先只清内存状态，信箱里仍有无用请求；现在同时移除本会话请求和响应。测试验证等待中的请求不再推理，进行中的结果会被丢弃。
- 另发现部分已选、尚未上屏的前缀没有进入上下文，现已加入候选范围前连续的已选分段，并在前缀变化时使旧响应失效。它是另一个缺陷，不能解释用户本次已上屏场景。
- 无上下文、无学习的恒等结果显示“缺少上下文，保留原候选”，避免误以为模型已经做了语义判断。

## 本机真实模型诊断

环境：Windows、Python 3.12.11、Ollama 0.35.0、NVIDIA RTX 5060 Laptop GPU（8151 MiB，驱动 617.14）。`ollama ps` 显示 0.6B 和 1.7B 均为 `100% GPU`、4096 上下文，显示占用约 930 MB / 1.7 GB；服务日志确认 CUDA offload。当前高延迟不能简单归因为未启用 GPU。

使用 [固定诊断集](../tests/data/ollama_cases.json) 的 28 个人工例子，每组重复 2 次，共 56 次调用。候选顺序人为固定，原序基线为 14/56；该数字不代表真实 Rime 首选准确率。基线代码来自当时修改前的 `ae28bc2`。请求串行执行，未特意卸载模型，不声称测到了冷启动。

| 策略 | 模型 | 首选命中 / 56 次 | 首次调用 | 后续 p50 | 后续 p95 | 最大调用耗时 |
|---|---|---:|---:|---:|---:|---:|
| 原整组排列 | Qwen3 0.6B | 10 | 117.7 ms | 125.7 ms | 168.3 ms | 186.8 ms |
| 原整组排列 | Qwen3 1.7B | 47 | 172.2 ms | 153.9 ms | 212.7 ms | 235.3 ms |
| 首选提升 | Qwen3 0.6B | 50 | 92.1 ms | 75.1 ms | 100.8 ms | 104.0 ms |
| 首选提升（最终实现） | Qwen3 1.7B | 54 | 111.7 ms | 100.8 ms | 139.4 ms | 166.3 ms |

各组请求/解析错误均为 0。最终 1.7B 两轮都把“铁血”候选选为“战士”、“解决”候选选为“方案”；剩余错误是把“领导不能滥用手中的权力”选成“权利”，两轮重复同一错误。0.6B 也明显受益于简化任务，但另一些候选池仍不可靠。

**长尾没有得到保证：** 首选提升的先前一轮 1.7B 诊断同样为 54/56，p50 114.9 ms、p95 162.1 ms，却有一次 9314.0 ms 调用，原因未定位。最终一轮没有重复该长尾，不代表它被消除了。本项目不控制 Ollama 调度、系统负载或首次模型加载。

这些是参与开发诊断的小样本，不能推广成真实使用 96.4% 准确率。测量覆盖 `OllamaReranker.rerank` 的本机 HTTP 调用，**不包含 Rime 候选生成、80 ms 稳定窗口、文件轮询、Tab 和界面刷新**。完整输入链路延迟仍需小狼毫实机验收。

可复现最终实现：

```powershell
uv run python scripts/evaluate_ollama.py --model qwen3:1.7b --repeat 2
uv run python scripts/evaluate_ollama.py --model qwen3:0.6b --repeat 2
# 默认只打印；需要保留报告时显式指定 --output 路径
```

脚本不下载模型，也不读取实际输入文本。当前默认使用 `qwen3:1.7b`；模型接口与边界见 [模型说明](MODEL_CARD.md)。

## 自动化与构建

仅保留 Ollama 接口后的完整测试为 **400 passed**。wheel 内容检查确认只包含当前 Python 代码与 Rime 资源，没有模型数据目录，包元数据无第三方运行时依赖。干净虚拟环境安装验证了默认 Ollama 配置及无上下文 CLI 保持原序；该验证未调用真实模型。

- 真实 Lua 运行时 + 模拟 Rime 对象，验证已上屏/部分已选上下文完整透传、服务通知后自动应用排列及保留原 Candidate 对象；真实语义效果另由上述模型诊断验证。
- 信箱去抖、取消失效、最新会话优先、推理时心跳、学习双开关与事件处理、无服务回退。
- 本地 HTTP 模拟验证完整候选池传递、最佳索引类型/范围、重复候选、尾部稳定、无上下文跳过、总请求超时、截断和错误响应回退。
- 长上下文只在顶层完整发送一次，各候选附最近 16 字；最大合法候选池扩大窗口预算以避免静默截断。普通请求保持 4096。
- 安装、默认 Ollama CLI、个人数据管理和依赖边界回归。

```powershell
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv lock --check
uv build
```

## 部署与剩余验收

当前代码清理未替换正在使用的小狼毫安装。使用本版本时，先停止旧 Smart IM 服务，再在对应工作目录运行：

```powershell
uv run smart-im install-rime --force
# 在小狼毫菜单执行“重新部署”，然后启动新服务
uv run smart-im serve
```

按 [Rime 使用说明](RIME.md) 复测用户两例、连续输入、取消/删除、部分选词、Tab 和数字选词，以及多应用/控件切换。仍需独立真实候选评测集、完整链路延迟及 Windows 文件竞争验收；此前的有限会话历史不能等同于编辑位置周围的正文，现已由上述 TSF 局部读取替换。
