"""Mixed-army Protoss strategy and shared defensive combat."""

from loguru import logger
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2
from sc2bot.protoss.army_plan import needs_full_defense


class NormalStrategy:
    async def normal_advanced_tech(self):
        # Reserve this one-off tech purchase; otherwise 100-mineral zealots
        # and 125-mineral stalkers can starve the 150-mineral robotics forever.
        if self.time >= 180 and self.townhalls.amount >= 2:
            if not self.structures(UnitTypeId.ROBOTICSFACILITY):
                await self.build_safely(UnitTypeId.ROBOTICSFACILITY)
                return
        robos = self.structures(UnitTypeId.ROBOTICSFACILITY).ready
        if robos and self.time >= 300 and not self.structures(UnitTypeId.ROBOTICSBAY):
            await self.build_safely(UnitTypeId.ROBOTICSBAY)
            return
        if self.time >= 270 and self.townhalls.amount >= 2 and not self.structures(UnitTypeId.TWILIGHTCOUNCIL):
            await self.build_safely(UnitTypeId.TWILIGHTCOUNCIL)
            return
        if self.time >= 330 and self.structures(UnitTypeId.TWILIGHTCOUNCIL).ready:
            if not self.structures(UnitTypeId.TEMPLARARCHIVE):
                await self.build_safely(UnitTypeId.TEMPLARARCHIVE)
                return
        if self.structures(UnitTypeId.TEMPLARARCHIVE).ready:
            self.upgrade_once(UnitTypeId.TEMPLARARCHIVE, UpgradeId.PSISTORMTECH)
        if self.structures(UnitTypeId.ROBOTICSBAY).ready:
            self.upgrade_once(UnitTypeId.ROBOTICSBAY, UpgradeId.EXTENDEDTHERMALLANCE)
        if self.units(UnitTypeId.ZEALOT).amount >= 4:
            self.upgrade_once(UnitTypeId.TWILIGHTCOUNCIL, UpgradeId.CHARGE)
        if self.units(UnitTypeId.STALKER).amount >= 6:
            self.upgrade_once(UnitTypeId.TWILIGHTCOUNCIL, UpgradeId.BLINKTECH)

    def normal_reserved_minerals(self):
        if self.workers.amount >= 20 and self.townhalls.amount < 2 and not self.pending_count(UnitTypeId.NEXUS):
            threats = self.enemy_units.filter(lambda u: not u.is_structure and (u.can_attack_ground or u.can_attack_air))
            if not any(threats.closer_than(20, base) for base in self.townhalls):
                return 400
        if self.structures(UnitTypeId.TWILIGHTCOUNCIL).ready and self.time >= 330:
            if not self.structures(UnitTypeId.TEMPLARARCHIVE) and not self.pending_count(UnitTypeId.TEMPLARARCHIVE):
                return 150
        if self.structures(UnitTypeId.TEMPLARARCHIVE).ready:
            if UpgradeId.PSISTORMTECH not in self.state.upgrades and not self.already_pending_upgrade(UpgradeId.PSISTORMTECH):
                return 200
        if self.time >= 240 and self.townhalls.ready.amount >= 2 and not self.structures(UnitTypeId.FORGE) and not self.pending_count(UnitTypeId.FORGE):
            return 150
        available_upgrades = getattr(self, "available_forge_upgrades", set())
        if available_upgrades:
            for upgrade in self.ground_upgrade_candidates():
                if upgrade in available_upgrades:
                    return self.calculate_cost(upgrade).minerals
        # Save for the next useful purchase rather than switching to cheap units.
        if self.structures(UnitTypeId.CYBERNETICSCORE).ready and self.townhalls.amount >= 2:
            if self.time >= 180 and not self.structures(UnitTypeId.ROBOTICSFACILITY) and not self.pending_count(UnitTypeId.ROBOTICSFACILITY):
                return 150
            if self.structures(UnitTypeId.ROBOTICSFACILITY).ready:
                if self.time >= 300 and not self.structures(UnitTypeId.ROBOTICSBAY) and not self.pending_count(UnitTypeId.ROBOTICSBAY):
                    return 150
                if self.time >= 270 and not self.structures(UnitTypeId.TWILIGHTCOUNCIL) and not self.pending_count(UnitTypeId.TWILIGHTCOUNCIL):
                    return 150
                if self.needs_more_robotics():
                    return 150
                if self.structures(UnitTypeId.ROBOTICSFACILITY).ready.idle.filter(
                        lambda b: b.is_powered and b.tag not in self.unit_tags_received_action):
                    kind = self.robotics_choice()
                    if kind is not None:
                        return self.calculate_cost(kind).minerals
        return 0

    def normal_gateway_unit(self, planned=None):
        planned = planned or {}
        armored, light, air = self.enemy_profile()
        stalkers = self.units(UnitTypeId.STALKER).amount + self.already_pending(UnitTypeId.STALKER) + planned.get(UnitTypeId.STALKER, 0)
        zealots = self.units(UnitTypeId.ZEALOT).amount + self.already_pending(UnitTypeId.ZEALOT) + planned.get(UnitTypeId.ZEALOT, 0)
        if armored >= 3 and armored > light and air * 2 < armored and stalkers >= 4:
            if zealots < max(4, stalkers) and self.can_afford(UnitTypeId.ZEALOT):
                return UnitTypeId.ZEALOT
        if self.structures(UnitTypeId.TEMPLARARCHIVE).ready:
            storm_ready = UpgradeId.PSISTORMTECH in self.state.upgrades or self.already_pending_upgrade(UpgradeId.PSISTORMTECH)
            templars = self.units(UnitTypeId.HIGHTEMPLAR).amount + self.already_pending(UnitTypeId.HIGHTEMPLAR) + planned.get(UnitTypeId.HIGHTEMPLAR, 0)
            if storm_ready and templars < 4 and self.units(UnitTypeId.STALKER).amount >= 6 and self.can_afford(UnitTypeId.HIGHTEMPLAR):
                return UnitTypeId.HIGHTEMPLAR
        if self.structures(UnitTypeId.CYBERNETICSCORE).ready:
            if self.can_afford(UnitTypeId.STALKER):
                return UnitTypeId.STALKER
            # Do not buy a 100-mineral zealot every time we approach the
            # 125-mineral stalker price while gas keeps accumulating.
            if self.vespene >= 50 and (not self.defense_priority or air >= 3):
                return None
        if self.can_afford(UnitTypeId.ZEALOT):
            return UnitTypeId.ZEALOT
        return None

    def control_army(self):
        army = self.main_army()
        if not army:
            return
        core = self.largest_group(army)
        center = core.center
        bases = self.townhalls.ready or self.townhalls
        home_base = bases.closest_to(center)
        home = home_base.position.towards(self.game_info.map_center, 4)
        threats = self.enemy_units.filter(lambda u: not u.is_structure and (u.can_attack_ground or u.can_attack_air))
        unsafe = threats | self.enemy_structures.filter(lambda u: u.can_attack_ground)
        safe_bases = bases.filter(lambda base: not unsafe.closer_than(23, base))
        retreat_home = (safe_bases.closest_to(center) if safe_bases else bases.closest_to(self.start_location)).position
        invaders = threats.filter(lambda u: any(u.distance_to(base) < 22 for base in self.townhalls))
        local_enemies = threats.closer_than(16, center)
        defenses = self.enemy_structures.filter(lambda u: u.can_attack_ground).closer_than(16, center)
        enemy_power = sum(self.combat_power(u) for u in local_enemies | defenses)
        power = sum(self.combat_power(u) for u in core)
        total_power = sum(self.combat_power(u) for u in army)
        invasion_power = 0
        defense_target = home
        critical_base = False
        if invaders:
            threatened_base = max(self.townhalls, key=lambda base:
                sum(self.combat_power(u) for u in invaders.closer_than(22, base)))
            raiders = invaders.closer_than(22, threatened_base)
            invasion_power = sum(self.combat_power(u) for u in raiders)
            defense_target = raiders.closest_to(threatened_base).position
            critical_base = (threatened_base.health_percentage < 0.35
                and bool(raiders.closer_than(10, threatened_base)))
        raid = getattr(self, "raid_position", None)
        full_defense = raid is not None or (self.stalker_mode and self.defense_priority and bool(invaders)) or needs_full_defense(invasion_power, total_power, critical_base)
        if raid is not None:
            defense_target = raid
        old_defenders = self.defender_tags
        self.defender_tags = set()
        if invaders and not full_defense:
            # Prefer nearby reinforcements, retaining at least 3/4 of the army.
            limit = min(8, max(2, len(army) // 4))
            candidates = sorted(army, key=lambda u: (
                u.distance_to(defense_target) + (0 if u.tag in old_defenders else 3)))
            response_power = 0
            for unit in candidates:
                if not any(unit.can_attack_air if e.is_flying else unit.can_attack_ground for e in invaders):
                    continue
                self.defender_tags.add(unit.tag)
                response_power += self.combat_power(unit)
                if response_power >= invasion_power * 1.4 or len(self.defender_tags) >= limit:
                    break
        supply = sum(self.calculate_supply_cost(u.type_id) for u in army)
        shields = sum(u.shield_percentage for u in core) / len(core)
        cohesion = len(core) / len(army)
        if self.army_plan.mode == "RETREAT":
            cohesion = army.closer_than(13, retreat_home).amount / len(army)
        advanced = bool(army.of_type({UnitTypeId.IMMORTAL, UnitTypeId.COLOSSUS}))
        changed = self.army_plan.update(self.time, supply, power, enemy_power,
            cohesion, shields, advanced, full_defense)
        self.pushing = self.army_plan.mode == "ATTACK"
        if changed:
            logger.info("Army mode={} reason={} supply={} cohesion={:.0%} strength={:.1f}/{:.1f}",
                self.army_plan.mode, self.army_plan.reason, supply, cohesion, power, enemy_power)
        mode = self.army_plan.mode
        if mode == "RETREAT":
            home = retreat_home
        if mode == "ATTACK":
            target = self.enemy_structures.closest_to(center).position if self.enemy_structures else self.enemy_start_locations[0]
            if center.distance_to(target) < 8 and not self.enemy_structures:
                locations = self.expansion_locations_list
                target = locations[int(self.time // 20) % len(locations)] if locations else target
        elif mode == "DEFEND":
            target = defense_target
        else:
            target = home
        outsiders = army.filter(lambda u: u.distance_to(center) > 13)
        self.reinforcement_tags.intersection_update(outsiders.tags)
        if outsiders and not self.reinforcement_tags:
            batch = self.largest_group(outsiders)
            if sum(self.calculate_supply_cost(u.type_id) for u in batch) >= 10:
                self.reinforcement_tags = batch.tags
        guard = None
        gap = getattr(self, "wall_gap", None)
        if mode == "DEFEND" and gap is not None and threats.closer_than(12, gap):
            zealots = army(UnitTypeId.ZEALOT)
            if zealots:
                guard = zealots.closest_to(gap)
        army_tags = army.tags
        self.advance_samples = {tag: sample for tag, sample in self.advance_samples.items() if tag in army_tags}
        self.advance_until = {tag: until for tag, until in self.advance_until.items() if until > self.time}
        for unit in army:
            if unit.tag in self.unit_tags_received_action:
                continue
            if guard is not None and unit.tag == guard.tag:
                if unit.distance_to(gap) > 0.75:
                    unit.move(gap)
                else:
                    unit.hold_position()
                continue
            if mode == "RETREAT":
                if unit.type_id != UnitTypeId.ZEALOT and unit.weapon_cooldown == 0:
                    pursuers = threats.closer_than(12, unit).filter(unit.target_in_range)
                    if pursuers:
                        unit.attack(min(pursuers, key=lambda enemy: enemy.health + enemy.shield))
                        continue
                if unit.distance_to(home) > 3:
                    unit.move(home)
                elif not unit.is_idle:
                    unit.hold_position()
                continue
            enemies = threats.filter(lambda e: unit.can_attack_air if e.is_flying else unit.can_attack_ground).closer_than(11, unit)
            if mode == "ATTACK" and unit.tag not in self.defender_tags and unit.distance_to(center) > 13:
                # Never send single fresh units all the way to the enemy main.
                rally = center.towards(home, 4) if unit.tag in self.reinforcement_tags else home
                if unit.distance_to(rally) > 4:
                    unit.move(rally)
                continue
            if enemies:
                nearest = enemies.closest_to(unit)
                if unit.type_id in {UnitTypeId.STALKER, UnitTypeId.COLOSSUS} and unit.weapon_cooldown > 0:
                    danger_distance = 7 if unit.type_id == UnitTypeId.STALKER else 8
                    if unit.shield_percentage < 0.55 and unit.distance_to(nearest) < danger_distance:
                        retreat = unit.position.towards(nearest.position, -2.5)
                        if self.in_pathing_grid(retreat):
                            unit.move(retreat)
                            continue
                in_range = enemies.filter(lambda enemy: unit.target_in_range(enemy))
                if in_range and unit.weapon_cooldown == 0:
                    unit.attack(min(in_range, key=lambda enemy: enemy.health + enemy.shield))
                    continue
            destination = defense_target if unit.tag in self.defender_tags else target
            if mode == "ATTACK" and unit.tag not in self.defender_tags and not enemies.filter(unit.target_in_range):
                if unit.tag in self.advance_until:
                    continue
                previous, since = self.advance_samples.setdefault(unit.tag, (unit.position, self.time))
                if unit.distance_to(previous) > 1.5:
                    self.advance_samples[unit.tag] = (unit.position, self.time)
                elif self.time - since >= 2 and unit.distance_to(destination) > 5:
                    forward = unit.position.towards(destination, 5)
                    dx, dy = forward.x - unit.position.x, forward.y - unit.position.y
                    side = 1 if unit.tag % 2 else -1
                    for offset in (side * 0.6, -side * 0.6, 0):
                        point = Point2((forward.x - dy * offset, forward.y + dx * offset))
                        if self.in_map_bounds(point) and self.in_pathing_grid(point):
                            unit.move(point)
                            self.advance_samples[unit.tag] = (unit.position, self.time)
                            self.advance_until[unit.tag] = self.time + 1.5
                            break
                    else:
                        unit.attack(destination)
                    continue
            if unit.tag in self.defender_tags or unit.is_idle or unit.distance_to(destination) > 5:
                unit.attack(destination)
        support = self.units(UnitTypeId.HIGHTEMPLAR)
        support_target = home if mode == "RETREAT" else center.towards(home, 5)
        for unit in support:
            if unit.tag in self.unit_tags_received_action:
                continue
            close_threats = threats.filter(lambda enemy: enemy.can_attack_air if unit.is_flying else enemy.can_attack_ground).closer_than(8, unit)
            destination = home if close_threats else support_target
            if unit.distance_to(destination) > 3:
                unit.move(destination)
