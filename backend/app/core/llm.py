"""LLM服务封装 - 支持流式和非流式调用"""
import json
import re
from typing import AsyncGenerator, Optional, List, Dict

from openai import AsyncOpenAI
from loguru import logger

from app.config import settings


def _strip_think(text: str) -> str:
    """移除qwen3模型输出中的<think>...</think>标签（兜底保护）"""
    if not text:
        return text
    return re.sub(r'<think>[\s\S]*?</think>\s*', '', text).strip()


def _needs_thinking_param(model: str, base_url: str) -> bool:
    """判断是否需要传enable_thinking参数（仅DashScope上的qwen3需要）"""
    is_qwen3 = 'qwen3' in model.lower()
    is_dashscope = 'dashscope' in base_url.lower()
    return is_qwen3 and is_dashscope


class LLMService:
    """大语言模型服务"""

    def __init__(self):
        self.client = AsyncOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
        )
        self.model = settings.LLM_MODEL
        self.base_url = settings.LLM_BASE_URL

    def _extra_body(self) -> dict:
        """DashScope上的qwen3非流式必须传enable_thinking=False，否则400错误。
        客户本地部署(vLLM/Ollama)不支持此参数，不能传。"""
        if _needs_thinking_param(self.model, self.base_url):
            return {"enable_thinking": False}
        return {}

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None,
    ) -> str:
        """非流式对话"""
        try:
            kwargs = dict(
                model=self.model,
                messages=messages,
                temperature=temperature or settings.LLM_TEMPERATURE,
                max_tokens=max_tokens or settings.LLM_MAX_TOKENS,
                stream=False,
            )
            extra = self._extra_body()
            if extra:
                kwargs["extra_body"] = extra

            logger.debug(f"[LLM.chat] model={self.model}, msgs={len(messages)}, extra_body={extra or 'none'}")
            response = await self.client.chat.completions.create(**kwargs)
            msg = response.choices[0].message

            # 日志：原始返回字段
            raw_content = msg.content or ""
            reasoning = getattr(msg, 'reasoning_content', None)
            logger.debug(f"[LLM.chat] 返回 content={repr(raw_content[:100])}, reasoning_content={repr(str(reasoning)[:80]) if reasoning else 'None'}")

            return _strip_think(raw_content)
        except Exception as e:
            logger.error(f"LLM调用失败: {e}")
            raise

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None,
    ) -> AsyncGenerator[str, None]:
        """流式对话 — 兼容<think>标签格式（客户本地模型）和reasoning_content字段（DashScope）"""
        try:
            kwargs = dict(
                model=self.model,
                messages=messages,
                temperature=temperature or settings.LLM_TEMPERATURE,
                max_tokens=max_tokens or settings.LLM_MAX_TOKENS,
                stream=True,
            )
            extra = self._extra_body()
            if extra:
                kwargs["extra_body"] = extra

            logger.debug(f"[LLM.stream] model={self.model}, msgs={len(messages)}, extra_body={extra or 'none'}")
            response = await self.client.chat.completions.create(**kwargs)

            chunk_count = 0
            # 状态机过滤<think>标签（客户本地部署模型会把thinking放在content里）
            in_think = False  # 是否在<think>...</think>块内
            buf = ""          # 缓冲区，用于检测跨chunk的标签

            async for chunk in response:
                delta = chunk.choices[0].delta
                if not delta.content:
                    continue

                text = delta.content

                # 快速路径：如果确认不含think标签且不在think块内，直接yield
                if not in_think and '<' not in text and '<' not in buf:
                    if buf:
                        yield buf
                        buf = ""
                    chunk_count += 1
                    yield text
                    continue

                # 慢速路径：拼接缓冲区逐字符处理
                buf += text
                output = ""
                i = 0
                while i < len(buf):
                    if in_think:
                        # 在think块内，寻找</think>
                        end_pos = buf.find('</think>', i)
                        if end_pos != -1:
                            in_think = False
                            i = end_pos + 8  # 跳过</think>
                        else:
                            # 还没找到结束标签，保留末尾可能不完整的部分
                            buf = buf[max(i, len(buf) - 10):]
                            i = len(buf)
                            break
                    else:
                        # 不在think块内，寻找<think>
                        start_pos = buf.find('<think>', i)
                        if start_pos != -1:
                            # 输出<think>之前的内容
                            output += buf[i:start_pos]
                            in_think = True
                            i = start_pos + 7  # 跳过<think>
                        elif buf[i:].startswith('<') and len(buf) - i < 7:
                            # 可能是不完整的<think标签开头，保留在缓冲区
                            buf = buf[i:]
                            i = len(buf)
                            break
                        else:
                            output += buf[i]
                            i += 1

                if i >= len(buf):
                    buf = ""

                if output:
                    chunk_count += 1
                    yield output

            # 刷新缓冲区
            if buf and not in_think:
                yield buf

            logger.debug(f"[LLM.stream] 完成, {chunk_count} content chunks")
        except Exception as e:
            logger.error(f"LLM流式调用失败: {e}")
            raise

    async def chat_stream_with_thinking(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None,
    ) -> AsyncGenerator[Dict, None]:
        """流式对话，同时输出思考内容和正文。
        每次 yield 一个字典：
          {"type": "thinking", "text": "..."}  — 思考文字（增量）
          {"type": "content", "text": "..."}   — 正文（增量）

        支持两种思考模式：
        1. Qwen3 原生 reasoning_content 字段（优先）
        2. <think>...</think> 文本标签（兼容兜底）
        """
        try:
            kwargs = dict(
                model=self.model,
                messages=messages,
                temperature=temperature or settings.LLM_TEMPERATURE,
                max_tokens=max_tokens or settings.LLM_MAX_TOKENS,
                stream=True,
                extra_body={"enable_thinking": True},
            )

            logger.debug(f"[LLM.stream_thinking] model={self.model}, msgs={len(messages)}, enable_thinking=True")
            response = await self.client.chat.completions.create(**kwargs)
            native_thinking = False
            in_think = False
            buffer = ""
            async for chunk in response:
                delta = chunk.choices[0].delta
                # 优先：Qwen3 原生 reasoning_content 字段
                reasoning = getattr(delta, 'reasoning_content', None)
                if reasoning:
                    native_thinking = True
                    yield {"type": "thinking", "text": reasoning}
                    continue
                text = delta.content
                if not text:
                    continue
                # 如果已检测到原生 thinking，后续 content 直接输出
                if native_thinking:
                    yield {"type": "content", "text": text}
                    continue
                # 兜底：解析 <think> 文本标签
                buffer += text
                while buffer:
                    if in_think:
                        end_idx = buffer.find("</think>")
                        if end_idx != -1:
                            think_piece = buffer[:end_idx]
                            if think_piece:
                                yield {"type": "thinking", "text": think_piece}
                            buffer = buffer[end_idx + 8:]
                            in_think = False
                        else:
                            if buffer:
                                yield {"type": "thinking", "text": buffer}
                            buffer = ""
                            break
                    else:
                        start_idx = buffer.find("<think>")
                        if start_idx != -1:
                            before = buffer[:start_idx]
                            if before:
                                yield {"type": "content", "text": before}
                            buffer = buffer[start_idx + 7:]
                            in_think = True
                        elif "<think" in buffer and not buffer.endswith(">"):
                            safe_end = buffer.rfind("<")
                            if safe_end > 0:
                                yield {"type": "content", "text": buffer[:safe_end]}
                                buffer = buffer[safe_end:]
                            break
                        else:
                            yield {"type": "content", "text": buffer}
                            buffer = ""
                            break
            if buffer:
                yield_type = "thinking" if in_think else "content"
                yield {"type": yield_type, "text": buffer}
        except Exception as e:
            logger.error(f"LLM流式调用(with_thinking)失败: {e}")
            raise

    async def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
        on_thinking=None,
    ) -> dict:
        """JSON格式输出的对话，支持 on_thinking 回调推送思考过程"""
        if on_thinking:
            content_buf = ""
            async for piece in self.chat_stream_with_thinking(messages):
                if piece["type"] == "thinking":
                    await on_thinking(piece["text"])
                else:
                    content_buf += piece["text"]
            result = content_buf
        else:
            result = await self.chat(messages, temperature=temperature)
        try:
            text = _strip_think(result).strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            parsed = json.loads(text.strip())
            logger.debug(f"[LLM.json] 解析成功, keys={list(parsed.keys()) if isinstance(parsed, dict) else 'list'}")
            return parsed
        except json.JSONDecodeError:
            logger.warning(f"[LLM.json] 输出非法JSON: {result[:200]}")
            return {"error": "解析失败", "raw": result}

    def reinitialize(self, base_url: str, api_key: str, model: str):
        """运行时重新初始化LLM客户端"""
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.base_url = base_url
        logger.info(f"LLM客户端已重新初始化: base_url={base_url}, model={model}")


# 全局单例
llm_service = LLMService()
