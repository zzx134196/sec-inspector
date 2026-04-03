#!/bin/bash
# ============================================
#  等保测评助手 - 一键部署脚本
#  用法: bash deploy.sh
# ============================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
BACKEND_PORT=8021
FRONTEND_PORT=8003

echo ""
echo "╔══════════════════════════════════════╗"
echo "║     等保测评助手 - 一键部署               ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ========== 1. 检查基础依赖 ==========
echo "🔍 [1/6] 检查基础依赖..."

# Python
PYTHON=""
for cmd in python3.11 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PY_VER=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
        MAJOR=$(echo "$PY_VER" | cut -d. -f1)
        MINOR=$(echo "$PY_VER" | cut -d. -f2)
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 9 ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "❌ 未找到 Python 3.9+，请先安装 Python"
    exit 1
fi
echo "  ✅ Python: $($PYTHON --version)"

# Node.js
if ! command -v node &>/dev/null; then
    echo "❌ 未找到 Node.js，请先安装 Node.js 16+"
    exit 1
fi
echo "  ✅ Node.js: $(node --version)"

# npm
if ! command -v npm &>/dev/null; then
    echo "❌ 未找到 npm"
    exit 1
fi
echo "  ✅ npm: $(npm --version)"

# ========== 2. 后端虚拟环境 ==========
echo ""
echo "📦 [2/6] 配置后端 Python 环境..."

cd "$BACKEND_DIR"

if [ ! -d "venv" ]; then
    echo "  创建虚拟环境..."
    $PYTHON -m venv venv
fi

# 激活虚拟环境
source venv/bin/activate
echo "  ✅ 虚拟环境: $(which python)"

# ========== 3. 安装后端依赖 ==========
echo ""
echo "📥 [3/6] 安装后端依赖..."

pip install -q --upgrade pip 2>/dev/null
pip install -q -r requirements.txt 2>&1 | tail -3

echo "  ✅ 后端依赖安装完成"

# ========== 4. 创建 .env（如不存在） ==========
echo ""
echo "⚙️  [4/6] 检查配置文件..."

if [ ! -f ".env" ]; then
    cat > .env << 'EOF'
# 等保测评助手 - 环境配置
# 数据库（默认SQLite，无需安装MySQL）
DATABASE_URL=sqlite:///./sec_inspector.db

# 如需使用 MySQL，取消注释下面这行并注释上面的 SQLite 行：
# DATABASE_URL=mysql+pymysql://root:root@localhost:3306/sec_inspector?charset=utf8mb4

# LLM配置
LLM_BASE_URL=http://localhost:8080/v1
LLM_API_KEY=not-needed
LLM_MODEL=qwen2.5
LLM_MAX_TOKENS=4096
LLM_TEMPERATURE=0.7

# Embedding配置
EMBEDDING_BASE_URL=http://localhost:8080/v1
EMBEDDING_API_KEY=not-needed
EMBEDDING_MODEL=bge-large-zh-v1.5

# Milvus配置
MILVUS_HOST=localhost
MILVUS_PORT=19530
MILVUS_COLLECTION=sec_knowledge

# NVD API Key（可选，有Key速率更高）
NVD_API_KEY=
EOF
    echo "  ✅ 已创建 .env 默认配置"
else
    echo "  ✅ .env 已存在，跳过"
fi

# ========== 5. 安装前端依赖 ==========
echo ""
echo "🎨 [5/6] 安装前端依赖..."

cd "$FRONTEND_DIR"

if [ ! -d "node_modules" ]; then
    npm install --silent 2>&1 | tail -3
    echo "  ✅ 前端依赖安装完成"
else
    echo "  ✅ node_modules 已存在，跳过"
fi

# ========== 6. 启动服务 ==========
echo ""
echo "🚀 [6/6] 启动服务..."

# 杀掉占用端口的旧进程
lsof -ti:$BACKEND_PORT | xargs kill -9 2>/dev/null || true
lsof -ti:$FRONTEND_PORT | xargs kill -9 2>/dev/null || true
sleep 1

# 启动后端
cd "$BACKEND_DIR"
source venv/bin/activate
echo "  启动后端 (端口 $BACKEND_PORT)..."
python -m uvicorn app.main:app --host 0.0.0.0 --port $BACKEND_PORT &
BACKEND_PID=$!

# 等后端就绪
sleep 3

# 检查后端是否启动成功
if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo "  ❌ 后端启动失败，请检查日志"
    exit 1
fi
echo "  ✅ 后端已启动 (PID: $BACKEND_PID)"

# 启动前端
cd "$FRONTEND_DIR"
echo "  启动前端 (端口 $FRONTEND_PORT)..."
npm run dev -- --port $FRONTEND_PORT &
FRONTEND_PID=$!

sleep 3

echo ""
echo "╔══════════════════════════════════════╗"
echo "║         ✅ 部署完成！                ║"
echo "╠══════════════════════════════════════╣"
echo "║  前端界面: http://localhost:$FRONTEND_PORT    ║"
echo "║  API文档:  http://localhost:$BACKEND_PORT/docs ║"
echo "║  账号:     admin / admin123          ║"
echo "╠══════════════════════════════════════╣"
echo "║  按 Ctrl+C 停止所有服务              ║"
echo "╚══════════════════════════════════════╝"
echo ""

# 捕获退出信号，停止所有子进程
cleanup() {
    echo ""
    echo "正在停止服务..."
    kill $BACKEND_PID 2>/dev/null
    kill $FRONTEND_PID 2>/dev/null
    echo "✅ 所有服务已停止"
    exit 0
}

trap cleanup INT TERM
wait
