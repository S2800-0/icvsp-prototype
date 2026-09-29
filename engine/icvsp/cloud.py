"""Stand-in for the AWS path.

The engine never imports this module: that is the point. Real-time decisions
cannot depend on the cloud, because the decision code has no route to it.
Events and decisions are published here afterwards, and queue while offline.
"""
from __future__ import annotations


class CloudSync:
    def __init__(self, online: bool = True):
        self.online = online
        self.stored: list[dict] = []
        self.queue: list[dict] = []

    def publish(self, item: dict) -> None:
        (self.stored if self.online else self.queue).append(item)

    def reconnect(self) -> int:
        self.online = True
        n = len(self.queue)
        self.stored.extend(self.queue)
        self.queue.clear()
        return n
