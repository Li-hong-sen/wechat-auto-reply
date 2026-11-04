# 微信普通好友自动回复：Computer Use 与百度 OCR 调研

调研日期：2026-11-03。范围：Apple Silicon Mac、微信桌面版、小号普通好友私聊；不更换为 iLink 机器人身份。
本次为官方资料核查和现有代码审查，没有安装新模型，没有将聊天截图上传到第三方。

## 结论

建议验证“本地 OCR 常规识别 + Computer Use 按需定位/恢复”的组合。不要将每次轮询、读消息、找输入框、点击发送都交给远程视觉 Agent；推理轮数增加，可能比当前脚本更慢。
先修消息检测和去重，再比较 Apple Vision 与 PaddleOCR，不能假设换模型就能消除漏回。

## 当前代码中的已知限制

macos/wechat_ocr_bot.py 的 parse 使用固定窗口宽高比例、文字 x/y 范围、气泡颜色取样点；换布局、缩放和主题就可能失效。
去重键为联系人标题与文字内容的哈希，会将同一联系人再次发出的相同文字长期视为重复。
仅提取最后一条可见对方消息，连续消息可能漏读；不自动巡视会话列表。
这些是应用层问题，单独替换 OCR 引擎无法解决。

## Computer Use

Claude Computer Use：由模型理解截图并输出鼠标/键盘等动作，执行端由开发者实现。官方明确提示交互延迟和坐标准确性问题。
来源：https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool

UI-TARS Desktop：官方提供 macOS 客户端，需要辅助功能、屏幕录制权限；模型服务需单独配置。客户端在本地运行不代表模型必然本地运行。
来源：https://github.com/bytedance/UI-TARS-desktop/blob/main/docs/quick-start.md

适用：初始化微信窗口、定位联系人列表/输入框、处理弹窗、布局改变后重新定位。不保证识别微信新消息的时序或每次发送投递成功。
采用云模型时，所需截图会发送到模型供应商；现有只发送文字的 DeepSeek 调用不能直接代替截图操作模型。

## 百度相关 OCR 的三个选项

PP-OCRv6：通用文字检测/识别，提供 tiny/small/medium 档位。适合优先用 tiny/small 与当前 Apple Vision 对比。官方有 Apple M4 的性能数据，但不能套用为这台 Mac 的微信截图性能。
来源：https://www.paddleocr.ai/latest/en/version3.x/algorithm/PP-OCRv6/PP-OCRv6.html

PaddleOCR-VL-1.6：偏文档解析的视觉语言模型。官方有 Apple Silicon 手动部署教程；仅 M4 完成精度验证，其他 Apple Silicon 兼容性未验证。本机简单聊天气泡识别应先比较更轻量的普通 OCR，不能从文档解析榜单推断微信效果。
来源：https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL-Apple-Silicon.md

百度智能云 OCR：标准/高精度及含位置版 API。微信定位任务应使用含位置结果，不只取整图纯文字。需要云端凭据和调用资源，截图需上传百度。按 1 秒一次全天轮询是 86400 次/天，因此应在画面有变化且本地识别失败时才调用；费用以控制台对应接口实时报价为准。
来源：https://ai.baidu.com/tech/ocr/general
来源：https://cloud.baidu.com/product/OCR/tui02.html

## 建议的实现结构

1. 检测指定微信窗口与消息区域变化，不每秒重复调用模型。
2. 本地 OCR 输出文本框坐标、文字、置信度；按相对气泡位置分组，区分发言方向。
3. 会话状态保存可见消息序列，通过相邻消息对齐判断新增；不能只按内容哈希去重。
4. 新消息传给现有 DeepSeek 回复模块。
5. 发送前核对会话标题、输入框空白和窗口位置；发送后回读确认出现己方气泡。
6. 仅在无法定位或出现弹窗时调用 Computer Use，完成后回到确定性流程。

## 实测与验收

先用已有授权截图做离线比较，补充短英文、中文、多行、相同内容重复消息、连续消息、窗口移动与不同缩放样本。
分别记录文字正确率、漏消息率、发言方向识别、会话定位与 OCR P50/P95 延迟。再用测试账号完成端到端收发，单独量出消息检测、模型生成、发送核对耗时。
任何误发会话都应阻止自动发送上线；没有实际投递验证，不能仅凭“执行发送”日志宣称成功。
软件开源免费不等于零本机资源成本；Computer Use/OCR 都不能替代微信消息订阅接口，也不改变平台对桌面自动化的规则。

## 2026-11-03 补充纠正

已核实 DeepSeek V4.1 Flash 原生支持图片，官方当前接口名 deepseek-flash。此前“现有只传文字的调用不能充当截图模型”仅适用于当前代码，并不代表需要另买其他供应商模型。可以扩展当前 DeepSeek 调用传入截图，另接本地动作执行器；GUI定位精度与性能尚需测试。详见“主流桌面Agent实现调研.md”。
