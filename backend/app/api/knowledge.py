"""知识库API路由（精简版 — 本系统仅提供测评模板查询，漏洞查询走NVD API）
本地知识库上传/管理功能已移除。
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from loguru import logger

from app.models.database import get_db
from app.models.user import User
from app.models.eval_template import EvalTemplate
from app.core.auth import get_current_user
from app.config import settings

router = APIRouter(prefix="/api/knowledge", tags=["知识库"])


# ========== 测评模板管理 ==========

@router.get("/templates")
async def list_templates(
    category: Optional[str] = None,
    object_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取测评参考描述模板列表（按分类汇总）"""
    query = db.query(EvalTemplate)
    if category:
        query = query.filter(EvalTemplate.category.like(f"%{category}%"))
    if object_type:
        query = query.filter(EvalTemplate.object_type.like(f"%{object_type}%"))

    # 按分类和对象类型汇总
    from sqlalchemy import func
    summary = (
        db.query(
            EvalTemplate.category,
            EvalTemplate.object_type,
            EvalTemplate.source_file,
            func.count(EvalTemplate.id).label("count"),
        )
        .group_by(EvalTemplate.category, EvalTemplate.object_type, EvalTemplate.source_file)
        .all()
    )

    return [
        {
            "category": s[0],
            "object_type": s[1],
            "source_file": s[2],
            "record_count": s[3],
        }
        for s in summary
    ]


@router.get("/templates/search")
async def search_templates(
    control_point: str = Query(..., description="安全控制点"),
    object_type: Optional[str] = Query(None, description="测评对象类型"),
    keyword: Optional[str] = Query(None, description="关键词"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """搜索测评参考描述模板"""
    from sqlalchemy import or_

    query = db.query(EvalTemplate)

    if control_point:
        query = query.filter(
            or_(
                EvalTemplate.control_point.like(f"%{control_point}%"),
                EvalTemplate.control_item.like(f"%{control_point}%"),
            )
        )

    if object_type:
        query = query.filter(
            or_(
                EvalTemplate.object_type.like(f"%{object_type}%"),
                EvalTemplate.category.like(f"%{object_type}%"),
            )
        )

    if keyword:
        query = query.filter(
            or_(
                EvalTemplate.control_item.like(f"%{keyword}%"),
                EvalTemplate.compliant_desc.like(f"%{keyword}%"),
                EvalTemplate.non_compliant_desc.like(f"%{keyword}%"),
            )
        )

    templates = query.limit(limit).all()
    return [
        {
            "id": t.id,
            "category": t.category,
            "object_type": t.object_type,
            "control_point": t.control_point,
            "control_item": t.control_item,
            "compliant_desc": t.compliant_desc,
            "non_compliant_desc": t.non_compliant_desc,
            "not_applicable_desc": t.not_applicable_desc,
            "high_risk_criteria": t.high_risk_criteria,
            "fix_suggestion": t.fix_suggestion,
            "remarks": t.remarks,
        }
        for t in templates
    ]


@router.get("/templates/categories")
async def get_template_categories(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取所有可用的分类和对象类型"""
    from sqlalchemy import distinct

    categories = [r[0] for r in db.query(distinct(EvalTemplate.category)).all() if r[0]]
    object_types = [r[0] for r in db.query(distinct(EvalTemplate.object_type)).all() if r[0]]
    control_points = [r[0] for r in db.query(distinct(EvalTemplate.control_point)).all() if r[0]]

    return {
        "categories": sorted(categories),
        "object_types": sorted(object_types),
        "control_points": sorted(control_points),
    }


# ========== 知识库统计 ==========

@router.get("/stats")
async def get_knowledge_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取知识库统计信息"""
    template_count = db.query(EvalTemplate).count()

    return {
        "knowledge_source": "NVD漏洞库 + 本地测评模板",
        "total_templates": template_count,
    }
