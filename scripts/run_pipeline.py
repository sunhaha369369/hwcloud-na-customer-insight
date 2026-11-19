#!/usr/bin/env python
"""一键执行完整流程：接入 → 分析 → 报告 → CRM 导出。

    python scripts/run_pipeline.py --config config.json

产物（默认输出到 config.output.dir，即 ./insight-output）：
  interim/                     标准化后的标准表 + data_quality.json
  analysis/account_profile.csv 客户画像与分层策略
  analysis/pain_points.csv     痛点优先级清单
  analysis/opportunity_list.csv 机会点清单（全量）
  analysis/alerts.csv          趋势预警清单
  analysis/summary.json        报告数据源
  analysis/report.html         图文报告（浏览器打开 / 打印 PDF）
  analysis/crm_opportunities.* CRM 可导入清单（CSV 中英文 + XLSX 交付包）
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import analyze
import export_crm
import ingest
import make_demo_data
import render_report
from lib.common import ensure_dir, eprint, write_json


def run(config_path, skip_report=False):
    t0 = time.time()
    cfg_dir = os.path.dirname(os.path.abspath(config_path))
    cfg = ingest.load_config(config_path)
    out_root = cfg["output"]["dir"]
    if not os.path.isabs(out_root):
        out_root = os.path.join(cfg_dir, out_root)
    interim = os.path.join(out_root, "interim")
    analysis = os.path.join(out_root, "analysis")
    ensure_dir(interim)
    ensure_dir(analysis)

    eprint("=" * 62)
    eprint(" 客户洞察报告与机会点分析 · 流程开始")
    eprint("=" * 62)

    eprint("\n[1/4] 数据接入与标准化")
    ingest.run(config_path, interim)

    eprint("\n[2/4] 分层画像 / 痛点 / 机会 / 预警")
    summary = analyze.run(interim, analysis)

    eprint("\n[3/4] 生成图文报告")
    report = render_report.build(analysis)

    eprint("\n[4/4] 导出 CRM 机会清单")
    sys.argv = ["export_crm.py", "--analysis", analysis]
    export_crm.main()

    k = summary["kpi"]
    eprint("\n" + "=" * 62)
    eprint(f" 完成，用时 {time.time() - t0:.1f}s")
    eprint(f" 客户 {k['accounts']} 家 ｜ 窗口消费 {k['revenue_window'] / 1e8:.2f} 亿 ｜ "
           f"机会 {k['opp_count']} 条 ｜ P0 预警 {k['alerts_p0']} 条")
    eprint("-" * 62)
    for label, path in [("报告", report),
                        ("客户画像", os.path.join(analysis, "account_profile.csv")),
                        ("痛点清单", os.path.join(analysis, "pain_points.csv")),
                        ("机会清单", os.path.join(analysis, "opportunity_list.csv")),
                        ("预警清单", os.path.join(analysis, "alerts.csv")),
                        ("CRM 清单", os.path.join(analysis, "crm_opportunities.xlsx"))]:
        eprint(f"  {label:<8} {path}")
    eprint("=" * 62)
    return {"out_root": out_root, "analysis": analysis, "report": report, "summary": summary}


def make_demo_setup(root):
    """生成演示数据 + 对应配置，用于无真实数据时快速验证流程。"""
    demo_dir = os.path.join(root, "demo-data")
    if not os.path.isdir(demo_dir) or not os.listdir(demo_dir):
        eprint("→ 生成演示数据（12 个客户 / 24 个月）")
        make_demo_data.generate(demo_dir)
    cfg = {
        "brand": {"company": "华为云", "portfolio": "NA 客户", "report_title": "客户洞察报告与机会点分析"},
        "as_of": "2026-06", "window_months": 12,
        "input_dir": demo_dir,
        "output": {"dir": os.path.join(root, "insight-output")},
    }
    path = os.path.join(root, "config.demo.json")
    write_json(path, cfg)
    return path


def main():
    ap = argparse.ArgumentParser(description="客户洞察全流程")
    ap.add_argument("--config", help="配置文件路径（JSON）")
    ap.add_argument("--demo", action="store_true", help="用内置演示数据快速试跑")
    ap.add_argument("--no-report", action="store_true")
    a = ap.parse_args()
    if not a.config and not a.demo:
        ap.error("必须提供 --config 或 --demo")
    cfg_path = a.config or make_demo_setup(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    run(cfg_path, a.no_report)


if __name__ == "__main__":
    main()
