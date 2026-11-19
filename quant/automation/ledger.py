"""Append-only 幂等账本（P4-C 最小实现）。

约束来自方案 B 设计 6.1.1 / P4-C：
- consumption_key = sha256(canonical_json_bytes({environment, market, snapshot_id, eligible_execution_session}))，
  与 enrollment/token/client path 无关（换 token 不得重置消费历史）；
- 一个 key 只能绑定一份 execute plan；
- 进入 SUBMITTING 之后，若崩溃/退出/unknown，后续 run 只能只读对账，不得重试或提交 sibling；
- 事件按 hash 链追加，HEAD 用 CAS 更新；事件文件不可修改。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from quant.automation.canonical import canonical_json_bytes, sha256_hex

LOCK_TIMEOUT = 10.0


class LedgerError(RuntimeError):
    pass


class AlreadyBound(LedgerError):
    pass


class StateConflict(LedgerError):
    pass


def consumption_key(*, market: str, snapshot_id: str, eligible_execution_session: str) -> str:
    payload = {
        "environment": "paper",
        "market": market,
        "snapshot_id": snapshot_id,
        "eligible_execution_session": eligible_execution_session,
    }
    return sha256_hex(canonical_json_bytes(payload))


class Ledger:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.events = self.root / "events"
        self.events.mkdir(parents=True, exist_ok=True)
        self.head_path = self.root / "HEAD.json"
        self.lock_path = self.root / ".lock"

    # ---------- 基础 ----------
    def _lock(self):
        deadline = time.time() + LOCK_TIMEOUT
        while True:
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return fd
            except FileExistsError:
                if time.time() > deadline:
                    raise LedgerError("账本被占用，获取锁超时")
                time.sleep(0.05)

    def _unlock(self, fd: int) -> None:
        os.close(fd)
        try:
            os.unlink(self.lock_path)
        except FileNotFoundError:
            pass  # noqa: silent-ok - 锁文件已被其他进程释放

    def head(self) -> dict[str, Any]:
        if not self.head_path.exists():
            return {"seq": 0, "event_hash": None}
        return json.loads(self.head_path.read_text(encoding="utf-8"))

    def _write_atomic(self, path: Path, payload: bytes) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, path)

    def _append_locked(self, event: dict[str, Any]) -> dict[str, Any]:
        head = self.head()
        body = {"seq": head["seq"] + 1, "prev_hash": head["event_hash"], **event}
        event_hash = sha256_hex(canonical_json_bytes(body))
        body["event_hash"] = event_hash
        self._write_atomic(self.events / f"{event_hash}.json",
                           json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
        self._write_atomic(self.head_path,
                           json.dumps({"seq": body["seq"], "event_hash": event_hash},
                                      sort_keys=True).encode("utf-8"))
        return body

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        fd = self._lock()
        try:
            return self._append_locked(event)
        finally:
            self._unlock(fd)

    # ---------- 事件查询 ----------
    def events_for(self, key: str) -> list[dict[str, Any]]:
        out = []
        for f in sorted(self.events.glob("*.json")):
            ev = json.loads(f.read_text(encoding="utf-8"))
            if ev.get("consumption_key") == key:
                out.append(ev)
        out.sort(key=lambda e: e.get("seq", 0))
        return out

    def status(self, key: str) -> str:
        events = self.events_for(key)
        return events[-1]["status"] if events else "NONE"

    # ---------- 状态机 ----------
    def bind_plan(self, *, key: str, plan_id: str) -> dict[str, Any]:
        bound = [e for e in self.events_for(key) if e.get("event") == "BOUND"]
        if bound:
            raise AlreadyBound(f"consumption_key 已绑定 plan: {bound[-1].get('plan_id')}")
        return self.append({"event": "BOUND", "consumption_key": key, "plan_id": plan_id,
                            "status": "PREPARED"})

    def mark_submitting(self, *, key: str, plan_id: str, intent_id: str) -> dict[str, Any]:
        if self.status(key) == "SUBMITTING":
            raise StateConflict("上一次 SUBMITTING 未终结，只能只读对账")
        return self.append({"event": "SUBMITTING", "consumption_key": key, "plan_id": plan_id,
                            "intent_id": intent_id, "status": "SUBMITTING"})

    def mark_result(self, *, key: str, plan_id: str, intent_id: str, status: str) -> dict[str, Any]:
        if status not in {"WORKING", "FILLED", "PARTIAL", "UNKNOWN", "REJECTED",
                          "CANCELED", "EXPIRED_UNSUBMITTED", "ABORTED_UNSUBMITTED"}:
            raise StateConflict(f"非法状态: {status}")
        return self.append({"event": "RESULT", "consumption_key": key, "plan_id": plan_id,
                            "intent_id": intent_id, "status": status})

    def verify_chain(self) -> bool:
        head = self.head()
        if head["seq"] == 0:
            return True
        loaded = [json.loads(f.read_text(encoding="utf-8")) for f in self.events.glob("*.json")]
        loaded.sort(key=lambda e: e.get("seq", 0))
        prev = None
        for i, ev in enumerate(loaded, 1):
            if ev.get("seq") != i or ev.get("prev_hash") != prev:
                return False
            body = {k: v for k, v in ev.items() if k != "event_hash"}
            if sha256_hex(canonical_json_bytes(body)) != ev.get("event_hash"):
                return False
            prev = ev["event_hash"]
        return prev == head["event_hash"]
