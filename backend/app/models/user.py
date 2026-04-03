"""用户模型"""
from sqlalchemy import Column, Integer, String, DateTime, func
from app.models.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    hashed_password = Column(String(200), nullable=False)
    display_name = Column(String(100), nullable=True, comment="显示名称")
    role = Column(String(20), default="user", comment="角色: admin/user")
    is_active = Column(Integer, default=1, comment="是否启用: 1=是 0=否")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
