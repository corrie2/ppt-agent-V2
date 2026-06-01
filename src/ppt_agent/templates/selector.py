"""模板自动选择器"""

from typing import Optional


class TemplateSelector:
    """模板自动选择器"""
    
    # 关键词到模板的映射
    KEYWORD_MAP = {
        # 季度汇报相关
        "季度": "quarterly-review",
        "Q1": "quarterly-review",
        "Q2": "quarterly-review",
        "Q3": "quarterly-review",
        "Q4": "quarterly-review",
        "季报": "quarterly-review",
        
        # 项目相关
        "项目": "project-status",
        "进展": "project-status",
        "进度": "project-status",
        "状态": "project-status",
        
        # 年度相关
        "年度": "annual-summary",
        "年终": "annual-summary",
        "全年": "annual-summary",
        "年报": "annual-summary",
        "总结": "annual-summary",
        "回顾": "annual-summary",
        
        # 战略相关
        "战略": "strategy-proposal",
        "规划": "strategy-proposal",
        "提案": "strategy-proposal",
        "方案": "strategy-proposal",
        "计划": "strategy-proposal",
        "投资": "strategy-proposal",
        
        # 数据分析相关
        "数据": "data-analysis",
        "分析": "data-analysis",
        "报告": "data-analysis",
        "洞察": "data-analysis",
        "研究": "data-analysis",
    }
    
    # 受众到模板的映射
    AUDIENCE_MAP = {
        "高层": "quarterly-review",
        "领导": "quarterly-review",
        "管理层": "quarterly-review",
        "董事会": "strategy-proposal",
        "投资": "strategy-proposal",
        "团队": "project-status",
        "项目": "project-status",
        "客户": "strategy-proposal",
    }
    
    @classmethod
    def select(cls, topic: str, audience: Optional[str] = None, 
               duration: Optional[str] = None) -> str:
        """根据主题和受众自动选择模板
        
        Args:
            topic: 汇报主题
            audience: 目标受众
            duration: 汇报时长
            
        Returns:
            模板名称
        """
        # 1. 首先根据主题关键词匹配
        for keyword, template in cls.KEYWORD_MAP.items():
            if keyword in topic:
                return template
        
        # 2. 根据受众匹配
        if audience:
            for keyword, template in cls.AUDIENCE_MAP.items():
                if keyword in audience:
                    return template
        
        # 3. 根据时长推断
        if duration:
            if "10" in duration or "12" in duration:
                return "data-analysis"
            elif "15" in duration:
                return "quarterly-review"
            elif "20" in duration or "30" in duration:
                return "annual-summary"
        
        # 4. 默认返回季度汇报模板
        return "quarterly-review"
    
    @classmethod
    def get_template_info(cls, template_name: str) -> dict:
        """获取模板信息
        
        Args:
            template_name: 模板名称
            
        Returns:
            模板信息字典
        """
        templates = {
            "quarterly-review": {
                "name": "季度汇报",
                "description": "适用于季度工作汇报，包含KPI、成果、问题、计划",
                "page_count": 12,
                "duration": "15分钟",
                "audience": ["公司高层", "部门领导"]
            },
            "project-status": {
                "name": "项目进展汇报",
                "description": "适用于项目进展汇报，包含进度、风险、资源、里程碑",
                "page_count": 10,
                "duration": "12分钟",
                "audience": ["项目团队", "管理层"]
            },
            "annual-summary": {
                "name": "年度总结",
                "description": "适用于年度工作总结，包含全年回顾、成就、挑战、展望",
                "page_count": 15,
                "duration": "20分钟",
                "audience": ["公司高层", "全体员工"]
            },
            "strategy-proposal": {
                "name": "战略提案",
                "description": "适用于战略规划、业务提案、投资方案等",
                "page_count": 12,
                "duration": "15分钟",
                "audience": ["投资方", "董事会", "高层管理"]
            },
            "data-analysis": {
                "name": "数据分析汇报",
                "description": "适用于数据分析报告、业务洞察、研究结果等",
                "page_count": 10,
                "duration": "12分钟",
                "audience": ["数据团队", "业务部门", "管理层"]
            }
        }
        
        return templates.get(template_name, templates["quarterly-review"])
    
    @classmethod
    def list_templates(cls) -> list:
        """列出所有可用模板
        
        Returns:
            模板列表
        """
        return [
            {
                "id": "quarterly-review",
                "name": "季度汇报",
                "description": "适用于季度工作汇报"
            },
            {
                "id": "project-status",
                "name": "项目进展汇报",
                "description": "适用于项目进展汇报"
            },
            {
                "id": "annual-summary",
                "name": "年度总结",
                "description": "适用于年度工作总结"
            },
            {
                "id": "strategy-proposal",
                "name": "战略提案",
                "description": "适用于战略规划、业务提案"
            },
            {
                "id": "data-analysis",
                "name": "数据分析汇报",
                "description": "适用于数据分析报告"
            }
        ]
