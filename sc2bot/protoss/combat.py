"""Shared Protoss combat behavior."""

from loguru import logger
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2

from sc2bot.protoss.army_plan import storm_target
from sc2bot.protoss.protoss_stalker import aligned_void_rays, safer_stalker_step


class Combat:
    @property
    def defense_priority(self):
        return self.time < getattr(self, "defense_priority_until", 0)

    def update_defense_priority(self):
        self.worker_hits = {tag: (when, pos) for tag, (when, pos) in self.worker_hits.items()
                            if self.time - when < 7}
        hits = [pos for _, pos in self.worker_hits.values()]
        raid = next((pos for pos in hits if sum(pos.distance_to(other) < 10 for other in hits) >= 3), None)
        if raid is not None:
            if self.raid_position is None:
                logger.info("Worker raid detected at {}; defending and evacuating", raid)
            self.raid_position = raid
            self.defense_priority_until = self.time + 12
        else:
            self.raid_position = None
        armed = self.enemy_units | self.enemy_structures
        armed = armed.filter(lambda u: u.can_attack_ground and u.type_id not in {
            UnitTypeId.SCV, UnitTypeId.PROBE, UnitTypeId.DRONE,
        })
        for base in self.townhalls:
            nearby = armed.closer_than(24, base)
            if nearby and (sum(self.combat_power(u) for u in nearby) >= 8
                           or base.health_percentage < 0.5):
                if not self.defense_priority:
                    logger.info("Emergency production: enemy force near base {}", base.position)
                self.defense_priority_until = self.time + 12
                return

    async def on_unit_took_damage(self, unit, amount_damage_taken):
        if unit.type_id == UnitTypeId.PROBE and any(unit.distance_to(base) < 14 for base in self.townhalls):
            self.worker_hits[unit.tag] = (self.time, unit.position)

    def evacuate_raided_workers(self):
        raid = self.raid_position
        if raid is None:
            return False
        safe = self.townhalls.ready.filter(lambda base: base.distance_to(raid) > 20 and not
            self.enemy_units.filter(lambda u: u.can_attack_ground).closer_than(16, base))
        if safe:
            minerals = self.mineral_field.closer_than(10, safe.closest_to(raid))
            if minerals:
                for worker in self.workers.closer_than(10, raid):
                    if worker.tag not in self.used_builders and worker.tag not in self.unit_tags_received_action:
                        worker.gather(minerals.closest_to(worker))
        # Do not redistribute workers back into the threatened mineral line.
        return True

    @staticmethod
    def combat_power(unit):
        maximum = max(1, unit.health_max + unit.shield_max)
        fraction = (unit.health + unit.shield) / maximum
        power = (maximum / 100 + max(unit.ground_dps, unit.air_dps) / 10)
        if unit.type_id in {UnitTypeId.SIEGETANKSIEGED, UnitTypeId.LURKERMPBURROWED}:
            power *= 1.5
        return max(0.5, power) * (0.35 + 0.65 * fraction)

    def main_army(self):
        return self.units.of_type({UnitTypeId.STALKER, UnitTypeId.ZEALOT,
            UnitTypeId.IMMORTAL, UnitTypeId.COLOSSUS, UnitTypeId.ADEPT})

    def largest_group(self, units):
        seed = max(units, key=lambda u: units.closer_than(11, u).amount)
        return units.closer_than(11, seed)

    async def cast_combat_spells(self):
        army = self.main_army()
        if not army:
            return
        self.recent_storms = [(pos, until) for pos, until in self.recent_storms if until > self.time]
        if UpgradeId.PSISTORMTECH in self.state.upgrades:
            for templar in self.units(UnitTypeId.HIGHTEMPLAR):
                if templar.energy < 75:
                    continue
                enemies = self.enemy_units.filter(lambda u: not u.is_structure
                    and u.type_id not in {UnitTypeId.SCV, UnitTypeId.PROBE, UnitTypeId.DRONE}).closer_than(10, templar)
                point = storm_target(
                    [tuple(u.position) for u in enemies],
                    [tuple(u.position) for u in self.units],
                    [tuple(pos) for pos, _ in self.recent_storms],
                )
                if point is not None:
                    pos = Point2(point)
                    if await self.can_cast(templar, AbilityId.PSISTORM_PSISTORM, pos):
                        templar(AbilityId.PSISTORM_PSISTORM, pos)
                        self.recent_storms.append((pos, self.time + 3))
                        logger.info("Storm at {}", pos)
        if UpgradeId.BLINKTECH in self.state.upgrades:
            threshold = 0.4 if self.stalker_mode else 0.25
            aligned = aligned_void_rays(self.enemy_units)
            for stalker in self.units(UnitTypeId.STALKER):
                charged = aligned.filter(lambda ray: ray.distance_to(stalker) <= ray.ground_range + ray.radius + stalker.radius + 1)
                if stalker.shield_percentage >= threshold and not charged:
                    continue
                enemies = self.enemy_units.filter(lambda u: u.can_attack_ground).closer_than(8, stalker)
                if not enemies and not charged:
                    continue
                base = self.townhalls.closest_to(stalker)
                pos = stalker.position.towards(base.position, min(7, stalker.distance_to(base)))
                if charged:
                    nearby = enemies | charged | self.enemy_structures.filter(lambda u: u.can_attack_ground).closer_than(12, stalker)
                    danger = [(u.position, u.ground_range + u.radius + stalker.radius + 1.5) for u in nearby]
                    pos = safer_stalker_step(stalker.position, base.position, danger,
                        lambda point: self.in_map_bounds(point) and self.in_pathing_grid(point), distance=7)
                    if pos is None:
                        continue
                if self.in_map_bounds(pos) and self.in_pathing_grid(pos) and await self.can_cast(stalker, AbilityId.EFFECT_BLINK_STALKER, pos):
                    stalker(AbilityId.EFFECT_BLINK_STALKER, pos)

    def control_observers(self):
        observers = self.units(UnitTypeId.OBSERVER)
        if not observers:
            return
        army = self.main_army()
        center = self.largest_group(army).center if army else self.townhalls.first.position
        home = (self.townhalls.ready or self.townhalls).closest_to(center).position
        enemies = self.enemy_units | self.enemy_structures
        dangers = enemies.filter(lambda u: u.can_attack_air or u.is_detector)
        cloaked = self.enemy_units.filter(lambda u: u.is_cloaked or u.is_burrowed)
        detection_needed = cloaked.filter(lambda u:
            u.distance_to(center) < 20 or any(u.distance_to(base) < 20 for base in self.townhalls))
        fighting = self.army_plan.mode == "ATTACK" and bool(self.enemy_units.filter(
            lambda u: u.can_attack_ground or u.can_attack_air).closer_than(16, center))
        if self.observer_scout_started is None and not self.observer_scout_done:
            self.observer_scout_tag = observers.first.tag
            self.observer_scout_started = self.time
            route = []
            for start in self.enemy_start_locations:
                route.append(start.towards(self.game_info.map_center, 8))
                expansions = sorted((pos for pos in self.expansion_locations_list
                    if 10 < pos.distance_to(start) < 45), key=lambda pos: pos.distance_to(start))
                route.extend(pos.towards(self.game_info.map_center, 3) for pos in expansions[:2])
            self.observer_scout_route = list(dict.fromkeys(route))
            logger.info("First observer scouting: {} waypoints", len(self.observer_scout_route))
        if detection_needed or fighting or self.army_plan.mode == "DEFEND":
            self.observer_scout_done = True
        if self.observer_scout_started is not None and self.time - self.observer_scout_started > 90:
            self.observer_scout_done = True
        for observer in observers:
            if observer.tag in self.unit_tags_received_action:
                continue
            nearby = dangers.filter(lambda enemy: observer.distance_to(enemy) <
                max(enemy.air_range if enemy.can_attack_air else 0,
                    enemy.detect_range if enemy.is_detector else 0) + 3)
            if nearby:
                threat = nearby.closest_to(observer)
                destination = observer.position.towards(threat.position, -6) if observer.distance_to(threat) > 0.1 else home
                # Observers fly, but destinations still must be inside the map.
                size = self.game_info.map_size
                destination = Point2((max(1, min(size.x - 1, destination.x)),
                                      max(1, min(size.y - 1, destination.y))))
                observer.move(destination)
                continue
            if observer.tag != observers.first.tag and self.townhalls.ready.amount >= 3:
                outposts = self.townhalls.ready
                target = self.raid_position or outposts.closest_to(self.game_info.map_center).position
                if observer.distance_to(target) > 3:
                    observer.move(target)
                continue
            if observer.tag == self.observer_scout_tag and not self.observer_scout_done:
                route = self.observer_scout_route
                while self.observer_scout_index < len(route) and observer.distance_to(route[self.observer_scout_index]) < 4:
                    self.observer_scout_index += 1
                if self.observer_scout_index < len(route):
                    observer.move(route[self.observer_scout_index])
                    continue
                self.observer_scout_done = True
                logger.info("Observer scouting finished; returning to army")
            target = detection_needed.closest_to(center).position if detection_needed else center.towards(home, min(3, center.distance_to(home)))
            if observer.distance_to(target) > 3:
                observer.move(target)
