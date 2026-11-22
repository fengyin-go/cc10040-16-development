"""报告共享与受控指标切片：把报告样本聚合成按读者授权的统计口径。

安全边界：
  * 切片在生成时固化「报告 + 读者 + 授权范围」快照，交付时重新校验当前授权；
  * 门店明细列只出现在获批明细范围的切片里；
  * 区域汇总口径按区域聚合，且小于最小成组规模的分组既不出行、也不计入合计，
    避免用合计反推被裁去的门店明细。
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from app.store import store

# 两种接收口径：区域汇总只看聚合值，门店明细可以看单店记录。
SCOPES: dict[str, dict[str, Any]] = {
    "region_summary": {
        "key": "region_summary",
        "label": "区域汇总",
        "granularity": "region",
        "group_by": "区域",
        "allowed_columns": ["区域", "销售额", "订单数", "活跃用户数"],
        "suppressed_columns": ["门店编号", "门店名称"],
        "metric_columns": ["销售额", "订单数", "活跃用户数"],
        "min_group_size": 2,
        "aggregation": {
            "method": "SUM",
            "group_by": "区域",
            "suppression": "分组门店数小于最小成组规模时，该分组不出行也不计入合计",
        },
    },
    "store_detail": {
        "key": "store_detail",
        "label": "门店明细",
        "granularity": "store",
        "group_by": None,
        "allowed_columns": ["区域", "门店编号", "门店名称", "销售额", "订单数", "活跃用户数"],
        "suppressed_columns": [],
        "metric_columns": ["销售额", "订单数", "活跃用户数"],
        "min_group_size": 1,
        "aggregation": {"method": "SUM", "group_by": None, "suppression": "无"},
    },
}

READER_PRESETS = [
    {"reader": "大区汇总组", "scope": "region_summary"},
    {"reader": "门店运营组", "scope": "store_detail"},
]


class SharingError(Exception):
    """带稳定错误码的共享/交付失败，路由层据此返回 4xx 而不是 500。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "code": self.code, "message": self.message}


def scope_spec(scope: str) -> dict[str, Any]:
    spec = SCOPES.get(scope)
    if spec is None:
        raise SharingError("unknown_scope", f"未知的共享范围：{scope}")
    return spec


def reader_presets() -> list[dict[str, Any]]:
    return [
        {**preset, "scope_label": SCOPES[preset["scope"]]["label"]}
        for preset in READER_PRESETS
    ]


def _grant_snapshot(grant: dict[str, Any]) -> dict[str, Any]:
    return {
        "report_id": grant.get("report_id"),
        "reader": grant.get("reader"),
        "scope": grant.get("scope"),
        "granted_at": grant.get("granted_at"),
    }


def digest(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _aggregate(rows: list[dict[str, Any]], metrics: list[str]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for metric in metrics:
        total = 0
        for row in rows:
            value = row.get(metric, 0)
            if isinstance(value, (int, float)):
                total += int(value)
        totals[metric] = total
    return totals


def build_slice(report: dict[str, Any], reader: str,
                grant: dict[str, Any]) -> dict[str, Any]:
    """按授权范围聚合报告样本，生成只含获准列的受控指标切片。"""
    spec = scope_spec(str(grant.get("scope", "")))
    report_id = int(report["id"])
    # 切片只取本报告的样本行，不跨报告混算。
    samples = [
        row for row in store.report_samples()
        if int(row.get("报告ID", 0)) == report_id
    ]
    metric_columns = spec["metric_columns"]

    if spec["granularity"] == "region":
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in samples:
            grouped.setdefault(str(row.get("区域", "")), []).append(row)
        released_rows: list[dict[str, Any]] = []
        suppressed = False
        for region, group_rows in sorted(grouped.items()):
            if len(group_rows) < spec["min_group_size"]:
                # 小组整体裁去：既不出现在行里，也不进入任何合计。
                suppressed = True
                continue
            released_rows.append({
                "区域": region,
                **{
                    metric: sum(int(row.get(metric, 0) or 0) for row in group_rows)
                    for metric in metric_columns
                },
            })
        sample_stats = {
            "reader": reader,
            "scope": spec["key"],
            "granularity": "region",
            "row_count": len(released_rows),
            "released_groups": len(released_rows),
            "aggregation": "SUM",
            "suppression_applied": suppressed,
            "minimum_group_size": spec["min_group_size"],
        }
    else:
        released_rows = [
            {column: row.get(column) for column in spec["allowed_columns"]}
            for row in samples
        ]
        sample_stats = {
            "reader": reader,
            "scope": spec["key"],
            "granularity": "store",
            "row_count": len(released_rows),
            "source_row_count": len(samples),
            "aggregation": "NONE",
            "suppression_applied": False,
        }

    aggregates = _aggregate(released_rows, metric_columns)
    snapshot = _grant_snapshot(grant)
    slice_id = f"slice-{uuid.uuid4().hex[:12]}"
    metric_slice = {
        "slice_id": slice_id,
        "report_id": report_id,
        "report_name": report.get("模板名称", ""),
        "report_period": report.get("报告周期", ""),
        "reader": reader,
        "scope": spec["key"],
        "scope_label": spec["label"],
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "grant_snapshot": snapshot,
        "allowed_columns": list(spec["allowed_columns"]),
        "suppressed_columns": list(spec["suppressed_columns"]),
        "metric_columns": list(metric_columns),
        "granularity": spec["granularity"],
        "aggregation": dict(spec["aggregation"]),
        "rows": released_rows,
        "aggregates": aggregates,
        "sample_stats": sample_stats,
        "source_fingerprint": digest(samples),
        "scope_fingerprint": digest({
            "scope": spec["key"],
            "columns": spec["allowed_columns"],
            "grant": snapshot,
        }),
    }
    metric_slice["slice_digest"] = digest({
        "report_id": metric_slice["report_id"],
        "reader": reader,
        "scope": spec["key"],
        "rows": released_rows,
        "aggregates": aggregates,
    })
    return metric_slice


def public_slice(metric_slice: dict[str, Any]) -> dict[str, Any]:
    """切片的对外视图，不暴露任何未获准列。"""
    return {
        "slice_id": metric_slice["slice_id"],
        "report_id": metric_slice["report_id"],
        "report_name": metric_slice["report_name"],
        "report_period": metric_slice["report_period"],
        "reader": metric_slice["reader"],
        "scope": metric_slice["scope"],
        "scope_label": metric_slice["scope_label"],
        "granularity": metric_slice["granularity"],
        "generated_at": metric_slice["generated_at"],
        "allowed_columns": metric_slice["allowed_columns"],
        "suppressed_columns": metric_slice["suppressed_columns"],
        "aggregation": metric_slice["aggregation"],
        "aggregates": metric_slice["aggregates"],
        "sample_stats": metric_slice["sample_stats"],
        "slice_digest": metric_slice["slice_digest"],
        "grant_snapshot": metric_slice["grant_snapshot"],
    }


def current_grant(report_id: int, reader: str) -> dict[str, Any] | None:
    return store.get_report_grant(report_id, reader)


def grant_status(report_id: int, reader: str,
                 snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """对照当前授权判断一份已生成的切片/交付还能不能用。"""
    report = store.find("report_template", report_id)
    grant = current_grant(report_id, reader)
    if report is None:
        return {"state": "report_missing", "usable": False,
                "message": "报告模板已不存在，结果不可再用"}
    if grant is None:
        return {"state": "revoked", "usable": False,
                "message": "该接收团队的授权已被撤销，结果不可再用"}
    if snapshot is not None and grant.get("scope") != snapshot.get("scope"):
        return {"state": "scope_changed", "usable": False,
                "message": "获准统计范围已变更，需要重新取数"}
    if report.get("status") != "已发布":
        return {"state": "report_unpublished", "usable": False,
                "message": "报告已停用，结果不可再用"}
    return {
        "state": "active",
        "usable": True,
        "message": "授权有效，结果可用",
        "scope": grant.get("scope"),
        "scope_label": scope_spec(str(grant.get("scope", "")))["label"],
        "granted_at": grant.get("granted_at"),
    }


def require_active_grant(report_id: int, reader: str) -> dict[str, Any]:
    grant = current_grant(report_id, reader)
    if grant is None:
        raise SharingError(
            "access_denied",
            f"接收团队「{reader}」未获报告 {report_id} 的共享授权", 403)
    status = grant_status(report_id, reader)
    if not status["usable"]:
        raise SharingError(
            "access_revoked" if status["state"] == "revoked" else "access_invalid",
            status["message"], 403)
    return grant


def prepare_slice(report_id: int, reader: str,
                  persist: bool = True) -> dict[str, Any]:
    report = store.find("report_template", report_id)
    if report is None:
        raise SharingError("report_missing", f"报告模板 {report_id} 不存在", 404)
    grant = require_active_grant(report_id, reader)
    metric_slice = build_slice(report, reader, grant)
    if persist:
        store.save_metric_slice(metric_slice)
    return metric_slice
