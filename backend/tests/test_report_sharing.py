"""报告共享与受控指标切片的业务行为测试。

覆盖：按接收团队授权 → 两组各取一次 → 撤销其中一组 → 复核交付是否仍可用。
"""
from __future__ import annotations

import pytest

from app.services import report_sharing as sharing
from app.services.data_service import DataServiceService
from app.services.report_template import ReportTemplateService
from app.store import store

REGION_READER = "大区汇总组"
STORE_READER = "门店运营组"
REPORT_ID = 1
API_ID = 2


@pytest.fixture(autouse=True)
def isolated_sharing_state():
    """每个用例独享授权状态，避免撤销/恢复互相污染。"""
    grants = {reader: dict(grant) for reader, grant in store.report_grants(REPORT_ID).items()}
    revoked = dict(store._report_access_history.get(str(REPORT_ID), {}))
    store._deliveries.clear()
    yield
    store.report_grants(REPORT_ID).clear()
    store.report_grants(REPORT_ID).update(grants)
    store._report_access_history[str(REPORT_ID)] = revoked
    store._deliveries.clear()


def test_default_grants_cover_both_readers():
    shares = ReportTemplateService().list_shares(REPORT_ID)
    readers = {item["reader"]: item for item in shares["readers"]}
    assert readers[REGION_READER]["scope"] == "region_summary"
    assert readers[STORE_READER]["scope"] == "store_detail"


def test_region_reader_gets_no_store_level_columns():
    result = DataServiceService().deliver_slice(
        API_ID, ReportTemplateService().prepare_slice(REPORT_ID, REGION_READER)["slice_id"])
    assert "门店编号" not in result["columns"]
    assert "门店名称" not in result["columns"]
    for row in result["rows"]:
        assert "门店编号" not in row and "门店名称" not in row


def test_region_aggregation_suppresses_small_groups_everywhere():
    slice_ = ReportTemplateService().prepare_slice(REPORT_ID, REGION_READER)
    regions = {row["区域"] for row in DataServiceService().deliver_slice(
        API_ID, slice_["slice_id"])["rows"]}
    # 西南区域只有一家门店，区域汇总既不出行，也不计入合计。
    assert "西南" not in regions
    assert slice_["sample_stats"]["suppression_applied"] is True
    # 合计只累加已释放分组，不包含被抑制分组的任何数值。
    released = sum(
        row["销售额"] for row in store.report_samples()
        if row["报告ID"] == REPORT_ID and row["区域"] in regions)
    assert slice_["aggregates"]["销售额"] == released
    suppressed = sum(
        row["销售额"] for row in store.report_samples()
        if row["报告ID"] == REPORT_ID and row["区域"] == "西南")
    assert slice_["aggregates"]["销售额"] + suppressed == sum(
        row["销售额"] for row in store.report_samples() if row["报告ID"] == REPORT_ID)


def test_store_reader_gets_store_rows_for_same_report():
    result = DataServiceService().deliver_slice(
        API_ID, ReportTemplateService().prepare_slice(REPORT_ID, STORE_READER)["slice_id"])
    expected = len([r for r in store.report_samples() if r["报告ID"] == REPORT_ID])
    assert len(result["rows"]) == expected
    assert "门店编号" in result["columns"]
    assert result["aggregates"]["销售额"] == sum(
        r["销售额"] for r in store.report_samples() if r["报告ID"] == REPORT_ID)


def test_delivery_receipt_identifies_report_and_scope():
    svc = ReportTemplateService()
    slice_ = svc.prepare_slice(REPORT_ID, REGION_READER)
    receipt = svc.deliver(REPORT_ID, REGION_READER, slice_id=slice_["slice_id"])
    assert receipt["report_id"] == REPORT_ID
    assert receipt["report_name"]
    assert receipt["scope"] == "region_summary"
    assert receipt["allowed_columns"] == slice_["allowed_columns"]
    assert receipt["delivery_digest"]
    listed = svc.list_deliveries(REPORT_ID)
    assert any(item["delivery_id"] == receipt["delivery_id"] for item in listed)


def test_report_generation_uses_reader_scope():
    svc = ReportTemplateService()
    summary = svc.generate_report(REPORT_ID, reader=REGION_READER)
    detail = svc.generate_report(REPORT_ID, reader=STORE_READER)
    assert summary["scope"] == "region_summary"
    assert summary["visible_columns"] and "门店编号" not in summary["visible_columns"]
    assert summary["sample_stats"]["granularity"] == "region"
    assert detail["scope"] == "store_detail"
    assert detail["sample_stats"]["granularity"] == "store"


def test_revoke_blocks_new_slices_and_deliveries():
    svc = ReportTemplateService()
    svc.revoke_share(REPORT_ID, STORE_READER)

    with pytest.raises(sharing.SharingError) as err:
        svc.prepare_slice(REPORT_ID, STORE_READER)
    assert err.value.status_code == 403

    with pytest.raises(sharing.SharingError):
        svc.deliver(REPORT_ID, STORE_READER)

    with pytest.raises(sharing.SharingError):
        svc.generate_report(REPORT_ID, reader=STORE_READER)


def test_old_slice_cannot_be_delivered_after_revoke():
    svc = ReportTemplateService()
    slice_ = svc.prepare_slice(REPORT_ID, STORE_READER)
    svc.revoke_share(REPORT_ID, STORE_READER)

    with pytest.raises(sharing.SharingError) as err:
        DataServiceService().deliver_slice(API_ID, slice_["slice_id"])
    assert err.value.code == "access_revoked"
    assert "门店编号" not in str(err.value.to_dict())


def test_delivery_verification_reflects_revocation():
    svc = ReportTemplateService()
    region_receipt = svc.deliver(
        REPORT_ID, REGION_READER,
        slice_id=svc.prepare_slice(REPORT_ID, REGION_READER)["slice_id"])
    store_receipt = svc.deliver(
        REPORT_ID, STORE_READER,
        slice_id=svc.prepare_slice(REPORT_ID, STORE_READER)["slice_id"])

    svc.revoke_share(REPORT_ID, STORE_READER)

    still_usable = svc.verify_delivery(region_receipt["delivery_id"])
    revoked = svc.verify_delivery(store_receipt["delivery_id"])
    assert still_usable["grant"]["usable"] is True
    assert still_usable["grant"]["state"] == "active"
    assert revoked["grant"]["usable"] is False
    assert revoked["grant"]["state"] == "revoked"
    assert "不可再用" in revoked["advisory"]


def test_region_reader_refetch_after_revoke_matches_original():
    svc = ReportTemplateService()
    before = svc.deliver(
        REPORT_ID, REGION_READER,
        slice_id=svc.prepare_slice(REPORT_ID, REGION_READER)["slice_id"])
    svc.revoke_share(REPORT_ID, STORE_READER)
    after_slice = svc.prepare_slice(REPORT_ID, REGION_READER)
    after = DataServiceService().deliver_slice(API_ID, after_slice["slice_id"])

    assert after["aggregates"] == before["aggregates"]
    assert set(after["columns"]) == set(before["allowed_columns"])
    assert svc.verify_delivery(before["delivery_id"])["grant"]["usable"] is True


def test_unknown_scope_rejected():
    with pytest.raises(sharing.SharingError):
        ReportTemplateService().share_report(REPORT_ID, "测试团队", "whole_database")


def test_slice_only_covers_its_own_report():
    svc = ReportTemplateService()
    slice_ = svc.prepare_slice(REPORT_ID, STORE_READER)
    other_report_rows = [
        row for row in store.report_samples() if row["报告ID"] != REPORT_ID
    ]
    assert other_report_rows
    assert slice_["aggregates"]["销售额"] < sum(
        row["销售额"] for row in store.report_samples())
