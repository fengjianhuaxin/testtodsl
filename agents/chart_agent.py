"""图表/报告生成智能体"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from agents.base_agent import BaseAgent


# 中文字体设置
plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


class ChartAgent(BaseAgent):

    def __init__(self, output_dir: str):
        super().__init__("图表/报告生成智能体", "根据结果生成图表或报告")
        self.output_dir = output_dir

    def run(self, input_data: dict) -> dict:
        compute_result = input_data.get("compute_result", [])
        calc_rule = input_data.get("calc_rule", {})
        raw_question = input_data.get("raw_question", "")
        self.log("检查是否需要生成图表...")

        calc_type = calc_rule.get("type", "detail")
        calc_params = calc_rule.get("params", {}) if isinstance(calc_rule, dict) else {}
        group_by = calc_params.get("group_by", []) if isinstance(calc_params, dict) else []
        if isinstance(group_by, str):
            group_by = [group_by]
        if not isinstance(group_by, list):
            group_by = []
        has_group_by = bool([item for item in group_by if str(item).strip()])
        chart_path = None

        if not compute_result:
            self.log("无数据，跳过图表生成")
            return {**input_data, "chart_path": None}

        df = pd.DataFrame(compute_result)

        # 仅分组统计自动生成图表，非分组统计只返回文本与表格。
        if calc_type in ("group_count", "count", "avg", "sum", "max", "min", "rate", "topn") and has_group_by:
            try:
                chart_path = self._generate_chart(df, calc_type, raw_question)
                self.log(f"图表已生成: {chart_path}")
            except Exception as e:
                self.log(f"图表生成失败: {e}")
        else:
            self.log("非分组统计，跳过图表生成")

        return {**input_data, "chart_path": chart_path}

    def _generate_chart(self, df: pd.DataFrame, calc_type: str, title: str) -> str:
        os.makedirs(self.output_dir, exist_ok=True)

        fig, ax = plt.subplots(figsize=(10, 6))

        if len(df) <= 1 and calc_type in ("count", "avg", "sum", "max", "min"):
            # 单值结果用文字展示
            ax.text(0.5, 0.5, str(df.to_dict(orient="records")),
                   transform=ax.transAxes, ha="center", va="center", fontsize=14)
            ax.set_title(title, fontsize=14)
        elif calc_type == "group_count":
            # 分组统计用柱状图
            str_cols = df.select_dtypes(include=["object"]).columns.tolist()
            num_cols = df.select_dtypes(include=["number"]).columns.tolist()
            if str_cols and num_cols:
                x = df[str_cols[0]].astype(str)
                y = df[num_cols[0]]
                bars = ax.bar(x, y, color=plt.cm.Set2.colors[:len(x)])
                ax.set_xlabel(str_cols[0], fontsize=12)
                ax.set_ylabel(num_cols[0], fontsize=12)
                ax.set_title(title, fontsize=14)
                # 添加数值标签
                for bar, val in zip(bars, y):
                    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height(),
                           f"{val}", ha="center", va="bottom", fontsize=11)
                plt.xticks(rotation=30, ha="right")
        else:
            # 通用柱状图
            num_cols = df.select_dtypes(include=["number"]).columns.tolist()
            str_cols = df.select_dtypes(include=["object"]).columns.tolist()
            if str_cols and num_cols:
                x = df[str_cols[0]].astype(str)
                y = df[num_cols[0]]
                bars = ax.bar(x, y, color=plt.cm.Paired.colors[:len(x)])
                ax.set_xlabel(str_cols[0], fontsize=12)
                ax.set_ylabel(num_cols[0], fontsize=12)
                ax.set_title(title, fontsize=14)
                for bar, val in zip(bars, y):
                    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height(),
                           f"{val:.1f}" if isinstance(val, float) else f"{val}",
                           ha="center", va="bottom", fontsize=11)
                plt.xticks(rotation=30, ha="right")

        plt.tight_layout()
        path = os.path.join(self.output_dir, "chart.png")
        plt.savefig(path, dpi=150, bbox_inches="tight")
        plt.close()
        return path
