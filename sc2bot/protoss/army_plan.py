"""Small, testable decisions for regrouping instead of feeding reinforcements."""

from dataclasses import dataclass


@dataclass
class ArmyPlan:
    mode: str = "RALLY"
    retreat_until: float = 0
    peak_power: float = 0
    peak_supply: float = 0
    outmatched_since: float | None = None
    last_enemy_time: float = -100
    rematch_power: float = 0
    rematch_until: float = 0
    reason: str = "gather army"

    def update(self, now, army_supply, power, enemy_power, cohesion,
               shields, advanced, base_threat=False):
        if enemy_power:
            self.last_enemy_time = now
        old = self.mode
        if self.mode == "ATTACK":
            self.peak_power = max(self.peak_power, power)
            outmatched = enemy_power > power * 1.35
            if outmatched:
                if self.outmatched_since is None:
                    self.outmatched_since = now
            else:
                self.outmatched_since = None
            overwhelmed = self.outmatched_since is not None and now - self.outmatched_since >= 2
            self.peak_supply = max(self.peak_supply, army_supply)
            heavy_losses = (now - self.last_enemy_time < 10
                            and army_supply < self.peak_supply * 0.72)
            if overwhelmed or heavy_losses or army_supply < 16:
                self.mode = "RETREAT"
                self.reason = "outmatched" if overwhelmed else "army losses"
                self.retreat_until = now + 25
                self.rematch_power = max(enemy_power * 1.2, self.peak_power * 1.1)
                self.rematch_until = now + 100
        if self.mode == "RETREAT":
            if now >= self.retreat_until and cohesion >= 0.75 and shields >= 0.65:
                self.mode = "RALLY"
                self.reason = "regrouped and shields recovered"
        elif base_threat:
            self.mode = "DEFEND"
            self.reason = "enemy near base"
        elif self.mode != "ATTACK":
            self.mode = "RALLY"
            self.reason = "gather army"
            ready = army_supply >= 44 or (army_supply >= 34 and advanced)
            rebuilt = now >= self.rematch_until or power >= self.rematch_power
            if ready and rebuilt and cohesion >= 0.75 and shields >= 0.65 and enemy_power <= power * 0.7:
                self.mode = "ATTACK"
                self.reason = "army assembled"
                self.peak_power = power
                self.peak_supply = army_supply
                self.outmatched_since = None
        return old != self.mode


def needs_full_defense(invasion_power, total_power, critical_base=False):
    return invasion_power > 0 and (
        critical_base or invasion_power >= max(14, total_power * 0.35))


def storm_target(enemies, allies, recent_centers, radius=1.5):
    """Choose a dense enemy cluster, rejecting friendly fire and overlap.

    Arguments are position tuples. Return None unless three enemies are hit.
    """
    def d2(a, b):
        return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
    best, hits = None, 2
    for point in enemies:
        if any(d2(point, ally) <= (radius + 0.5) ** 2 for ally in allies):
            continue
        if any(d2(point, old) <= (radius * 2) ** 2 for old in recent_centers):
            continue
        count = sum(d2(point, enemy) <= radius ** 2 for enemy in enemies)
        if count > hits:
            best, hits = point, count
    return best
