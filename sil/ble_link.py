"""Software model of the device -> phone Bluetooth LE link, carrying the REAL signed report bytes (v2, 2026-10-28).

What it models
  * Fragmenting: a signed ICVSP report is about 700 bytes, an ATT notification carries ATT_MTU - 3 bytes (20 at the default
    MTU 23; Android 14 requests 517; the ATT attribute value limit is 512), minus our 4-byte app header (msg_id u16 |
    index u8 | total u8). A report is complete only when ALL its fragments have arrived.
  * Connection events: the radio only transmits at connection events (every `conn_interval_s`, anchor + small host jitter
    drawn per event). Each event carries at most `notifs_per_event` notifications IN TOTAL, shared by all queued messages
    in FIFO order, so simultaneous reports queue behind each other.
  * Delivery guarantee: BLE notifications are acknowledged and retransmitted by the LINK LAYER, so a lost radio packet
    mostly costs airtime and delay; real application-level loss comes from connection drops, buffer/queue overflow and
    stack errors. `fragment_loss` is therefore a SYNTHETIC STRESS parameter (default 0): independent residual erasure per
    fragment, useful for what-if tests, not a measured property of BLE.
  * Message ack: the phone acknowledges a complete report with a write after `ack_delay_s`; if the ack is lost or too slow
    the device resends the whole message after `ack_timeout_s` (up to `max_attempts`). The phone keeps partial fragments
    across attempts until `reassembly_timeout_s` and delivers a message every time it completes (duplicates after a
    lost ack: the backend must be idempotent).
  * Link drops: `disconnects` are windows in which the application transport is unavailable (radio loss until the
    supervision timeout, advertising, reconnect, MTU exchange and subscription are all folded into the window). The
    device buffers at most `queue_limit` unfinished messages (in-flight ones included); the oldest is dropped on overflow.

What it does NOT model: PHY and data-length details, airtime of long events, phone scheduling, bursty radio errors,
iOS/Android differences. It is a model of the logic around the radio, not a radio test. Standard library only;
deterministic for a given seed (one instance, one `simulate` call, per seed).
"""
from __future__ import annotations

import math
import random
import struct
from dataclasses import dataclass, field

HEADER = 4
ATT_OVERHEAD = 3
MAX_FRAGMENTS = 255
MAX_ATT_VALUE = 512


@dataclass(frozen=True)
class BleConfig:
    mtu: int = 185                   # negotiated ATT MTU (23 = default; phones can negotiate up to 517)
    conn_interval_s: float = 0.03    # connection interval (peripheral latency 0)
    notifs_per_event: int = 4        # notifications that fit in one connection event, shared by all queued messages
    fragment_loss: float = 0.0       # SYNTHETIC residual per-fragment erasure (0 = BLE's own retransmissions succeed)
    jitter_s: float = 0.005          # host/stack delay added to each connection event, uniform [0, jitter_s]
    ack_delay_s: float = 0.03        # phone's ack write reaches the device this long after the report completes
    ack_timeout_s: float = 1.0       # device resends the whole message if no ack arrives within this time of its last fragment
    ack_loss: float = 0.0            # probability the ack is lost (device resends -> duplicate delivery)
    max_attempts: int = 3            # total tries per message
    disconnects: tuple = ()          # ((start_s, end_s), ...) transport-unavailable windows, sorted, non-overlapping
    queue_limit: int = 16            # unfinished messages the device holds (oldest dropped first)
    reassembly_timeout_s: float = 2.0

    def __post_init__(self):
        payload_room(self.mtu)                                   # raises if the MTU leaves no payload room
        if self.conn_interval_s <= 0 or self.notifs_per_event < 1 or self.max_attempts < 1 or self.queue_limit < 1:
            raise ValueError("conn_interval_s > 0, notifs_per_event >= 1, max_attempts >= 1, queue_limit >= 1 required")
        if not 0 <= self.jitter_s < self.conn_interval_s:
            raise ValueError("jitter_s must be in [0, conn_interval_s)")
        if not (0 <= self.fragment_loss <= 1 and 0 <= self.ack_loss <= 1):
            raise ValueError("probabilities must be in [0, 1]")
        if min(self.ack_delay_s, self.ack_timeout_s, self.reassembly_timeout_s) < 0:
            raise ValueError("times must be >= 0")
        prev = float("-inf")
        for a, b in self.disconnects:
            if b <= a or a < prev:
                raise ValueError("disconnect windows must have end > start and be sorted and non-overlapping")
            prev = b


@dataclass
class Delivery:
    index: int                # index of the message in the input list
    payload: bytes            # exactly the bytes that arrived (re-assembled from fragments)
    t_sent: float             # time the device wanted to send
    t_arrival: float          # time the last fragment reached the phone
    attempt: int              # attempt in which it completed (1 = first try)
    duplicate: bool           # second or later completion of the same message
    fragments: int


@dataclass
class Lost:
    index: int
    t_sent: float
    reason: str               # "fragment loss" (attempts exhausted) | "queue overflow"
    t_lost: float = 0.0


@dataclass
class Result:
    deliveries: list[Delivery] = field(default_factory=list)
    lost: list[Lost] = field(default_factory=list)


def payload_room(mtu: int) -> int:
    """Application bytes per notification: min(MTU - 3, 512) attribute-value bytes minus our 4-byte header."""
    room = min(mtu - ATT_OVERHEAD, MAX_ATT_VALUE) - HEADER
    if room < 1:
        raise ValueError(f"MTU {mtu} leaves no room for payload")
    return room


def fragment_count(size: int, mtu: int) -> int:
    n = max(1, -(-size // payload_room(mtu)))
    if n > MAX_FRAGMENTS:
        raise ValueError(f"{size} bytes need {n} fragments (max {MAX_FRAGMENTS})")
    return n


def fragment(msg_id: int, payload: bytes, mtu: int) -> list[bytes]:
    n = fragment_count(len(payload), mtu)
    room = payload_room(mtu)
    return [struct.pack(">HBB", msg_id & 0xFFFF, i, n) + payload[i * room:(i + 1) * room] for i in range(n)]


class Reassembler:
    """Phone side: collects fragments per msg_id; an incomplete message is dropped `timeout_s` after its first fragment.
    Keyed by msg_id only: a real receiver must also scope by connection/session, and ids must not repeat while a
    partial message is alive (u16 wrap)."""

    def __init__(self, timeout_s: float = 2.0):
        self.timeout_s = timeout_s
        self._partial: dict[int, tuple[float, int, dict[int, bytes]]] = {}

    def push(self, t: float, frag: bytes) -> bytes | None:
        for k in [k for k, (t0, _, _) in self._partial.items() if t - t0 > self.timeout_s]:
            del self._partial[k]
        if len(frag) < HEADER:
            return None
        msg_id, idx, total = struct.unpack(">HBB", frag[:HEADER])
        if total == 0 or idx >= total:
            return None
        t0, tot, parts = self._partial.setdefault(msg_id, (t, total, {}))
        if tot != total:
            return None
        parts[idx] = frag[HEADER:]
        if len(parts) == total:
            del self._partial[msg_id]
            return b"".join(parts[i] for i in range(total))
        return None


@dataclass
class _Msg:
    index: int
    t_sent: float
    payload: bytes
    frags: list
    ra: Reassembler
    pos: int = 0                 # next fragment to send in the current attempt
    attempt: int = 1
    state: str = "sending"       # sending | waiting_ack
    resend_at: float = 0.0
    delivered: int = 0
    completed: bool = False      # the phone completed the message during the current attempt


class BleLink:
    def __init__(self, cfg: BleConfig | None = None, seed: int = 0):
        self.cfg = cfg or BleConfig()
        self.rng = random.Random(seed)

    def _up(self, t: float) -> bool:
        return not any(a <= t < b for a, b in self.cfg.disconnects)

    def simulate(self, messages: list[tuple[float, bytes]]) -> Result:
        """messages: (time the device wants to send, signed report bytes). Returns arrivals at the phone and losses."""
        cfg, rng = self.cfg, self.rng
        res = Result()
        if not messages:
            return res
        order = sorted(range(len(messages)), key=lambda i: (messages[i][0], i))
        nxt = 0
        pending: list[_Msg] = []
        k = math.ceil(messages[order[0]][0] / cfg.conn_interval_s)
        iv = cfg.conn_interval_s

        def finish_lost(m: _Msg, t: float, reason: str):
            if not m.delivered:
                res.lost.append(Lost(m.index, m.t_sent, reason, t))

        while nxt < len(order) or pending:
            t_e = k * iv + rng.uniform(0, cfg.jitter_s)
            # admit messages the device wants to send up to this event; the queue is bounded
            while nxt < len(order) and messages[order[nxt]][0] <= t_e:
                i = order[nxt]
                nxt += 1
                t_s, payload = messages[i]
                pending.append(_Msg(i, t_s, payload, fragment(i, payload, cfg.mtu), Reassembler(cfg.reassembly_timeout_s)))
                while len(pending) > cfg.queue_limit:
                    old = pending.pop(0)
                    finish_lost(old, t_e, "queue overflow")
            if self._up(t_e):
                # ack timeouts: resend or give up
                for m in list(pending):
                    if m.state == "waiting_ack" and t_e >= m.resend_at:
                        if m.attempt >= cfg.max_attempts:
                            pending.remove(m)
                            finish_lost(m, t_e, "fragment loss")
                        else:
                            m.attempt += 1
                            m.pos = 0
                            m.state = "sending"
                budget = cfg.notifs_per_event
                for m in list(pending):
                    if budget <= 0:
                        break
                    if m.state != "sending":
                        continue
                    while budget > 0 and m.pos < len(m.frags):
                        budget -= 1
                        frag = m.frags[m.pos]
                        m.pos += 1
                        if rng.random() < cfg.fragment_loss:
                            continue
                        out = m.ra.push(t_e, frag)
                        if out is not None:
                            res.deliveries.append(Delivery(m.index, out, m.t_sent, t_e, m.attempt, m.delivered > 0, len(m.frags)))
                            m.delivered += 1
                            m.completed = True
                    if m.pos >= len(m.frags):                       # attempt finished
                        done, m.completed = m.completed, False
                        if done and rng.random() >= cfg.ack_loss:
                            pending.remove(m)                       # acked: leaves the device queue
                        else:
                            m.state = "waiting_ack"
                            m.resend_at = t_e + max(cfg.ack_timeout_s, cfg.ack_delay_s)
            # jump over idle time
            if not pending:
                if nxt < len(order):
                    k = max(k + 1, math.ceil(messages[order[nxt]][0] / iv))
                    continue
                break
            if all(m.state == "waiting_ack" for m in pending):
                t_next = min(m.resend_at for m in pending)
                if nxt < len(order):
                    t_next = min(t_next, messages[order[nxt]][0])
                k = max(k + 1, math.ceil(t_next / iv))
            else:
                k += 1
        res.deliveries.sort(key=lambda d: d.t_arrival)
        return res


def report_loss_probability(fragment_loss: float, n_fragments: int, attempts: int = 1) -> float:
    """Analytic loss of one message under SYNTHETIC independent fragment erasure, assuming the receiver keeps no
    partial fragments between attempts and ignoring ack loss. With partial retention (the default behaviour of
    `BleLink`) the real loss for attempts > 1 is lower; this is an upper bound."""
    p_ok = (1 - fragment_loss) ** n_fragments
    return (1 - p_ok) ** attempts
