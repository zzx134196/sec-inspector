"""统一检索模块 — 等保测评助手不依赖知识库，直接返回空结果

该系统通过 Agent 意图识别 + 工具调用（国标查询、漏洞搜索等）完成任务，
不需要 Milvus 向量知识库。
"""
from typing import List, Dict
from loguru import logger


async def async_search_knowledge_chunks(query: str, top_k: int = 5) -> List[Dict]:
    """知识库检索（未启用，直接返回空）"""
    logger.debug(f"知识库检索未启用，查询: {query[:30]}")
    return []


def search_knowledge_chunks(query: str, top_k: int = 5) -> List[Dict]:
    """同步版本（未启用，直接返回空）"""
    return []
