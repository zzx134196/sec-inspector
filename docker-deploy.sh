#!/bin/bash
# ============================================
#  等保测评助手 - 一键 Docker 部署
#  用法: bash docker-deploy.sh
# ============================================

set -e

cd "$(dirname "$0")"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   等保测评助手 - Docker 一键部署          ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ========== 1. 检查 Docker ==========
echo "🔍 [1/4] 检查 Docker 环境..."

if ! command -v docker &>/dev/null; then
    echo "❌ 未找到 Docker，请先安装: https://docs.docker.com/get-docker/"
    exit 1
fi
echo "  ✅ Docker: $(docker --version | cut -d' ' -f3)"

if ! docker compose version &>/dev/null && ! docker-compose version &>/dev/null; then
    echo "❌ 未找到 Docker Compose，请安装 Docker Compose V2"
    exit 1
fi

# 确定 compose 命令
COMPOSE="docker compose"
if ! docker compose version &>/dev/null; then
    COMPOSE="docker-compose"
fi
echo "  ✅ Compose: $($COMPOSE version --short 2>/dev/null || $COMPOSE version | head -1)"

# 检查 Docker daemon
if ! docker info &>/dev/null; then
    echo "❌ Docker 服务未运行，请先启动 Docker Desktop 或 Docker daemon"
    exit 1
fi
echo "  ✅ Docker daemon 运行中"

# ========== 2. 检查配置 ==========
echo ""
echo "⚙️  [2/4] 检查环境配置..."

if [ ! -f ".env" ]; then
    cat > .env << 'EOF'
# ============================================
#  等保测评助手 - Docker 部署配置
# ============================================

# 端口映射
BACKEND_PORT=8021
FRONTEND_PORT=8003

# LLM 配置
LLM_BASE_URL=http://192.168.1.100:18888/v1
LLM_API_KEY=gpustack_cd9723bca82e5e9e_a9b7da5f0badf8ad9568d5275624847c
LLM_MODEL=DeepSeek-R1
LLM_MAX_TOKENS=4096
LLM_TEMPERATURE=0.7

# NVD API Key（可选，有Key速率更高）
NVD_API_KEY=
EOF
    echo "  ✅ 已创建 .env 默认配置"
    echo ""
    echo "  ⚠️  请根据实际情况修改 .env 中的配置："
    echo "     - LLM_BASE_URL: LLM 服务地址"
    echo "     - LLM_MODEL:    模型名称"
    echo ""
    read -p "  是否继续部署？(Y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Nn]$ ]]; then
        echo "  已取消，请修改 .env 后重新运行 bash docker-deploy.sh"
        exit 0
    fi
else
    echo "  ✅ .env 已存在"
fi

# 创建数据持久化目录
mkdir -p data/uploads data/exports

# ========== 3. 构建镜像 ==========
echo ""
echo "🔨 [3/4] 构建 Docker 镜像..."
$COMPOSE build
echo "  ✅ 镜像构建完成"

# ========== 4. 启动服务 ==========
echo ""
echo "🚀 [4/4] 启动所有服务..."

# 停掉旧容器
$COMPOSE down 2>/dev/null || true

# 启动
$COMPOSE up -d

echo ""
echo "⏳ 等待服务就绪..."

# 等待后端健康检查通过
MAX_WAIT=60
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
    if docker inspect --format='{{.State.Health.Status}}' sec-inspector-backend 2>/dev/null | grep -q "healthy"; then
        break
    fi
    sleep 3
    WAITED=$((WAITED + 3))
    echo "  等待后端启动... (${WAITED}s)"
done

if [ $WAITED -ge $MAX_WAIT ]; then
    echo "  ⚠️  后端启动超时，查看日志: docker logs sec-inspector-backend"
else
    echo "  ✅ 后端已就绪"
fi

# 获取实际端口
BACKEND_PORT=$(grep -E "^BACKEND_PORT=" .env 2>/dev/null | cut -d= -f2 || echo "8021")
FRONTEND_PORT=$(grep -E "^FRONTEND_PORT=" .env 2>/dev/null | cut -d= -f2 || echo "8003")
BACKEND_PORT=${BACKEND_PORT:-8021}
FRONTEND_PORT=${FRONTEND_PORT:-8003}

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║           ✅ Docker 部署完成！            ║"
echo "╠══════════════════════════════════════════╣"
echo "║  前端界面: http://localhost:${FRONTEND_PORT}          ║"
echo "║  API文档:  http://localhost:${BACKEND_PORT}/docs      ║"
echo "║  账号:     admin / admin123               ║"
echo "╠══════════════════════════════════════════╣"
echo "║  常用命令:                                 ║"
echo "║    查看日志: docker compose logs -f        ║"
echo "║    停止服务: docker compose down           ║"
echo "║    重启服务: docker compose restart        ║"
echo "╚══════════════════════════════════════════╝"
echo ""
