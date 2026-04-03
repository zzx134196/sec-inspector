"""认证模块 — 对接总系统 JWT 统一认证

总系统签发 JWT Token，子系统验证并提取用户信息。
"""
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from loguru import logger

from app.config import settings
from app.models.database import get_db
from app.models.user import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def decode_jwt_token(token: str) -> dict:
    """解码并验证总系统签发的 JWT Token"""
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            issuer=settings.JWT_ISSUER,
            audience=settings.JWT_AUDIENCE,
        )
        return payload
    except JWTError as e:
        logger.warning(f"JWT 验证失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token 无效或已过期: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """验证 JWT Token 并返回/创建本地用户"""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证信息，请从总系统登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_jwt_token(credentials.credentials)

    username = payload.get("username", "")
    is_admin = payload.get("is_admin", False)

    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 中缺少用户名信息",
        )

    # 查找或创建本地用户
    user = db.query(User).filter(User.username == username).first()
    if not user:
        user = User(
            username=username,
            hashed_password="jwt-managed",
            display_name=username,
            role="admin" if is_admin else "user",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info(f"自动创建用户（来自总系统）: {username}, admin={is_admin}")
    else:
        new_role = "admin" if is_admin else "user"
        if user.role != new_role:
            user.role = new_role
            db.commit()

    return user
