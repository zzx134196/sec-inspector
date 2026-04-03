"""等保测评助手 - 主应用入口"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.config import settings
from app.models.database import engine, Base, SessionLocal
from app.models.user import User
from app.models.conversation import Conversation, Message
from app.models.knowledge import QueryLog
from app.models.eval_template import EvalTemplate, SystemConfig
from app.core.auth import get_password_hash

# API路由
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.knowledge import router as knowledge_router

from app.api.export import router as export_router


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="网络安全等级保护测评智能审核助手",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册路由
    app.include_router(auth_router)
    app.include_router(chat_router)
    app.include_router(knowledge_router)

    app.include_router(export_router)

    @app.on_event("startup")
    async def startup():
        logger.info(f"🚀 {settings.APP_NAME} v{settings.APP_VERSION} 启动中...")

        # 创建目录
        os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
        os.makedirs(settings.EXPORT_DIR, exist_ok=True)

        # 创建数据库表
        Base.metadata.create_all(bind=engine)
        logger.info("✅ 数据库表已就绪")

        # 初始化默认管理员
        _init_default_admin()

        # 从数据库加载持久化配置（与party-brain一致的模式） - 系统配置已移除，直接使用配置文件的硬编码
        # from app.api.settings import load_llm_config_from_db
        # load_llm_config_from_db()

        logger.info(f"✅ {settings.APP_NAME} 启动完成")
        logger.info(f"📖 API文档: http://{settings.HOST}:{settings.PORT}/docs")

    @app.get("/")
    async def root():
        return {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "status": "running",
        }

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/temp-token")
    def get_temp_token():
        from jose import jwt
        import datetime
        from app.config import settings
        payload = {
             "username": "admin",
             "is_admin": True,
             "exp": datetime.datetime.utcnow() + datetime.timedelta(days=1),
             "iss": settings.JWT_ISSUER,
             "aud": settings.JWT_AUDIENCE,
        }
        return {"token": jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)}

    return app


def _init_default_admin():
    """初始化默认管理员"""
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        if not admin:
            admin = User(
                username="admin",
                hashed_password=get_password_hash("admin123"),
                display_name="系统管理员",
                role="admin",
            )
            db.add(admin)
            db.commit()
            logger.info("✅ 已创建默认管理员: admin / admin123")
    finally:
        db.close()


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
