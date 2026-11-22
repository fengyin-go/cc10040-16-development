"""报告模板领域服务：管理定期分析报告的模板、指标组合和自动分发。

跨模块数据流: pivot_table -> report_template
             metric_definition -> query_builder -> report_template（包含指标查询结果）
             report_template -> report_sharing -> data_service（受控指标切片）

共享分发按接收团队授权：区域汇总读者只能拿到聚合值，门店明细读者才能拿到单店记录。
每次交付都记录报告编号、读者、获准列和统计口径，并带可复核的指纹。
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from app.store import store
from app.services.base import BaseModuleService
from app.services import report_sharing as sharing


class ReportTemplateService(BaseModuleService):
    MODULE = "report_template"
    ENTITY = "报告模板"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["模板名称", "包含指标集", "报告周期", "输出格式", "分发渠道"]
    REQUIRED_FIELDS = ["模板名称", "包含指标集", "报告周期"]
    STATUS_ORDER = ["草稿", "已发布", "已停用"]
    ACTION_RULES = {"发布模板": "已发布", "停用模板": "已停用"}
    NEGATIVE_ACTIONS = ["停用模板"]

    # ── 共享与授权 ──

    def share_report(self, entry_id: int, reader: str,
                     scope: str) -> dict[str, Any] | None:
        """把报告共享给一个接收团队，并指定其获准统计范围。"""
        report = store.find(self.MODULE, entry_id)
        if report is None:
            raise sharing.SharingError("report_missing", f"报告模板 {entry_id} 不存在", 404)
        if not str(reader or "").strip():
            raise sharing.SharingError("reader_required", "接收团队不能为空")
        spec = sharing.scope_spec(scope)
        grant = store.grant_report_access(entry_id, reader.strip(), {"scope": spec["key"]})
        return {
            "report_id": entry_id,
            "report_name": report.get("模板名称"),
            "reader": grant["reader"],
            "scope": spec["key"],
            "scope_label": spec["label"],
            "allowed_columns": list(spec["allowed_columns"]),
            "suppressed_columns": list(spec["suppressed_columns"]),
            "granted_at": grant["granted_at"],
        }

    def revoke_share(self, entry_id: int, reader: str) -> dict[str, Any]:
        """撤销接收团队对报告的授权；已发出的结果随即失效。"""
        report = store.find(self.MODULE, entry_id)
        if report is None:
            raise sharing.SharingError("report_missing", f"报告模板 {entry_id} 不存在", 404)
        revoked = store.revoke_report_access(entry_id, reader)
        if revoked is None:
            raise sharing.SharingError(
                "grant_missing", f"接收团队「{reader}」未获报告 {entry_id} 的授权", 404)
        affected = [
            delivery["delivery_id"] for delivery in store.deliveries()
            if delivery.get("report_id") == entry_id and delivery.get("reader") == reader
        ]
        return {
            "report_id": entry_id,
            "report_name": report.get("模板名称"),
            "reader": reader,
            "revoked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "revoked_scope": revoked.get("scope"),
            "invalidated_deliveries": affected,
        }

    def list_shares(self, entry_id: int) -> dict[str, Any] | None:
        report = store.find(self.MODULE, entry_id)
        if report is None:
            return None
        grants = store.report_grants(entry_id)
        readers = []
        for reader, grant in sorted(grants.items()):
            spec = sharing.SCOPES.get(str(grant.get("scope")), {})
            readers.append({
                "reader": reader,
                "scope": grant.get("scope"),
                "scope_label": spec.get("label", "未知范围"),
                "allowed_columns": spec.get("allowed_columns", []),
                "suppressed_columns": spec.get("suppressed_columns", []),
                "granted_at": grant.get("granted_at"),
                "state": "active",
            })
        active_readers = set(grants)
        for reader, grant in sorted(store.revoked_report_grants(entry_id).items()):
            if reader in active_readers:
                continue
            spec = sharing.SCOPES.get(str(grant.get("scope")), {})
            readers.append({
                "reader": reader,
                "scope": grant.get("scope"),
                "scope_label": spec.get("label", "未知范围"),
                "allowed_columns": spec.get("allowed_columns", []),
                "suppressed_columns": spec.get("suppressed_columns", []),
                "granted_at": grant.get("granted_at"),
                "state": "revoked",
            })
        return {
            "report_id": entry_id,
            "report_name": report.get("模板名称"),
            "status": report.get("status"),
            "readers": readers,
            "presets": sharing.reader_presets(),
        }

    # ── 受控取数 ──

    def prepare_slice(self, entry_id: int, reader: str) -> dict[str, Any]:
        """为指定接收团队生成受控指标切片（只含获准列与聚合结果）。"""
        metric_slice = sharing.prepare_slice(entry_id, reader)
        return {**sharing.public_slice(metric_slice), "sample_stats": metric_slice["sample_stats"]}

    def deliver(self, entry_id: int, reader: str, slice_id: str | None = None) -> dict[str, Any]:
        """交付：校验授权 → 生成/复用切片 → 记录交付凭证。

        交付时把报告编号、读者、获准统计范围写进凭证，之后可用它复核是否还可使用。
        每次交付都会重新判断授权；被撤销的读者不会拿到任何样本数据。
        """
        if store.find(self.MODULE, entry_id) is None:
            raise sharing.SharingError("report_missing", f"报告模板 {entry_id} 不存在", 404)
        grant = sharing.require_active_grant(entry_id, reader)

        metric_slice = None
        if slice_id:
            cached = store.get_metric_slice(slice_id)
            snapshot_scope = (cached or {}).get("grant_snapshot", {}).get("scope")
            if cached is not None and cached.get("report_id") == entry_id \
                    and cached.get("reader") == reader \
                    and snapshot_scope == grant.get("scope"):
                metric_slice = cached
        if metric_slice is None:
            metric_slice = sharing.prepare_slice(entry_id, reader)

        delivery = {
            "delivery_id": f"dlv-{uuid.uuid4().hex[:12]}",
            "delivered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "report_id": entry_id,
            "report_name": metric_slice.get("report_name"),
            "report_period": metric_slice.get("report_period"),
            "reader": reader,
            "scope": metric_slice.get("scope"),
            "scope_label": metric_slice.get("scope_label"),
            "allowed_columns": list(metric_slice.get("allowed_columns", [])),
            "suppressed_columns": list(metric_slice.get("suppressed_columns", [])),
            "aggregates": dict(metric_slice.get("aggregates", {})),
            "slice_id": metric_slice.get("slice_id"),
            "slice_digest": metric_slice.get("slice_digest"),
            "grant_snapshot": dict(metric_slice.get("grant_snapshot", {})),
            "sample_stats": dict(metric_slice.get("sample_stats", {})),
        }
        delivery["delivery_digest"] = sharing.digest({
            "report_id": delivery["report_id"],
            "reader": delivery["reader"],
            "scope": delivery["scope"],
            "slice_digest": delivery["slice_digest"],
        })
        store.record_delivery(delivery)
        return {
            **delivery,
            "grant": {
                "scope": grant.get("scope"),
                "scope_label": sharing.scope_spec(str(grant.get("scope")))["label"],
                "granted_at": grant.get("granted_at"),
            },
        }

    def verify_delivery(self, delivery_id: str) -> dict[str, Any] | None:
        """开会前复核：手里的交付结果现在还能不能用。"""
        delivery = store.get_delivery(delivery_id)
        if delivery is None:
            return None
        status = sharing.grant_status(
            int(delivery["report_id"]), delivery["reader"], delivery.get("grant_snapshot"))
        slice_now = store.get_metric_slice(delivery.get("slice_id", ""))
        current_aggregates = None
        if slice_now is not None:
            current_aggregates = slice_now.get("aggregates")
        aggregates_match = current_aggregates == delivery.get("aggregates")
        return {
            "delivery_id": delivery_id,
            "report_id": delivery["report_id"],
            "report_name": delivery["report_name"],
            "report_period": delivery["report_period"],
            "reader": delivery["reader"],
            "scope": delivery["scope"],
            "scope_label": delivery["scope_label"],
            "allowed_columns": delivery["allowed_columns"],
            "suppressed_columns": delivery["suppressed_columns"],
            "aggregates": delivery["aggregates"],
            "delivered_at": delivery["delivered_at"],
            "delivery_digest": delivery["delivery_digest"],
            "grant": {
                "state": status["state"],
                "usable": status["usable"],
                "message": status["message"],
                "scope": delivery["scope"],
                "granted_at": delivery["grant_snapshot"].get("granted_at"),
            },
            "samples_unchanged": aggregates_match,
            "advisory": (
                "结果仍可用：报告、读者身份与获准统计范围均未变化"
                if status["usable"] and aggregates_match
                else (
                    "结果不可再用：授权已变化，请重新取数"
                    if not status["usable"]
                    else "样本数据已更新：结果可追溯，但建议重新取数"
                )
            ),
        }

    def list_deliveries(self, entry_id: int | None = None) -> list[dict[str, Any]]:
        return [
            {
                "delivery_id": item["delivery_id"],
                "delivered_at": item["delivered_at"],
                "report_id": item["report_id"],
                "reader": item["reader"],
                "scope": item["scope"],
                "scope_label": item["scope_label"],
                "aggregates": item["aggregates"],
            }
            for item in store.deliveries()
            if entry_id is None or item["report_id"] == entry_id
        ]

    def generate_report(self, entry_id: int, reader: str | None = None) -> dict[str, Any] | None:
        """生成报告：收集关联指标的数据并汇总。

        跨模块数据流: query_builder -> data_service -> report_template
        传 reader 时按该读者身份计算样本统计；不传则沿用管理视角的完整汇总。
        """
        template = store.find(self.MODULE, entry_id)
        if template is None:
            return {"error": f"报告模板 {entry_id} 不存在"}
        if template["status"] != "已发布":
            return {"error": f"报告模板 {entry_id} 未发布"}

        if reader is not None:
            grant = sharing.require_active_grant(entry_id, reader)
            metric_slice = sharing.build_slice(template, reader, grant)
            spec = sharing.scope_spec(str(grant.get("scope")))
            return {
                "report_template": template.get("模板名称"),
                "period": template.get("报告周期"),
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "format": template.get("输出格式", "HTML"),
                "reader": reader,
                "scope": spec["key"],
                "scope_label": spec["label"],
                "visible_columns": list(spec["allowed_columns"]),
                "sections": [{
                    "metric": metric.strip(),
                    "aggregate": {
                        metric.strip(): metric_slice["aggregates"].get(metric.strip())
                    } if metric.strip() in metric_slice["aggregates"] else {},
                } for metric in template.get("包含指标集", "").split(",") if metric.strip()],
                "sample_stats": metric_slice["sample_stats"],
                "total_metrics": len([
                    metric for metric in template.get("包含指标集", "").split(",") if metric.strip()
                ]),
            }

        metric_set = template.get("包含指标集", "")
        metric_names = [m.strip() for m in metric_set.split(",") if m.strip()]

        sections = []
        for metric_name in metric_names:
            # 获取查询结果
            queries = store.find_by_field("query_builder", "关联指标", metric_name)
            query_results = []
            for q in queries:
                if q.get("status") == "已发布":
                    from app.services.query_builder import QueryBuilderService
                    builder = QueryBuilderService()
                    result = builder.execute_query(int(q.get("id", 0)))
                    if "rows" in result and result["rows"]:
                        # 将 rows 转为可序列化的 dict list
                        serialized_rows = [dict(r) if not isinstance(r, dict) else r for r in result["rows"]]
                        query_results.append({
                            "template": q.get("模板名称"),
                            "source": result.get("source", "computed"),
                            "row_count": len(result["rows"]),
                            "sample_rows": serialized_rows[:3],
                        })

            # 获取口径版本信息
            calibers = store.find_by_field("caliber_version", "所属指标", metric_name)
            active_caliber = next((c for c in calibers if c.get("status") == "已生效"), None)

            sections.append({
                "metric": metric_name,
                "caliber_version": active_caliber.get("版本号") if active_caliber else "未知",
                "caliber_desc": active_caliber.get("口径描述") if active_caliber else "未知",
                "query_results_count": len(query_results),
                "query_results": query_results,
            })

        return {
            "report_template": template.get("模板名称"),
            "period": template.get("报告周期"),
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "format": template.get("输出格式", "HTML"),
            "sections": sections,
            "total_metrics": len(sections),
        }