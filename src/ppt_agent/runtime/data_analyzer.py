"""数据分析Agent - 用于从数据源提取信息并生成图表建议"""

import json
from pathlib import Path
from typing import Any, Optional


class DataAnalyzerAgent:
    """数据分析Agent"""
    
    def __init__(self):
        self.supported_formats = [".json", ".csv", ".txt"]
    
    def analyze(self, data_source: str) -> dict:
        """分析数据源，提取关键指标
        
        Args:
            data_source: 数据源路径或JSON字符串
            
        Returns:
            分析结果字典
        """
        # 1. 读取数据源
        data = self._load_data(data_source)
        
        # 2. 提取关键指标
        metrics = self._extract_metrics(data)
        
        # 3. 生成图表建议
        charts = self._suggest_charts(metrics)
        
        # 4. 生成洞察
        insights = self._generate_insights(metrics)
        
        return {
            "metrics": metrics,
            "charts": charts,
            "insights": insights,
            "summary": self._generate_summary(metrics, insights)
        }
    
    def _load_data(self, data_source: str) -> dict:
        """加载数据源"""
        # 如果是JSON字符串
        if data_source.startswith("{") or data_source.startswith("["):
            return json.loads(data_source)
        
        # 如果是文件路径
        path = Path(data_source)
        if path.exists():
            if path.suffix == ".json":
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            elif path.suffix == ".csv":
                return self._parse_csv(path)
            elif path.suffix == ".txt":
                with open(path, "r", encoding="utf-8") as f:
                    return {"text": f.read()}
        
        # 如果是简单的键值对
        return {"raw": data_source}
    
    def _parse_csv(self, path: Path) -> dict:
        """解析CSV文件"""
        import csv
        
        data = {"rows": [], "columns": []}
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            data["columns"] = reader.fieldnames or []
            for row in reader:
                data["rows"].append(row)
        
        return data
    
    def _extract_metrics(self, data: dict) -> dict:
        """提取关键指标"""
        metrics = {}
        
        # 递归提取数值型数据
        self._extract_numeric_values(data, metrics, prefix="")
        
        return metrics
    
    def _extract_numeric_values(self, data: Any, metrics: dict, prefix: str):
        """递归提取数值型数据"""
        if isinstance(data, dict):
            for key, value in data.items():
                new_prefix = f"{prefix}.{key}" if prefix else key
                self._extract_numeric_values(value, metrics, new_prefix)
        elif isinstance(data, list):
            for i, item in enumerate(data):
                new_prefix = f"{prefix}[{i}]"
                self._extract_numeric_values(item, metrics, new_prefix)
        elif isinstance(data, (int, float)):
            metrics[prefix] = data
    
    def _suggest_charts(self, metrics: dict) -> list:
        """根据数据类型推荐图表"""
        charts = []
        
        # 分析数据特征
        for key, value in metrics.items():
            # 判断数据类型
            if self._is_time_series(key):
                charts.append({
                    "type": "line",
                    "data": key,
                    "reason": "时序数据适合用折线图展示趋势"
                })
            elif self._is_comparison(key):
                charts.append({
                    "type": "bar",
                    "data": key,
                    "reason": "分类对比适合用柱状图"
                })
            elif self._is_percentage(key):
                charts.append({
                    "type": "pie",
                    "data": key,
                    "reason": "占比数据适合用饼图"
                })
            elif self._is_correlation(key):
                charts.append({
                    "type": "scatter",
                    "data": key,
                    "reason": "相关性数据适合用散点图"
                })
            else:
                charts.append({
                    "type": "bar",
                    "data": key,
                    "reason": "默认使用柱状图展示"
                })
        
        return charts
    
    def _is_time_series(self, key: str) -> bool:
        """判断是否为时序数据"""
        time_keywords = ["月", "季度", "年", "日", "周", "month", "quarter", "year", "date", "time"]
        return any(kw in key.lower() for kw in time_keywords)
    
    def _is_comparison(self, key: str) -> bool:
        """判断是否为对比数据"""
        compare_keywords = ["对比", "比较", "vs", "versus", "竞争", "排名"]
        return any(kw in key.lower() for kw in compare_keywords)
    
    def _is_percentage(self, key: str) -> bool:
        """判断是否为占比数据"""
        percent_keywords = ["占比", "比例", "百分比", "percent", "ratio", "份额"]
        return any(kw in key.lower() for kw in percent_keywords)
    
    def _is_correlation(self, key: str) -> bool:
        """判断是否为相关性数据"""
        corr_keywords = ["相关", "关系", "影响", "correlation", "relationship"]
        return any(kw in key.lower() for kw in corr_keywords)
    
    def _generate_insights(self, metrics: dict) -> list:
        """生成数据洞察"""
        insights = []
        
        # 找出最大值
        if metrics:
            max_key = max(metrics, key=metrics.get)
            max_value = metrics[max_key]
            insights.append(f"最高指标：{max_key} = {max_value}")
            
            # 找出最小值
            min_key = min(metrics, key=metrics.get)
            min_value = metrics[min_key]
            insights.append(f"最低指标：{min_key} = {min_value}")
            
            # 计算平均值
            if len(metrics) > 1:
                avg_value = sum(metrics.values()) / len(metrics)
                insights.append(f"平均值：{avg_value:.2f}")
        
        return insights
    
    def _generate_summary(self, metrics: dict, insights: list) -> str:
        """生成总结"""
        if not metrics:
            return "未发现可分析的数据"
        
        summary_parts = [
            f"共发现 {len(metrics)} 个数据指标",
            f"生成 {len(insights)} 条洞察"
        ]
        
        return "，".join(summary_parts)

