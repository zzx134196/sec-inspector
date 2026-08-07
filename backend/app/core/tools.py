"""Agent工具实现 — 6个Tool Handler，封装业务逻辑供Agent调用"""
import json
from typing import Dict, Any, List, Optional

from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.core.agent import ToolResult, ToolRegistry
from app.core.llm import llm_service
from app.core.search import async_search_knowledge_chunks
from app.core.audit import audit_eval_description, check_logic_consistency
from app.models.database import SessionLocal
from app.models.eval_template import EvalTemplate
from app.models.knowledge import QueryLog
from app.config import settings


# ========== Tool 1: audit_report ==========

async def handle_audit_report(
    content: str,
    control_point: str = "",
    object_type: str = "",
    result_type: str = "",
    db: Session = None,
    **kwargs,
) -> ToolResult:
    """审核等保测评报告内容"""
    if not content or len(content.strip()) < 5:
        return ToolResult(
            success=False,
            summary="待审核内容过短或为空，请提供完整的测评描述文本",
            data={"error": "内容过短"}
        )

    # 1. 查找参考模板
    reference_template = ""
    high_risk_ref = ""
    if control_point:
        try:
            templates = _query_eval_templates(
                control_point=control_point,
                object_type=object_type,
            )
            if templates:
                ref_parts = []
                for t in templates[:3]:
                    part = f"【控制项】{t.get('control_item', '')}\n"
                    if t.get("compliant_desc"):
                        part += f"符合描述: {t['compliant_desc']}\n"
                    if t.get("non_compliant_desc"):
                        part += f"不符合描述: {t['non_compliant_desc']}\n"
                    if t.get("not_applicable_desc"):
                        part += f"不适用描述: {t['not_applicable_desc']}\n"
                    ref_parts.append(part)
                reference_template = "\n---\n".join(ref_parts)

                # 提取高风险参考
                hr_parts = [t.get("high_risk_criteria", "") for t in templates if t.get("high_risk_criteria")]
                if hr_parts:
                    high_risk_ref = "\n".join(hr_parts)
        except Exception as e:
            logger.warning(f"查找参考模板失败: {e}")

    # 2. 调用审核引擎（大文件自动分段）
    from app.core.audit import audit_eval_chunked, MAX_CHUNK_CHARS
    on_thinking = kwargs.get("on_thinking")

    async def _progress_callback(current: int, total: int):
        if on_thinking:
            await on_thinking(f"正在审核第 {current}/{total} 段...")

    try:
        if len(content) > MAX_CHUNK_CHARS:
            logger.info(f"文件内容超过{MAX_CHUNK_CHARS}字，启用分段审核")
            audit_result = await audit_eval_chunked(
                content=content,
                control_point=control_point,
                object_type=object_type,
                result_type=result_type,
                reference_template=reference_template,
                high_risk_ref=high_risk_ref,
                on_progress=_progress_callback,
                on_thinking=on_thinking,
            )
        else:
            audit_result = await audit_eval_description(
                content=content,
                control_point=control_point,
                object_type=object_type,
                result_type=result_type,
                reference_template=reference_template,
                high_risk_ref=high_risk_ref,
                on_thinking=on_thinking,
            )
    except Exception as e:
        logger.error(f"审核引擎调用失败: {e}")
        return ToolResult(
            success=False,
            summary=f"审核执行失败: {str(e)}",
            data={"error": str(e)}
        )

    # 3. 如果有明确结论类型，额外做逻辑一致性检查
    if result_type and result_type in ("符合", "不符合", "部分符合"):
        try:
            logic_result = await check_logic_consistency(
                content=content,
                result_type=result_type,
                control_item=reference_template[:500] if reference_template else "",
            )
            if logic_result.get("has_contradiction"):
                # 合并到审核结果
                if "issues" not in audit_result:
                    audit_result["issues"] = []
                for contradiction in logic_result.get("contradictions", []):
                    audit_result["issues"].append({
                        "dimension": "逻辑一致性",
                        "severity": "high",
                        "description": contradiction,
                        "suggestion": "请检查测评结论与描述内容是否一致",
                        "location": "",
                    })
                audit_result["logic_check"] = logic_result
        except Exception as e:
            logger.warning(f"逻辑检查附加失败: {e}")

    overall = audit_result.get("overall_result", "未知")
    score = audit_result.get("score", 0)
    issue_count = len(audit_result.get("issues", []))

    _log_query("audit_report", f"{control_point}|{object_type}|{result_type}", f"{overall}, {score}分, {issue_count}个问题")

    return ToolResult(
        success=True,
        summary=f"审核完成：{overall}（{score}分），发现{issue_count}个问题",
        data={
            "type": "audit_result",
            "audit": audit_result,
            "control_point": control_point,
            "object_type": object_type,
            "result_type": result_type,
        }
    )


# ========== Tool 2: search_standard ==========

async def handle_search_standard(
    query: str,
    top_k: int = 5,
    **kwargs,
) -> ToolResult:
    """检索国标法规知识库"""
    clauses = await async_search_knowledge_chunks(query, top_k=top_k)

    if not clauses:
        return ToolResult(
            success=True,
            summary="未检索到相关内容，统一知识库可能暂未收录相关信息。",
            data={"type": "standard_search", "clauses": [], "count": 0, "empty_kb": True}
        )

    _log_query("standard_search", query, f"检索到{len(clauses)}条条款")

    # 提取去重后的来源文件名列表
    sources = []
    seen = set()
    for c in clauses:
        src = c.get("source", "")
        if src and src not in seen:
            seen.add(src)
            sources.append(src)

    return ToolResult(
        success=True,
        summary=f"检索到{len(clauses)}条相关国标条款",
        data={
            "type": "standard_search",
            "clauses": clauses,
            "count": len(clauses),
            "sources": sources,
        }
    )


# ========== Tool 3: check_item ==========

async def handle_check_item(
    control_point: str,
    object_type: str = "",
    control_item_keyword: str = "",
    **kwargs,
) -> ToolResult:
    """查询测评参考描述模板"""
    templates = _query_eval_templates(
        control_point=control_point,
        object_type=object_type,
        keyword=control_item_keyword,
    )

    if not templates:
        # 尝试模糊搜索
        templates = _query_eval_templates(
            control_point=control_point,
            object_type="",
            keyword=control_item_keyword,
        )

    if not templates:
        return ToolResult(
            success=True,
            summary=f"未找到'{control_point}'相关的测评参考描述模板",
            data={
                "type": "check_item",
                "templates": [],
                "count": 0,
                "query": {"control_point": control_point, "object_type": object_type},
            }
        )

    _log_query("check_item", f"{control_point}|{object_type}|{control_item_keyword}", f"找到{len(templates)}条模板")

    return ToolResult(
        success=True,
        summary=f"找到{len(templates)}条'{control_point}'的测评参考描述模板",
        data={
            "type": "check_item",
            "templates": templates,
            "count": len(templates),
            "query": {"control_point": control_point, "object_type": object_type},
        }
    )


# ========== Tool 4: search_vulnerability ==========

async def handle_search_vulnerability(
    keyword: str,
    severity: str = None,
    limit: int = 10,
    **kwargs,
) -> ToolResult:
    """搜索漏洞信息"""
    import httpx
    
    # 构造请求
    url = "https://services.nvd.nist.gov/rest/json/cves/2.0"
    params = {"keywordSearch": keyword, "resultsPerPage": limit}
    if severity:
        params["cvssV3Severity"] = severity.upper()
        
    vulnerabilities = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params, headers={"User-Agent": "SecInspector/1.0"})
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("vulnerabilities", []):
                    cve = item.get("cve", {})
                    cve_id = cve.get("id")
                    
                    descriptions = cve.get("descriptions", [])
                    desc = next((d["value"] for d in descriptions if d["lang"] == "en"), "无描述")
                    
                    metrics = cve.get("metrics", {})
                    cvss_data = None
                    if "cvssMetricV31" in metrics:
                        cvss_data = metrics["cvssMetricV31"][0].get("cvssData", {})
                    elif "cvssMetricV30" in metrics:
                        cvss_data = metrics["cvssMetricV30"][0].get("cvssData", {})
                        
                    score = cvss_data.get("baseScore", 0.0) if cvss_data else 0.0
                    sev = cvss_data.get("baseSeverity", "UNKNOWN") if cvss_data else "UNKNOWN"
                    
                    vulnerabilities.append({
                        "cve_id": cve_id,
                        "cvss_score": score,
                        "cvss_severity": sev,
                        "description": desc
                    })
    except Exception as e:
        logger.warning(f"请求NVD API失败: {e}")
        _log_query("vulnerability_search", keyword, "NVD API请求失败")
        return ToolResult(
            success=False,
            summary=f"漏洞搜索失败：无法连接NVD漏洞库（网络异常），请稍后重试",
            data={"type": "vulnerability_search", "vulnerabilities": [], "total": 0, "returned": 0, "error": "network"}
        )

    if not vulnerabilities:
        _log_query("vulnerability_search", keyword, "未找到匹配漏洞")
        return ToolResult(
            success=True,
            summary=f"在NVD漏洞库中未搜索到与\u201c{keyword}\u201d相关的漏洞记录",
            data={"type": "vulnerability_search", "vulnerabilities": [], "total": 0, "returned": 0}
        )

    _log_query("vulnerability_search", keyword, f"找到 {len(vulnerabilities)} 个漏洞")

    return ToolResult(
        success=True,
        summary=f"在NVD漏洞库中搜索到关于\u201c{keyword}\u201d的 {len(vulnerabilities)} 个漏洞记录",
        data={
            "type": "vulnerability_search",
            "vulnerabilities": vulnerabilities,
            "total": len(vulnerabilities),
            "returned": len(vulnerabilities),
        }
    )

# ========== Tool 5: get_vulnerability_detail ==========

async def handle_get_vulnerability_detail(
    cve_id: str,
    **kwargs,
) -> ToolResult:
    """获取漏洞详情"""
    import httpx
    url = f"https://services.nvd.nist.gov/rest/json/cves/2.0?cveId={cve_id}"
    
    vuln_detail = None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"User-Agent": "SecInspector/1.0"})
            if resp.status_code == 200:
                data = resp.json()
                volns = data.get("vulnerabilities", [])
                if volns:
                    cve = volns[0].get("cve", {})
                    
                    desc = next((d["value"] for d in cve.get("descriptions", []) if d["lang"] == "en"), "无描述")
                    metrics = cve.get("metrics", {})
                    cvss_data = None
                    if "cvssMetricV31" in metrics:
                        cvss_data = metrics["cvssMetricV31"][0].get("cvssData", {})
                    elif "cvssMetricV30" in metrics:
                        cvss_data = metrics["cvssMetricV30"][0].get("cvssData", {})
                        
                    cwes = []
                    for weakness in cve.get("weaknesses", []):
                        for wd in weakness.get("description", []):
                            if wd.get("lang") == "en":
                                cwes.append(wd.get("value"))
                                
                    affected = []
                    for conf in cve.get("configurations", []):
                        for node in conf.get("nodes", []):
                            for match in node.get("cpeMatch", []):
                                cpe = match.get("criteria", "")
                                parts = cpe.split(":")
                                if len(parts) >= 5:
                                    affected.append({"vendor": parts[3], "product": parts[4]})
                    
                    unique_affected = []
                    seen = set()
                    for a in affected:
                        key = f"{a['vendor']}:{a['product']}"
                        if key not in seen:
                            seen.add(key)
                            unique_affected.append(a)

                    vuln_detail = {
                        "cve_id": cve_id,
                        "cvss_score": cvss_data.get("baseScore", 0.0) if cvss_data else 0.0,
                        "cvss_severity": cvss_data.get("baseSeverity", "UNKNOWN") if cvss_data else "UNKNOWN",
                        "description": desc,
                        "full_description": desc,
                        "cwes": cwes,
                        "affected_products": unique_affected
                    }
    except Exception as e:
        logger.warning(f"请求NVD API详情失败: {e}")
        _log_query("vulnerability_detail", cve_id, "NVD API请求失败")
        return ToolResult(
            success=False,
            summary=f"获取 {cve_id} 详情失败：无法连接NVD漏洞库（网络异常），请稍后重试",
            data={"type": "vulnerability_detail", "vulnerability": None, "error": "network"}
        )

    if not vuln_detail:
        _log_query("vulnerability_detail", cve_id, "NVD中未收录该CVE")
        return ToolResult(
            success=True,
            summary=f"NVD漏洞库中未收录 {cve_id}，该CVE编号可能不存在或尚未公开",
            data={"type": "vulnerability_detail", "vulnerability": None, "not_found": True}
        )

    _log_query("vulnerability_detail", cve_id, f"获取到 {cve_id} 详情")

    return ToolResult(
        success=True,
        summary=f"已获取 {cve_id} 的漏洞详情",
        data={
            "type": "vulnerability_detail",
            "vulnerability": vuln_detail
        }
    )


# ========== Tool 6: export_file ==========

async def handle_export_file(
    format: str,
    title: str,
    content: str = None,
    columns: list = None,
    rows: list = None,
    **kwargs,
) -> ToolResult:
    """导出文件（返回导出标记，由前端触发实际下载）"""
    export_data = {"type": "export_ready", "format": format, "title": title}

    if format in ("word", "pdf") and content:
        export_data["content"] = content
    elif format == "excel" and columns and rows:
        export_data["columns"] = columns
        export_data["rows"] = rows
    else:
        return ToolResult(
            success=False,
            summary=f"导出{format}缺少必要内容",
            data={"error": f"导出{format}需要提供{'content' if format != 'excel' else 'columns和rows'}"}
        )

    _log_query("export", f"{format}|{title}", "导出准备完成")

    return ToolResult(
        success=True,
        summary=f"已准备好{format.upper()}文件【{title}】的导出，前端将自动触发下载",
        data=export_data
    )


# ========== 辅助函数 ==========

def _query_eval_templates(
    control_point: str = "",
    object_type: str = "",
    keyword: str = "",
    limit: int = 10,
) -> List[Dict]:
    """查询测评参考描述模板"""
    db = SessionLocal()
    try:
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
                    EvalTemplate.remarks.like(f"%{keyword}%"),
                )
            )

        templates = query.limit(limit).all()

        result = []
        for t in templates:
            item = {
                "id": t.id,
                "category": t.category,
                "object_type": t.object_type,
                "control_point": t.control_point or "",
                "control_item": t.control_item or "",
                "test_item_number": t.test_item_number or "",
                "std_code": t.std_code or "",
                "compliant_desc": t.compliant_desc or "",
                "partial_compliant_desc": t.partial_compliant_desc or "",
                "non_compliant_desc": t.non_compliant_desc or "",
                "not_applicable_desc": t.not_applicable_desc or "",
                "problem_desc": t.problem_desc or "",
                "problem_analysis": t.problem_analysis or "",
                "harm_analysis": t.harm_analysis or "",
                "fix_suggestion": t.fix_suggestion or "",
                "high_risk_criteria": t.high_risk_criteria or "",
                "risk_reduction": t.risk_reduction or "",
                "remarks": t.remarks or "",
            }
            result.append(item)

        return result
    except Exception as e:
        logger.error(f"查询测评模板失败: {e}")
        return []
    finally:
        db.close()


def _log_query(query_type: str, query_text: str, result_summary: str = "", user_id: int = None):
    """记录查询日志到QueryLog表"""
    try:
        db = SessionLocal()
        log = QueryLog(
            user_id=user_id,
            query_type=query_type,
            query_text=query_text[:500],
            result_summary=result_summary[:500] if result_summary else None,
        )
        db.add(log)
        db.commit()
        db.close()
    except Exception as e:
        logger.debug(f"QueryLog写入失败(非致命): {e}")


# ========== 注册所有工具 ==========

def create_tool_registry() -> ToolRegistry:
    """创建并注册所有工具"""
    registry = ToolRegistry()
    registry.register("audit_report", handle_audit_report)
    registry.register("search_standard", handle_search_standard)
    registry.register("check_item", handle_check_item)
    registry.register("search_vulnerability", handle_search_vulnerability)
    registry.register("get_vulnerability_detail", handle_get_vulnerability_detail)
    registry.register("export_file", handle_export_file)
    logger.info(f"✅ 已注册 {len(registry.tool_names)} 个Agent工具: {registry.tool_names}")
    return registry
