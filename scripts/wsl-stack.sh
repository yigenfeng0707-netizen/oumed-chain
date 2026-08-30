#!/usr/bin/env bash
# MedSignal 栈的 WSL Docker 启停助手（Windows 侧 Git Bash 调用）
# 用法: ./scripts/wsl-stack.sh up|down|ps|logs [dev|prod]
set -e
DISTRO=Ubuntu-D
REPO=/mnt/d/APPs/温州AI医疗比赛/oumed-chain
PROFILE=${2:-dev}

case "$1" in
  up)
    echo ">>> 在 WSL($DISTRO) 内启动 $PROFILE 栈（首次构建较慢）..."
    wsl.exe -d "$DISTRO" -e bash -lc "cd $REPO && docker compose --profile $PROFILE up -d --build"
    echo ">>> 前端 http://localhost:3000  后端 http://localhost:8000/docs"
    ;;
  down)
    wsl.exe -d "$DISTRO" -e bash -lc "cd $REPO && docker compose --profile $PROFILE down"
    ;;
  ps)
    wsl.exe -d "$DISTRO" -e bash -lc "cd $REPO && docker compose --profile $PROFILE ps"
    ;;
  logs)
    wsl.exe -d "$DISTRO" -e bash -lc "cd $REPO && docker compose --profile $PROFILE logs -f --tail=100"
    ;;
  *)
    echo "用法: $0 up|down|ps|logs [dev|prod]"; exit 1
    ;;
esac
