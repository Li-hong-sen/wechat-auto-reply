#!/bin/bash
cd "$(dirname "$0")"
.venv/bin/python run-whalegirl.py --doctor
read -r -p '按回车关闭窗口'
