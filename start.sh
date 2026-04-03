#!/bin/bash
# 等保测评助手 - 一键启动脚本

cd "$(dirname "$0")"

echo "🚀 等保测评助手 启动中..."
export PATH=$PATH:/Users/yu22x/Library/Python/3.9/bin:/Users/yu22x/.nvm/versions/node/v22.22.1/bin

# 杀掉已有进程
lsof -ti:8022 | xargs kill -9 2>/dev/null
lsof -ti:8003 | xargs kill -9 2>/dev/null
sleep 1

# 启动后端
echo "📦 启动后端 (端口8022)..."
cd backend
/Users/yu22x/Library/Python/3.9/bin/uvicorn app.main:app --port 8022 &
BACKEND_PID=$!
cd ..

# 等后端就绪
sleep 3

# 启动前端
echo "🎨 启动前端 (端口8003)..."
cd frontend
npm install --silent 2>/dev/null
npm run dev &
FRONTEND_PID=$!
cd ..

sleep 2

echo ""
echo "✅ 启动完成！"
echo "================================"
echo "  前端界面: http://localhost:8003"
echo "  API文档:  http://localhost:8022/docs"
echo "  账号:     admin / admin123"
echo "================================"
echo ""
echo "按 Ctrl+C 停止所有服务"

# 捕获退出信号，停止所有子进程
trap "echo '正在停止服务...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM
wait
