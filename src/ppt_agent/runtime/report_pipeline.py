"""汇报专用流水线 - 用于生成汇报PPT"""

import asyncio
from pathlib import Path
from typing import Optional, Any
from datetime import datetime

from ppt_agent.templates.selector import TemplateSelector
from ppt_agent.runtime.data_analyzer import DataAnalyzerAgent
from ppt_agent.storage.project_memory import record_project_memory


class ReportPipeline:
    """汇报专用流水线"""
    
    def __init__(
        self,
        template: Optional[str] = None,
        audience: Optional[str] = None,
        duration: Optional[str] = None,
        data_source: Optional[str] = None,
        output_dir: str = "."
    ):
        self.template = template
        self.audience = audience or "公司高层"
        self.duration = duration or "15分钟"
        self.data_source = data_source
        self.output_dir = Path(output_dir)
        self.data_analyzer = DataAnalyzerAgent()
    
    async def run(self, topic: str) -> dict:
        """运行汇报生成流水线
        
        Args:
            topic: 汇报主题
            
        Returns:
            生成结果
        """
        print(f"开始生成汇报PPT: {topic}")
        
        # 1. 自动选择模板
        if not self.template:
            self.template = TemplateSelector.select(topic, self.audience, self.duration)
            print(f"自动选择模板: {self.template}")
        
        # 2. 增强需求解析
        intent = self._enhance_intent(topic)
        print(f"需求解析完成: {intent['purpose']}")
        
        # 3. 分析数据源（如果有）
        data_analysis = None
        if self.data_source:
            print(f"分析数据源: {self.data_source}")
            data_analysis = self.data_analyzer.analyze(self.data_source)
            print(f"数据分析完成: {data_analysis['summary']}")
        
        # 4. 获取模板信息
        template_info = TemplateSelector.get_template_info(self.template)
        print(f"模板信息: {template_info['name']}")
        
        # 5. 生成PPT（调用现有的多智能体流水线）
        result = await self._generate_ppt(intent, template_info, data_analysis)
        
        # 6. 记录到记忆系统
        self._record_to_memory(topic, result)
        
        print(f"汇报PPT生成完成: {result['output_path']}")
        
        return result
    
    def _enhance_intent(self, topic: str) -> dict:
        """增强需求解析"""
        # 推断汇报目的
        purpose = "工作汇报"
        if "季度" in topic or "Q" in topic:
            purpose = "季度工作汇报"
        elif "年度" in topic or "年终" in topic:
            purpose = "年度工作总结"
        elif "项目" in topic:
            purpose = "项目进展汇报"
        elif "战略" in topic or "规划" in topic:
            purpose = "战略规划提案"
        elif "数据" in topic or "分析" in topic:
            purpose = "数据分析报告"
        
        # 推断语气
        tone = "专业、简洁、数据驱动"
        if "高层" in self.audience or "领导" in self.audience:
            tone = "正式、专业、结论先行"
        elif "团队" in self.audience:
            tone = "亲切、务实、行动导向"
        
        return {
            "topic": topic,
            "audience": self.audience,
            "purpose": purpose,
            "tone": tone,
            "duration": self.duration,
            "template": self.template,
            "language": "中文",
            "style_keywords": ["专业", "简洁", "数据驱动"]
        }
    
    async def _generate_ppt(self, intent: dict, template_info: dict, 
                           data_analysis: Optional[dict]) -> dict:
        """生成PPT
        
        这里应该调用现有的多智能体流水线，但为了简化示例，
        我们直接返回一个模拟结果。
        """
        # 生成输出文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_topic = intent["topic"].replace(" ", "_")[:20]
        output_filename = f"report_{safe_topic}_{timestamp}.pptx"
        output_path = self.output_dir / output_filename
        
        # 这里应该调用实际的PPT生成逻辑
        # 为了示例，我们创建一个简单的PPT
        from ppt_agent.templates.executive_generator import ExecutiveTemplateGenerator
        
        generator = ExecutiveTemplateGenerator(str(self.output_dir))
        
        # 根据模板类型生成对应的PPT
        template_methods = {
            "quarterly-review": generator._generate_quarterly_review,
            "project-status": generator._generate_project_status,
            "annual-summary": generator._generate_annual_summary,
            "strategy-proposal": generator._generate_strategy_proposal,
            "data-analysis": generator._generate_data_analysis,
        }
        
        generator_method = template_methods.get(self.template)
        if generator_method:
            generator_method()
            # 重命名输出文件
            generated_file = self.output_dir / f"{self.template}.pptx"
            if generated_file.exists():
                generated_file.rename(output_path)
        
        return {
            "success": True,
            "output_path": str(output_path),
            "template": self.template,
            "template_info": template_info,
            "intent": intent,
            "data_analysis": data_analysis
        }
    
    def _record_to_memory(self, topic: str, result: dict):
        """记录到记忆系统"""
        try:
            record_project_memory(
                workspace=Path.cwd(),
                feedback=f"用户生成了汇报PPT：{topic}",
                category="user_preference",
                source="report_pipeline",
                metadata={
                    "template": self.template,
                    "audience": self.audience,
                    "duration": self.duration,
                    "success": result.get("success", False)
                }
            )
        except Exception as e:
            print(f"记录记忆失败: {e}")


async def generate_report(
    topic: str,
    audience: str = "公司高层",
    duration: str = "15分钟",
    template: Optional[str] = None,
    data_source: Optional[str] = None,
    output_dir: str = "."
) -> dict:
    """生成汇报PPT的便捷函数
    
    Args:
        topic: 汇报主题
        audience: 目标受众
        duration: 汇报时长
        template: 模板名称
        data_source: 数据源路径
        output_dir: 输出目录
        
    Returns:
        生成结果
    """
    pipeline = ReportPipeline(
        template=template,
        audience=audience,
        duration=duration,
        data_source=data_source,
        output_dir=output_dir
    )
    
    return await pipeline.run(topic)
