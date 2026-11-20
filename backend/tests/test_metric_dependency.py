"""派生指标依赖校验与聚合计划回归测试。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engine.metric_calculator import MetricCalculator
from app.seed import SEED_ROWS
from app.store import store


def reset_store() -> None:
    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}


def test_report_paths_cycle_and_children():
    """甲乙互引、丙独立、丁依赖甲：路径、循环与可执行分支都要清晰。"""
    reset_store()
    calc = MetricCalculator()
    report = calc.build_dependency_report(
        ["DERI-JIA", "DERI-YI", "DERI-BING", "DERI-DING"])

    assert report["ok"] is False
    assert report["scope"]["mode"] == "partial"
    assert report["available_metrics"] == ["DERI-BING"]
    assert report["blocked_metrics"] == ["DERI-JIA", "DERI-YI", "DERI-DING"]

    metrics = {item["metric_code"]: item for item in report["metrics"]}
    assert metrics["DERI-JIA"]["path_cycle"] == ["DERI-JIA", "DERI-YI", "DERI-JIA"]
    assert metrics["DERI-YI"]["path_cycle"] == ["DERI-YI", "DERI-JIA", "DERI-YI"]
    assert metrics["DERI-BING"]["dependency_path"] == ["DERI-BING", "METR-TRADE"]
    assert metrics["DERI-DING"]["dependency_path"][:2] == ["DERI-DING", "DERI-JIA"]
    # 丁不能绕过被拦截的甲
    assert metrics["DERI-DING"]["available"] is False
    assert any("DERI-JIA" in issue for issue in metrics["DERI-DING"]["issues"])
    assert report["cycles"][0]["metrics"] == ["DERI-JIA", "DERI-YI"]


def test_plan_excludes_cycle_branches_and_marks_scope():
    """混合配置里循环分支不能排进计算步骤，部分结果要标清不可按完整报表交付。"""
    reset_store()
    calc = MetricCalculator()
    plan = calc.build_compute_plan(5)

    assert plan["ok"] is True
    assert plan["executable"] is True
    assert plan["complete"] is False
    assert plan["scope"]["mode"] == "partial"
    assert plan["scope"]["report_ready"] is False

    computed = [s["metric_code"] for s in plan["steps"] if s["type"] == "compute_derived"]
    assert computed == ["DERI-BING"]
    aggregate_step = next(s for s in plan["steps"] if s["type"] == "aggregate")
    assert aggregate_step["partial"] is True
    assert plan["dependency_report"]["blocked_metrics"] == [
        "DERI-JIA", "DERI-YI", "DERI-DING"]
    assert "-- 注意：" in calc.preview_sql(5)


def test_missing_and_invalid_parents_blocked():
    """缺少有效父指标、父指标不存在、父指标未生效都要拦住。"""
    reset_store()
    calc = MetricCalculator()

    store.rows("derived_metric").append({
        "id": 101, "status": "已生效", "pending": False, "abnormal": False,
        "指标编号": "DERI-NO-PARENT", "父指标编号": "", "计算公式": "SUM(x)",
    })
    store.rows("derived_metric").append({
        "id": 102, "status": "已生效", "pending": False, "abnormal": False,
        "指标编号": "DERI-GHOST-PARENT", "父指标编号": "DERI-NONE", "计算公式": "SUM(x)",
    })

    report = calc.build_dependency_report(["DERI-NO-PARENT", "DERI-GHOST-PARENT"])
    assert report["ok"] is False
    assert report["scope"]["mode"] == "blocked"
    assert report["available_metrics"] == []
    assert "DERI-GHOST-PARENT" in report["missing_metrics"] or "DERI-NONE" in report["missing_metrics"]

    # 父指标状态未生效：把丙的父域停用后，丙也应被拦截
    domain = next(r for r in store.rows("metric_domain") if r["域编号"] == "METR-TRADE")
    domain["status"] = "已停用"
    report = calc.build_dependency_report(["DERI-BING"])
    assert report["ok"] is False
    assert report["available_metrics"] == []


def test_effective_chain_still_complete():
    """正常的父子链不受影响，仍按父先子后排序并标记完整计算。"""
    reset_store()
    calc = MetricCalculator()
    report = calc.build_dependency_report(["DERI-ARPU"])
    assert report["ok"] is True
    assert report["available_metrics"] == ["DERI-GMV", "DERI-ARPU"]
    assert report["scope"]["mode"] == "complete"
    assert report["scope"]["report_ready"] is True


def test_derived_metric_detail_exposes_dependency():
    """派生指标详情要能核对父指标关系与引用路径。"""
    reset_store()
    from app.services.derived_metric import DerivedMetricService

    service = DerivedMetricService()
    entry = service.get_entry(7)  # DERI-JIA
    dependency = entry["_dependency"]
    assert dependency["cycle"] is True
    assert dependency["dependency_path"] == ["DERI-JIA", "DERI-YI", "DERI-JIA"]
    assert dependency["parents"][0]["metric_code"] == "DERI-YI"
    assert dependency["valid"] is False


def test_aggregate_endpoints_report_scope():
    """聚合页面接口：详情与计划里返回同一份依赖信息与计算范围。"""
    reset_store()
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    detail = client.get("/api/data_aggregate/5").json()
    assert detail["_dependency_report"]["blocked_metrics"] == [
        "DERI-JIA", "DERI-YI", "DERI-DING"]
    assert detail["_compute_scope"]["mode"] == "partial"

    plan = client.get("/api/data_aggregate/5/compute-plan").json()
    assert plan["scope"]["mode"] == "partial"
    assert [s.get("metric_code") for s in plan["steps"] if s["type"] == "compute_derived"] == [
        "DERI-BING"]

    report = client.get("/api/data_aggregate/5/dependency-report").json()
    assert report["aggregate_code"] == "AGG-MIXED-001"
    assert report["available_metrics"] == ["DERI-BING"]
