"""终端测试鲸鲸，不读取或操作微信。"""
import argparse
import os
from pathlib import Path
from core.config import load_config
from core.llm_openai import build_writer
from core.models import IncomingMessage

os.chdir(Path(__file__).resolve().parent)
parser=argparse.ArgumentParser()
parser.add_argument('--once',help='只发送一条测试文字')
args=parser.parse_args()
config=load_config('core/config.yaml')
writer=build_writer(config)
print('终端聊天测试：只把你在这里输入的文字发给 DeepSeek，不操作微信。',flush=True)
while True:
    try:
        text=args.once if args.once is not None else input('\n你：').strip()
        if not text or text in ('/quit','/exit'): break
        reply=writer(IncomingMessage(chat_id='local-console',chat_name='终端测试',text=text,platform='console'),config)
        print('鲸鲸：'+(reply or '本次没有生成回复。'),flush=True)
    except (EOFError,KeyboardInterrupt): break
    except Exception as error:
        print('调用失败：'+str(error),flush=True)
        if args.once is not None: raise SystemExit(1)
    if args.once is not None: break
