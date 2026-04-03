"""文档切片器 — 将国标法规文档切分为语义块（适配等保标准文档结构）"""
import re
from typing import List, Dict

from loguru import logger


def chunk_document(text: str, doc_title: str = "", max_chunk_size: int = 800) -> List[Dict[str, str]]:
    """
    将文档文本切分为语义块
    :param text: 文档文本
    :param doc_title: 文档标题
    :param max_chunk_size: 单块最大字符数
    :return: 切片列表，每个切片包含 title, content, hierarchy
    """
    if not text.strip():
        return []

    # 先尝试按国标结构切分
    chunks = _chunk_by_standard_structure(text, doc_title)

    # 如果结构化切分结果太少，使用通用切分
    if len(chunks) < 3:
        chunks = _chunk_by_paragraphs(text, doc_title)

    # 对超长块进行二次切分
    final_chunks = []
    for chunk in chunks:
        content = chunk["content"]
        if len(content) > max_chunk_size:
            sub_chunks = _split_long_chunk(content, chunk["title"], chunk["hierarchy"], max_chunk_size)
            final_chunks.extend(sub_chunks)
        else:
            final_chunks.append(chunk)

    # 过滤过短的块
    final_chunks = [c for c in final_chunks if len(c["content"].strip()) >= 15]

    logger.info(f"文档 '{doc_title}' 切分为 {len(final_chunks)} 个块")
    return final_chunks


def _chunk_by_standard_structure(text: str, doc_title: str) -> List[Dict[str, str]]:
    """
    按国标文档结构切分（章、节、条、款层级）
    国标结构通常为:
    - 章: 1 范围, 2 规范性引用文件, 3 术语和定义 ...
    - 节: 7.1 安全物理环境, 7.2 安全通信网络 ...
    - 条: 7.1.1 物理位置选择, 7.1.2 物理访问控制 ...
    - 款: a) b) c) 或 1) 2) 3)
    """
    chunks = []

    # 匹配章节标题模式
    # 模式1: "数字 标题" (章级，如 "7 安全通用要求")
    # 模式2: "数字.数字 标题" (节级，如 "7.1 安全物理环境")
    # 模式3: "数字.数字.数字 标题" (条级，如 "7.1.1 物理位置选择")
    # 模式4: "第X章/节/条" (中文章节)
    # 模式5: "一、二、三" (中文序号)
    section_pattern = re.compile(
        r'^('
        r'\d+\.\d+\.\d+(?:\.\d+)?'  # 7.1.1 或 7.1.1.1
        r'|\d+\.\d+'                  # 7.1
        r'|\d+'                       # 7
        r'|第[一二三四五六七八九十百]+[章节条款]'
        r'|[一二三四五六七八九十]+[、.]'
        r'|附录\s*[A-Z]'             # 附录A
        r')\s*(.+)',
        re.MULTILINE,
    )

    lines = text.split("\n")
    current_section = doc_title or "文档内容"
    current_hierarchy = ""
    current_content_lines = []

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        match = section_pattern.match(line_stripped)
        # 判断是否为标题行：匹配模式且长度合理（标题通常较短）
        is_title = match and len(line_stripped) < 80

        if is_title:
            # 保存前一个section的内容
            if current_content_lines:
                content = "\n".join(current_content_lines).strip()
                if content:
                    chunks.append({
                        "title": current_section,
                        "content": content,
                        "hierarchy": current_hierarchy,
                    })
                current_content_lines = []

            current_hierarchy = match.group(1).strip()
            current_section = line_stripped
        else:
            current_content_lines.append(line_stripped)

    # 保存最后一个section
    if current_content_lines:
        content = "\n".join(current_content_lines).strip()
        if content:
            chunks.append({
                "title": current_section,
                "content": content,
                "hierarchy": current_hierarchy,
            })

    return chunks


def _chunk_by_paragraphs(text: str, doc_title: str) -> List[Dict[str, str]]:
    """通用段落切分（当结构化切分效果不佳时使用）"""
    chunks = []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    current_section = doc_title or "文档内容"
    current_hierarchy = ""

    for para in paragraphs:
        # 检测标题
        title_match = re.match(
            r'^([一二三四五六七八九十]+[、.]|第[一二三四五六七八九十]+[章节条款]|[\d]+[、.\s])\s*(.+)$',
            para,
        )

        if title_match and len(para) < 60:
            current_section = para
            current_hierarchy = title_match.group(1).strip("、. ")
        elif len(para) > 15:
            chunks.append({
                "title": current_section,
                "content": para,
                "hierarchy": current_hierarchy,
            })

    # 如果仍然没有有效切分，按固定大小切
    if not chunks:
        chunk_size = 500
        for i in range(0, len(text), chunk_size):
            chunk_text = text[i:i + chunk_size].strip()
            if chunk_text:
                chunks.append({
                    "title": doc_title or f"片段{i // chunk_size + 1}",
                    "content": chunk_text,
                    "hierarchy": str(i // chunk_size + 1),
                })

    return chunks


def _split_long_chunk(
    content: str,
    title: str,
    hierarchy: str,
    max_size: int,
) -> List[Dict[str, str]]:
    """将超长块按句子边界二次切分"""
    # 按句号、分号等句子边界切分
    sentences = re.split(r'(?<=[。；;！!？?\n])', content)

    chunks = []
    current_text = ""

    for sentence in sentences:
        if len(current_text) + len(sentence) > max_size and current_text:
            chunks.append({
                "title": title,
                "content": current_text.strip(),
                "hierarchy": hierarchy,
            })
            current_text = sentence
        else:
            current_text += sentence

    if current_text.strip():
        chunks.append({
            "title": title,
            "content": current_text.strip(),
            "hierarchy": hierarchy,
        })

    return chunks
