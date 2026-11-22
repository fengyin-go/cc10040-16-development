"""数据服务领域服务：将指标和模型发布为对外数据服务API。

跨模块数据流: query_builder -> data_service -> cache_layer 以及
              data_service 调用 query_builder.execute_query 获取数据
"""
from __future__ import annotations

import time
from typing import Any

from app.store import store
from app.services import report_sharing as sharing
from app.services.base import BaseModuleService


class DataServiceService(BaseModuleService):
    MODULE = "data_service"
    ENTITY = "服务接口"
    OPERATION_TERM = "关联"
    LIST_FIELDS = ["接口名称", "关联指标或模型", "请求方式", "限流配额", "发布状态"]
    REQUIRED_FIELDS = ["接口名称", "关联指标或模型", "请求方式"]
    STATUS_ORDER = ["开发中", "已发布", "已下线"]
    ACTION_RULES = {"发布接口": "已发布", "下线接口": "已下线"}
    NEGATIVE_ACTIONS = ["下线接口"]

    def call_service(self, entry_id: int, operator: str = "客户端") -> dict[str, Any]:
        """调用数据服务API接口。

        跨模块数据流: data_service -> query_builder -> cache_layer -> query_journal
        首先验证接口和关联查询模板，然后通过查询构建器获取数据。
        """
        api = store.find(self.MODULE, entry_id)
        if api is None:
            return {"error": f"服务接口 {entry_id} 不存在"}
        if api["status"] != "已发布":
            return {"error": f"服务接口 {entry_id} 未发布"}

        target = api.get("关联指标或模型", "")
        if not target:
            return {"error": "服务接口未关联指标或模型"}

        # 查找关联的查询模板
        query_templates = store.find_by_field("query_builder", "关联指标", target)
        if not query_templates:
            return {"error": f"没有找到关联 {target} 的查询模板"}

        # 使用第一个已发布的查询模板
        from app.services.query_builder import QueryBuilderService
        builder = QueryBuilderService()

        results = []
        for tmpl in query_templates:
            if tmpl.get("status") == "已发布":
                query_result = builder.execute_query(int(tmpl.get("id", 0)),
                                                     operator=operator)
                if "rows" in query_result:
                    results.append(query_result)

        if not results:
            return {"error": "没有可用的已发布查询模板"}

        return {
            "api": api.get("接口名称"),
            "request_method": api.get("请求方式"),
            "result_count": sum(len(r.get("rows", [])) for r in results),
            "query_results": results,
        }

    def deliver_slice(self, entry_id: int, slice_id: str) -> dict[str, Any]:
        """按受控指标切片交付数据：只返回切片获准的列与聚合结果。

        跨模块数据流: report_template -> report_sharing(受控切片) -> data_service
        交付前重新校验切片固化的授权快照对应当前授权；授权撤销后不再交付任何样本。
        """
        api = store.find(self.MODULE, entry_id)
        if api is None:
            return {"error": f"服务接口 {entry_id} 不存在"}
        if api["status"] != "已发布":
            return {"error": f"服务接口 {entry_id} 未发布"}

        metric_slice = store.get_metric_slice(slice_id)
        if metric_slice is None:
            raise sharing.SharingError("slice_missing", f"受控指标切片 {slice_id} 不存在", 404)

        status = sharing.grant_status(
            int(metric_slice["report_id"]),
            metric_slice["reader"],
            metric_slice.get("grant_snapshot"),
        )
        if not status["usable"]:
            raise sharing.SharingError(
                "access_revoked" if status["state"] == "revoked" else "slice_stale",
                f"切片不可交付：{status['message']}", 403)

        columns = list(metric_slice["allowed_columns"])
        leaked = sorted(
            set(metric_slice.get("suppressed_columns", []))
            & set(metric_slice.get("aggregates", {}))
        )
        if leaked:
            raise sharing.SharingError(
                "scope_violation", "切片包含被裁去的明细列，拒绝交付", 500)

        return {
            "api": api.get("接口名称"),
            "request_method": api.get("请求方式"),
            "slice_id": metric_slice["slice_id"],
            "slice_digest": metric_slice["slice_digest"],
            "report_id": metric_slice["report_id"],
            "report_name": metric_slice["report_name"],
            "reader": metric_slice["reader"],
            "scope": metric_slice["scope"],
            "scope_label": metric_slice["scope_label"],
            "columns": columns,
            "rows": [
                {column: row.get(column) for column in columns}
                for row in metric_slice.get("rows", [])
            ],
            "aggregates": dict(metric_slice.get("aggregates", {})),
            "aggregation": dict(metric_slice.get("aggregation", {})),
            "sample_stats": dict(metric_slice.get("sample_stats", {})),
            "delivered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def get_service_health(self, entry_id: int) -> dict[str, Any]:
        """获取服务接口健康状态。

        检查关联的指标口径版本和数据服务可用性。
        """
        api = store.find(self.MODULE, entry_id)
        if api is None:
            return {"error": f"服务接口 {entry_id} 不存在"}

        target = api.get("关联指标或模型", "")

        # 检查关联指标的口径版本是否有效
        calibers = store.find_by_field("caliber_version", "所属指标", target)
        active_caliber = next((c for c in calibers if c.get("status") == "已生效"), None)

        # 检查关联查询模板
        query_templates = store.find_by_field("query_builder", "关联指标", target)
        published = [t for t in query_templates if t.get("status") == "已发布"]

        return {
            "api_id": entry_id,
            "api_name": api.get("接口名称"),
            "status": api.get("status"),
            "target_metric": target,
            "active_caliber": active_caliber.get("版本号") if active_caliber else None,
            "published_query_templates": len(published),
            "health": "healthy" if active_caliber and published else "degraded",
        }