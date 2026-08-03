"""雪花 ID：64 位，1 符号位 + 41 位毫秒 + 10 位机器号 + 12 位序列号。

同一毫秒内同一台机器能出 4096 个，够用；超了就自旋等下一毫秒。
生成的数远超 JS 的 Number.MAX_SAFE_INTEGER（2^53），出接口必须转字符串。
"""

import os
import threading
import time

# 起始时间戳。往后 41 位毫秒够用到 2095 年左右
EPOCH_MS = 1767225600000  # 2026-01-01 00:00:00 UTC

WORKER_ID_BITS = 10
SEQUENCE_BITS = 12

MAX_WORKER_ID = (1 << WORKER_ID_BITS) - 1
SEQUENCE_MASK = (1 << SEQUENCE_BITS) - 1

WORKER_ID_SHIFT = SEQUENCE_BITS
TIMESTAMP_SHIFT = SEQUENCE_BITS + WORKER_ID_BITS


class Snowflake:
    def __init__(self, worker_id: int) -> None:
        if not 0 <= worker_id <= MAX_WORKER_ID:
            raise ValueError(f"WORKER_ID 必须在 0 到 {MAX_WORKER_ID} 之间，当前是 {worker_id}")
        self._worker_id = worker_id
        self._sequence = 0
        self._last_ms = -1
        # FastAPI 是单进程多协程，但 uvicorn 的线程池也可能调到，加锁最省心
        self._lock = threading.Lock()

    def next_id(self) -> int:
        with self._lock:
            now = self._now()

            if now < self._last_ms:
                # 机器时钟被回拨了。差得少就等回来，差得多说明运维出了事，宁可报错也不发重复 id
                drift = self._last_ms - now
                if drift > 5000:
                    raise RuntimeError(f"系统时钟回拨了 {drift}ms，拒绝生成 id")
                while now < self._last_ms:
                    now = self._now()

            if now == self._last_ms:
                self._sequence = (self._sequence + 1) & SEQUENCE_MASK
                if self._sequence == 0:
                    # 这一毫秒的 4096 个用光了，等下一毫秒
                    while now <= self._last_ms:
                        now = self._now()
            else:
                self._sequence = 0

            self._last_ms = now
            return ((now - EPOCH_MS) << TIMESTAMP_SHIFT) | (self._worker_id << WORKER_ID_SHIFT) | self._sequence

    @staticmethod
    def _now() -> int:
        return int(time.time() * 1000)


# 多机部署时每台机器的 WORKER_ID 必须不同，否则会发出重复 id
_generator = Snowflake(int(os.getenv("WORKER_ID", "0")))


def next_id() -> int:
    return _generator.next_id()
