# REA 与 Mac 微信消息通道初步分析

检查日期：2026-11-04。目标为本机 `/Applications/WeChat.app`，版本 4.1.15。

## 已核实的证据

初查阶段只读取应用安装包和项目代码。后续经用户授权，已使用独立调试副本读取可见前端控件并完成一次文件传输助手发送测试；最新进展见本文末尾和《前端控件定位组件说明.md》。没有读取聊天数据库或调用微信业务收发接口。

- 官方项目：https://github.com/morluto/rea ，官网：https://rea.tools/ 。REA 为 CLI/MCP 分析工具，原生反编译需要 Hopper/Ghidra/IDA 等分析引擎。不能因为完成安装就认为已经有微信接口。
- 本机微信启动程序为包含 arm64/x86_64 的 Mach-O。`Contents/Resources/wechat.dylib` 是约 351 MB 的业务库。应用包含 WeChatAppEx、Chromium 风格 pak 和网页运行组件，但没有发现可直接分析的 ASAR 聊天前端工程；不能据此判断聊天 UI 就是 DOM，也不能断言包内完全没有脚本。
- 在业务库静态字符串中找到以下候选。地址均为 FAT 文件的十六进制文件偏移，不是进程虚拟地址，不是经过确认的可调用函数入口。

| 文件偏移 | 字符串 | 可用于调查的方向 |
|---|---|---|
| 0xa8c3340 | mmui::ChatVoiceItemView | 语音气泡视图 |
| 0xa8c3970 | mmui::ChatImageGroupItemView | 图片组合气泡视图 |
| 0xa8c36a8 | mmui::ChatBubbleItemView | 通用气泡视图 |
| 0xa8c4818 | mmui::ChatInputField | 输入控件 |
| 0xa8c4b70 | mmui::ChatInputView | 输入区域 |
| 0x92b28aa | kChatMessageVoiceTranslationMenu | 聊天语音转文字菜单 |
| 0xa1baa74 | QQuickWidget | 存在 Qt Quick 相关字符串；具体使用范围待证实 |

这些名称不能证明稳定消息 ID、发送接口、附件路径或转写接口已经可用。需要反编译交叉引用和实际行为验证。完整候选与目标 SHA256 保存在 `artifacts/rea-research/wechat-static-candidates.json` 和 `wechat-ui-candidates.json`。

## 当前机器的工具条件

- PATH 有 Node.js 24.14.1、npm、otool、strings。
- npm 官方元数据显示 rea-agents 最新版本为 6.8.0，要求 Node `^22.23.4 || ^24.22.1 || >=26.12.1`；本机当前 Node 不满足这个版本要求。
- 初查时没有 REA 和原生引擎。继续探索后，已在 `artifacts/rea-tools` 隔离安装官方 REA 6.7.0，兼容本机 Node；不修改全局 Node 或 Codex 配置。
- 后续环境路线：准备满足要求的独立 Node 运行时，在项目工具目录固定 REA 版本，再配置一个原生分析引擎。先执行 provider doctor，分析对象只选择上述业务库。无需为初查更改微信安装包。

## 代码现状

`core/desktop_vision.py` 已定义 text/image/sticker/voice/file/system，并让视觉模型返回每个气泡矩形。

`macos/wechat_vision_agent.py` 目前将图片/表情的文字描述拼接到回复输入，未获取原始图片；纯语音/文件批次直接跳过。因此支持识别“这是语音”并不等于已支持理解语音内容。

## 建议实施顺序

1. 先验证一条指定语音的微信自带“转文字”：定位语音气泡及转写按钮，操作后重新读取对应转写区域，并把它绑定到原气泡，不能当成另一条新消息。失败应明确记录未转写，不编造内容。
2. 图片：以气泡定位打开大图，验证图像来源及会话；拿到清晰图片后，作为图像输入交给视觉模型。缩略图描述仅作为降级路径，不能冒充原图识别。
3. 若微信无法转写且能通过可验证的途径拿到音频，再增加音频解码与 ASR。没有音频数据时，安装 Whisper 等转写模型也无法解决输入问题。仅有截图不能恢复语音。
4. 文本及动作：调查输入控件、发送按钮以及辅助功能实现的交叉引用，验证是否能取得结构化控件。静态类名不能直接作为 macOS 辅助功能选择器。
5. 统一消息事件结构：account_id、conversation_id、message_id（可获取时）、sender、kind、text、attachment、transcript、source、status。每用户摘要和历史继续复用现有存储；附件识别先完成，再进入多轮回复生成。
6. 有结构化消息 ID/类型通道时，减少全窗口重复识别。当前反复调用视觉 API 的耗时、断线问题，不会因安装 REA 自动消失。

成功标准：文本、图片、语音各有可复现样本；同一条消息不重复入历史；转写不产生第二条回复；发送后确认新己方气泡；失败阶段可见。深入分析尚未验证任何可调用的微信私有接口。

## 官方参考

- https://github.com/morluto/rea
- https://rea.tools/
- https://raw.githubusercontent.com/morluto/rea/main/docs/installation.md
- https://raw.githubusercontent.com/morluto/rea/main/docs/cli.md

## 继续探索：Qt 元数据已结构化恢复

新增 `macos/analysis/extract_qt_metadata.py`。直接验证 Qt 5 的 64 位 QByteArrayData 结构、相对地址、字符串长度和终止符，两个架构均恢复了 974 个 mmui 界面类。对 5 个重点类继续跟踪候选 staticMetaObject 指针，验证 revision-8 方法表，恢复 51 个信号/槽的方法签名；两个架构的 51 个方法签名完全一致。

这次不是根据相邻字符串猜测方法归属，而是依据 Qt 方法表的名称、参数、信号/槽标记。解析器对当前目标的 chained pointer 使用低 36 位候选地址并验证指向结构，不是通用 dyld fixup 解码器。结果仍不包含方法正文，也不证明可以从进程外直接调用。

| 类 | 已恢复的重点元数据 |
|---|---|
| mmui::ChatInputField | signal `send(SendActionType)` |
| mmui::ChatInputView | slot `Send(SendActionType)`；signal `OnSendResult(bool)`；signal `InputTextChanged()` |
| mmui::MessageView | signal `OnNewSessionMessageLoaded(std::string)`；signal `ViewModelChanged(std::shared_ptr<ChatMessageViewModel>)` |
| mmui::ChatSessionList | signal `CellClicked()`；signal `CellHoverEntered(QRect,int)` |
| mmui::ChatVoiceRecordView | signal `TranscriptionReady(QString)` |

**必须区分：** `ChatVoiceRecordView` 是录音输入组件，其 `TranscriptionReady` 不能当作收到好友语音后的转写入口。`InsertMessage(kernel::MessageWrapper)` 在多个类中出现，具体语义还要读方法正文，不能仅凭名称断言它就是来信事件。

arm64 的 `ChatInputView` 静态分发入口镜像地址为 `0x12fbf4`；`MessageView` 为 `0x130390`。这是静态镜像地址，运行时还需要 ASLR 映射、有效对象及类型定义，不能直接拿来作为发送 API。

完整数据：`artifacts/rea-research/qt-ui-metadata.json`；重点签名与定位：`qt-chat-methods.json`。

复现命令：

```sh
python3 macos/analysis/extract_qt_metadata.py /Applications/WeChat.app/Contents/Resources/wechat.dylib --output artifacts/rea-research/qt-ui-metadata.json --methods-for mmui::ChatInputView --methods-for mmui::ChatInputField --methods-for mmui::MessageView --methods-for mmui::ChatVoiceRecordView --methods-for mmui::ChatSessionList
```

Qt 格式对照：[元对象字段与标记](https://raw.githubusercontent.com/qt/qtbase/5.15/src/corelib/kernel/qmetaobject_p.h)、[参数类型编号](https://raw.githubusercontent.com/qt/qtbase/5.15/src/corelib/kernel/qmetatype.h)。

工具环境修复：REA 在默认沙箱下首次编译 macOS 进程归属辅助程序时无法写全局 Clang 缓存；使用它支持的 `REA_DARWIN_MODULE_CACHE_PATH=/private/tmp/whalegirl-rea-module-cache` 后完成诊断准备。原生静态命令还需要进程表权限，已按工具需求申请受控执行。


## REA / Hopper 实际运行状态

- REA 6.7.0 安装完成（174 个依赖，安装时禁用了 npm 生命周期脚本）；CLI 可正常执行。
- REA `inspect-macho` 已成功生成证据，选中 arm64，确认该库 UUID 为 `9CEB6CCF-81A5-3B7F-8FB5-B4256FE67EEB`。输出保存在 `artifacts/rea-research/rea-macho.json`。
- Hopper 官方包已安装到 `/Users/lhs/Applications/Hopper Disassembler.app`，安装器执行了官方来源、摘要及代码签名验证。选择免费演示模式，没有购买授权。
- 修正新安装 Hopper 的 PythonPath：其默认值指向不存在的 Xcode Python；改用本机现有的 CommandLineTools Python 3.9。界面已确认 `Python library successfully loaded`。
- 为保证地址架构一致，生成业务库的独立 arm64 副本 `artifacts/rea-research/wechat-arm64.dylib`，原微信程序保持原样。
- 已启动对 `ChatInputView` 静态分发入口 `0x12fbf4` 的定点反编译。结果文件为 `chat-input-dispatch-decompile.json`，以文件中的实际成功/错误结果为准。

本次读取微信辅助功能界面树仍只能看到主窗口按钮和菜单。因此恢复 Qt 内部方法，不能直接视为解决了外部辅助功能访问。

已交叉核验：Qt 解析器的目标 SHA256 与 REA Mach-O 证据中的 SHA256 完全相同；5 个重点类的 51 个信号/槽方法签名在 arm64 与 x86_64 中逐项一致。


## 首个原生反编译结果

REA/Hopper 已成功返回 `0x12fbf4` 的伪代码（`chat-input-dispatch-decompile.json`，可读文本 `chat-input-dispatch.pseudo.txt`）。为避免全库分析持续占用机器，后台扫描在第一遍过程中人工停止，本次结果仅用于已识别的局部函数，不能宣称全库分析完成。REA 自身也标明结果依赖 Hopper 的静态分析质量。

分发函数在 InvokeMetaMethod 分支按方法序号进行 switch。Qt 元数据中序号 8 为槽 `Send(SendActionType)`；该分支在伪代码中转入 `sub_839608()`。因此 `0x839608` 是本版本 arm64 中该发送槽的已定位实现入口；尚未确认其内部的业务发包层。

此关联由方法表与真实反编译分支共同支持，不是通过函数名猜测。继续定点分析 `0x839608` 的输出保存在 `chat-input-send-decompile.json`。


## 已读取发送槽正文

`0x839608` 的真实伪代码已成功取得，保存为 `chat-input-send-decompile.json` 和 `chat-input-send.pseudo.txt`。这个槽在处理输入区域状态后，将发送动作参数及临时数据传给对象成员所指向的后续实现 `sub_8811d0`。这支持“Qt 发送槽已定位”，不支持“已经恢复独立发包接口”。Hopper 对寄存器和字段的推断仍需后续汇编核验，暂不把偏移强行命名为业务字段。

该函数的两个静态日志字符串按其实际代码中的变换解码后，分别为 `chat_input_view.cc` 和 `[merge-photo][ERASE] user=[%_] (发送清除)`，再次支持输入视图中的处理路径归属。

微信签名信息中存在 hardened runtime 标志。仅凭这个标志不能断言运行时附加一定可行或一定失败。已准备一次一秒的 Frida 进程/模块信息探测，脚本只读取 PID、架构和 wechat.dylib 模块名与大小，不读取消息、附件或凭证，也不调用收发函数。最终以 `wechat-module-probe-result.json` 的实际返回为准。


## 导出函数边界检查

该 arm64 业务库公开导出的函数仅观察到 `_WeChatMain` 和 `_SetWeixinCallbackFunc`。后者位于 `0x4c828d8`，读取其反汇编发现只根据第一参数 0/1，将第二参数写入两个全局回调槽（`0xa33a228`、`0xa33a230`），随后返回。目前没有证据表明这是好友消息回调；不能因为名字含 Callback 就把它接成来信接口。原始指令见 `wechat-callback-export.asm.txt`。

Frida 的 Node 绑定构建失败：预编译包下载未完成，回退源码构建时 frida-core 仓库传输中断。Python 官方二进制包首次下载也超时，已改用断点续传和更长超时；没有启动后台无限重试任务。


## 本轮结束状态

- 两份定点反编译证据已生成成功，实际发送槽继续调用 `0x8811d0`。
- Frida Node 原生绑定构建失败；Python 官方 wheel 随后经断点续传完成下载，Frida 17.25.2 已隔离安装到 `artifacts/rea-python`。
- 已执行限定的模块级短时附加探测，结果为 `attached: false`、`PermissionDeniedError: unable to access process with pid 48463 from the current user account`。没有进入注入脚本执行阶段，没有读取聊天/附件/凭证，没有调用原生收发函数。完整结果保存在 `wechat-module-probe-result.json`。
- 这次返回只证明当前用户权限和当前客户端状态下附加失败；没有证明具体是哪一个系统保护机制导致，也没有证明需要或能够通过某一种系统配置更改解决。
- 已安装 REA 6.7.0、Hopper 免费演示工具及其可用 Python 桥接。现有 v9 机器人消息通道未替换。通过 CUA 查看过当前聊天窗口；截图没有发送给 DeepSeek。
- 已定位的 Qt 方法属于进程内实现，当前还不是稳定的外部接口。消息类型字段、附件数据和来信语音转写仍未接通。下一阶段可分别研究运行时访问条件，以及外部界面的转文字/图片获取流程。


## 后续权限诊断与调试副本验证（当前状态）

用户明确批准：改签并运行独立调试副本，临时退出原微信，测试读取，且只向文件传输助手发送一条自测消息。

### 权限定位

- 当前 SIP 开启，开发者调试模式未启用；没有修改这两项。
- Frida 分别成功附加了自行编译的子进程和独立启动的同用户测试进程，因此工具本身可用，不能把微信失败简单归因为缺少辅助功能或录屏权限。
- 通过只读 `csops(CS_OPS_STATUS)` 核实，原微信及相关辅助进程均启用 Hardened Runtime，且无 `CS_GET_TASK_ALLOW`。原主进程标志为 `0x22015311`。完整记录见 `permission-check/runtime-signature-flags.json`。
- Apple 说明：针对 Hardened Runtime 程序，目标自身是否允许 get-task-allow 是调试访问的重要条件；普通系统设置开关不能直接给发行版程序添加该签名属性。参考：https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.security.cs.debugger

### 已批准副本

路径：`artifacts/wechat-debug/WeChat-Debug.app`。通过 APFS 克隆独立准备，仅改签副本，原版可执行文件 SHA256 已再次核验保持一致。副本启用以下三项调试例外，同时保留 Hardened Runtime：

- `com.apple.security.get-task-allow`
- `com.apple.security.cs.disable-library-validation`
- `com.apple.security.cs.allow-unsigned-executable-memory`

前两项生效后，首次调试器加载触发 `CODESIGNING / Invalid Page` 退出，读取了崩溃摘要后，在已批准的副本调试范围内补充第三项以允许调试代码页。签名经 `codesign --verify --deep --strict` 检查通过。随后实际探测成功：`attached: true`、`detached: true`，读取到 arm64 wechat.dylib 模块。证据见 `permission-check/debug-copy-module-probe-code-pages.json`。

### 原生读取链路已完成的部分

- `wechat_objc_probe.js` 成功读取运行时 Qt/Cocoa 类、方法和字段元数据，包括 QNSView、QNSWindow、QMacAccessibilityElement。
- `wechat_accessibility_probe.js` 在应用主线程只读查询原生辅助功能属性，实际读取到登录页的 17 个控件，以及昵称、登录和切换账号按钮等文字。证据见 `permission-check/debug-copy-native-accessibility.json`。
- 这是登录页控件验证，**尚未验证登录后的聊天树或消息内容**。不能将它表述为真实消息收发已打通。
- 探测脚本 `macos/analysis/wechat_runtime_probe.py` 限定当前用户的原微信/调试副本进程，提供 module-only、UI runtime metadata、native accessibility 三种受限模式；不执行登录、点击或发送。

### 当前阻塞与恢复

用户目前暂时无法在手机上确认登录，副本停留在登录页，没有发送任何测试消息。可以在方便时打开上述调试副本，点击登录并在手机上确认，然后继续验证聊天节点、输入框与文件传输助手的一条自测消息。原版 `/Applications/WeChat.app` 仍保留，可正常作为恢复入口；切回原版也可能需要手机确认登录。

本轮没有关闭 SIP、没有启用全局调试授权、没有修改系统 taskport 授权数据库。副本的调试例外会允许本机调试工具访问其进程数据，应仅用于当前测试。完整操作清单与状态见 `artifacts/wechat-debug/plan.json`。


## 已登录后的前端控件读写验证

2026-11-04：登录完成后，外部 AX 仍为空，但已沿 NSWindow → QNSView → QCocoaWindow → QWidgetWindow → MainWindow 找到实际 Qt 控件树。在 UI 主线程读取白名单 Qt 文字属性及控件几何，完整遍历样本 716 个对象，读到会话名、发送人、双方气泡文字、输入框与发送按钮。

新增 core/wechat_frontend.py 和 macos/wechat_frontend_map.py。通过模拟鼠标键盘只向文件传输助手发送了一条随机标记文字，原生控件回读确认新己方气泡及空输入框。未调用 Send/消息业务函数，未启动全联系人自动回复。详见《前端控件定位组件说明.md》。

## 语音转写与图片取图已实测

用户提供“时平”新的语音与图片后，已实际识别 `ChatVoiceItemView`，点击界面“转文字”，回读“测试语音，测试语音。”。转写文字在原语音行的深层 bottom_content_v_view_ 下，包含另一个 ChatBubbleFrame，现已避免误判成第二条消息或破坏原语音的收发方向。完整流程及已存在转写的复用均通过实测。

该图片实际是 ChatBubbleReferItemView 内的 XImageView。双击打开 PreviewWindow / PreviewImage，普通 Cmd+C 取得微信提供的 800×800 JPEG（257758 字节）；通过新组件重复取图，文件 SHA256 相同。未调用图片业务接口、未上传模型、未发送回复。新增 macos/wechat_frontend_media.py，详见组件说明；自动回复循环尚未切换。
