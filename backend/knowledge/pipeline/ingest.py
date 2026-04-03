"""知识库导入Pipeline — 统一入口：PDF/Excel → 解析 → 切片 → 向量化 → 入库"""
import os
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional

from loguru import logger

from knowledge.pipeline.parser import parse_document
from knowledge.pipeline.excel_parser import parse_excel_template, parse_high_risk_excel
from knowledge.pipeline.chunker import chunk_document
from knowledge.pipeline.embedder import embed_and_store


async def ingest_pdf_document(
    file_path: str,
    doc_title: str = "",
    db_session=None,
) -> Dict[str, Any]:
    """
    导入PDF文档（国标法规）到知识库
    流程: PDF → 文本解析 → 语义切片 → 向量化 → 写入MySQL + Milvus
    :param file_path: PDF文件路径
    :param doc_title: 文档标题（为空则从文件名推断）
    :param db_session: 数据库会话
    :return: 导入结果统计
    """
    from app.models.database import SessionLocal
    from app.models.knowledge import KnowledgeDocument, KnowledgeChunk

    if not doc_title:
        doc_title = Path(file_path).stem

    db = db_session or SessionLocal()
    should_close = db_session is None

    try:
        # 1. 解析文档
        logger.info(f"[Pipeline] 开始解析PDF: {file_path}")
        text = parse_document(file_path)
        if not text or len(text.strip()) < 50:
            return {"success": False, "error": "文档内容为空或过短", "file": file_path}

        # 2. 创建文档记录
        doc = KnowledgeDocument(
            title=doc_title,
            file_name=os.path.basename(file_path),
            file_path=file_path,
            file_type="pdf",
            file_size=os.path.getsize(file_path),
            content_length=len(text),
            status="processing",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        doc_id = doc.id

        # 3. 切片
        logger.info(f"[Pipeline] 文档切片: {doc_title}, 文本长度={len(text)}")
        chunks = chunk_document(text, doc_title=doc_title)
        if not chunks:
            doc.status = "error"
            doc.status_message = "切片结果为空"
            db.commit()
            return {"success": False, "error": "文档切片结果为空", "doc_id": doc_id}

        # 4. 写入数据库
        chunk_records = []
        for idx, chunk in enumerate(chunks):
            chunk_record = KnowledgeChunk(
                document_id=doc_id,
                chunk_index=idx,
                title=chunk.get("title", "")[:500],
                content=chunk.get("content", ""),
                hierarchy=chunk.get("hierarchy", ""),
            )
            chunk_records.append(chunk_record)

        db.bulk_save_objects(chunk_records)
        db.commit()

        # 5. 向量化（可选，Milvus不可用时跳过）
        milvus_ids = []
        try:
            milvus_ids = await embed_and_store(chunks, doc_id)
        except Exception as e:
            logger.warning(f"[Pipeline] 向量化跳过: {e}")

        # 6. 更新文档状态
        doc.status = "indexed"
        doc.chunk_count = len(chunks)
        db.commit()

        result = {
            "success": True,
            "doc_id": doc_id,
            "title": doc_title,
            "text_length": len(text),
            "chunk_count": len(chunks),
            "milvus_stored": sum(1 for mid in milvus_ids if mid),
        }
        logger.info(f"[Pipeline] PDF导入完成: {result}")
        return result

    except Exception as e:
        logger.error(f"[Pipeline] PDF导入失败: {e}")
        db.rollback()
        return {"success": False, "error": str(e), "file": file_path}
    finally:
        if should_close:
            db.close()


async def ingest_excel_template(
    file_path: str,
    db_session=None,
) -> Dict[str, Any]:
    """
    导入Excel测评模板到结构化数据库
    流程: Excel → 结构化解析 → 写入EvalTemplate表
    :param file_path: Excel文件路径
    :param db_session: 数据库会话
    :return: 导入结果统计
    """
    from app.models.database import SessionLocal
    from app.models.eval_template import EvalTemplate

    db = db_session or SessionLocal()
    should_close = db_session is None

    try:
        file_name = os.path.basename(file_path)
        logger.info(f"[Pipeline] 开始解析Excel模板: {file_name}")

        # 1. 解析Excel
        records = parse_excel_template(file_path)
        if not records:
            return {"success": False, "error": "Excel解析结果为空", "file": file_path}

        # 2. 清除已有同名文件的旧记录（支持重新导入）
        old_count = db.query(EvalTemplate).filter(
            EvalTemplate.source_file == file_name
        ).delete()
        if old_count > 0:
            logger.info(f"[Pipeline] 清除旧记录: {file_name}, {old_count}条")

        # 3. 写入数据库
        template_records = []
        for r in records:
            template = EvalTemplate(
                category=r.get("category", ""),
                object_type=r.get("object_type", ""),
                source_file=r.get("source_file", file_name),
                control_point=r.get("control_point", ""),
                control_item=r.get("control_item", ""),
                item_index=r.get("item_index", 0),
                test_item_number=r.get("test_item_number", ""),
                std_code=r.get("std_code", ""),
                compliant_desc=r.get("compliant_desc", ""),
                partial_compliant_desc=r.get("partial_compliant_desc", ""),
                non_compliant_desc=r.get("non_compliant_desc", ""),
                not_applicable_desc=r.get("not_applicable_desc", ""),
                problem_desc=r.get("problem_desc", ""),
                problem_analysis=r.get("problem_analysis", ""),
                harm_analysis=r.get("harm_analysis", ""),
                fix_suggestion=r.get("fix_suggestion", ""),
                high_risk_criteria=r.get("high_risk_criteria", ""),
                risk_reduction=r.get("risk_reduction", ""),
                remarks=r.get("remarks", ""),
            )
            template_records.append(template)

        db.bulk_save_objects(template_records)
        db.commit()

        result = {
            "success": True,
            "file": file_name,
            "record_count": len(template_records),
            "category": records[0].get("category", "") if records else "",
        }
        logger.info(f"[Pipeline] Excel模板导入完成: {result}")
        return result

    except Exception as e:
        logger.error(f"[Pipeline] Excel模板导入失败: {e}")
        db.rollback()
        return {"success": False, "error": str(e), "file": file_path}
    finally:
        if should_close:
            db.close()


async def ingest_directory(
    dir_path: str,
    db_session=None,
) -> Dict[str, Any]:
    """
    批量导入目录下的所有PDF和Excel文件
    :param dir_path: 目录路径
    :param db_session: 数据库会话
    :return: 批量导入结果
    """
    if not os.path.isdir(dir_path):
        return {"success": False, "error": f"目录不存在: {dir_path}"}

    pdf_files = []
    excel_files = []

    for f in os.listdir(dir_path):
        full_path = os.path.join(dir_path, f)
        if not os.path.isfile(full_path):
            continue
        ext = Path(f).suffix.lower()
        if ext == ".pdf":
            pdf_files.append(full_path)
        elif ext in (".xlsx", ".xls"):
            excel_files.append(full_path)

    logger.info(f"[Pipeline] 批量导入: {len(pdf_files)} PDF, {len(excel_files)} Excel")

    results = {
        "success": True,
        "pdf_results": [],
        "excel_results": [],
        "total_pdf": len(pdf_files),
        "total_excel": len(excel_files),
        "success_count": 0,
        "fail_count": 0,
    }

    # 导入PDF
    for pdf_path in pdf_files:
        try:
            r = await ingest_pdf_document(pdf_path, db_session=db_session)
            results["pdf_results"].append(r)
            if r.get("success"):
                results["success_count"] += 1
            else:
                results["fail_count"] += 1
        except Exception as e:
            logger.error(f"导入PDF失败: {pdf_path}: {e}")
            results["pdf_results"].append({"success": False, "error": str(e), "file": pdf_path})
            results["fail_count"] += 1

    # 导入Excel
    for excel_path in excel_files:
        try:
            r = await ingest_excel_template(excel_path, db_session=db_session)
            results["excel_results"].append(r)
            if r.get("success"):
                results["success_count"] += 1
            else:
                results["fail_count"] += 1
        except Exception as e:
            logger.error(f"导入Excel失败: {excel_path}: {e}")
            results["excel_results"].append({"success": False, "error": str(e), "file": excel_path})
            results["fail_count"] += 1

    logger.info(f"[Pipeline] 批量导入完成: 成功={results['success_count']}, 失败={results['fail_count']}")
    return results


async def ingest_single_file(file_path: str, db_session=None) -> Dict[str, Any]:
    """
    导入单个文件（自动识别类型）
    :param file_path: 文件路径
    :param db_session: 数据库会话
    :return: 导入结果
    """
    ext = Path(file_path).suffix.lower()

    if ext == ".pdf":
        return await ingest_pdf_document(file_path, db_session=db_session)
    elif ext in (".xlsx", ".xls"):
        return await ingest_excel_template(file_path, db_session=db_session)
    else:
        return {"success": False, "error": f"不支持的文件类型: {ext}"}
