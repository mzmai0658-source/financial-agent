# -*- coding: utf-8 -*-
"""
比赛残留检查：保证在线服务代码（src/agent、src/api、frontend/src）是通用财报 Agent，
不包含竞赛专用的硬编码、赛题文案或废弃依赖。
"""

import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

AGENT_DIR = ROOT_DIR / "src" / "agent"
API_DIR = ROOT_DIR / "src" / "api"
FRONTEND_SRC = ROOT_DIR / "frontend" / "src"


def iter_py_files(*dirs: Path):
    for directory in dirs:
        for path in directory.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            yield path


def iter_frontend_files():
    for pattern in ("*.vue", "*.ts", "*.css", "*.html"):
        for path in FRONTEND_SRC.rglob(pattern):
            if "node_modules" in path.parts:
                continue
            yield path


class TestBackendResidue:
    # 赛题/竞赛文案不允许出现在在线服务代码中
    FORBIDDEN_TERMS = [
        "比赛", "竞赛", "赛题", "B题", "泰迪", "teddy",
        "competition", "附件6", "提交结果", "评分标准",
    ]

    def test_no_competition_terms_in_online_code(self):
        violations = []
        for path in iter_py_files(AGENT_DIR, API_DIR):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for term in self.FORBIDDEN_TERMS:
                if re.search(term, text, re.IGNORECASE):
                    violations.append(f"{path.relative_to(ROOT_DIR)}: {term}")
        assert not violations, "在线服务代码包含比赛残留：\n" + "\n".join(violations)

    def test_legacy_rule_engine_removed(self):
        assert not (AGENT_DIR / "query_agent.py").exists(), "query_agent.py 应已被 orchestrator.py 取代"
        assert not (AGENT_DIR / "chat_planner.py").exists(), "chat_planner.py 应已被移除"
        assert not (AGENT_DIR / "competition_runner.py").exists()
        assert not (AGENT_DIR / "task2_executor.py").exists()

    def test_etl_moved_out_of_agent(self):
        for name in ("etl_worker.py", "boss.py", "rag_builder.py"):
            assert not (AGENT_DIR / name).exists(), f"{name} 应已移动到 src/etl"
            assert (ROOT_DIR / "src" / "etl" / name).exists(), f"src/etl/{name} 缺失"

    def test_no_langchain_dependency_in_agent(self):
        for path in iter_py_files(AGENT_DIR, API_DIR):
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "langchain" not in text.lower(), f"{path.relative_to(ROOT_DIR)} 仍依赖 langchain"

    def test_no_hardcoded_company_answer_logic(self):
        """不允许把特定公司名写死在 agent 决策逻辑里（公司表应来自附件1注册表）。"""
        # 这些公司名只允许出现在示例问题/注释中，不允许出现在 src/agent 的代码常量里
        hardcoded_patterns = [
            r"_answer_insurance_catalog",
            r"insurance_catalog",
            r"医保目录.*硬编码",
        ]
        for path in iter_py_files(AGENT_DIR):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for pattern in hardcoded_patterns:
                assert not re.search(pattern, text), f"{path.relative_to(ROOT_DIR)} 包含硬编码答案逻辑: {pattern}"

    def test_new_core_modules_exist(self):
        for name in ("orchestrator.py", "prompts.py", "sql_guard.py", "fallback.py", "domain.py"):
            assert (AGENT_DIR / name).exists(), f"src/agent/{name} 缺失"


class TestFrontendResidue:
    def test_no_ant_design_in_dependencies(self):
        package_json = (ROOT_DIR / "frontend" / "package.json").read_text(encoding="utf-8")
        assert "ant-design-vue" not in package_json
        assert "@ant-design" not in package_json

    def test_no_ant_design_imports_in_source(self):
        for path in iter_frontend_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "ant-design" not in text, f"{path.relative_to(ROOT_DIR)} 仍引用 ant-design"
            assert not re.search(r"<a-[a-z]", text), f"{path.relative_to(ROOT_DIR)} 仍使用 antd 组件"

    def test_no_competition_terms_in_frontend(self):
        forbidden = ["比赛", "竞赛", "赛题", "B题", "泰迪", "teddy"]
        for path in iter_frontend_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for term in forbidden:
                assert term not in text, f"{path.relative_to(ROOT_DIR)} 包含比赛残留：{term}"
