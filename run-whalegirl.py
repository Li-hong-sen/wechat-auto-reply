"""本地启动器；默认仅试运行，优先读取本地配置的 API Key。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
parser = argparse.ArgumentParser()
parser.add_argument('--send', action='store_true', help='启用真实发送')
parser.add_argument('--doctor', action='store_true', help='检查微信界面兼容性')
parser.add_argument('--check', action='store_true', help='仅验证配置与依赖')
args = parser.parse_args()
from core.config import load_config
cfg = load_config('core/config.yaml')
if args.check:
    import fastapi, requests, uvicorn
    print('依赖与配置通过；测试群：幸福快乐一家亲；群昵称：明前茶话弄；私聊开启。')
    sys.exit(0)
if args.doctor:
    sys.exit(subprocess.call([sys.executable, 'macos/wechat_mac_bot.py', '--doctor']))
print('请先确认 Mac 微信登录的是准备运行机器人的账号，并核对本地群昵称配置。')
print('当前模式：' + ('真实发送' if args.send else '试运行，不发送消息；会点开私聊或测试群并标记已读'))
key = cfg.llm.api_key or os.environ.get('DEEPSEEK_API_KEY') or getpass.getpass('DeepSeek API Key（隐藏输入，仅本次进程使用）：').strip()
if not key:
    sys.exit('未填写 API Key，已退出。')
env = os.environ.copy()
for proxy_key in ("NO_PROXY", "no_proxy"):
    env[proxy_key] = env.get(proxy_key, "") + ",127.0.0.1,localhost"
os.environ.update({k: env[k] for k in ("NO_PROXY", "no_proxy")})
env.update(DEEPSEEK_API_KEY=key, WXAUTO_TOKEN=secrets.token_hex(32),
           WXAUTO_CONFIG='core/config.yaml', WXAUTO_STATE='var/state.json' if args.send else 'var/dry-run-state.json',
           WXAUTO_SERVER='http://127.0.0.1:18848', WXAUTO_ACCOUNT='whalegirl-test',
           WXAUTO_TEST_GROUPS=json.dumps(['幸福快乐一家亲'], ensure_ascii=False), WXAUTO_BOT_NAME='明前茶话弄',
           WXAUTO_PRIVATE_ENABLED='1' if cfg.scope.reply_to_private else '0')
server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'server.app:app', '--host', '127.0.0.1', '--port', '18848'], env=env)
try:
    for _ in range(50):
        if server.poll() is not None:
            sys.exit('本地服务启动失败。')
        try:
            request = urllib.request.Request(env['WXAUTO_SERVER']+'/reload', data=b'', headers={'Authorization':'Bearer '+env['WXAUTO_TOKEN']})
            with urllib.request.urlopen(request, timeout=1) as response:
                if response.status == 200:
                    break
        except Exception:
            time.sleep(.2)
    else:
        sys.exit('等待本地服务超时。')
    cmd = [sys.executable, 'macos/wechat_mac_bot.py']
    if not args.send:
        cmd.append('--dry-run')
    subprocess.call(cmd, env=env)
except KeyboardInterrupt:
    pass
finally:
    server.terminate()
    try:
        server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait()
