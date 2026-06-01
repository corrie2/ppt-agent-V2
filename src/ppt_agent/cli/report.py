"""汇报PPT生成CLI命令"""

import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ppt_agent.runtime.report_pipeline import generate_report
from ppt_agent.templates.selector import TemplateSelector

app = typer.Typer(help="汇报PPT生成命令")
console = Console()


@app.command()
def report(
    topic: str = typer.Argument(..., help="汇报主题"),
    audience: str = typer.Option("公司高层", "--audience", "-a", help="目标受众"),
    duration: str = typer.Option("15分钟", "--duration", "-d", help="汇报时长"),
    template: Optional[str] = typer.Option(None, "--template", "-t", help="模板名称"),
    data_source: Optional[str] = typer.Option(None, "--data", help="数据源路径"),
    output: str = typer.Option(".", "--output", "-o", help="输出目录")
):
    """生成汇报PPT"""
    
    # 显示生成信息
    console.print(Panel.fit(
        f"[bold blue]开始生成汇报PPT[/bold blue]\n"
        f"主题: {topic}\n"
        f"受众: {audience}\n"
        f"时长: {duration}\n"
        f"模板: {template or '自动选择'}\n"
        f"数据源: {data_source or '无'}",
        title="汇报生成"
    ))
    
    # 运行生成流水线
    result = asyncio.run(generate_report(
        topic=topic,
        audience=audience,
        duration=duration,
        template=template,
        data_source=data_source,
        output_dir=output
    ))
    
    # 显示结果
    if result["success"]:
        console.print(Panel.fit(
            f"[bold green]✅ 汇报PPT生成成功！[/bold green]\n"
            f"输出文件: {result['output_path']}\n"
            f"使用模板: {result['template_info']['name']}\n"
            f"模板描述: {result['template_info']['description']}",
            title="生成完成"
        ))
    else:
        console.print("[bold red]❌ 生成失败[/bold red]")


@app.command()
def templates():
    """列出所有可用模板"""
    
    template_list = TemplateSelector.list_templates()
    
    table = Table(title="可用模板")
    table.add_column("模板ID", style="cyan")
    table.add_column("名称", style="green")
    table.add_column("描述", style="white")
    
    for tmpl in template_list:
        table.add_row(tmpl["id"], tmpl["name"], tmpl["description"])
    
    console.print(table)


@app.command()
def recommend(
    topic: str = typer.Argument(..., help="汇报主题"),
    audience: Optional[str] = typer.Option(None, "--audience", "-a", help="目标受众"),
    duration: Optional[str] = typer.Option(None, "--duration", "-d", help="汇报时长")
):
    """推荐模板"""
    
    template_id = TemplateSelector.select(topic, audience, duration)
    template_info = TemplateSelector.get_template_info(template_id)
    
    console.print(Panel.fit(
        f"[bold blue]推荐模板[/bold blue]\n"
        f"主题: {topic}\n"
        f"受众: {audience or '未指定'}\n"
        f"时长: {duration or '未指定'}\n\n"
        f"[bold green]推荐使用: {template_info['name']}[/bold green]\n"
        f"模板ID: {template_id}\n"
        f"描述: {template_info['description']}\n"
        f"页数: {template_info['page_count']}\n"
        f"建议时长: {template_info['duration']}\n"
        f"适合受众: {', '.join(template_info['audience'])}",
        title="模板推荐"
    ))


if __name__ == "__main__":
    app()
