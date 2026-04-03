"""审核引擎 — 等保测评报告内容审核核心逻辑"""
import json
from typing import Dict, Any, List, Optional

from loguru import logger

from app.core.llm import llm_service


AUDIT_PROMPT = """你是一个专业的等保测评报告审核专家（依据GB/T 22239-2019、GB/T 28448-2019、GB/T 28449-2018）。
请对以下测评描述进行全面审核。

## 审核维度（共10项）

### A. 内容质量类
1. **要素完整性**：测评描述是否包含所有必要的描述要素：
   - 测评方法（访谈/检查/测试/核查）
   - 核查命令或操作步骤
   - 核查结果（具体配置值、截图描述等）
   - 结论判定依据
2. **格式规范性**：描述是否符合等保测评报告标准格式：
   - 编号是否连续、层次是否清晰
   - 是否按"测评对象-测评项-符合情况-测评描述"的四段式结构
   - 专业术语使用是否规范
3. **专业准确性**：技术描述是否准确：
   - 命令/配置路径是否正确
   - 参数含义解释是否准确
   - 是否存在常识性技术错误

### B. 逻辑与合规类
4. **逻辑一致性**：测评结论与描述内容是否矛盾：
   - "符合"的描述中是否出现不符合的证据
   - "不符合"的描述中是否缺少不符合的依据
   - 单项结论与总体结论是否冲突
5. **标准条款对应性**：描述内容是否与GB/T 22239-2019对应条款的要求吻合：
   - 测评项编号a)/b)/c)/d)是否对应正确的标准条款
   - 核查内容是否覆盖了该条款的全部要求点
   - 是否遗漏了该控制点下的其他测评项
6. **结论判定合理性**：符合/部分符合/不符合/不适用的判定是否合理：
   - 是否存在应判为"不符合"却标为"部分符合"的情况
   - 等保2.0要求75分以上且无高危才算合格，评分是否偏高或偏低

### C. 风险与整改类
7. **高风险判例识别**：是否存在等保高风险判例条件（任一触发即不合格）：
   - 身份鉴别：弱口令、共享账户、无登录失败锁定、明文传输口令
   - 访问控制：默认口令、存在可远程利用的高危漏洞
   - 安全审计：审计功能未开启、审计记录保存不足6个月
   - 入侵防范：外部可利用的高危漏洞未修补
8. **整改建议可操作性**：是否给出了具体、可落地的整改建议：
   - 是否包含具体的配置修改方法
   - 是否指出了对应的技术实现方案
   - 是否标注了整改的优先级

### D. 报告完整性类
9. **测评方法合规性**：测评过程是否采用了规范的测评方法（依据GB/T 28449-2018）：
   - 是否包含访谈、文档审查、配置核查、工具测试等多种方法
   - 是否仅靠"访谈"就给出"符合"结论（需要配置核查佐证）
10. **测评对象覆盖度**：测评范围是否完整覆盖所有关键资产：
    - 服务器、数据库、网络设备、安全设备、应用系统是否均已评测
    - 是否遗漏了云平台、中间件等组件

## 待审核内容
- **安全控制点**: {control_point}
- **测评对象**: {object_type}
- **测评结论**: {result_type}
- **描述内容**:
{content}

## 参考模板（标准填写范例）
{reference_template}

## 高风险判例参考
{high_risk_ref}

审核约束（必须遵守）：
1. 只能依据“描述内容”原文、参考模板和高风险判例参考进行判断，不得编造原文中未出现的事实。
2. 如果原文没有出现具体资产数量、主机台数、SSH/Telnet/RDP等协议、账户权限、命令、路径、配置项，就不能在问题描述或整改建议中自行补出。
3. 尤其禁止编造类似“获取3台服务器SSH权限”“已登录多台服务器”“存在root远程登录”等未被原文明确提及的信息。
4. 整改建议必须与当前原文问题直接对应；如果原文只说明“审计策略”或“日志留存”，就不要扩展到无关的SSH配置整改。
5. location 字段必须优先引用原文片段；如果原文没有对应证据，则不要输出该问题。

请严格按以下JSON格式输出审核结果：
{{
  "overall_result": "通过/需修改/存在问题",
  "score": 85,
  "issues": [
    {{
      "dimension": "维度名称（从上述10项中选择）",
      "severity": "high/medium/low",
      "description": "问题描述",
      "suggestion": "修改建议",
      "location": "问题位置（引用原文片段）"
    }}
  ],
  "highlights": ["做得好的方面"],
  "high_risk_warning": "高风险提醒（如有）",
  "summary": "总体评价（一段话）"
}}"""


LOGIC_CHECK_PROMPT = """你是一个逻辑分析专家。请检查以下测评描述与其结论是否存在逻辑矛盾。

测评结论: {result_type}
测评描述:
{content}

控制项要求:
{control_item}

请分析：
1. 描述中的核查结果是否支持"{result_type}"的结论？
2. 是否存在前后矛盾的表述？
3. "符合"的描述中是否包含了不符合的内容，或"不符合"的描述中是否有符合的表述？

输出JSON：
{{
  "has_contradiction": true/false,
  "contradictions": ["矛盾点1", "矛盾点2"],
  "analysis": "分析说明"
}}"""


async def audit_eval_description(
    content: str,
    control_point: str = "",
    object_type: str = "",
    result_type: str = "",
    reference_template: str = "",
    high_risk_ref: str = "",
) -> Dict[str, Any]:
    """
    审核测评描述
    :param content: 待审核的测评描述文本
    :param control_point: 安全控制点
    :param object_type: 测评对象类型
    :param result_type: 测评结论
    :param reference_template: 参考描述模板
    :param high_risk_ref: 高风险判例参考
    :return: 审核结果
    """
    messages = [
        {"role": "system", "content": "你是一个专业的等保测评报告审核专家，只输出JSON。审核要严谨、具体、有建设性。"},
        {"role": "user", "content": AUDIT_PROMPT.format(
            control_point=control_point or "未指定",
            object_type=object_type or "未指定",
            result_type=result_type or "未指定",
            content=content,
            reference_template=reference_template or "（无参考模板）",
            high_risk_ref=high_risk_ref or "（无高风险参考）",
        )},
    ]

    try:
        result = await llm_service.chat_json(messages)
        if "error" not in result:
            return result
    except Exception as e:
        logger.error(f"审核LLM调用失败: {e}")

    # LLM失败时返回基础审核
    return _basic_audit(content, result_type, reference_template)


async def check_logic_consistency(
    content: str,
    result_type: str,
    control_item: str = "",
) -> Dict[str, Any]:
    """检查逻辑一致性"""
    messages = [
        {"role": "system", "content": "你是一个逻辑分析专家，只输出JSON。"},
        {"role": "user", "content": LOGIC_CHECK_PROMPT.format(
            result_type=result_type,
            content=content,
            control_item=control_item or "未提供",
        )},
    ]

    try:
        return await llm_service.chat_json(messages)
    except Exception as e:
        logger.warning(f"逻辑检查失败: {e}")
        return {"has_contradiction": False, "contradictions": [], "analysis": "无法执行逻辑检查"}


def _basic_audit(content: str, result_type: str, reference_template: str) -> Dict[str, Any]:
    """基础审核（LLM不可用时的回退方案）"""
    issues = []

    # 检查内容长度
    if len(content.strip()) < 20:
        issues.append({
            "dimension": "要素完整性",
            "severity": "high",
            "description": "测评描述内容过短，缺少必要的详细信息",
            "suggestion": "建议补充完整的核查过程和结果描述",
            "location": content[:50],
        })

    # 检查是否包含核查关键词
    check_keywords = ["经核查", "核查", "检查", "查看", "验证", "测试"]
    if not any(kw in content for kw in check_keywords):
        issues.append({
            "dimension": "格式规范性",
            "severity": "medium",
            "description": "描述中缺少核查过程的表述",
            "suggestion": "建议以'经核查'开头描述核查过程和结果",
            "location": "",
        })

    # 简单逻辑检查
    if result_type == "符合":
        negative_keywords = ["未", "不", "缺少", "没有", "无法", "未能"]
        for kw in negative_keywords:
            if kw in content and "不存在" not in content:
                issues.append({
                    "dimension": "逻辑一致性",
                    "severity": "high",
                    "description": f"结论为'符合'但描述中出现否定词'{kw}'，可能存在逻辑矛盾",
                    "suggestion": "请检查描述内容是否与'符合'结论一致",
                    "location": "",
                })
                break

    score = 100 - len(issues) * 15
    overall = "通过" if not issues else ("需修改" if score >= 60 else "存在问题")

    return {
        "overall_result": overall,
        "score": max(score, 0),
        "issues": issues,
        "highlights": [],
        "high_risk_warning": None,
        "summary": f"基础审核完成，发现{len(issues)}个问题。" if issues else "基础审核通过，未发现明显问题。",
    }
