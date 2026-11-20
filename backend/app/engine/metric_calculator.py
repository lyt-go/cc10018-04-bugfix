"""指标计算引擎：支持原子指标→派生指标→聚合指标的链式计算。

核心能力：
1. 公式解析：将「SUM(订单金额)/COUNT(DISTINCT 用户ID)」解析为可执行结构
2. 口径版本控制：同一指标编号支持多版本，按生效时间自动选择
3. 依赖追踪：记录派生指标对原子指标、聚合配置对派生指标的依赖关系
4. 计算编排：先校验父指标依赖，再按依赖拓扑排序执行，循环依赖不会进入计算步骤
"""
from __future__ import annotations

import re
from typing import Any

from app.store import store


class MetricFormula:
    """单个公式的解析表示。"""

    def __init__(self, raw: str) -> None:
        self.raw = raw.strip()
        self.agg_func: str | None = None
        self.agg_field: str | None = None
        self.operators: list[str] = []
        self.operands: list[str] = []
        self._parse()

    def _parse(self) -> None:
        raw = self.raw
        # 提取聚合函数
        m = re.match(r"(\w+)\(([^)]+)\)", raw)
        if m:
            self.agg_func = m.group(1).upper()
            self.agg_field = m.group(2).strip()
        # 提取四则运算
        parts = re.split(r"([+\-*/])", raw)
        for p in parts:
            p = p.strip()
            if p in {"+", "-", "*", "/"}:
                self.operators.append(p)
            elif p:
                self.operands.append(p)

    def to_sql_expr(self, table_alias: str = "t") -> str:
        if self.agg_func and self.agg_field:
            if self.agg_func == "COUNT_DISTINCT":
                return f"COUNT(DISTINCT {table_alias}.{self.agg_field})"
            if self.agg_func == "COUNT" and self.agg_field.upper().startswith("DISTINCT "):
                field = self.agg_field[len("DISTINCT "):].strip()
                return f"COUNT(DISTINCT {table_alias}.{field})"
            return f"{self.agg_func}({table_alias}.{self.agg_field})"
        return self.raw


class MetricCalculator:
    """指标计算引擎。"""

    # 父指标编号允许用中英文逗号、顿号或分号分隔多个父指标
    PARENT_SEPARATOR = re.compile(r"[,，、;；]")

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    def list_active_domains(self) -> list[dict[str, Any]]:
        """获取所有已启用的指标域。"""
        return [r for r in store.rows("metric_domain") if r.get("status") == "已启用"]

    def list_effective_metrics(self) -> list[dict[str, Any]]:
        """获取所有已生效的派生指标。"""
        return [r for r in store.rows("derived_metric") if r.get("status") == "已生效"]

    def get_metric_by_code(self, code: str) -> dict[str, Any] | None:
        for r in store.rows("derived_metric"):
            if r.get("指标编号") == code:
                return r
        return None

    def get_domain_by_code(self, code: str) -> dict[str, Any] | None:
        return next((r for r in store.rows("metric_domain") if r.get("域编号") == code), None)

    def parse_formula(self, formula_raw: str) -> MetricFormula:
        return MetricFormula(formula_raw)

    # ───────────────────────────────────────────────
    # 依赖关系解析
    # ───────────────────────────────────────────────
    def get_parent_codes(self, metric_code: str) -> list[str]:
        """读取派生指标声明的父指标编号（可能为空、可能多个）。"""
        metric = self.get_metric_by_code(metric_code)
        if not metric:
            return []
        raw = str(metric.get("父指标编号") or "").strip()
        return [part.strip() for part in self.PARENT_SEPARATOR.split(raw) if part.strip()]

    def compute_dependencies(self, metric_code: str) -> list[str]:
        """获取指定指标的直接依赖（父指标编号列表）。"""
        return self.get_parent_codes(metric_code)

    def dependency_path(self, metric_code: str) -> list[str]:
        """沿父指标链给出一条引用路径；遇到重复节点（循环）时在重复处终止。"""
        path: list[str] = []
        seen: set[str] = set()
        cursor = metric_code
        while cursor:
            path.append(cursor)
            if cursor in seen:
                break
            seen.add(cursor)
            parents = self.get_parent_codes(cursor)
            if not parents:
                break
            cursor = parents[0]
        return path

    def _path_cycle(self, path: list[str]) -> list[str]:
        """从引用路径中提取循环片段，例如 [丁, 甲, 乙, 甲] → [甲, 乙, 甲]。"""
        indexes: dict[str, int] = {}
        for index, code in enumerate(path):
            if code in indexes:
                return path[indexes[code]:index + 1]
            indexes[code] = index
        return []

    def _validate_atomic_parent(self, parent_code: str) -> tuple[bool, str]:
        if not parent_code.startswith("METR-"):
            return False, f"父指标 {parent_code} 编号格式无法识别（应为 METR- 或 DERI-）"
        domain = self.get_domain_by_code(parent_code)
        if not domain:
            return False, f"父指标（原子指标域）{parent_code} 不存在"
        if domain.get("status") != "已启用":
            return False, (
                f"父指标（原子指标域）{parent_code} 状态为 {domain.get('status')}，非已启用")
        return True, ""

    def build_dependency_report(self, metric_codes: list[str]) -> dict[str, Any]:
        """对一组派生指标做父指标依赖校验，返回可核对、可交付的校验报告。

        校验规则：
        - 派生指标必须存在且状态为「已生效」；
        - 父指标为原子指标域时必须存在且「已启用」，为派生指标时必须存在且「已生效」；
        - 父指标链成环时，环上以及依赖该环的指标全部拦截；
        - 子指标不能绕过被拦截的父指标，父链上任一节点不可用即整条分支不可用。
        """
        requested: list[str] = []
        for code in metric_codes:
            code = str(code).strip()
            if code and code not in requested:
                requested.append(code)

        issues: dict[str, list[str]] = {}
        invalid_parents: list[dict[str, str]] = []
        cycles: list[list[str]] = []
        discovered: list[str] = []
        missing_metrics: list[str] = []
        memo: dict[str, bool] = {}
        visiting: list[str] = []

        def add_issue(code: str, message: str) -> None:
            bucket = issues.setdefault(code, [])
            if message not in bucket:
                bucket.append(message)

        def add_invalid_parent(child: str, parent: str, reason: str) -> None:
            item = {"metric_code": child, "parent_code": parent, "reason": reason}
            if item not in invalid_parents:
                invalid_parents.append(item)

        def record_cycle(path: list[str]) -> None:
            if path not in cycles:
                cycles.append(list(path))

        def resolve(code: str) -> bool:
            if code in memo:
                return memo[code]
            if code in visiting:
                start = visiting.index(code)
                cycle_path = visiting[start:] + [code]
                record_cycle(cycle_path)
                message = f"依赖成环：{' → '.join(cycle_path)}"
                for member in visiting[start:]:
                    add_issue(member, message)
                memo[code] = False
                return False

            if not code.startswith("DERI-"):
                memo[code] = False
                return False

            if code not in discovered:
                discovered.append(code)

            metric = self.get_metric_by_code(code)
            if metric is None:
                if code not in missing_metrics:
                    missing_metrics.append(code)
                add_issue(code, f"派生指标 {code} 不存在")
                memo[code] = False
                return False

            valid = True
            status = metric.get("status")
            if status != "已生效":
                valid = False
                add_issue(code, f"派生指标 {code} 状态为 {status}，非已生效")

            visiting.append(code)
            parents = self.get_parent_codes(code)
            if not parents:
                valid = False
                add_issue(code, f"派生指标 {code} 缺少有效父指标")
                add_invalid_parent(code, "", "缺少父指标编号")
            for parent in parents:
                if parent.startswith("DERI-"):
                    if not resolve(parent):
                        valid = False
                        add_issue(code, f"父指标 {parent} 依赖不可用")
                        if parent in missing_metrics:
                            add_invalid_parent(code, parent, f"父指标 {parent} 不存在")
                        else:
                            add_invalid_parent(code, parent, f"父指标 {parent} 依赖校验未通过")
                else:
                    ok, reason = self._validate_atomic_parent(parent)
                    if not ok:
                        valid = False
                        add_issue(code, reason)
                        add_invalid_parent(code, parent, reason)
            visiting.pop()

            memo[code] = valid
            return valid

        for code in requested:
            resolve(code)

        cycle_members = {code for path in cycles for code in path}
        metrics: list[dict[str, Any]] = []
        for code in discovered:
            metric = self.get_metric_by_code(code)
            path = self.dependency_path(code)
            path_cycle = self._path_cycle(path)
            entry_issues = list(issues.get(code, []))
            has_cycle = code in cycle_members or bool(path_cycle)
            entry_invalid = [
                item for item in invalid_parents if item["metric_code"] == code]
            missing_parent = any(
                "缺少父指标" in item["reason"]
                or "不存在" in item["reason"]
                or "状态为" in item["reason"]
                for item in entry_invalid)
            metrics.append({
                "metric_code": code,
                "parent": metric.get("父指标编号", "") if metric else "",
                "parents": self.get_parent_codes(code),
                "status": metric.get("status") if metric else None,
                "exists": metric is not None,
                "dependency_path": path,
                "cycle": has_cycle,
                "path_cycle": path_cycle,
                "invalid_parents": entry_invalid,
                "missing_parent": missing_parent,
                "issues": entry_issues,
                "available": bool(memo.get(code)),
            })

        available = [item["metric_code"] for item in metrics if item["available"]]
        ordered_available = self._order_available(available, requested, discovered)
        blocked = [item["metric_code"] for item in metrics if not item["available"]]
        blocked_entries = [
            {
                "metric_code": item["metric_code"],
                "dependency_path": item["dependency_path"],
                "issues": item["issues"],
                "cycle": item["cycle"],
            }
            for item in metrics if not item["available"]
        ]

        complete = not blocked and not invalid_parents
        scope = self._build_scope(requested, ordered_available, blocked, complete)

        findings: list[str] = []
        for cycle in cycles:
            findings.append(f"循环依赖：{' → '.join(cycle)}")
        for item in missing_metrics:
            findings.append(f"派生指标 {item} 不存在")
        for item in invalid_parents:
            findings.append(
                f"指标 {item['metric_code']} 的父指标 {item['parent_code'] or '（空）'} 不可用："
                f"{item['reason']}")
        for item in blocked_entries:
            findings.append(
                f"指标 {item['metric_code']} 被拦截，引用路径 {' → '.join(item['dependency_path'])}")
        status_findings = [
            item["metric_code"] for item in metrics
            if item["exists"] and item["status"] != "已生效"
        ]
        for item in status_findings:
            findings.append(f"派生指标 {item} 非已生效状态，不能进入聚合计算")

        return {
            "ok": complete,
            "complete": complete,
            "conclusion": scope["note"],
            "findings": findings,
            "summary": {
                "requested": len(requested),
                "available": len(ordered_available),
                "blocked": len(blocked),
                "cycles": len(cycles),
                "invalid_parents": len(invalid_parents),
            },
            "requested_metrics": requested,
            "metrics": metrics,
            "available_metrics": ordered_available,
            "blocked_metrics": blocked,
            "blocked": blocked_entries,
            "cycles": [{"path": path, "metrics": path[:-1] if len(path) > 1 else path}
                       for path in cycles],
            "missing_parents": invalid_parents,
            "missing_metrics": missing_metrics,
            "scope": scope,
        }

    def _order_available(
        self,
        available: list[str],
        requested: list[str],
        discovered: list[str],
    ) -> list[str]:
        """按依赖优先排序可用指标，保证父指标先于子指标计算。"""
        available_set = set(available)
        ordered: list[str] = []
        visited: set[str] = set()

        def visit(code: str) -> None:
            if code in visited or code not in available_set:
                return
            visited.add(code)
            for parent in self.get_parent_codes(code):
                if parent in available_set:
                    visit(parent)
            ordered.append(code)

        for code in [*requested, *discovered]:
            visit(code)
        return ordered

    def _build_scope(
        self,
        requested: list[str],
        available: list[str],
        blocked: list[str],
        complete: bool,
    ) -> dict[str, Any]:
        """生成计算范围说明：完整、部分还是不可执行。

        部分计算时把「独立分支可先交付」与「汇总报表不完整」两个口径分开写明，
        避免把部分结果按完整报表交付。
        """
        if complete:
            return {
                "mode": "complete",
                "label": "完整计算",
                "complete": True,
                "report_complete": True,
                "included_metrics": available,
                "independent_branches": available,
                "excluded_metrics": [],
                "report_ready": True,
                "delivery": "可按完整报表交付",
                "delivery_options": [
                    {
                        "mode": "complete",
                        "label": "完整报表",
                        "metrics": available,
                        "report_ready": True,
                        "note": "全部依赖分支校验通过，可一次性交付完整报表。",
                    },
                ],
                "note": "全部依赖分支校验通过，聚合结果可按完整报表交付。",
            }
        if available:
            return {
                "mode": "partial",
                "label": "部分计算",
                "complete": False,
                "report_complete": False,
                "included_metrics": available,
                "independent_branches": available,
                "excluded_metrics": blocked,
                "report_ready": False,
                "delivery": "仅部分结果，不可按完整报表交付",
                "delivery_options": [
                    {
                        "mode": "partial",
                        "label": "部分结果",
                        "metrics": available,
                        "report_ready": False,
                        "note": (
                            f"独立分支 {'、'.join(available)} 可按计划先交付，"
                            "用于提前验证；该结果缺少被拦截分支，不能作为完整报表。"
                        ),
                    },
                    {
                        "mode": "complete",
                        "label": "完整报表",
                        "metrics": [],
                        "report_ready": False,
                        "note": (
                            f"{'、'.join(blocked)} 尚未通过依赖校验，"
                            "需修复父指标关系或解除循环后重新生成计划才能交付完整报表。"
                        ),
                    },
                ],
                "conflict": (
                    "独立分支可提前交付，但汇总报表完整性依赖被拦截分支；"
                    "两者不能同时满足，当前计划只保证部分结果的正确性。"
                ),
                "note": (
                    f"独立分支 {'、'.join(available)} 可按计划先交付；"
                    f"{'、'.join(blocked)} 因父指标无效或依赖成环被拦截，"
                    "汇总报表缺失这些分支，不能按完整报表交付，需修复依赖后重新生成完整计划。"
                ),
            }
        return {
            "mode": "blocked",
            "label": "无可执行分支",
            "complete": False,
            "report_complete": False,
            "included_metrics": [],
            "independent_branches": [],
            "excluded_metrics": blocked or requested,
            "report_ready": False,
            "delivery": "不可交付聚合结果",
            "delivery_options": [],
            "note": "所有派生指标分支均未通过父指标依赖校验，计划不可执行，不能按完整报表交付。",
        }

    def explain_metric_dependency(self, metric_code: str) -> dict[str, Any]:
        """供派生指标详情使用：核对父指标关系与引用路径。"""
        report = self.build_dependency_report([metric_code])
        entry = next(
            (item for item in report["metrics"] if item["metric_code"] == metric_code), None)
        parents: list[dict[str, Any]] = []
        for parent in self.get_parent_codes(metric_code):
            if parent.startswith("DERI-"):
                target = self.get_metric_by_code(parent)
                parents.append({
                    "metric_code": parent,
                    "kind": "派生指标",
                    "exists": target is not None,
                    "status": target.get("status") if target else None,
                    "valid": bool(target and target.get("status") == "已生效"),
                })
            elif parent.startswith("METR-"):
                domain = self.get_domain_by_code(parent)
                parents.append({
                    "metric_code": parent,
                    "kind": "原子指标域",
                    "exists": domain is not None,
                    "status": domain.get("status") if domain else None,
                    "valid": bool(domain and domain.get("status") == "已启用"),
                })
            else:
                parents.append({
                    "metric_code": parent,
                    "kind": "未知",
                    "exists": False,
                    "status": None,
                    "valid": False,
                })
        return {
            "metric_code": metric_code,
            "parent_metric": self.get_parent_codes(metric_code)[0]
            if self.get_parent_codes(metric_code) else "",
            "parents": parents,
            "dependency_path": entry["dependency_path"] if entry else [metric_code],
            "cycle": bool(entry and entry["cycle"]),
            "path_cycle": entry["path_cycle"] if entry else [],
            "issues": entry["issues"] if entry else [f"派生指标 {metric_code} 不存在"],
            "valid": bool(entry and entry["available"]),
            "dependency_report": report,
        }

    def topological_sort(self, metric_codes: list[str]) -> list[str]:
        """按依赖拓扑排序指标，保证先计算父指标。

        仅用于历史调用方；聚合计划改用依赖校验报告里的可用分支顺序，
        循环依赖不会在这里被当作可执行顺序。
        """
        report = self.build_dependency_report(metric_codes)
        ordered = list(report["available_metrics"])
        for code in report["blocked_metrics"]:
            if code not in ordered:
                ordered.append(code)
        return ordered

    def build_compute_plan(self, aggregate_config_id: int) -> dict[str, Any]:
        """为一条聚合配置生成计算计划。

        计划里附带派生指标依赖校验报告；只把报告确认可用的依赖分支排进步骤，
        循环分支与缺少有效父指标的分支会被排除，并标明计算范围与交付口径。
        """
        agg = store.find("data_aggregate", aggregate_config_id)
        if not agg:
            return {"ok": False, "error": "聚合配置不存在"}

        source = agg.get("数据来源", "")
        dims = agg.get("聚合维度", "")
        funcs = agg.get("聚合函数", "")

        # 从数据来源解析依赖的指标域和派生指标
        domain_codes: list[str] = []
        metric_codes: list[str] = []
        for part in source.split(","):
            part = part.strip()
            if part.startswith("METR-"):
                domain_codes.append(part)
            elif part.startswith("DERI-"):
                metric_codes.append(part)

        report = self.build_dependency_report(metric_codes)
        scope = report["scope"]
        available = report["available_metrics"]

        plan: dict[str, Any] = {
            "ok": True,
            "aggregate_id": aggregate_config_id,
            "complete": report["complete"],
            "executable": scope["mode"] != "blocked",
            "scope": scope,
            "dependency_report": report,
            "excluded_metrics": report["blocked_metrics"],
            "steps": [],
        }

        # Step 1: 加载指标域数据
        for dc in domain_codes:
            plan["steps"].append({
                "step": len(plan["steps"]) + 1,
                "type": "load_domain",
                "domain_code": dc,
                "description": f"从指标域 {dc} 加载原子数据",
            })

        # Step 2: 只计算依赖校验报告确认过的派生指标分支
        metric_index = {item["metric_code"]: item for item in report["metrics"]}
        for mc in available:
            metric = self.get_metric_by_code(mc)
            formula = self.parse_formula(metric.get("计算公式", "") if metric else "")
            dependency = metric_index.get(mc, {})
            plan["steps"].append({
                "step": len(plan["steps"]) + 1,
                "type": "compute_derived",
                "metric_code": mc,
                "formula": formula.raw,
                "sql_expr": formula.to_sql_expr(),
                "dependency_path": dependency.get("dependency_path", [mc]),
                "description": f"计算派生指标 {mc}: {formula.raw}",
            })

        # Step 3: 聚合；没有可用分支时不生成聚合步骤，避免把部分结果当完整报表
        if plan["executable"]:
            partial = scope["mode"] == "partial"
            plan["steps"].append({
                "step": len(plan["steps"]) + 1,
                "type": "aggregate",
                "dimensions": dims,
                "functions": funcs,
                "output_table": agg.get("输出表名", ""),
                "partial": partial,
                "scope": scope["mode"],
                "description": (
                    f"按 {dims} 聚合，函数 {funcs}"
                    + ("（部分计算，结果不可按完整报表交付）" if partial else "")
                ),
            })

        return plan

    def preview_sql(self, aggregate_config_id: int) -> str:
        """为聚合配置生成预览SQL。"""
        plan = self.build_compute_plan(aggregate_config_id)
        if not plan.get("ok"):
            return "-- 计算计划生成失败"
        if not plan.get("executable"):
            return "-- 依赖校验未通过，无可执行分支，未生成聚合SQL"

        lines = ["-- 指标聚合计算SQL（由计算引擎自动生成）"]
        lines.append("WITH")

        ctes: list[str] = []
        for step in plan["steps"]:
            if step["type"] == "load_domain":
                ctes.append(f"  domain_{step['domain_code']} AS (\n    SELECT * FROM source_table WHERE domain_id = '{step['domain_code']}'\n  )")
            elif step["type"] == "compute_derived":
                ctes.append(f"  metric_{step['metric_code']} AS (\n    SELECT {step['sql_expr']} AS val FROM domain_base\n  )")
            elif step["type"] == "aggregate":
                dims = step["dimensions"]
                funcs = step["functions"]
                output = step["output_table"]
                lines.append(",\n".join(ctes))
                lines.append(f"\nSELECT {dims}, {funcs}\nFROM aggregated\nGROUP BY {dims};")
                lines.append(f"\n-- 输出目标表: {output}")
                if step.get("partial"):
                    lines.append("-- 注意：依赖校验存在被拦截分支，当前结果仅为部分结果，不可按完整报表交付")
                return "\n".join(lines)

        return "\n".join(lines)

    def validate_metric_chain(self, domain_code: str, metric_code: str) -> dict[str, Any]:
        """校验从指标域到派生指标的链路完整性（含父指标依赖校验）。"""
        domain = self.get_domain_by_code(domain_code)
        metric = self.get_metric_by_code(metric_code)

        errors: list[str] = []
        if not domain:
            errors.append(f"指标域 {domain_code} 不存在")
        elif domain.get("status") != "已启用":
            errors.append(f"指标域 {domain_code} 状态为 {domain.get('status')}，非已启用")

        report = self.build_dependency_report([metric_code])
        metric_entry = next(
            (item for item in report["metrics"] if item["metric_code"] == metric_code), None)
        if metric_entry is None:
            errors.append(f"派生指标 {metric_code} 不存在")
        else:
            for issue in metric_entry["issues"]:
                if issue not in errors:
                    errors.append(issue)

        if domain and metric:
            # 校验父指标是否属于该域
            parent = metric.get("父指标编号", "")
            if parent.startswith("METR-") and parent != domain_code:
                errors.append(f"父指标 {parent} 不属于指标域 {domain_code}")

        return {
            "ok": len(errors) == 0,
            "domain_code": domain_code,
            "metric_code": metric_code,
            "errors": errors,
            "dependency_report": report,
        }
