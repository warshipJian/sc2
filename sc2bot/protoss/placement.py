"""Shared Protoss placement behavior."""

import math

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2


class Placement:
    async def protect_expansion(self):
        bases = self.townhalls.ready
        cannon = UnitTypeId.PHOTONCANNON
        if bases.amount < 3 or self.minerals < 225 or not self.structures(UnitTypeId.FORGE).ready:
            return
        if not self.can_afford(cannon) or self.pending_count(cannon):
            return
        pylons = self.structures(UnitTypeId.PYLON).ready
        for base in bases.sorted_by_distance_to(self.start_location, reverse=True):
            if base.distance_to(self.start_location) < 15 or self.structures(cannon).closer_than(12, base):
                continue
            minerals = self.mineral_field.closer_than(10, base)
            if not minerals:
                continue
            points = [Point2((p.position.x + x, p.position.y + y)) for p in pylons.closer_than(14, base)
                      for x in (-4, -2, 0, 2, 4) for y in (-4, -2, 0, 2, 4)]
            points = [pos for pos in points if pos.distance_to(base) < 12 and self.safe_build_position(pos)]
            points.sort(key=lambda pos: pos.distance_to(minerals.center))
            points = points[:24]
            if not points:
                continue
            for pos, allowed in zip(points, await self.can_place(cannon, points)):
                if allowed:
                    worker = self.pick_builder(pos)
                    if worker and worker.build(cannon, pos, can_afford_check=True):
                        self.remember_build(cannon, pos, worker)
                        return

    def safe_build_position(self, position):
        for start, end in getattr(self, "exit_corridor", ()):
            dx, dy = end.x - start.x, end.y - start.y
            length = dx * dx + dy * dy
            if length:
                t = max(0, min(1, ((position.x - start.x) * dx + (position.y - start.y) * dy) / length))
                if position.distance_to(Point2((start.x + t * dx, start.y + t * dy))) < 4:
                    return False
        # Keep building footprints away from resources AND worker travel lanes.
        if any(position.distance_to(pos) < 5 for pos in getattr(self, "reserved_build_positions", [])):
            return False
        if self.structures(UnitTypeId.ROBOTICSFACILITY).closer_than(4.5, position):
            return False
        if any(position.distance_to(pos) < 4 for pos in getattr(self, "wall_slots", [])):
            return False
        if getattr(self, "wall_gap", None) is not None and position.distance_to(self.wall_gap) < 3:
            return False
        resources = list(self.mineral_field) + list(self.vespene_geyser)
        if any(position.distance_to(resource) < 5 for resource in resources):
            return False
        for base in self.townhalls:
            if position.distance_to(base) < 6:
                return False
            for resource in resources:
                if resource.distance_to(base) > 12:
                    continue
                a, b = base.position, resource.position
                dx, dy = b.x - a.x, b.y - a.y
                length2 = dx * dx + dy * dy
                if not length2:
                    continue
                t = max(0, min(1, ((position.x - a.x) * dx + (position.y - a.y) * dy) / length2))
                nearest = Point2((a.x + t * dx, a.y + t * dy))
                if position.distance_to(nearest) < 3.5:
                    return False
        # Leave future Nexus locations clear.
        return all(position.distance_to(expansion) >= 6 for expansion in self.expansion_locations_list)

    def refresh_build_orders(self):
        # Retain commands until the structure appears, so travel time does not
        # cause duplicate buildings or repeated orders to the same probe.
        self.build_orders = [order for order in self.build_orders
            if order[2] > self.time and not self.structures(order[0]).closer_than(2, order[1])]
        self.reserved_build_positions = [order[1] for order in self.build_orders]
        self.used_builders = {order[3] for order in self.build_orders}
        self.placement_blocked = False

    def pending_count(self, building):
        return max(self.already_pending(building),
                   sum(order[0] == building for order in self.build_orders))

    def pick_builder(self, pos):
        workers = self.workers.filter(lambda w:
            w.tag not in self.used_builders
            and w.tag not in self.unit_tags_received_action
            and (w.is_idle or w.is_collecting))
        return workers.closest_to(pos) if workers else None

    def remember_build(self, building, pos, worker):
        self.used_builders.add(worker.tag)
        self.reserved_build_positions.append(pos)
        self.build_orders.append((building, pos, self.time + 15, worker.tag))

    async def try_wall_building(self, building):
        slots = getattr(self, "wall_slots", [])
        pos = None
        if building == UnitTypeId.PYLON and not self.structures(UnitTypeId.PYLON):
            pos = getattr(self, "wall_pylon", None)
        elif len(slots) == 2:
            if building == UnitTypeId.GATEWAY:
                pos = slots[0]
            elif building == UnitTypeId.CYBERNETICSCORE:
                pos = slots[1]
        if pos is None or self.structures.closer_than(1, pos):
            return False
        if getattr(self, "exit_corridor", None) and not self.safe_build_position(pos):
            return False
        if any(point.distance_to(pos) < 2.5 for point in self.reserved_build_positions):
            return False
        if not await self.can_place_single(building, pos):
            return False
        worker = self.pick_builder(pos)
        if worker and worker.build(building, pos, can_afford_check=True):
            self.remember_build(building, pos, worker)
            return True
        return False

    async def build_safely(self, building, max_pending=1):
        if not self.can_afford(building) or self.pending_count(building) >= max_pending:
            return False
        if await self.try_wall_building(building):
            return True
        pylons = self.structures(UnitTypeId.PYLON).ready
        if building != UnitTypeId.PYLON and not pylons:
            return False
        candidates = []
        if building == UnitTypeId.PYLON:
            # Restore power first, then extend the building area around pylons.
            unpowered = self.structures.filter(lambda b:
                b.type_id in {UnitTypeId.GATEWAY, UnitTypeId.WARPGATE, UnitTypeId.CYBERNETICSCORE, UnitTypeId.ROBOTICSFACILITY, UnitTypeId.ROBOTICSBAY, UnitTypeId.TWILIGHTCOUNCIL, UnitTypeId.FORGE, UnitTypeId.TEMPLARARCHIVE}
                and b.is_ready and not b.is_powered)
            anchors = [b.position for b in unpowered] + [p.position for p in pylons]
            for anchor in anchors:
                for dx in range(-4, 5, 2):
                    for dy in range(-4, 5, 2):
                        candidates.append(Point2((anchor.x + dx, anchor.y + dy)).rounded)
            for base in self.townhalls.ready:
                direction = self.game_info.map_center - base.position
                angle = math.atan2(direction.y, direction.x)
                for radius in (7, 10, 13, 16, 20, 24):
                    for offset in (0, 30, -30, 60, -60, 90, -90, 135, -135, 180):
                        theta = angle + math.radians(offset)
                        candidates.append(Point2((base.position.x + radius * math.cos(theta), base.position.y + radius * math.sin(theta))).rounded)
        else:
            # Dense coverage of each power field fixes gaps between the old rings.
            for pylon in pylons:
                for dx in range(-5, 6):
                    for dy in range(-5, 6):
                        if dx * dx + dy * dy <= 30:
                            candidates.append(Point2((pylon.position.x + dx, pylon.position.y + dy)).rounded)
        candidates = list(dict.fromkeys(pos for pos in candidates if self.safe_build_position(pos)))
        if building == UnitTypeId.PYLON:
            # Spread supply pylons instead of packing every one into the same field.
            candidates.sort(key=lambda pos: min((p.distance_to(pos) for p in pylons), default=0) < 6)
        if candidates:
            valid = await self.can_place(building, candidates)
            for pos, allowed in zip(candidates, valid):
                if allowed:
                    worker = self.pick_builder(pos)
                    if worker and worker.build(building, pos, can_afford_check=True):
                        self.remember_build(building, pos, worker)
                        return True
        if building != UnitTypeId.PYLON:
            self.placement_blocked = True
        return False
