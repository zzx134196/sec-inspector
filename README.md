# 等保测评助手 (Sec-Inspector)

网络安全等级保护测评智能审核助手 — 基于 Tool-Use Agent 架构

## 功能

1. **测评报告审核** — 检查要素完整性、格式规范性、逻辑一致性、高风险识别
2. **国标法规检索** — 查询 GB/T 22239、28448、28449 等 7 份核心等保标准
3. **测评参考描述** — 查看 12 份 Excel 覆盖全部安全层面的标准填写范例
4. **漏洞信息查询** — 联网搜索 NVD/CVE 漏洞库
5. **漏洞详情查看** — 按 CVE-ID 获取完整漏洞信息
6. **文件导出** — 支持 Word/PDF/Excel 格式导出

## 技术栈

- **后端**: Python 3.11+ / FastAPI / SQLAlchemy / OpenAI SDK
- **前端**: React 18 / Ant Design 5 / Zustand / Vite
- **向量库**: Milvus（可选）
- **LLM**: 兼容 OpenAI API 的任意模型（如 Qwen2.5）

## 快速开始

### 一键启动（推荐，与 party-brain 一致）

```bash
cd sec-inspector
./start.sh
```

停止服务：

```bash
./stop.sh
```

### 手动分别启动

```bash
# 后端
cd backend
pip install -r requirements.txt
python3.11 -m uvicorn app.main:app --port 8000

# 前端
cd frontend
npm install
npm run dev
```

LLM/NVD 等配置均可在管理后台页面修改，无需手动编辑配置文件。

## 默认账号

- 管理员: `admin` / `admin123`

## 项目结构

```
sec-inspector/
├── backend/
│   ├── app/
│   │   ├── api/          # API路由（认证、对话、知识库、设置）
│   │   ├── core/         # 核心逻辑（Agent、LLM、审核、搜索、NVD、工具）
│   │   ├── models/       # 数据模型
│   │   └── main.py       # 应用入口
│   ├── knowledge/
│   │   └── pipeline/     # 知识库处理（PDF/Excel解析、切片、向量化）
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── pages/        # 页面（登录、对话、管理后台）
│       ├── services/     # API服务
│       └── stores/       # 状态管理
├── Dockerfile
└── docker-compose.yml
```
