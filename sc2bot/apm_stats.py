"""Command frequency measured in game time, independent of score availability."""

from collections import deque


class CommandAPM:
    def __init__(self, start_time=0):
        self.start_time = start_time
        self.events = deque()
        self.total = 0

    def record(self, now, count):
        if count:
            self.events.append((now, count))
            self.total += count
        self.rates(now)

    def rates(self, now):
        while self.events and self.events[0][0] <= now - 60:
            self.events.popleft()
        elapsed = max(1, now - self.start_time)
        recent = 60 * sum(count for _, count in self.events) / min(60, elapsed)
        average = 60 * self.total / elapsed
        return recent, average
