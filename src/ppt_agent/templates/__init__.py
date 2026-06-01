"""汇报PPT生成模块"""

# 延迟导入以避免循环依赖
def get_report_pipeline():
    from ppt_agent.runtime.report_pipeline import ReportPipeline
    return ReportPipeline

def get_generate_report():
    from ppt_agent.runtime.report_pipeline import generate_report
    return generate_report

def get_data_analyzer():
    from ppt_agent.runtime.data_analyzer import DataAnalyzerAgent
    return DataAnalyzerAgent

def get_chart_generator():
    from ppt_agent.runtime.chart_generator import ChartGenerator
    return ChartGenerator

def get_template_selector():
    from ppt_agent.templates.selector import TemplateSelector
    return TemplateSelector
