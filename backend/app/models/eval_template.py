"""测评参考描述模型 — 结构化存储Excel中的测评模板数据"""
from sqlalchemy import Column, Integer, String, Text, DateTime, func
from app.models.database import Base


class EvalTemplate(Base):
    """测评参考描述模板（从Excel导入）"""
    __tablename__ = "eval_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 分类信息
    category = Column(String(100), nullable=False, index=True, comment="安全层面，如：安全计算环境、安全管理制度")
    object_type = Column(String(100), nullable=False, index=True, comment="测评对象类型，如：Linux、MySQL、Tomcat")
    source_file = Column(String(300), nullable=True, comment="来源Excel文件名")

    # 测评项信息
    control_point = Column(String(200), nullable=True, comment="安全控制点，如：身份鉴别")
    control_item = Column(Text, nullable=True, comment="控制项原文（国标要求）")
    item_index = Column(Integer, default=0, comment="序号")

    # 测评对象元数据（部分Excel有）
    test_item_number = Column(String(100), nullable=True, comment="测评项编号")
    std_code = Column(String(100), nullable=True, comment="对应标准编号")

    # 参考描述
    compliant_desc = Column(Text, nullable=True, comment="符合时的参考描述")
    partial_compliant_desc = Column(Text, nullable=True, comment="部分符合时的参考描述")
    non_compliant_desc = Column(Text, nullable=True, comment="不符合时的参考描述")
    not_applicable_desc = Column(Text, nullable=True, comment="不适用时的参考描述")

    # 问题分析（部分Excel有）
    problem_desc = Column(Text, nullable=True, comment="问题描述")
    problem_analysis = Column(Text, nullable=True, comment="问题分析")
    harm_analysis = Column(Text, nullable=True, comment="危害分析")
    fix_suggestion = Column(Text, nullable=True, comment="整改建议")

    # 高风险
    high_risk_criteria = Column(Text, nullable=True, comment="高风险判例/判定标准")
    risk_reduction = Column(Text, nullable=True, comment="降风险措施/理由")

    # 备注
    remarks = Column(Text, nullable=True, comment="备注/测评方法")

    created_at = Column(DateTime, server_default=func.now())


class SystemConfig(Base):
    """系统配置"""
    __tablename__ = "system_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
