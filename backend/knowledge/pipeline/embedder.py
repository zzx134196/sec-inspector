"""向量化和存储模块 — Milvus向量检索 + Embedding生成"""
import asyncio
from typing import List, Dict, Any, Optional

import httpx
from loguru import logger

from app.config import settings


_embedding_available = None  # 缓存Embedding服务可用状态


async def get_embeddings(texts: List[str]) -> List[List[float]]:
    """
    调用Embedding API获取文本向量
    :param texts: 文本列表
    :return: 向量列表
    """
    global _embedding_available

    if not texts:
        return []

    # 如果已知不可用，直接跳过
    if _embedding_available is False:
        return []

    # 分批处理，每批最多32条
    batch_size = 32
    all_embeddings = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    f"{settings.EMBEDDING_BASE_URL}/embeddings",
                    json={
                        "model": settings.EMBEDDING_MODEL,
                        "input": batch,
                    },
                    headers={"Authorization": f"Bearer {settings.EMBEDDING_API_KEY}"},
                )
                response.raise_for_status()
                data = response.json()

                batch_embeddings = [item["embedding"] for item in data["data"]]
                all_embeddings.extend(batch_embeddings)
                _embedding_available = True
                logger.debug(f"Embedding批次 {i // batch_size + 1}: {len(batch)} 条文本")
        except Exception as e:
            if _embedding_available is None:
                _embedding_available = False
                logger.info(f"Embedding服务不可用，后续跳过向量化: {e}")
            return []

    return all_embeddings


async def embed_and_store(chunks: List[Dict[str, str]], doc_id: int) -> List[str]:
    """
    向量化切片并存储到Milvus
    :param chunks: 切片列表 [{title, content, hierarchy}, ...]
    :param doc_id: 文档ID
    :return: Milvus ID列表
    """
    if not chunks:
        return []

    # 1. 生成向量
    texts = [f"{c.get('title', '')} {c.get('content', '')}" for c in chunks]

    try:
        embeddings = await get_embeddings(texts)
    except Exception as e:
        logger.error(f"向量生成失败: {e}")
        return ["" for _ in chunks]

    # 2. 存储到Milvus
    try:
        ids = await _store_to_milvus(chunks, embeddings, doc_id)
        logger.info(f"Milvus存储成功: doc_id={doc_id}, {len(ids)} 条向量")
        return ids
    except Exception as e:
        logger.warning(f"Milvus存储失败（将仅使用数据库关键词检索）: {e}")
        return ["" for _ in chunks]


async def _store_to_milvus(
    chunks: List[Dict],
    embeddings: List[List[float]],
    doc_id: int,
) -> List[str]:
    """存储到Milvus向量数据库"""
    try:
        from pymilvus import connections, Collection, FieldSchema, CollectionSchema, DataType, utility
    except ImportError:
        logger.info("pymilvus未安装，跳过Milvus存储")
        return ["" for _ in chunks]

    collection_name = settings.MILVUS_COLLECTION

    # 连接Milvus
    connections.connect(
        alias="default",
        host=settings.MILVUS_HOST,
        port=settings.MILVUS_PORT,
    )

    # 创建Collection（如不存在）
    if not utility.has_collection(collection_name):
        dim = len(embeddings[0]) if embeddings else 1024
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="doc_id", dtype=DataType.INT64),
            FieldSchema(name="chunk_index", dtype=DataType.INT64),
            FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=500),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=8000),
            FieldSchema(name="hierarchy", dtype=DataType.VARCHAR, max_length=200),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=dim),
        ]
        schema = CollectionSchema(fields=fields, description="等保知识库向量")
        collection = Collection(name=collection_name, schema=schema)

        # 创建索引
        index_params = {
            "metric_type": "COSINE",
            "index_type": "IVF_FLAT",
            "params": {"nlist": 128},
        }
        collection.create_index(field_name="embedding", index_params=index_params)
        logger.info(f"创建Milvus Collection: {collection_name}")
    else:
        collection = Collection(name=collection_name)

    # 准备数据
    doc_ids = [doc_id] * len(chunks)
    chunk_indices = list(range(len(chunks)))
    titles = [c.get("title", "")[:500] for c in chunks]
    contents = [c.get("content", "")[:8000] for c in chunks]
    hierarchies = [c.get("hierarchy", "")[:200] for c in chunks]

    # 插入数据
    result = collection.insert([doc_ids, chunk_indices, titles, contents, hierarchies, embeddings])
    collection.flush()

    ids = [str(pk) for pk in result.primary_keys]

    connections.disconnect("default")
    return ids


async def search_similar(query: str, top_k: int = 5) -> List[Dict]:
    """
    向量相似度检索
    :param query: 查询文本
    :param top_k: 返回数量
    :return: 相似文档块列表
    """
    try:
        from pymilvus import connections, Collection
    except ImportError:
        return []

    # 获取查询向量
    embeddings = await get_embeddings([query])
    if not embeddings or all(v == 0.0 for v in embeddings[0]):
        return []

    collection_name = settings.MILVUS_COLLECTION

    try:
        connections.connect(
            alias="default",
            host=settings.MILVUS_HOST,
            port=settings.MILVUS_PORT,
        )

        from pymilvus import utility
        if not utility.has_collection(collection_name):
            connections.disconnect("default")
            return []

        collection = Collection(name=collection_name)
        collection.load()

        results = collection.search(
            data=[embeddings[0]],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"nprobe": 16}},
            limit=top_k,
            output_fields=["doc_id", "title", "content", "hierarchy"],
        )

        hits = []
        for hit in results[0]:
            hits.append({
                "content": hit.entity.get("content", ""),
                "title": hit.entity.get("title", ""),
                "hierarchy": hit.entity.get("hierarchy", ""),
                "source": "",
                "score": hit.score,
            })

        collection.release()
        connections.disconnect("default")
        return hits

    except Exception as e:
        logger.error(f"Milvus检索失败: {e}")
        try:
            connections.disconnect("default")
        except Exception:
            pass
        return []


async def delete_doc_vectors(doc_id: int):
    """删除指定文档的所有向量"""
    try:
        from pymilvus import connections, Collection, utility
    except ImportError:
        return

    try:
        connections.connect(
            alias="default",
            host=settings.MILVUS_HOST,
            port=settings.MILVUS_PORT,
        )

        collection_name = settings.MILVUS_COLLECTION
        if not utility.has_collection(collection_name):
            connections.disconnect("default")
            return

        collection = Collection(name=collection_name)
        collection.delete(expr=f"doc_id == {doc_id}")
        collection.flush()
        connections.disconnect("default")
        logger.info(f"已删除文档 {doc_id} 的向量数据")
    except Exception as e:
        logger.warning(f"删除文档向量失败: {e}")
        try:
            connections.disconnect("default")
        except Exception:
            pass
