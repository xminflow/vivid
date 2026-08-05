"""雪花 ID：64 位，1 符号位 + 41 位毫秒 + 10 位机器号 + 12 位序列号。

同一毫秒内同一台机器能出 4096 个，够用；超了就自旋等下一毫秒。
生成的数远超 JS 的 Number.MAX_SAFE_INTEGER（2^53），出接口必须转字符串。
"""

import hashlib
import logging
import os
import socket
import threading
import time

logger = logging.getLogger(__name__)

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


def resolve_worker_id() -> int:
    """决定本进程用哪个机器号。

    机器号必须逐实例不同，否则两个实例在同一毫秒各自从序列 0 开始，会算出完全
    相同的 id。id 是 users 的主键，重复不会污染数据，但插入会直接失败。

    显式 WORKER_ID 优先。云托管这类平台会自动扩缩容，实例由平台拉起，没法给每个
    实例配不同的环境变量，所以没配时从主机名推导——容器主机名（Pod 名）带平台生成
    的唯一后缀，是这里唯一能拿到的实例身份。

    代价说清楚：主机名要压进 10 位，只有 1024 个槽，N 个实例的碰撞概率约 N²/2048
    （10 个实例约 5%）。这是在「全用默认 0，多实例必冲突」和「平台配不了逐实例变量」
    之间的取舍，不是没有风险。实例数多或要求确定性时，仍应显式配 WORKER_ID。
    """
    raw = os.getenv("WORKER_ID", "").strip()
    if raw:
        # 配错了直接抛。静默换成推导值会让「我明明配了」和实际行为对不上，更难查
        worker_id = int(raw)
        logger.info("WORKER_ID 取自环境变量：%d", worker_id)
        return worker_id

    hostname = socket.gethostname()
    digest = hashlib.sha1(hostname.encode()).digest()
    worker_id = int.from_bytes(digest[:2], "big") & MAX_WORKER_ID
    logger.warning(
        "未配置 WORKER_ID，按主机名推导：hostname=%s worker_id=%d（同批实例请核对是否有重复）",
        hostname,
        worker_id,
    )
    return worker_id


_generator = Snowflake(resolve_worker_id())


def next_id() -> int:
    return _generator.next_id()
