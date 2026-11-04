# 桌面 Agent 实现调研与微信改造建议

日期：2026-11-03。此次只查官方文档及审查本地调用方式，未修改运行模型、未上传微信截图，未实测 DeepSeek 的 GUI 坐标定位能力。

## 必须纠正的模型判断

DeepSeek 官方 2026-09-10 发布 V4.1 Flash，明确原生支持多模态视觉理解。当前推荐 API 名称 deepseek-flash。视觉文档允许在 Chat Completions 的 user.content 数组传 text + image_url（base64/远程URL），支持原图与低分辨率处理。
我们当前 config.yaml 仍写 deepseek-chat，core/llm_openai.py 只发送字符串文字。程序没传图不等于模型不能理解图片。现有密钥可用于尝试官方视觉接口，无需仅为图片理解另申请 Claude/OpenAI 凭据。
图片理解、GUI精确定位、桌面工具执行是三个不同能力；具备第一项不代表后两项已经完成或可靠。
官方：https://deepseek.com/news/deepseek-v4-1-flash/
官方：https://api-docs.deepseek.com/guides/vision/

## 共通架构

观察（截图/控件树/窗口元数据）→ 模型判断 → 结构化动作 → 本地执行器 → 新观察/验证 → 继续或停止。
模型 API 本身不直接操作鼠标；动作需要程序执行并回传结果。MCP是工具接口层，不是能自动看懂界面的模型。
常见动作：截图、点击、双击、输入、按键、滚动、拖动、等待、结束。连续依赖的动作需要新观察，不能长期沿用旧截图坐标。

## 代表性实现

OpenAI Computer Use：模型返回computer动作，本地运行环境执行后回传截图，循环直至完成；文档要求核实应用中的结果，支持步数限制及执行前检查。不能仅依据模型“完成了”或执行器未报错认定成功。
https://developers.openai.com/api/docs/guides/tools-computer-use
https://developers.openai.com/api/docs/guides/tools-computer-use-sdk-drivers

Claude Computer Use：应用将tool_use转换为鼠标/键盘操作，捕获截图并返回。官方建议每步截图判断动作是否达成，且明确延迟、坐标和工具选择可能出错。
https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool

UI-TARS SDK：GUIAgent连接模型与Operator；Operator实现screenshot()和execute()。SDK把指令、动作空间、最近截图送入模型，将预测动作转换为本地鼠标键盘操作。文档显式处理物理屏幕分辨率、DPR与坐标缩放。
https://github.com/bytedance/UI-TARS-desktop/blob/main/docs/sdk.md

微软 OmniParser：将截图解析成可交互区域、文字/图标语义、边界框及编号，再由推理模型选目标。它是GUI屏幕解析器，区别于以文章/表格/阅读顺序为目标的文档OCR。
https://github.com/microsoft/OmniParser

微软 UFO：原生UIA + OmniParser视觉检测可单用或融合，按IoU去除重叠控件；能走原生控件或应用API时使用对应执行路径。这套Windows执行器不能直接搬到Mac，但感知/执行分层值得参考。
https://github.com/microsoft/UFO/blob/main/documents/docs/ufo2/core_features/control_detection/overview.md

## 适用于当前微信的改造

1. 观测统一：窗口截图、尺寸、Retina缩放、窗口ID、标题、会话列表、消息气泡序列、输入框状态。
2. 感知分工：本地边缘/文字检测提供候选区域；DeepSeek Flash在截图变化或解析不明确时识别会话、发言方向、正文/图片和控件。评测纯视觉路径与混合路径，不能先断言OCR必需或视觉必然更慢。
3. 消息状态：对齐前后可见消息序列和发言方向识别新增。按正文哈希去重会丢掉重复内容消息；单读最后一条会漏连续消息。
4. 动作协议：模型返回有限动作与目标框，例如click、type_text、press_key、scroll、wait；本地校验坐标、窗口归属、焦点和当前标题。禁止把模型任意文本当shell命令执行。
5. 发送闭环：核对目标会话→聚焦输入区→粘贴→截图确认文字→发送→回读己方新气泡；“执行了回车”不等于消息发送成功。
6. 三种记忆分离：每用户摘要+最近聊天历史；GUI操作轨迹（少量最近截图/动作）；待处理/已确认发送的消息状态。无需把所有屏幕截图塞进每用户1万token聊天文件。
7. 性能实测：分别量消息检测、截图理解、模型回复、动作执行和验证延迟。静态画面不反复请求模型；只上传需要的窗口或区域，布局稳定时复用位置，变化后重新定位。

## 对现有工程的差距

版面检测器已离线标出区域，但未接入收发链路。主脚本仍依赖固定坐标和颜色；仅最后一条消息、按内容去重、发送后不回读是主要缺口。应把脚本重构成带状态与验证的桌面Agent，而非继续单改阈值。
现有DeepSeek持久化记忆可以复用；目前记的是生成结果，应在实际发送确认后标记交付，避免把未发出的候选当成对方已看到的回复。
图片/表情包可用视觉输入评测；仅凭多模态图片能力不能推断可直接读取微信语音音频，需要另核对音频链路。

## 建议下一步验证

用用户选定的一张截图，通过 deepseek-flash 输出会话标题、气泡列表与方向、输入框和发送按钮框；人工与本地标注比对。再验证单次点击输入区与发送后回读。只有这些小测试通过才加入自动巡视会话与持续回复。
此处是方案建议，不是已完成或已验证的功能。截图传入模型供应商前明确范围与授权。
