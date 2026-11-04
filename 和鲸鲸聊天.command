#!/bin/bash
cd "$(dirname "$0")"
.venv-ocr/bin/python -u chat-whalegirl.py
read -r -p '按回车关闭窗口'
