"""
财报 PDF 解析入库（按公司批量处理），入库后回填同比增长率等衍生指标。

用法（项目根目录执行）：
    .venv\\Scripts\\python.exe scripts\\reimport_data.py
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.agent.llm_client import LLMClient
from src.etl.boss import BossAgent
from src.utils.data_paths import find_financial_reports_root
from src.utils.yoy_calculator import run_all_calculations


def main():
    reports_dir = find_financial_reports_root()
    if not reports_dir:
        print("未找到财报目录")
        return

    print(f"财报目录: {reports_dir}")

    llm = LLMClient()
    boss = BossAgent(llm)

    print("开始批量导入...")
    results = boss.run_etl(str(reports_dir), skip_completed=False)

    success = sum(1 for r in results if r.get("status") == "success")
    error = sum(1 for r in results if r.get("status") != "success")
    print(f"\n导入完成: 成功 {success}, 失败 {error}")

    print("回填同比增长率与衍生比率...")
    run_all_calculations()
    print("全部完成")


if __name__ == "__main__":
    main()
