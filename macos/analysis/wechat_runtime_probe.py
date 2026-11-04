"""Bounded probes for this workspace's WeChat analysis.

Default mode reads module info only. Optional modes read runtime metadata or
visible native accessibility text. No mode invokes WeChat send/login actions.
Accepts only the installed executable or the prepared debug copy, owned by the
current user. Accessibility output may contain visible chat text after login.
"""
from __future__ import annotations
import argparse
import base64
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

ROOT=Path(__file__).resolve().parents[2]
ALLOWED={Path('/Applications/WeChat.app/Contents/MacOS/WeChat'),
         ROOT/'artifacts/wechat-debug/WeChat-Debug.app/Contents/MacOS/WeChat'}


def signature_flags(pid):
    library=ctypes.CDLL('/usr/lib/libSystem.B.dylib',use_errno=True)
    library.csops.argtypes=[ctypes.c_int,ctypes.c_uint,ctypes.c_void_p,ctypes.c_size_t]
    library.csops.restype=ctypes.c_int
    flags=ctypes.c_uint32()
    if library.csops(pid,0,ctypes.byref(flags),4):
        raise OSError(ctypes.get_errno(),'csops status failed')
    return {'raw':hex(flags.value),'get_task_allow':bool(flags.value&4),
            'hardened_runtime':bool(flags.value&0x10000)}


def validate_target(pid):
    command=subprocess.check_output(['ps','-p',str(pid),'-o','comm='],text=True).strip()
    owner=int(subprocess.check_output(['ps','-p',str(pid),'-o','uid='],text=True).strip())
    if owner!=os.getuid() or Path(command) not in ALLOWED:
        raise ValueError('目标必须是当前用户的微信主进程或本项目调试副本')
    return command


def probe(pid,timeout=15,inspect_ui_runtime=False,inspect_accessibility=False,inspect_qt_layout=False,capture_widget=None):
    command=validate_target(pid)
    result={'pid':pid,'executable':command,'signature':signature_flags(pid),
            'attached':False,'events':[],'wechat_messages_sent':0}
    sys.path.insert(0,str(ROOT/'artifacts/rea-python'))
    import frida
    session=None
    cancel=frida.Cancellable()
    timer=threading.Timer(timeout,cancel.cancel);timer.daemon=True;timer.start()
    try:
        with cancel:
            session=frida.attach(pid);result['attached']=True
            source="""'use strict';
const wechatModule=Process.getModuleByName('wechat.dylib');
send({probe:'module-only',pid:Process.id,architecture:Process.arch,
      module_name:wechatModule.name,module_size:wechatModule.size});
"""
            if inspect_ui_runtime:
                source=Path(__file__).with_name('wechat_objc_probe.js').read_text()
            if inspect_accessibility:
                source=Path(__file__).with_name('wechat_accessibility_probe.js').read_text()
            if inspect_qt_layout:
                source=Path(__file__).with_name('wechat_qt_layout_probe.js').read_text()
                helper=ROOT/('artifacts/frontend/qt_ui_reader_v2.dylib' if capture_widget else 'artifacts/frontend/qt_ui_reader.dylib')
                if not helper.exists():raise RuntimeError('请先编译 macos/analysis/qt_ui_reader.cpp')
                source=source.replace('__QT_UI_HELPER_PATH__',json.dumps(str(helper)))
                source=source.replace('__QT_CAPTURE_TARGET__',json.dumps(capture_widget))
            script=session.create_script(source)
            done=threading.Event()
            def message(value,data):
                if data is not None and value.get('payload',{}).get('probe')=='qt-widget-render':
                    value['payload']['rgba_base64']=base64.b64encode(data).decode('ascii')
                result['events'].append(value)
                if value.get('payload',{}).get('probe')=='qt-frontend-layout' or value.get('type')=='error':done.set()
                elif not inspect_qt_layout and value.get('payload',{}).get('probe')!='qt-layout-progress':done.set()
            script.on('message',message)
            script.load()
            if not done.wait(10 if inspect_qt_layout else 3):result['error']='探测没有在期限内返回结果'
            elif any(event.get('type')=='error' or event.get('payload',{}).get('error') for event in result['events']):
                result['error']='探测脚本返回错误，参见 events'
    except Exception as error:
        result.update(error_type=type(error).__name__,error=str(error))
    finally:
        if session is not None:
            try:session.detach();result['detached']=True
            except Exception as error:result['detach_error']=str(error)
        timer.cancel()
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid',required=True,type=int)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--inspect-ui-runtime',action='store_true',help='只读 Qt/Cocoa 类、方法、字段元数据，不读取聊天或执行界面动作')
    mode.add_argument('--inspect-accessibility',action='store_true',help='在主线程读取当前窗口的原生辅助功能属性，不执行界面动作')
    mode.add_argument('--inspect-qt-layout',action='store_true',help='读取 Qt 前端 QObject/控件布局，不调用微信消息接口')
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/rea-research/runtime-probe.json')
    args=parser.parse_args()
    result=probe(args.pid,inspect_ui_runtime=args.inspect_ui_runtime,inspect_accessibility=args.inspect_accessibility,inspect_qt_layout=args.inspect_qt_layout)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    sys.exit(0 if result['attached'] and not result.get('error') else 1)
