#!/bin/bash
# 等保测评助手 - 停止所有服务
echo "正在停止服务..."
lsof -ti:8021 | xargs kill -9 2>/dev/null
lsof -ti:8003 | xargs kill -9 2>/dev/null
echo "✅ 所有服务已停止"
