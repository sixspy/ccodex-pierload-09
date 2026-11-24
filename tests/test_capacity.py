"""额载禁入第9号 · 额载案壬：三红测试。

口径：账实一致，单一口径。车辆毛重 > 地磅额载即“超额载”，
甲路径——正式行零增并原子拒收，裁决写入额载禁入纪要并当班封死。
不允许“超额仍出正式票”的双口径，不允许只改地磅页红灯动画。

三红：
  1. 失败测甲：超载仍建正式票；
  2. 失败测乙：对账仍累计超载毛重；
  3. 争用失败测：双会话同磅抢建未抢地磅锁。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import app
from db import init_db

BRIDGE_CODE = "BR-I12"
LANE_CODE = "LN-I12-IN"
BL_CODE = "BL-2026-0112"
# 56t 毛重，BR-I12 额载 50t —— 超额载 6t。
OVER_AXLES = [
    {"axle": 1, "w": 13.0},
    {"axle": 2, "w": 14.5},
    {"axle": 3, "w": 15.0},
    {"axle": 4, "w": 13.5},
]


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient, user="admin", password="admin123") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": password}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _lane(c: TestClient, headers: dict) -> dict:
    lanes = c.get("/api/lanes", headers=headers).json()["items"]
    return next(x for x in lanes if x["lane_code"] == LANE_CODE)


def _ticket_codes(c: TestClient, headers: dict) -> set[str]:
    return {
        t["ticket_code"]
        for t in c.get("/api/tickets", headers=headers).json()["items"]
    }


def test_over_capacity_still_creates_formal_ticket():
    """失败测甲：超额载仍建正式票 —— 修复前必红。"""
    c = TestClient(app)
    h = _auth(c)
    lane = _lane(c, h)
    before = _ticket_codes(c, h)

    res = c.post(
        "/api/tickets",
        headers=h,
        json={
            "lane_id": lane["id"],
            "bl_ref": BL_CODE,
            "vehicle_plate": "沪A·超载9",
            "axle_weights": OVER_AXLES,
            "ticket_code": "PL-I12-甲",
        },
    )
    # 甲路径：原子拒收，正式行零增。
    assert res.status_code == 422, res.text
    assert "额载" in res.json()["detail"]
    assert "PL-I12-甲" not in _ticket_codes(c, h)
    assert _ticket_codes(c, h) == before

    # 裁决写入额载禁入纪要，当班封死。
    cap = c.get("/api/capacity", headers=h)
    assert cap.status_code == 200, cap.text
    data = cap.json()
    denial = next(
        (d for d in data["denials"] if d["bridge_code"] == BRIDGE_CODE), None
    )
    assert denial is not None, data
    assert denial["adjudication"] == "额载禁入"
    assert denial["sealed"] == 1
    assert denial["gross_t"] == pytest.approx(56.0)

    bridge = next(b for b in data["bridges"] if b["bridge_code"] == BRIDGE_CODE)
    assert bridge["capacity_state"] == "红灯"

    # 偏离纪要：记额载禁入偏航并拒写。
    audit = c.get("/api/audit", headers=h).json()["items"]
    assert any(x["action"] == "额载禁入偏航" for x in audit), audit[:5]


def test_reconcile_still_accumulates_over_capacity_gross():
    """失败测乙：对账仍累计超载毛重 —— 修复前必红。"""
    c = TestClient(app)
    h = _auth(c)

    recon = c.get(f"/api/reconcile/{BL_CODE}", headers=h).json()
    # 种子 PL-I12 毛重 55t > BR-I12 额载 50t，属超额载，不得计入账实合计。
    excess = {x["ticket_code"] for x in recon.get("capacity_excess", [])}
    assert "PL-I12" in excess, recon
    assert recon["capacity_blocked"] is True
    assert recon["ticket_sum_t"] != pytest.approx(55.0), recon
    assert recon["balanced"] is False


def test_two_sessions_contend_on_bridge_lock():
    """争用失败测：双会话同磅抢建须抢地磅锁，后到只记争用痕 —— 修复前必红。"""
    c = TestClient(app)
    h_first = _auth(c, "writer", "writer123")  # 先到会话
    h_late = _auth(c, "admin", "admin123")  # 后到会话
    lane = _lane(c, h_late)

    bridge_key = f"bridge:{BRIDGE_CODE}"
    first = c.post(
        "/api/locks/acquire", headers=h_first, json={"lock_key": bridge_key}
    )
    assert first.status_code == 200, first.text
    try:
        before = _ticket_codes(c, h_late)
        res = c.post(
            "/api/tickets",
            headers=h_late,
            json={
                "lane_id": lane["id"],
                "bl_ref": BL_CODE,
                "vehicle_plate": "沪A·抢建9",
                "axle_weights": OVER_AXLES,
                "ticket_code": "PL-I12-争用",
            },
        )
        assert res.status_code == 409, res.text
        assert "争用" in res.json()["detail"]
        assert _ticket_codes(c, h_late) == before

        cont = c.get("/api/contention", headers=h_late).json()["items"]
        hit = next((x for x in cont if x["lock_key"] == bridge_key), None)
        assert hit is not None, cont
        assert hit["actor"] == "admin"

        # 后到只记争用痕：不得留下额载禁入纪要业务写。
        cap = c.get("/api/capacity", headers=h_late).json()
        assert not cap["denials"], cap
    finally:
        c.post("/api/locks/release", headers=h_first, json={"lock_key": bridge_key})
