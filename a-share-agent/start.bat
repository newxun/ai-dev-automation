@echo off
rem 一键启动（Windows）。首次运行会创建虚拟环境并安装依赖。
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\python -m pip install -q --upgrade pip
  .venv\Scripts\python -m pip install -q -r requirements.txt
)
.venv\Scripts\python -m ashare_agent serve %*
