"""知识库文档模型"""
from sqlalchemy import Column, Integer, String, Text, DateTime, func
from app.models.database import Base


class KnowledgeDocument(Base):
    """知识库文档（PDF国标等）"""
    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(300), nullable=False, comment="文档标题")
    file_name = Column(String(300), nullable=False, comment="原始文件名")
    file_path = Column(String(500), nullable=True, comment="存储路径")
    file_type = Column(String(50), default="pdf", comment="文件类型: pdf/xlsx/docx")
    file_size = Column(Integer, default=0, comment="文件大小(字节)")
    content_length = Column(Integer, default=0, comment="文本内容长度")
    status = Column(String(20), default="uploaded", comment="状态: uploaded/processing/indexed/error")
    status_message = Column(String(500), nullable=True, comment="状态说明")
    chunk_count = Column(Integer, default=0, comment="切片数量")
    department = Column(String(200), default="", comment="所属科室")
    is_active = Column(String(10), default="有效", comment="有效/无效")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class KnowledgeChunk(Base):
    """知识库文档切片"""
    __tablename__ = "knowledge_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, nullable=False, index=True)
    title = Column(String(300), nullable=True, comment="章节标题")
    content = Column(Text, nullable=False, comment="切片内容")
    hierarchy = Column(String(500), nullable=True, comment="层级路径")
    chunk_index = Column(Integer, default=0, comment="切片序号")
    created_at = Column(DateTime, server_default=func.now())


class QueryLog(Base):
    """查询日志"""
    __tablename__ = "query_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True)
    query_type = Column(String(50), nullable=False, comment="查询类型")
    query_text = Column(String(500), nullable=False)
    result_summary = Column(String(500), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
