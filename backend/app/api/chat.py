"""对话API路由 — Agent架构，等保测评助手版"""
import json
import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from loguru import logger
import os
import tempfile
import shutil

from app.models.database import get_db, SessionLocal
from app.models.user import User
from app.models.conversation import Conversation, Message
from app.core.auth import get_current_user
from app.core.llm import llm_service
from app.core.agent import AgentEngine, AgentStreamEvent
from app.core.tools import create_tool_registry
from knowledge.pipeline.parser import parse_document

router = APIRouter(prefix="/api/chat", tags=["对话"])

# 全局Agent引擎实例
_tool_registry = None
_agent_engine = None


def get_agent_engine() -> AgentEngine:
    global _tool_registry, _agent_engine
    if _agent_engine is None:
        _tool_registry = create_tool_registry()
        _agent_engine = AgentEngine(llm_service, _tool_registry)
    return _agent_engine


# ========== Pydantic Models ==========

class ChatRequest(BaseModel):
    conversation_id: Optional[int] = None
    message: str
    context: Optional[dict] = None


class ChatResponse(BaseModel):
    conversation_id: int
    reply: str
    intent: str = "agent"
    data: Optional[dict] = None
    actions: Optional[list] = None
    tool_calls: Optional[list] = None


# ========== 对话管理 ==========

@router.get("/conversations")
async def list_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取用户的对话列表"""
    conversations = (
        db.query(Conversation)
        .filter(Conversation.user_id == current_user.id)
        .order_by(Conversation.updated_at.desc())
        .limit(50)
        .all()
    )
    return [
        {"id": c.id, "title": c.title, "created_at": str(c.created_at)}
        for c in conversations
    ]


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除对话及其消息"""
    conv = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.user_id == current_user.id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")
    db.query(Message).filter(Message.conversation_id == conversation_id).delete()
    db.delete(conv)
    db.commit()
    return {"message": "对话已删除"}


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取对话的消息历史"""
    conv = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.user_id == current_user.id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")

    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
        .all()
    )
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "intent": m.intent,
            "metadata": json.loads(m.metadata_json) if m.metadata_json else None,
            "created_at": str(m.created_at),
        }
        for m in messages
    ]


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """处理用户上传的文件并返回解析后的文本"""
    allowed_extensions = {".pdf", ".docx", ".doc", ".txt"}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail="不支持的文件格式，仅支持 PDF, DOCX, DOC, TXT")

    try:
        # 使用临时文件保存上传的数据
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name

        content = parse_document(tmp_path)
        os.unlink(tmp_path)

        if not content.strip():
            raise HTTPException(status_code=400, detail="提取不到文件内容或内容为空")

        return {"filename": file.filename, "content": content}

    except Exception as e:
        logger.error(f"文件解析失败: {e}")
        raise HTTPException(status_code=500, detail=f"文件解析失败: {str(e)}")


# ========== 核心：Agent对话 ==========

@router.post("/send", response_model=ChatResponse)
async def send_message(
    req: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """发送消息（非流式）"""
    # 获取或创建对话
    if req.conversation_id:
        conv = db.query(Conversation).filter(
            Conversation.id == req.conversation_id,
            Conversation.user_id == current_user.id,
        ).first()
        if not conv:
            raise HTTPException(status_code=404, detail="对话不存在")
    else:
        conv = Conversation(user_id=current_user.id, title=req.message[:50])
        db.add(conv)
        db.commit()
        db.refresh(conv)

    # 保存用户消息
    user_msg = Message(
        conversation_id=conv.id,
        role="user",
        content=req.message,
        intent="agent",
    )
    db.add(user_msg)
    db.commit()

    # 构建对话历史
    history = _build_conversation_history(db, conv.id, limit=10)

    # 运行Agent
    reply = ""
    data = None
    actions = None
    tool_calls = None

    try:
        agent = get_agent_engine()
        result = await agent.run(
            user_message=req.message,
            conversation_history=history,
            db=db,
        )
        reply = result.reply
        tool_calls = result.tool_calls

        if result.structured_data:
            data = _merge_structured_data(result.structured_data)
            actions = _extract_actions(result.structured_data)

    except Exception as e:
        logger.error(f"Agent处理失败: {e}")
        reply = _fallback_reply(req.message, e)

    # 保存AI回复
    ai_msg = Message(
        conversation_id=conv.id,
        role="assistant",
        content=reply,
        intent="agent",
        metadata_json=json.dumps({
            "data": data,
            "tool_calls": tool_calls,
        }, ensure_ascii=False, default=str) if (data or tool_calls) else None,
    )
    db.add(ai_msg)
    db.commit()

    return ChatResponse(
        conversation_id=conv.id,
        reply=reply,
        intent="agent",
        data=data,
        actions=actions,
        tool_calls=tool_calls,
    )


@router.post("/send/stream")
async def send_message_stream(
    req: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """流式Agent对话"""
    if req.conversation_id:
        conv = db.query(Conversation).filter(
            Conversation.id == req.conversation_id,
            Conversation.user_id == current_user.id,
        ).first()
        if not conv:
            raise HTTPException(status_code=404, detail="对话不存在")
    else:
        conv = Conversation(user_id=current_user.id, title=req.message[:50])
        db.add(conv)
        db.commit()
        db.refresh(conv)

    conv_id = conv.id

    user_msg = Message(conversation_id=conv_id, role="user", content=req.message, intent="agent")
    db.add(user_msg)
    db.commit()

    history = _build_conversation_history(db, conv_id, limit=10)

    async def generate():
        full_reply = ""
        all_structured = []
        all_tool_calls = []

        try:
            agent = get_agent_engine()

            async for event in agent.run_stream(
                user_message=req.message,
                conversation_history=history,
                db=db,
            ):
                if event.type == "thinking":
                    yield f"data: {json.dumps({'type': 'thinking', 'message': event.data.get('message', '正在思考...')}, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0)

                elif event.type == "tool_calling":
                    yield f"data: {json.dumps({'type': 'tool_calling', 'tool': event.data.get('tool', ''), 'args': event.data.get('args', {})}, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0)
                    all_tool_calls.append({"tool": event.data.get("tool"), "args": event.data.get("args")})

                elif event.type == "tool_result":
                    structured = event.data.get("structured")
                    if structured:
                        all_structured.append(structured)
                    yield f"data: {json.dumps({'type': 'tool_result', 'tool': event.data.get('tool', ''), 'success': event.data.get('success', False), 'summary': event.data.get('summary', ''), 'structured': structured}, ensure_ascii=False, default=str)}\n\n"
                    await asyncio.sleep(0)

                elif event.type == "thinking_content":
                    thinking_text = event.data.get("text", "")
                    yield f"data: {json.dumps({'type': 'thinking_content', 'text': thinking_text}, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0)

                elif event.type == "content":
                    text_chunk = event.data.get("text", "")
                    full_reply += text_chunk
                    yield f"data: {json.dumps({'content': text_chunk}, ensure_ascii=False)}\n\n"

                elif event.type == "done":
                    pass

        except Exception as e:
            logger.error(f"Agent流式处理失败: {e}")
            error_msg = _fallback_reply(req.message, e)
            full_reply = error_msg
            yield f"data: {json.dumps({'content': error_msg}, ensure_ascii=False)}\n\n"

        # 保存完整回复
        try:
            save_db = SessionLocal()
            merged = _merge_structured_data(all_structured) if all_structured else None
            ai_msg = Message(
                conversation_id=conv_id,
                role="assistant",
                content=full_reply,
                intent="agent",
                metadata_json=json.dumps({
                    "data": merged,
                    "tool_calls": all_tool_calls,
                }, ensure_ascii=False, default=str) if (merged or all_tool_calls) else None,
            )
            save_db.add(ai_msg)
            save_db.commit()
            save_db.close()
        except Exception as e:
            logger.error(f"保存流式回复失败: {e}")

        merged_data = _merge_structured_data(all_structured) if all_structured else None
        actions = _extract_actions(all_structured) if all_structured else None
        yield f"data: {json.dumps({'done': True, 'conversation_id': conv_id, 'data': merged_data, 'actions': actions, 'tool_calls': all_tool_calls}, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ========== 辅助函数 ==========

def _build_conversation_history(db: Session, conversation_id: int, limit: int = 10) -> list:
    """构建对话历史"""
    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(limit + 1)
        .all()
    )
    messages = list(reversed(messages))

    if messages and messages[-1].role == "user":
        messages = messages[:-1]

    history = []
    for m in messages:
        if m.role in ("user", "assistant"):
            history.append({"role": m.role, "content": m.content or ""})
    return history


def _merge_structured_data(structured_list: list) -> Optional[dict]:
    """合并多个工具返回的结构化数据，优先返回有实际内容的"""
    if not structured_list:
        return None

    priority = {
        "audit_result": 1,
        "vulnerability_detail": 2,
        "vulnerability_search": 3,
        "check_item": 4,
        "standard_search": 5,
        "export_ready": 6,
    }

    # 过滤掉空结果（如搜索返回0条的）
    non_empty = []
    for item in structured_list:
        item_type = item.get("type", "")
        if item_type == "vulnerability_search" and not item.get("vulnerabilities"):
            continue
        if item_type == "standard_search" and not item.get("clauses"):
            continue
        if item_type == "check_item" and not item.get("templates"):
            continue
        non_empty.append(item)

    candidates = non_empty if non_empty else structured_list

    best = None
    best_priority = 999
    for item in candidates:
        item_type = item.get("type", "")
        p = priority.get(item_type, 100)
        if p < best_priority:
            best_priority = p
            best = item

    return best


def _extract_actions(structured_list: list) -> Optional[list]:
    """从结构化数据中提取前端动作按钮"""
    if not structured_list:
        return None

    actions = []
    for item in structured_list:
        item_type = item.get("type", "")

        if item_type == "audit_result":
            actions.extend([
                {"type": "export_audit", "label": "导出审核报告"},
                {"type": "fix_issues", "label": "查看修改建议"},
            ])
        elif item_type == "vulnerability_search":
            actions.append({"type": "export_excel", "label": "导出漏洞列表"})
        elif item_type == "vulnerability_detail":
            actions.append({"type": "export_report", "label": "导出漏洞报告"})
        elif item_type == "check_item":
            actions.extend([
                {"type": "use_template", "label": "使用此模板"},
                {"type": "export_excel", "label": "导出参考描述"},
            ])
        elif item_type == "standard_search":
            actions.append({"type": "export_clauses", "label": "导出条款"})
        elif item_type == "export_ready":
            actions.append({"type": f"download_{item.get('format', 'file')}", "label": f"下载{item.get('format', '').upper()}"})

    return actions if actions else None


def _fallback_reply(user_message: str, error: Exception = None) -> str:
    """LLM不可用时的降级回复"""
    error_detail = f"\n\n错误详情：{str(error)[:200]}" if error else ""
    return (
        "您好！我是「等保测评助手」智能助手。\n\n"
        "当前AI模型服务未连接，请管理员在【系统设置 → LLM配置】中配置正确的模型服务地址。\n\n"
        "配置完成后，我可以为您提供以下服务：\n"
        "1. 📋 测评报告审核 — 检查要素完整性、格式规范性、逻辑一致性\n"
        "2. 📖 国标法规检索 — 查询GB/T 22239等7份核心等保标准\n"
        "3. 📝 测评参考模板 — 查看标准填写范例和参考描述\n"
        "4. 📄 文件导出 — 导出Word/PDF/Excel格式文件\n"
        f"{error_detail}"
    )
