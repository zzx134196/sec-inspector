"""Milvus向量存储封装（来源：总管理系统SDK，适配本地项目依赖）"""
from typing import Any, Dict, List, Optional, Union
import asyncio
from pymilvus import (
    DataType, AsyncMilvusClient, MilvusClient, AnnSearchRequest,
    WeightedRanker, CollectionSchema, Function, FunctionType,
)
from pymilvus.milvus_client import IndexParams
from loguru import logger
from app.core.vector_store_base import VectorStoreBase

Feild_map = {
    "int": DataType.INT64,
    "float": DataType.FLOAT,
    "string": DataType.VARCHAR,
    "dense_vector": DataType.FLOAT_VECTOR,
    "float_vector": DataType.FLOAT_VECTOR,
    "sparse_vector": DataType.SPARSE_FLOAT_VECTOR,
    "dict": DataType.JSON,
    "json": DataType.JSON,
}


class MilvusVectorStore(VectorStoreBase):
    """
    基于Milvus的向量存储实现
    
    支持向量搜索、关键词搜索和混合搜索
    使用Milvus的多向量字段功能和混合搜索
    """
    
    def __init__(
        self,
        uri: str = "http://localhost:19530",
        username: str = "",
        password: str = "",
        use_sparse_vector: bool = True,
        **kwargs
    ):
        self.uri = uri
        self.username = username
        self.password = password
        self.use_sparse_vector = use_sparse_vector
        
        self.client = None
        self.Asyclient = None
        try:
            self.client = MilvusClient(uri=uri, user=username, password=password)
            self.Asyclient = AsyncMilvusClient(uri=uri, user=username, password=password)
        except Exception as exc:
            logger.warning(f"Milvus client init failed, will retry on demand: {exc}")

    def _ensure_clients(self) -> None:
        if self.client is not None and self.Asyclient is not None:
            return
        self.client = MilvusClient(uri=self.uri, user=self.username, password=self.password)
        self.Asyclient = AsyncMilvusClient(
            uri=self.uri, user=self.username, password=self.password
        )
    
    async def _check_collection_exists(self, collection_name: str) -> bool:
        self._ensure_clients()
        return self.client.has_collection(collection_name)

    async def initialize(self, collection_name: str) -> bool:
        """初始化集合（检查存在并加载到内存）"""
        has_collection = await self._check_collection_exists(collection_name)
        if not has_collection:
            logger.warning(f"集合 {collection_name} 不存在")
            return False
        await self.Asyclient.load_collection(collection_name=collection_name)
        return True

    async def fulltext_search(
        self,
        query_texts: List[str],
        collection_name: str,
        anns_field: str = "sparse_vector",
        output_fields: List[str] = ["text", "filename", "department", "metadata"],
        limit: int = 10,
        filter: str = "",
    ) -> List[Dict[str, Any]]:
        """
        Milvus 2.5 BM25 全文搜索

        直接传入原始文本，Milvus 内部通过 BM25 Function 自动分词和匹配。
        不需要外部 embedding 模型。

        参数:
            query_texts: 查询文本列表（原始字符串，非向量）
            collection_name: 目标集合名称
            anns_field: 稀疏向量字段名，默认 sparse_vector
            output_fields: 返回字段列表
            limit: 返回结果数量上限
            filter: 元数据过滤条件

        返回:
            匹配的文档列表，按 BM25 相关性降序排列
        """
        if not self.use_sparse_vector:
            raise ValueError("全文搜索需要启用稀疏向量（BM25），请设置 use_sparse_vector=True")

        if not await self.initialize(collection_name):
            raise ValueError(f"集合 {collection_name} 初始化失败，请检查集合是否存在")

        logger.info(
            "Milvus fulltext_search: collection=%s, anns_field=%s, limit=%s, filter=%s, queries=%s",
            collection_name,
            anns_field,
            limit,
            filter,
            query_texts,
        )

        search_params = {}
        result = await self.Asyclient.search(
            collection_name=collection_name,
            data=query_texts,
            anns_field=anns_field,
            limit=limit,
            filter=filter,
            output_fields=output_fields,
            search_params=search_params,
        )
        return [hit['entity'] for hits in result for hit in hits]

    async def ensure_collection(self, collection_name: str) -> bool:
        """确保BM25 collection存在，不存在则自动创建"""
        self._ensure_clients()
        if self.client.has_collection(collection_name):
            return True

        from pymilvus import (
            MilvusClient, DataType, CollectionSchema, FieldSchema,
            Function, FunctionType,
        )

        schema = MilvusClient.create_schema(auto_id=True, enable_dynamic_field=True)
        schema.add_field("id", DataType.INT64, is_primary=True)
        schema.add_field("text", DataType.VARCHAR, max_length=65535, enable_analyzer=True)
        schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field("filename", DataType.VARCHAR, max_length=500)
        schema.add_field("department", DataType.VARCHAR, max_length=200)
        schema.add_field("metadata", DataType.VARCHAR, max_length=2000)

        bm25_func = Function(
            name="bm25",
            function_type=FunctionType.BM25,
            input_field_names=["text"],
            output_field_names=["sparse_vector"],
        )
        schema.add_function(bm25_func)

        index_params = MilvusClient.prepare_index_params()
        index_params.add_index(
            field_name="sparse_vector",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
        )

        self.client.create_collection(
            collection_name=collection_name,
            schema=schema,
            index_params=index_params,
        )
        logger.info(f"已创建BM25 collection: {collection_name}")
        return True

    async def insert_batch(
        self,
        rows: list,
        collection_name: str,
    ) -> list:
        """批量插入文本行，返回插入的ID列表
        每个row需包含: text, filename, department, metadata
        """
        self._ensure_clients()
        await self.ensure_collection(collection_name)
        result = self.client.insert(collection_name=collection_name, data=rows)
        ids = result.get("ids", [])
        logger.info(f"向 {collection_name} 写入 {len(ids)} 条记录")
        return ids

    async def delete_by_filter(
        self,
        collection_name: str,
        filter_expr: str,
    ) -> int:
        """按条件删除记录，返回删除数量"""
        self._ensure_clients()
        if not self.client.has_collection(collection_name):
            return 0
        result = self.client.delete(collection_name=collection_name, filter=filter_expr)
        count = result.get("delete_count", 0)
        logger.info(f"从 {collection_name} 删除 {count} 条记录，条件: {filter_expr}")
        return count

    async def close(self):
        """关闭连接"""
        if self.client:
            collections = self.client.list_collections()
            for collection_name in collections:
                try:
                    await self.Asyclient.release_collection(collection_name=collection_name)
                except Exception as e:
                    logger.error(f"释放集合 {collection_name} 失败: {str(e)}")
            logger.info("所有集合已释放，资源已清理")
