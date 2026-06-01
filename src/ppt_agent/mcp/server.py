"""PPT Agent MCP Server - 提供标准化的MCP接口"""

import asyncio
from typing import Optional
from pathlib import Path

from fastmcp import FastMCP

from ppt_agent.runtime.report_pipeline import generate_report
from ppt_agent.runtime.data_analyzer import DataAnalyzerAgent
from ppt_agent.templates.selector import TemplateSelector

# 创建MCP Server
mcp = FastMCP("ppt-agent")


@mcp.tool()
async def generate_report_ppt(
    topic: str,
    audience: str = "公司高层",
    duration: str = "15分钟",
    template: Optional[str] = None,
    data_source: Optional[str] = None,
    output_dir: str = "."
) -> str:
    """生成汇报PPT
    
    Args:
        topic: 汇报主题
        audience: 目标受众（公司高层、团队成员、客户等）
        duration: 汇报时长（10分钟、15分钟、20分钟、30分钟）
        template: 模板名称（quarterly-review、project-status、annual-summary、strategy-proposal、data-analysis）
        data_source: 数据源路径或JSON字符串
        output_dir: 输出目录
        
    Returns:
        生成结果信息
    """
    try:
        result = await generate_report(
            topic=topic,
            audience=audience,
            duration=duration,
            template=template,
            data_source=data_source,
            output_dir=output_dir
        )
        
        if result["success"]:
            return f"✅ 汇报PPT生成成功！\n输出文件: {result['output_path']}\n使用模板: {result['template_info']['name']}"
        else:
            return "❌ 生成失败"
    except Exception as e:
        return f"❌ 生成失败: {str(e)}"


@mcp.tool()
def get_template_suggestions(
    topic: str,
    audience: Optional[str] = None,
    duration: Optional[str] = None
) -> dict:
    """根据主题推荐模板
    
    Args:
        topic: 汇报主题
        audience: 目标受众
        duration: 汇报时长
        
    Returns:
        推荐的模板信息
    """
    template_id = TemplateSelector.select(topic, audience, duration)
    template_info = TemplateSelector.get_template_info(template_id)
    
    return {
        "template_id": template_id,
        "template_info": template_info,
        "reason": f"根据主题'{topic}'推荐使用{template_info['name']}模板"
    }


@mcp.tool()
def list_templates() -> list:
    """列出所有可用模板
    
    Returns:
        模板列表
    """
    return TemplateSelector.list_templates()


@mcp.tool()
def analyze_data_for_ppt(
    data_source: str,
    chart_type: str = "auto"
) -> dict:
    """分析数据并生成图表建议
    
    Args:
        data_source: 数据源路径或JSON字符串
        chart_type: 图表类型（auto、bar、line、pie、table）
        
    Returns:
        分析结果和图表建议
    """
    try:
        analyzer = DataAnalyzerAgent()
        result = analyzer.analyze(data_source)
        
        return {
            "success": True,
            "metrics": result["metrics"],
            "charts": result["charts"],
            "insights": result["insights"],
            "summary": result["summary"]
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool()
def get_user_preferences() -> dict:
    """获取用户偏好
    
    Returns:
        用户偏好信息
    """
    try:
        from ppt_agent.storage.project_memory import retrieve_project_memory
        
        preferences = retrieve_project_memory(Path.cwd())
        return {
            "success": True,
            "preferences": preferences
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool()
def record_user_preference(
    preference: str,
    category: str = "user_preference"
) -> dict:
    """记录用户偏好
    
    Args:
        preference: 偏好内容
        category: 偏好类别
        
    Returns:
        记录结果
    """
    try:
        from ppt_agent.storage.project_memory import record_project_memory
        
        result = record_project_memory(
            workspace=Path.cwd(),
            feedback=preference,
            category=category,
            source="mcp_server"
        )
        
        return {
            "success": True,
            "message": "偏好已记录"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def main():
    """启动MCP Server"""
    mcp.run()


if __name__ == "__main__":
    main()
