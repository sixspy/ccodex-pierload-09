"""复现：超额载种子夹具下，过磅写口仍建正式票（假绿）。

用法（修改前后均可跑，用于对照）：
    PYTHONPATH=backend python3 scripts/_repro_capacity_false_green.py

判定：
  - 仍返回 200 且生成正式票 → 假绿（缺陷存在）
  - 返回 4xx 且无正式票、写入额载禁入纪要 → 已修复
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import db  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="pierload-capacity-"))
db.DB_PATH = tmp / "repro.db"
db.init_db()

from fastapi.testclient import TestClient  # noqa: E402

from app import app  # noqa: E402

c = TestClient(app)
tok = c.post(
    "/api/login", json={"username": "admin", "password": "admin123"}
).json()["token"]
h = {"Authorization": "Bearer " + tok}

bridges = c.get("/api/bridges", headers=h).json()["items"]
br = next(b for b in bridges if b["bridge_code"] == "BR-I12")
lanes = c.get("/api/lanes", headers=h).json()["items"]
lane = next(x for x in lanes if x["lane_code"] == "LN-I12-IN")

print(f"地磅 {br['bridge_code']} 额载 {br['max_gross_t']}t；车道 {lane['lane_code']}")

seeded = [t for t in c.get("/api/tickets", headers=h).json()["items"]
          if t["ticket_code"] == "PL-I12"]
print("种子正式票 PL-I12：", seeded[0] if seeded else "无")

res = c.post(
    "/api/tickets",
    headers=h,
    json={
        "lane_id": lane["id"],
        "bl_ref": "BL-2026-0112",
        "vehicle_plate": "沪A·超载9",
        "axle_weights": [
            {"axle": 1, "w": 13.0},
            {"axle": 2, "w": 14.5},
            {"axle": 3, "w": 15.0},
            {"axle": 4, "w": 13.5},
        ],
        "ticket_code": "PL-I12-REPRO",
    },
)
print("超额载过磅写口响应：", res.status_code, res.json())
if res.status_code == 200:
    print("== 假绿：毛重 56t 超额载 50t，仍建正式票 ==")
else:
    print("== 已阻断：未建正式票 ==")
