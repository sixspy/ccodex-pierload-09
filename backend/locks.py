"""码头锁与争用登记（写入路径嵌入）。"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit

router = APIRouter(tags=["locks"])


class LockIn(BaseModel):
    lock_key: str
    action: str = "acquire"


def try_acquire(conn, lock_key: str, holder: str) -> bool:
    now = now_iso()
    row = conn.execute(
        "SELECT holder FROM pier_locks WHERE lock_key=?", (lock_key,)
    ).fetchone()
    if row is None:
        try:
            # INSERT OR IGNORE 依赖 PK 原子性：真人并发同磅抢建时只有一个能插进去。
            cur = conn.execute(
                """INSERT OR IGNORE INTO pier_locks(lock_key,holder,acquired_at)
                   VALUES (?,?,?)""",
                (lock_key, holder, now),
            )
        except sqlite3.OperationalError:
            conn.rollback()
            return False
        if cur.rowcount == 0:
            # 同刻另一会话已抢先插入，本会话按后到处理。
            conn.execute(
                """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
                   VALUES (?,?,?,?,?)""",
                (lock_key, holder, "争用", "同刻抢占落败", now),
            )
            return False
        conn.commit()
        return True
    if row["holder"] == holder:
        return True
    conn.execute(
        """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
           VALUES (?,?,?,?,?)""",
        (lock_key, holder, "争用", f"持有者={row['holder']}", now),
    )
    return False


def release(conn, lock_key: str, holder: str) -> None:
    # 先丢弃未提交的业务半写，再单独落盘锁释放：
    # 成功路径此前已 commit，回滚为空操作；失败路径保证不留半写、锁也不泄漏。
    conn.rollback()
    conn.execute(
        "DELETE FROM pier_locks WHERE lock_key=? AND holder=?",
        (lock_key, holder),
    )
    conn.commit()


def require_lock(conn, lock_key: str, holder: str) -> None:
    if not try_acquire(conn, lock_key, holder):
        # 后到只记争用痕：争用行先落盘，业务写一律不落。
        conn.commit()
        raise HTTPException(409, "争用：后到只记争用，不写业务")


@router.get("/api/locks")
def list_locks(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM pier_locks ORDER BY acquired_at DESC"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/contention")
def list_contention(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM contention_log ORDER BY id DESC LIMIT 200"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/locks/acquire")
def acquire_lock(body: LockIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        ok = try_acquire(conn, body.lock_key, user)
        write_audit(conn, user, "lock_acquire", body.lock_key, {"ok": ok})
        conn.commit()
        if not ok:
            raise HTTPException(409, "争用：未能获得锁")
        return {"ok": True, "lock_key": body.lock_key, "holder": user}


@router.post("/api/locks/release")
def release_lock(body: LockIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        release(conn, body.lock_key, user)
        write_audit(conn, user, "lock_release", body.lock_key, {})
        conn.commit()
        return {"ok": True}