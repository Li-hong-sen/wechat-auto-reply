"""Native Qt frontend mapper; default read-only. Self-test targets only self chat.

Run with .venv-ocr/bin/python. Requires the approved, independently signed debug
copy and the compiled qt_ui_reader helper. Does not use WeChat business APIs.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.wechat_frontend import FrontendMap, FrontendUnavailable, unique
from macos.analysis.wechat_runtime_probe import probe, validate_target


def read(pid):
    executable = Path(validate_target(pid))
    if 'WeChat-Debug.app' not in executable.parts:
        raise FrontendUnavailable('此组件当前只支持已授权的独立调试副本')
    with (executable.parents[1] / 'Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    if info.get('CFBundleShortVersionString') != '4.1.15':
        raise FrontendUnavailable('Qt ABI 仅验证过微信 4.1.15；拒绝操作其他版本')
    result = probe(pid, inspect_qt_layout=True)
    return FrontendMap(result), result


def discover():
    expected = str(ROOT / 'artifacts/wechat-debug/WeChat-Debug.app/Contents/MacOS/WeChat')
    lines = subprocess.check_output(['ps', '-axo', 'pid=,comm='], text=True).splitlines()
    return unique((int(parts[0]) for line in lines if len(parts := line.strip().split(None, 1)) == 2 and parts[1] == expected), '调试微信进程')


def self_test(pid, output):
    # User-authorized mouse/keyboard implementation. No send slot invocation.
    import AppKit
    import Quartz
    import ctypes
    app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is None:
        raise FrontendUnavailable('微信进程已退出')
    accessibility = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/Frameworks/HIServices.framework/HIServices')
    accessibility.AXIsProcessTrusted.restype = ctypes.c_bool
    if not accessibility.AXIsProcessTrusted():
        raise FrontendUnavailable('运行脚本的应用缺少辅助功能权限')
    app.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
    time.sleep(.25)

    def click(point):
        for kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
            event = Quartz.CGEventCreateMouseEvent(None, kind, point, Quartz.kCGMouseButtonLeft)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def key(code, command=False):
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, code, down)
            if command:
                Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def refresh():
        m, raw = read(pid)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.with_suffix('.raw.json').write_text(json.dumps(raw, ensure_ascii=False, indent=2))
        return m

    m = refresh()
    if m.draft.strip():
        raise FrontendUnavailable('当前会话有草稿，停止自测')
    if m.conversation != '文件传输助手':
        contact = unique((c for c in m.contacts if c['name'] == '文件传输助手'), '文件传输助手')
        node = unique((n for n in m.nodes if n.get('node_id') == contact['node_id']), '联系人控件')
        click(m.click_point(node, expected_conversation=m.conversation))
        time.sleep(.25)
        m = refresh()
    # Fail closed before any text is entered; only this conversation is allowed.
    click(m.click_point(m.input, expected_conversation='文件传输助手'))
    test_text = '鲸鲸UI定位自测：前端控件读取与鼠标发送验证。[' + uuid.uuid4().hex[:8] + ']'
    pasteboard = AppKit.NSPasteboard.generalPasteboard()
    saved = []
    for item in pasteboard.pasteboardItems() or []:
        saved.append([(typ, bytes(item.dataForType_(typ))) for typ in item.types() if item.dataForType_(typ) is not None])
    try:
        pasteboard.clearContents()
        pasteboard.setString_forType_(test_text, AppKit.NSPasteboardTypeString)
        key(0, command=True)  # Select whitespace-only draft, if any.
        key(9, command=True)  # Cmd+V, ordinary GUI input.
        time.sleep(.2)
        m = refresh()
        click(m.click_point(m.send, expected_conversation='文件传输助手', expected_draft=test_text))
        # No blind retry: a failed read after clicking may still mean it was sent.
        time.sleep(.4)
        after = refresh()
        matches = [msg for msg in after.messages if msg['direction'] == 'outgoing' and msg['text'] == test_text]
        verified = after.conversation == '文件传输助手' and len(matches) == 1 and not after.draft.strip()
        result = {'verified': verified, 'recipient': '文件传输助手', 'test_text': test_text,
                  'send_clicks': 1, 'verification': 'new outgoing native text bubble and empty input',
                  'layout': after.json()}
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        if not verified:
            raise FrontendUnavailable('已点击一次发送，但未确认新气泡；不重复发送，请查看输出')
        return result
    finally:
        pasteboard.clearContents()
        restored = []
        for item_data in saved:
            item = AppKit.NSPasteboardItem.alloc().init()
            for typ, data in item_data:
                item.setData_forType_(AppKit.NSData.dataWithBytes_length_(data, len(data)), typ)
            restored.append(item)
        if restored:
            pasteboard.writeObjects_(restored)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int)
    parser.add_argument('--from-json', type=Path, help='离线解析，不连接微信')
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/frontend/frontend-map.json')
    parser.add_argument('--self-test', action='store_true', help='仅向文件传输助手发送一条随机标记自测文本')
    args = parser.parse_args()
    if args.from_json and args.self_test:
        parser.error('离线数据不能执行界面操作')
    if args.self_test:
        result = self_test(args.pid or discover(), args.output)
        print(json.dumps({k: v for k, v in result.items() if k != 'layout'}, ensure_ascii=False))
    else:
        result = (FrontendMap(json.loads(args.from_json.read_text())) if args.from_json else read(args.pid or discover())[0]).json()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
