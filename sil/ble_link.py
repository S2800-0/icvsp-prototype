"""Software model of the device -> phone Bluetooth LE link, carrying the REAL signed report bytes.

Why not one coin flip per report: a signed ICVSP v2 report is about 700 bytes of JSON, but a BLE notification carries
ATT_MTU - 3 bytes (20 at the default MTU 23, about 180-240 once a larger MTU is negotiated). So each report travels as
many fragments and is lost if ANY fragment is missing: P(report lost per attempt) = 1 - (1 - p_fragment) ** n_fragments.
The model also covers delay (connection events), link drop-outs with a bounded device queue, a message-level ack with
retries, and duplicates when the ack is lost (the backend must be idempotent). Standard library only; deterministic for a
given seed.

Fragment format (app level, 4-byte header): msg_id (u16 big-endian) | index (u8) | total (u8) | payload slice.
This is a SIMULATION model: radio effects are summarised by a few parameters (fragment_loss is the loss left AFTER the
link-layer retransmissions). It does not replace testing with a real radio.
"""
from __future__ import annotations

import random
import struct
from dataclasses import dataclass, field

HEADER = 4
ATT_OVERHEAD = 3


@dataclass(frozen=True)
class BleConfig:
    mtu: int = 185                   # negotiated ATT MTU (23 = default, 247 = common maximum on phones/ESP32)
    conn_interval_s: float = 0.03    # connection interval
    notifs_per_event: int = 4        # notifications that fit in one connection event
    fragment_loss: float = 0.02      # per-fragment loss after link-layer retransmission
    jitter_s: float = 0.01           # extra random delay per connection event, uniform [0, jitter_s]
    ack_timeout_s: float = 1.0       # device resends the whole message if no ack arrives in this time
    ack_loss: float = 0.0            # probability the phone's ack is lost (device resends -> duplicate delivery)
    max_attempts: int = 3            # tries per message (first send + resends)
    disconnects: tuple = ()          # ((start_s, end_s), ...) link-down windows, non-overlapping
    queue_limit: int = 16            # messages the device buffers while the link is down (oldest dropped first)
    reassembly_timeout_s: float = 2.0


@dataclass
class Delivery:
    index: int                # index of the message in the input list
    payload: bytes            # exactly the bytes that arrived (re-assembled from fragments)
    t_sent: float
    t_arrival: float
    attempt: int              # 1 = first try
    duplicate: bool           # second or later delivery of the same message (lost ack)
    fragments: int


@dataclass
class Lost:
    index: int
    t_sent: float
    reason: str               # "fragment loss" | "queue overflow"


@dataclass
class Result:
    deliveries: list[Delivery] = field(default_factory=list)
    lost: list[Lost] = field(default_factory=list)


def fragment(msg_id: int, payload: bytes, mtu: int) -> list[bytes]:
    room = mtu - ATT_OVERHEAD - HEADER
    if room < 1:
        raise ValueError(f"MTU {mtu} leaves no room for payload")
    n = max(1, -(-len(payload) // room))
    if n > 255:
        raise ValueError(f"{len(payload)} bytes need {n} fragments (max 255)")
    return [struct.pack(">HBB", msg_id & 0xFFFF, i, n) + payload[i * room:(i + 1) * room] for i in range(n)]


def fragment_count(size: int, mtu: int) -> int:
    return max(1, -(-size // (mtu - ATT_OVERHEAD - HEADER)))


class Reassembler:
    """Phone side: collects fragments per msg_id; incomplete messages are dropped after the timeout."""

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


class BleLink:
    def __init__(self, cfg: BleConfig | None = None, seed: int = 0):
        self.cfg = cfg or BleConfig()
        self.rng = random.Random(seed)
        for a, b in self.cfg.disconnects:
            if b <= a:
                raise ValueError("disconnect window must have end > start")

    def _window_of(self, t: float):
        for a, b in self.cfg.disconnects:
            if a <= t < b:
                return (a, b)
        return None

    def _link_up_at(self, t: float) -> bool:
        return self._window_of(t) is None

    def simulate(self, messages: list[tuple[float, bytes]]) -> Result:
        """messages: (time the device wants to send, signed report bytes). Returns arrivals at the phone and losses."""
        cfg, rng = self.cfg, self.rng
        res = Result()
        order = sorted(range(len(messages)), key=lambda i: (messages[i][0], i))
        queued: dict[tuple, list[int]] = {}
        ready: list[tuple[float, int]] = []
        for i in order:
            t = messages[i][0]
            w = self._window_of(t)
            if w is None:
                ready.append((t, i))
            else:
                queued.setdefault(w, []).append(i)
        for (a, b), idxs in queued.items():
            for i in idxs[:max(0, len(idxs) - cfg.queue_limit)]:
                res.lost.append(Lost(i, messages[i][0], "queue overflow"))
            keep = idxs[max(0, len(idxs) - cfg.queue_limit):]
            ready += [(b, i) for i in keep]
        ready.sort()
        radio_free = float("-inf")
        for t_ready, i in ready:
            t_sent, payload = messages[i]
            start = max(t_ready, radio_free)
            n = fragment_count(len(payload), cfg.mtu)
            delivered_once = False
            for attempt in range(1, cfg.max_attempts + 1):
                frags = fragment(i, payload, cfg.mtu)
                ra = Reassembler(cfg.reassembly_timeout_s)
                out = None
                t_end = start
                for k, fr in enumerate(frags):
                    t_k = start + (k // cfg.notifs_per_event) * cfg.conn_interval_s + rng.uniform(0, cfg.jitter_s)
                    t_end = max(t_end, t_k)
                    if rng.random() < cfg.fragment_loss or not self._link_up_at(t_k):
                        continue
                    out = ra.push(t_k, fr) or out
                if out is not None:
                    res.deliveries.append(Delivery(i, out, t_sent, t_end, attempt, delivered_once, n))
                    delivered_once = True
                    if rng.random() >= cfg.ack_loss:
                        radio_free = t_end
                        break
                start = max(t_end, start) + cfg.ack_timeout_s
            else:
                radio_free = start
            if not delivered_once:
                res.lost.append(Lost(i, t_sent, "fragment loss"))
        res.deliveries.sort(key=lambda d: d.t_arrival)
        return res


def report_loss_probability(fragment_loss: float, n_fragments: int, attempts: int = 1) -> float:
    """Analytic loss of one message: every attempt needs all fragments (ack loss ignored)."""
    p_ok = (1 - fragment_loss) ** n_fragments
    return (1 - p_ok) ** attempts
