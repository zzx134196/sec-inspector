# -*- coding: utf-8 -*-
"""Agent核心引擎 — 意图识别模式（适配低参数模型，不依赖function calling）"""
import json
import re
import time
from typing import List, Dict, Any, Optional, AsyncGenerator
from dataclasses import dataclass, field

from loguru import logger


# ========== 数据类 ==========

@dataclass
class ToolResult:
    """工具执行结果"""
    success: bool
    summary: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    """Agent最终返回结果"""
    reply: str
    tool_calls: List[Dict] = field(default_factory=list)
    structured_data: List[Dict] = field(default_factory=list)


@dataclass
class AgentStreamEvent:
    """Agent流式事件"""
    type: str  # "thinking" | "tool_calling" | "tool_result" | "content" | "done"
    data: Dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        return json.dumps({"type": self.type, **self.data}, ensure_ascii=False)


# ========== 意图识别 Prompt ==========

INTENT_RECOGNITION_PROMPT = """你是一个意图识别器。分析用户消息，判断用户意图并提取参数。

意图列表（只能选一个）：
1. audit_report — 用户要审核/检查测评描述内容或文件
2. search_standard — 用户要查询国标、法规、等保标准要求
3. check_item — 用户要查询某个控制点的参考描述/模板/怎么写
4. search_vulnerability — 用户要搜索漏洞信息（关键词搜索）
5. get_vulnerability_detail — 用户要查询某个具体CVE编号的漏洞详情
6. export_file — 用户要导出/下载文件
7. chat — 闲聊、打招呼、帮助说明等，不需要工具

请严格按JSON格式输出，不要输出其他内容：
{"intent": "意图名称", "params": {"参数名": "参数值"}}

各意图的参数说明：
- audit_report: content(如果包含万字长文或【附件内容：...】等标志则切勿提取原样留空即可), control_point(控制点名), object_type(对象类型如Linux/MySQL), result_type(符合/不符合/部分符合/不适用)
- search_standard: query(必填,查询内容)
- check_item: control_point(必填,控制点名), object_type(对象类型), control_item_keyword(关键词)
- search_vulnerability: keyword(必填,搜索词), severity(LOW/MEDIUM/HIGH/CRITICAL)
- get_vulnerability_detail: cve_id(必填,如CVE-2021-44228)
- export_file: format(必填,word/pdf/excel), title(必填,标题)
- chat: message(用户原文)

示例：
用户："身份鉴别的等保标准要求是什么"
{"intent": "search_standard", "params": {"query": "身份鉴别的等保标准要求"}}

用户："查询CVE-2021-44228漏洞详情"
{"intent": "get_vulnerability_detail", "params": {"cve_id": "CVE-2021-44228"}}

用户："Linux访问控制怎么写参考描述"
{"intent": "check_item", "params": {"control_point": "访问控制", "object_type": "Linux"}}

用户："你好"
{"intent": "chat", "params": {"message": "你好"}}"""


# ========== 回复生成 Prompt ==========

REPLY_SYSTEM_PROMPT = """你是「等保测评助手」，一个专业的网络安全等级保护测评智能助手。

根据工具查询结果，为用户生成专业、准确、结构清晰的回复。

规则：
- 审核结果：先给总体结论，再逐项列出问题和建议
- 国标条款：基于条款原文回答，标注来源（如"依据GB/T 22239-2019"）
- 漏洞信息：**严格只基于工具返回的数据进行整理展示**，包含CVE编号、CVSS评分、影响范围、修复建议
- **绝对禁止编造**：不得编造任何工具未返回的CVE编号、漏洞描述、评分、产品名等信息
- 工具返回空或失败时，如实告知用户"未查询到相关信息"，不得自行补充
- 回复用中文，格式用Markdown"""


# ========== Tool Registry ==========

class ToolRegistry:
    """工具注册中心"""

    def __init__(self):
        self._tools: Dict[str, Any] = {}

    def register(self, name: str, handler):
        self._tools[name] = handler

    async def execute(self, name: str, args: dict, **kwargs) -> ToolResult:
        handler = self._tools.get(name)
        if not handler:
            return ToolResult(success=False, summary=f"未知工具: {name}", data={"error": f"Tool '{name}' not found"})
        try:
            result = await handler(**args, **kwargs)
            return result
        except Exception as e:
            logger.error(f"工具 {name} 执行失败: {e}")
            return ToolResult(success=False, summary=f"工具执行失败: {str(e)}", data={"error": str(e)})

    @property
    def tool_names(self) -> list:
        return list(self._tools.keys())


# ========== 意图识别辅助 ==========

_CVE_PATTERN = re.compile(r'CVE-\d{4}-\d{4,}', re.IGNORECASE)

VALID_INTENTS = {"audit_report", "search_standard", "check_item",
                 "search_vulnerability", "get_vulnerability_detail",
                 "export_file", "chat"}


def _detect_last_action(conversation_history: List[Dict] = None) -> str:
    """从对话历史中检测上一轮系统执行了什么动作"""
    if not conversation_history:
        return ""
    for msg in reversed(conversation_history):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if any(kw in content for kw in ["审核完成", "审核结果", "总体结论", "问题和建议", "逐项"]):
            return "audit_done"
        if any(kw in content for kw in ["检索到", "条款", "依据", "GB/T", "根据"]):
            return "search_done"
        if any(kw in content for kw in ["CVE-", "CVSS", "漏洞", "评分"]):
            return "vuln_done"
        if any(kw in content for kw in ["参考描述", "控制点", "符合描述", "不符合描述"]):
            return "check_item_done"
        break
    return ""


def _is_modification_feedback(user_input: str, last_action: str) -> bool:
    """判断用户消息是否是对上一轮结果的修改/追问反馈"""
    if not last_action:
        return False
    text = user_input.strip()
    modify_keywords = [
        "修改", "改成", "改为", "换成", "调整", "更改",
        "增加", "添加", "加上", "补充", "加入",
        "删除", "去掉", "移除", "删掉", "不要", "不需要",
        "太长", "太短", "太多", "太少", "简短", "详细",
        "重新", "再写", "重写", "再审",
        "还有吗", "继续", "更多", "其他",
        "为什么", "解释", "能不能详细", "具体说说",
    ]
    if any(kw in text for kw in modify_keywords):
        return True
    new_task_keywords = ["审核", "检查", "查询", "搜索", "CVE", "漏洞", "国标",
                         "标准", "控制点", "怎么写", "导出", "下载"]
    if len(text) < 30 and not any(kw in text for kw in new_task_keywords):
        return True
    return False


def _keyword_fallback_intent(message: str, conversation_history: List[Dict] = None) -> Dict[str, Any]:
    """关键词兜底：当LLM意图识别失败时用规则匹配（支持上下文感知）"""
    # 1. 剥离附件内容，只使用用户的真实指令来进行意图判断
    user_query = message
    has_attachment = False
    if "【附件内容：" in message:
        user_query = message.split("【附件内容：")[0]
        has_attachment = True
        
    msg = user_query.strip()

    # 优先级0.5：如果带了长附件，且说了“分析、报告、审核、检查”等词，绝对是审核报告
    if has_attachment and any(kw in msg for kw in ["分析", "报告", "审核", "检查", "审查", "看看"]):
        return {"intent": "audit_report", "params": {"content": message}}

    # 优先级1：上下文感知 — 检测修改反馈
    last_action = _detect_last_action(conversation_history)
    if _is_modification_feedback(msg, last_action):
        logger.info(f"[Intent] 检测到修改反馈（上一轮: {last_action}）: '{msg[:50]}'")
        return {"intent": "chat", "params": {"message": msg}}
        
    cve_match = _CVE_PATTERN.search(msg)
    if cve_match:
        return {"intent": "get_vulnerability_detail", "params": {"cve_id": cve_match.group(0).upper()}}
        
    if any(kw in msg for kw in ["漏洞", "CVE", "安全漏洞", "Log4j", "OpenSSL"]):
        return {"intent": "search_vulnerability", "params": {"keyword": msg}}
        
    if any(kw in msg for kw in ["审核", "检查", "审查", "审计描述", "测评描述", "分析报告", "分析"]):
        return {"intent": "audit_report", "params": {"content": message}}
        
    if any(kw in msg for kw in ["国标", "标准", "法规", "GB", "等保要求", "等级保护"]):
        return {"intent": "search_standard", "params": {"query": msg}}
        
    if any(kw in msg for kw in ["参考描述", "模板", "怎么写", "填写范例", "控制点"]):
        cp = msg
        for kw in ["参考描述", "模板", "怎么写", "填写范例", "查询"]:
            cp = cp.replace(kw, "")
        return {"intent": "check_item", "params": {"control_point": cp.strip() or msg}}
        
    if any(kw in msg for kw in ["导出", "下载", "export"]):
        return {"intent": "export_file", "params": {"format": "word", "title": "导出文件"}}
        
    # 如果实在匹配不到，但有附件，默认当作报告审核
    if has_attachment:
        return {"intent": "audit_report", "params": {"content": message}}
        
    return {"intent": "chat", "params": {"message": msg}}


def _has_attachment_content(user_message: str) -> bool:
    return "【附件内容：" in (user_message or "")


def _format_audit_reply(audit_payload: Dict[str, Any]) -> str:
    """生成简洁的审核回复文本（详细信息由前端结构化卡片展示）"""
    audit = audit_payload.get("audit") or {}
    overall = audit.get("overall_result", "未知")
    score = audit.get("score", 0)
    issues = audit.get("issues") or []
    high_risk_warning = audit.get("high_risk_warning")

    line = f"审核完成，结果：**{overall}**（{score}分）"
    if issues:
        line += f"，发现 {len(issues)} 个问题。"
    elif high_risk_warning:
        line += f"，存在高风险提醒。"
    else:
        line += "。"
    return line


def _try_fixed_vulnerability_reply(tool_results: list) -> Optional[str]:
    """漏洞查询结果为空或失败时，生成固定回复，避免 LLM 编造不存在的漏洞信息"""
    for tool_name, result in tool_results:
        if not result.success:
            return f"**查询失败**\n\n{result.summary}\n\n请检查网络连接后重试，或确认输入的漏洞信息是否正确。"
        if result.data:
            data = result.data
            if data.get("not_found"):
                cve_id = (data.get("vulnerability") or {}).get("cve_id", "") if data.get("vulnerability") else ""
                if not cve_id and "cve_id" in str(result.summary):
                    cve_id = ""
                return (
                    f"**未找到漏洞信息**\n\n"
                    f"{result.summary}\n\n"
                    f"可能的原因：\n"
                    f"- 该CVE编号不存在或输入有误\n"
                    f"- 该漏洞尚未被NVD收录\n"
                    f"- NVD数据库更新延迟\n\n"
                    f"建议核实CVE编号后重新查询。"
                )
            if data.get("type") == "vulnerability_search" and data.get("total", 0) == 0:
                return (
                    f"**未搜索到相关漏洞**\n\n"
                    f"{result.summary}\n\n"
                    f"建议：\n"
                    f"- 检查关键词拼写是否正确\n"
                    f"- 尝试更通用的关键词（如产品名、协议名）\n"
                    f"- 确认CVE编号格式是否正确（如 CVE-2021-44228）"
                )
    return None


def _split_text_for_stream(text: str, chunk_size: int = 120) -> List[str]:
    if not text:
        return [""]
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


# ========== Agent Engine ==========

class AgentEngine:
    """Agent引擎 — 意图识别模式（适配低参数模型，不依赖function calling）"""

    def __init__(self, llm_service, tool_registry: ToolRegistry):
        self.llm = llm_service
        self.tools = tool_registry

    async def _recognize_intent(self, user_message: str, history: List[Dict] = None) -> Dict[str, Any]:
        """Step1: 意图识别 + 参数提取（上下文感知）"""
        # 最高优先级：只要带有附件内容，直接强行进入报告审核（简化用户心智）
        if "【附件内容：" in user_message:
            logger.info("[Intent] 检测到附件，默认强制使用 audit_report 意图")
            return {"intent": "audit_report", "params": {"content": user_message}}

        # 优先：上下文感知检测修改反馈
        last_action = _detect_last_action(history)
        if _is_modification_feedback(user_message, last_action):
            logger.info(f"[Intent] 检测到修改反馈（上一轮: {last_action}）: '{user_message[:50]}'")
            return {"intent": "chat", "params": {"message": user_message}}

        messages = [
            {"role": "system", "content": INTENT_RECOGNITION_PROMPT},
        ]
        if history and len(history) >= 2 and not _has_attachment_content(user_message):
            messages.append({"role": "user", "content": f"上文摘要：{history[-2].get('content', '')[:200]}"})
            messages.append({"role": "assistant", "content": "好的，我已了解上文。"})
        messages.append({"role": "user", "content": user_message})

        try:
            result = await self.llm.chat_json(messages, temperature=0.1)
            intent = result.get("intent", "")
            params = result.get("params", {})

            if intent not in VALID_INTENTS or "error" in result:
                logger.warning(f"LLM意图识别结果无效: {result}, 使用关键词兜底")
                return _keyword_fallback_intent(user_message, history)

            logger.info(f"意图识别成功: intent={intent}, params={params}")
            return {"intent": intent, "params": params}
        except Exception as e:
            logger.warning(f"意图识别LLM调用失败: {e}, 使用关键词兜底")
            return _keyword_fallback_intent(user_message, history)

    async def _recognize_intent_stream(self, user_message: str, history: List[Dict] = None):
        """流式意图识别 — thinking 实时推送，content（JSON）收集后解析

        Yields:
            {"type": "thinking", "text": "..."} — 思考过程
            {"type": "result", "data": {...}}   — 最终意图结果
        """
        import json as _json

        # 最高优先级：只要带有附件内容，直接强行进入报告审核（简化用户心智）
        if "【附件内容：" in user_message:
            logger.info("[Intent-Stream] 检测到附件，默认强制使用 audit_report 意图")
            yield {"type": "thinking", "text": "检测到上传的附件文件，将进行等保测评报告审核...\n"}
            yield {"type": "result", "data": {"intent": "audit_report", "params": {"content": user_message}}}
            return

        # 优先：上下文感知检测修改反馈
        last_action = _detect_last_action(history)
        if _is_modification_feedback(user_message, last_action):
            yield {"type": "thinking", "text": "识别为修改反馈，将基于上下文回复...\n"}
            yield {"type": "result", "data": {"intent": "chat", "params": {"message": user_message}}}
            return

        messages = [
            {"role": "system", "content": INTENT_RECOGNITION_PROMPT},
        ]
        if history and len(history) >= 2 and not _has_attachment_content(user_message):
            messages.append({"role": "user", "content": f"上文摘要：{history[-2].get('content', '')[:200]}"})
            messages.append({"role": "assistant", "content": "好的，我已了解上文。"})
        messages.append({"role": "user", "content": user_message})

        try:
            content_buf = ""
            async for piece in self.llm.chat_stream_with_thinking(messages):
                if piece["type"] == "thinking":
                    yield {"type": "thinking", "text": piece["text"]}
                else:
                    content_buf += piece["text"]

            try:
                clean = content_buf.strip()
                if clean.startswith("```"):
                    clean = re.sub(r'^```\w*\n?', '', clean)
                    clean = re.sub(r'\n?```$', '', clean)
                result = _json.loads(clean)
            except _json.JSONDecodeError:
                logger.warning(f"流式意图识别JSON解析失败: {content_buf[:200]}")
                yield {"type": "result", "data": _keyword_fallback_intent(user_message, history)}
                return

            intent = result.get("intent", "")
            params = result.get("params", {})
            if intent not in VALID_INTENTS:
                yield {"type": "result", "data": _keyword_fallback_intent(user_message, history)}
                return

            logger.info(f"意图识别(stream): intent={intent}, params={params}")
            yield {"type": "result", "data": {"intent": intent, "params": params}}
        except Exception as e:
            logger.warning(f"流式意图识别异常: {e}")
            yield {"type": "result", "data": _keyword_fallback_intent(user_message, history)}

    async def _execute_tools(self, intent: str, params: dict, user_message: str = "", db=None, on_thinking=None) -> List[Dict]:
        """Step2: 根据意图执行工具（程序化分发，不依赖LLM）"""
        results = []

        if intent == "audit_report":
            content = params.get("content", "")
            if "【附件内容：" in user_message:
                parts = user_message.split("】\n", 1)
                content = parts[-1].strip() if len(parts) > 1 else content
            elif not content:
                content = user_message.strip()
            
            cp = params.get("control_point", "")
            if cp:
                r = await self.tools.execute("check_item", {"control_point": cp, "object_type": params.get("object_type", "")}, db=db)
                results.append(("check_item", r))

            extra_kwargs = {}
            if on_thinking:
                extra_kwargs["on_thinking"] = on_thinking

            r = await self.tools.execute("audit_report", {
                "content": content,
                "control_point": cp,
                "object_type": params.get("object_type", ""),
                "result_type": params.get("result_type", ""),
                **extra_kwargs,
            }, db=db)
            results.append(("audit_report", r))

        elif intent == "search_standard":
            r = await self.tools.execute("search_standard", {"query": params.get("query", ""), "top_k": 5}, db=db)
            results.append(("search_standard", r))

        elif intent == "check_item":
            r = await self.tools.execute("check_item", {
                "control_point": params.get("control_point", ""),
                "object_type": params.get("object_type", ""),
                "control_item_keyword": params.get("control_item_keyword", ""),
            }, db=db)
            results.append(("check_item", r))

        elif intent == "search_vulnerability":
            r = await self.tools.execute("search_vulnerability", {
                "keyword": params.get("keyword", ""),
                "severity": params.get("severity"),
                "limit": 10,
            }, db=db)
            results.append(("search_vulnerability", r))

        elif intent == "get_vulnerability_detail":
            r = await self.tools.execute("get_vulnerability_detail", {"cve_id": params.get("cve_id", "")}, db=db)
            results.append(("get_vulnerability_detail", r))

        elif intent == "export_file":
            r = await self.tools.execute("export_file", {
                "format": params.get("format", "word"),
                "title": params.get("title", "导出文件"),
                "content": params.get("content", ""),
            }, db=db)
            results.append(("export_file", r))

        return results

    async def run(
        self,
        user_message: str,
        conversation_history: List[Dict] = None,
        db=None,
    ) -> AgentResult:
        """非流式Agent：意图识别 → 工具执行 → 回复生成"""
        # Step1: 意图识别
        intent_result = await self._recognize_intent(user_message, conversation_history)
        intent = intent_result["intent"]
        params = intent_result["params"]

        # chat意图不需要工具（包括修改反馈场景，利用对话历史上下文回复）
        if intent == "chat":
            messages = [
                {"role": "system", "content": REPLY_SYSTEM_PROMPT},
            ]
            if conversation_history:
                messages.extend(conversation_history[-8:])
            messages.append({"role": "user", "content": user_message})
            reply = await self.llm.chat(messages)
            return AgentResult(reply=reply)

        # Step2: 执行工具
        tool_results = await self._execute_tools(intent, params, user_message=user_message, db=db)
        tool_call_log = []
        structured_data_list = []
        tool_context = ""

        for tool_name, result in tool_results:
            tool_call_log.append({"tool": tool_name, "args": {}, "success": result.success, "summary": result.summary})
            if result.data:
                structured_data_list.append(result.data)
            tool_context += f"\n【{tool_name}结果】{result.summary}\n"
            if result.data:
                tool_context += json.dumps(result.data, ensure_ascii=False, default=str)[:3000] + "\n"

        if intent == "audit_report":
            audit_payload = next((item for item in structured_data_list if item.get("type") == "audit_result"), None)
            if audit_payload:
                reply = _format_audit_reply(audit_payload)
            else:
                reply = "审核完成，但未获取到结构化审核结果，请重试。"
            return AgentResult(reply=reply, tool_calls=tool_call_log, structured_data=structured_data_list)

        # Step3: 基于工具结果生成回复
        messages = [
            {"role": "system", "content": REPLY_SYSTEM_PROMPT},
        ]
        if conversation_history:
            messages.extend(conversation_history[-4:])
        messages.append({"role": "user", "content": f"用户问题：{user_message}\n\n工具查询结果：\n{tool_context}"})

        reply = await self.llm.chat(messages)
        return AgentResult(reply=reply, tool_calls=tool_call_log, structured_data=structured_data_list)

    async def run_stream(
        self,
        user_message: str,
        conversation_history: List[Dict] = None,
        db=None,
    ) -> AsyncGenerator[AgentStreamEvent, None]:
        """流式Agent：意图识别 → 工具执行 → 流式回复生成"""
        # Step1: 流式意图识别（thinking 实时推送）
        yield AgentStreamEvent(type="thinking", data={"message": "正在分析意图..."})

        intent_result = None
        async for chunk in self._recognize_intent_stream(user_message, conversation_history):
            if chunk["type"] == "thinking":
                yield AgentStreamEvent(type="thinking_content", data={"text": chunk["text"]})
            elif chunk["type"] == "result":
                intent_result = chunk["data"]

        if not intent_result:
            intent_result = _keyword_fallback_intent(user_message, conversation_history)

        intent = intent_result["intent"]
        params = intent_result.get("params", {})

        logger.info(f"Agent(stream) 意图={intent}, 参数={params}")

        # chat意图直接流式回复（包括修改反馈场景）
        if intent == "chat":
            messages = [
                {"role": "system", "content": REPLY_SYSTEM_PROMPT},
            ]
            if conversation_history:
                messages.extend(conversation_history[-8:])
            messages.append({"role": "user", "content": user_message})
            async for piece in self.llm.chat_stream_with_thinking(messages):
                if piece["type"] == "thinking":
                    yield AgentStreamEvent(type="thinking_content", data={"text": piece["text"]})
                else:
                    yield AgentStreamEvent(type="content", data={"text": piece["text"]})
            yield AgentStreamEvent(type="done", data={})
            return

        # Step2: 执行工具（审核工具通过队列实时推送思考过程）
        import asyncio
        thinking_queue: asyncio.Queue = asyncio.Queue()

        async def _on_thinking(text: str):
            await thinking_queue.put(text)

        if intent == "audit_report":
            yield AgentStreamEvent(type="tool_calling", data={"tool": "audit_report", "args": params, "status": "starting"})
            yield AgentStreamEvent(type="thinking_content", data={"text": "正在审核测评报告，请稍候...\n"})

        async def _run_tools():
            if intent == "audit_report":
                return await self._execute_tools(intent, params, user_message=user_message, db=db, on_thinking=_on_thinking)
            return await self._execute_tools(intent, params, user_message=user_message, db=db)

        tool_task = asyncio.create_task(_run_tools())

        if intent == "audit_report":
            while not tool_task.done():
                try:
                    text = await asyncio.wait_for(thinking_queue.get(), timeout=0.5)
                    yield AgentStreamEvent(type="thinking_content", data={"text": text + "\n"})
                except asyncio.TimeoutError:
                    continue

        tool_results = await tool_task

        while not thinking_queue.empty():
            text = thinking_queue.get_nowait()
            yield AgentStreamEvent(type="thinking_content", data={"text": text + "\n"})

        tool_context = ""
        structured_data_list = []

        for tool_name, result in tool_results:
            yield AgentStreamEvent(type="tool_calling", data={"tool": tool_name, "args": params})
            yield AgentStreamEvent(type="tool_result", data={
                "tool": tool_name,
                "success": result.success,
                "summary": result.summary,
                "structured": result.data,
            })
            if result.data:
                structured_data_list.append(result.data)
            tool_context += f"\n【{tool_name}结果】{result.summary}\n"
            if result.data:
                tool_context += json.dumps(result.data, ensure_ascii=False, default=str)[:3000] + "\n"

        if intent == "audit_report":
            audit_payload = next((item for item in structured_data_list if item.get("type") == "audit_result"), None)
            reply = _format_audit_reply(audit_payload) if audit_payload else "审核完成，但未获取到结构化审核结果，请重试。"
            for chunk in _split_text_for_stream(reply):
                yield AgentStreamEvent(type="content", data={"text": chunk})
            yield AgentStreamEvent(type="done", data={})
            return

        # 漏洞查询：工具返回空/失败时，直接固定回复，禁止LLM编造
        if intent in ("search_vulnerability", "get_vulnerability_detail"):
            vuln_fixed = _try_fixed_vulnerability_reply(tool_results)
            if vuln_fixed:
                for chunk in _split_text_for_stream(vuln_fixed):
                    yield AgentStreamEvent(type="content", data={"text": chunk})
                yield AgentStreamEvent(type="done", data={})
                return

        # Step3: 流式生成回复
        messages = [
            {"role": "system", "content": REPLY_SYSTEM_PROMPT},
        ]
        if conversation_history:
            messages.extend(conversation_history[-4:])
        messages.append({"role": "user", "content": f"用户问题：{user_message}\n\n工具查询结果：\n{tool_context}"})

        try:
            async for piece in self.llm.chat_stream_with_thinking(messages):
                if piece["type"] == "thinking":
                    yield AgentStreamEvent(type="thinking_content", data={"text": piece["text"]})
                else:
                    yield AgentStreamEvent(type="content", data={"text": piece["text"]})
        except Exception as e:
            logger.error(f"流式回复生成失败: {e}")
            yield AgentStreamEvent(type="content", data={"text": "抱歉，回复生成失败，请稍后重试。"})

        yield AgentStreamEvent(type="done", data={})
