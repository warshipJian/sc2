"""Blink-stalker opening and harassment strategy."""

from loguru import logger
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.buff_id import BuffId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2


def aligned_void_rays(enemies):
    return enemies.of_type({UnitTypeId.VOIDRAY}).filter(
        lambda ray: ray.has_buff(BuffId.VOIDRAYSWARMDAMAGEBOOST))


def safer_stalker_step(position, home, threats, walkable, distance=3):
    """Pick a nearby step that reduces exposure, or keep the current position."""
    def risk(point):
        return sum(max(0, reach - point.distance_to(origin)) for origin, reach in threats)

    current = risk(position)
    candidates = [position.towards(home, distance)]
    for dx, dy in ((3, 0), (-3, 0), (0, 3), (0, -3), (2, 2), (2, -2), (-2, 2), (-2, -2)):
        candidates.append(Point2((position.x + dx * distance / 3, position.y + dy * distance / 3)))
    safe = [point for point in candidates if walkable(point) and risk(point) < current - 0.1]
    return min(safe, key=lambda point: (risk(point), point.distance_to(home)), default=None)


class StalkerStrategy:
    def update_stalker_counterattack(self):
        if self.defense_priority:
            self.stalker_wave_seen = True
            if self.harass_target is not None:
                self.harass_avoid[self.harass_target] = self.time + 90
            self.harass_target = None
        elif self.stalker_wave_seen and self.stalker_counter_until is None:
            self.stalker_counter_until = self.time + 45
            logger.info("First wave cleared: opening a counterattack and expansion window")

    @property
    def stalker_counterattack_window(self):
        return self.stalker_counter_until is not None and self.time < self.stalker_counter_until and not self.defense_priority

    def save_for_stalker_expansion(self):
        minimum = 4 if self.stalker_wave_seen else 6
        return (self.townhalls.amount == 1 and self.units(UnitTypeId.STALKER).amount >= minimum
                and self.powered_gate_count() >= 2 and not self.pending_count(UnitTypeId.NEXUS)
                and not self.defense_priority)

    async def stalker_advanced_tech(self):
        if self.units(UnitTypeId.STALKER).amount < 4 or self.powered_gate_count() < 2:
            return
        if not self.structures(UnitTypeId.TWILIGHTCOUNCIL):
            await self.build_safely(UnitTypeId.TWILIGHTCOUNCIL)
            return
        self.upgrade_once(UnitTypeId.TWILIGHTCOUNCIL, UpgradeId.BLINKTECH)
        if (self.units(UnitTypeId.STALKER).amount >= 6 and self.townhalls.amount >= 2
                and (UpgradeId.BLINKTECH in self.state.upgrades or self.already_pending_upgrade(UpgradeId.BLINKTECH))
                and not self.structures(UnitTypeId.ROBOTICSFACILITY)):
            await self.build_safely(UnitTypeId.ROBOTICSFACILITY)
        return

    def stalker_reserved_minerals(self):
        if self.powered_gate_count() < 2:
            return 150 if self.gate_count_with_pending() < 2 else 0
        stalkers = self.units(UnitTypeId.STALKER).amount
        if stalkers < 4:
            return 0
        if self.save_for_stalker_expansion():
            return 400
        if not self.structures(UnitTypeId.TWILIGHTCOUNCIL) and not self.pending_count(UnitTypeId.TWILIGHTCOUNCIL):
            return 150
        if (self.structures(UnitTypeId.TWILIGHTCOUNCIL).ready and UpgradeId.BLINKTECH not in self.state.upgrades
                and not self.already_pending_upgrade(UpgradeId.BLINKTECH)):
            return self.calculate_cost(UpgradeId.BLINKTECH).minerals
        if stalkers >= 6 and (UpgradeId.BLINKTECH in self.state.upgrades or self.already_pending_upgrade(UpgradeId.BLINKTECH)):
            if self.townhalls.amount >= 2 and not self.structures(UnitTypeId.ROBOTICSFACILITY) and not self.pending_count(UnitTypeId.ROBOTICSFACILITY):
                return 150
        return 0

    def stalker_gateway_unit(self):
        if self.structures(UnitTypeId.CYBERNETICSCORE).ready and self.can_afford(UnitTypeId.STALKER):
            if (not self.defense_priority and self.structures(UnitTypeId.TWILIGHTCOUNCIL)
                    and self.units(UnitTypeId.STALKER).amount >= 4 and UpgradeId.BLINKTECH not in self.state.upgrades
                    and not self.already_pending_upgrade(UpgradeId.BLINKTECH)):
                blink = self.calculate_cost(UpgradeId.BLINKTECH)
                stalker = self.calculate_cost(UnitTypeId.STALKER)
                if self.vespene < blink.vespene + stalker.vespene:
                    return None
            return UnitTypeId.STALKER
        return None

    def control_stalker_harass(self):
        army = self.units(UnitTypeId.STALKER)
        if not army:
            return
        home_base = (self.townhalls.ready or self.townhalls).closest_to(self.game_info.map_center)
        home = home_base.position.towards(self.game_info.map_center, 5)
        if UpgradeId.BLINKTECH not in self.state.upgrades and self.townhalls.amount == 1:
            home = self.main_base_ramp.top_center.towards(home_base.position, 3)
            batteries = self.structures(UnitTypeId.SHIELDBATTERY).ready.filter(lambda b: b.is_powered)
            if batteries:
                home = batteries.closest_to(home).position.towards(home_base.position, 2)
        core = self.largest_group(army)
        center = core.center
        threats = (self.enemy_units | self.enemy_structures).filter(lambda u: u.can_attack_ground)
        aligned = aligned_void_rays(self.enemy_units)
        local = threats.closer_than(14, center)
        healthy = core.filter(lambda u: u.shield_percentage >= 0.6)
        power = sum(self.combat_power(u) for u in core)
        enemy_power = sum(self.combat_power(u) for u in local)
        if self.harass_target is not None and (core.amount < 4 or enemy_power > power * 1.15
                or healthy.amount * 2 < core.amount):
            self.harass_avoid[self.harass_target] = self.time + 90
            self.harass_target = None
            self.harass_retry = self.time + 18
            logger.info("Harassment disengaged: healthy={}/{} local strength={:.1f}/{:.1f}",
                        healthy.amount, core.amount, power, enemy_power)
        if self.harass_target is not None and center.distance_to(self.harass_target) < 6:
            if not (self.enemy_units | self.enemy_structures).closer_than(9, self.harass_target):
                self.harass_avoid[self.harass_target] = self.time + 45
                self.harass_target = None
        counter = self.stalker_counterattack_window
        if (self.harass_target is None and self.time >= self.harass_retry and healthy.amount >= (4 if counter else 6)
                and (counter or UpgradeId.BLINKTECH in self.state.upgrades) and enemy_power <= power * 0.8):
            enemy_main = self.enemy_start_locations[0]
            targets = [s.position for s in self.enemy_structures.of_type({UnitTypeId.NEXUS, UnitTypeId.COMMANDCENTER,
                       UnitTypeId.ORBITALCOMMAND, UnitTypeId.PLANETARYFORTRESS, UnitTypeId.HATCHERY, UnitTypeId.LAIR, UnitTypeId.HIVE})]
            targets.extend(pos for pos in self.expansion_locations_list if 12 < pos.distance_to(enemy_main) < 48)
            targets.append(enemy_main)
            candidates = [pos for pos in targets if self.harass_avoid.get(pos, 0) <= self.time
                          and not self.townhalls.closer_than(14, pos)]
            if candidates:
                self.harass_target = min(candidates, key=lambda pos: pos.distance_to(center)
                    + 5 * sum(self.combat_power(u) for u in threats.closer_than(14, pos))
                    + (12 if pos.distance_to(enemy_main) < 10 else 0))
                logger.info("{} toward {} with {} stalkers", "Opening counterattack" if counter else "Blink harassment", self.harass_target, core.amount)
        self.army_plan.mode = "ATTACK" if self.harass_target is not None else "RALLY"
        for stalker in army:
            if stalker.tag in self.unit_tags_received_action:
                continue
            targets = (self.enemy_units | self.enemy_structures).closer_than(10, stalker).filter(stalker.target_in_range)
            nearby = threats.closer_than(9, stalker)
            charged = aligned.filter(lambda ray: ray.distance_to(stalker) <= ray.ground_range + ray.radius + stalker.radius + 1)
            if charged:
                danger = [(enemy.position, enemy.ground_range + enemy.radius + stalker.radius + 1.5)
                          for enemy in nearby]
                step = safer_stalker_step(stalker.position, home, danger,
                    lambda point: self.in_map_bounds(point) and self.in_pathing_grid(point))
                if step is not None:
                    stalker.move(step)
                    continue
            retreat = self.harass_target is None or stalker.shield_percentage < 0.4
            if nearby and (retreat or stalker.weapon_cooldown > 0):
                # Range plus a buffer for unit radii and moving melee threats.
                danger = [(enemy.position, enemy.ground_range + enemy.radius + stalker.radius + 1.5)
                          for enemy in nearby]
                step = safer_stalker_step(stalker.position, home, danger,
                    lambda point: self.in_map_bounds(point) and self.in_pathing_grid(point))
                if step is not None and (stalker.shield_percentage < 0.4 or stalker.weapon_cooldown > 0):
                    stalker.move(step)
                    continue
            if targets and stalker.weapon_cooldown == 0:
                # Pick off exposed workers when safe; otherwise focus one combat target.
                workers = targets.of_type({UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE})
                combat = targets.filter(lambda u: u.can_attack_ground and u.type_id not in {UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE})
                pool = (combat if nearby else workers) or targets
                stalker.attack(min(pool, key=lambda u: (u.health + u.shield, u.tag)))
            elif retreat:
                if stalker.distance_to(home) > 4:
                    stalker.move(home)
            elif stalker.distance_to(center) > 12:
                stalker.move(center.towards(home, 4))
            else:
                stalker.attack(self.harass_target)

    async def build_stalker_opening_battery(self):
        battery = UnitTypeId.SHIELDBATTERY
        if self.structures(battery) or not self.structures(UnitTypeId.CYBERNETICSCORE).ready:
            return
        if self.units(UnitTypeId.STALKER).amount < 2 and not self.defense_priority:
            return
        await self.build_safely(battery)
