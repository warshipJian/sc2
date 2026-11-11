"""Shared Protoss macro behavior."""

from loguru import logger
from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2


GROUND_UPGRADES = (
    (UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1, UpgradeId.PROTOSSGROUNDWEAPONSLEVEL2, UpgradeId.PROTOSSGROUNDWEAPONSLEVEL3),
    (UpgradeId.PROTOSSGROUNDARMORSLEVEL1, UpgradeId.PROTOSSGROUNDARMORSLEVEL2, UpgradeId.PROTOSSGROUNDARMORSLEVEL3),
    (UpgradeId.PROTOSSSHIELDSLEVEL1, UpgradeId.PROTOSSSHIELDSLEVEL2, UpgradeId.PROTOSSSHIELDSLEVEL3),
)


class Macro:
    def shift_gas_workers(self):
        if self.vespene < 600 or self.minerals >= 200 or not self.mineral_field:
            return False
        excess = {gas.tag: gas.assigned_harvesters - 1 for gas in self.gas_buildings.ready}
        moved = 0
        for worker in self.workers:
            if worker.tag in self.used_builders or worker.tag in self.unit_tags_received_action:
                continue
            if worker.is_idle or (excess.get(worker.order_target, 0) > 0 and moved < 2 and not worker.is_carrying_resource):
                worker.gather(self.mineral_field.closest_to(worker))
                if not worker.is_idle:
                    excess[worker.order_target] -= 1
                    moved += 1
        return True

    def build_probes(self):
        target = min(80, self.townhalls.amount * 16 + self.gas_buildings.amount * 3)
        # Reserve the first pylon's minerals at 14 supply.
        if self.supply_used >= 14 and not self.structures(UnitTypeId.PYLON) and not self.pending_count(UnitTypeId.PYLON):
            return
        for nexus in self.townhalls.ready.idle:
            if self.workers.amount + self.already_pending(UnitTypeId.PROBE) >= target:
                break
            if self.supply_left >= 1 and self.can_afford(UnitTypeId.PROBE):
                if nexus.train(UnitTypeId.PROBE, can_afford_check=True):
                    self.supply_left -= 1

    async def on_building_construction_started(self, unit):
        if unit.type_id != UnitTypeId.ASSIMILATOR:
            return
        # Wait until the building exists before redirecting the probe: issuing
        # gather immediately could cancel its journey to place the assimilator.
        for kind, pos, _, worker_tag in self.build_orders:
            if kind != UnitTypeId.ASSIMILATOR or pos.distance_to(unit) >= 2:
                continue
            worker = self.workers.find_by_tag(worker_tag)
            bases = self.townhalls.ready
            if worker and bases and self.mineral_field:
                base = bases.closest_to(unit)
                minerals = self.mineral_field.closer_than(10, base)
                if minerals:
                    worker.gather(minerals.closest_to(worker))
            break

    async def on_building_construction_complete(self, unit):
        if unit.type_id != UnitTypeId.ASSIMILATOR or not unit.has_vespene:
            return
        # Fill the completed gas now, rather than waiting for a worker to idle
        # or for the next general redistribution pass.
        inbound = self.workers.filter(lambda w: w.order_target == unit.tag)
        missing = max(0, unit.ideal_harvesters - max(unit.assigned_harvesters, inbound.amount))
        mineral_tags = self.mineral_field.tags
        candidates = self.workers.filter(lambda w:
            w.tag not in self.used_builders
            and w.tag not in self.unit_tags_received_action
            and w.order_target != unit.tag
            and (w.is_idle or (w.is_collecting and w.order_target in mineral_tags))
            and not w.is_carrying_resource)
        for worker in candidates.sorted_by_distance_to(unit)[:missing]:
            worker.gather(unit)

    def upgrade_once(self, structure_type, upgrade):
        if upgrade in self.state.upgrades or self.already_pending_upgrade(upgrade):
            return False
        buildings = self.structures(structure_type).ready.idle.filter(
            lambda b: b.is_powered and b.tag not in self.unit_tags_received_action)
        if buildings and self.can_afford(upgrade):
            return buildings.first.research(upgrade, can_afford_check=True)
        return False

    async def advanced_tech(self):
        if not self.structures(UnitTypeId.CYBERNETICSCORE).ready:
            return
        if self.stalker_mode:
            await self.stalker_advanced_tech()
            return
        await self.normal_advanced_tech()

    def ground_upgrade_levels(self):
        done = getattr(self.state, "upgrades", set())
        return tuple(max((level for level, upgrade in enumerate(chain, 1) if upgrade in done), default=0)
                     for chain in GROUND_UPGRADES)

    def ground_upgrade_candidates(self):
        # Alternate attack and armor; level 2/3 also need the previous level
        # and a completed Twilight Council. Client abilities verify exact tech.
        for level in range(3):
            if level and not self.structures(UnitTypeId.TWILIGHTCOUNCIL).ready:
                continue
            for index, chain in enumerate(GROUND_UPGRADES):
                if index == 2 and not all(other[0] in self.state.upgrades for other in GROUND_UPGRADES[:2]):
                    continue
                upgrade = chain[level]
                if level and chain[level - 1] not in self.state.upgrades:
                    continue
                if upgrade not in self.state.upgrades and not self.already_pending_upgrade(upgrade):
                    yield upgrade

    async def manage_ground_upgrades(self):
        forges = self.structures(UnitTypeId.FORGE)
        if not forges:
            if self.time >= 240 and self.townhalls.ready.amount >= 2:
                await self.build_safely(UnitTypeId.FORGE)
            return
        pending = []
        for level in range(3):
            for name, chain in zip(("Attack", "Armor", "Shields"), GROUND_UPGRADES):
                if chain[level] not in self.state.upgrades and self.already_pending_upgrade(chain[level]):
                    pending.append(f"{name} {level + 1}")
        self.ground_research_label = ", ".join(pending) or "-"
        idle = forges.ready.idle.filter(lambda b:
            b.is_powered and b.tag not in self.unit_tags_received_action)
        self.available_forge_upgrades = set()
        if idle:
            abilities = await self.get_available_abilities(idle)
            candidates = list(self.ground_upgrade_candidates())
            ordered = set()
            for forge, available in zip(idle, abilities):
                for upgrade in candidates:
                    ability = self.game_data.upgrades[upgrade.value].research_ability
                    if ability is None or ability.exact_id not in available:
                        continue
                    self.available_forge_upgrades.add(upgrade)
                    if upgrade in ordered or not self.can_afford(upgrade):
                        continue
                    if forge.research(upgrade, can_afford_check=True):
                        ordered.add(upgrade)
                        self.available_forge_upgrades.discard(upgrade)
                        logger.info("Research started: {}", upgrade.name)
                        break
        # Parallel attack/armor once the economy can support a second Forge.
        if self.workers.amount >= 44 and self.minerals >= 500 and forges.amount < 2:
            await self.build_safely(UnitTypeId.FORGE)

    def update_enemy_composition(self):
        self.enemy_sightings = {tag: sighting for tag, sighting in self.enemy_sightings.items()
                                if sighting[2] > self.time and tag not in self.state.dead_units}
        for unit in self.enemy_units:
            if unit.is_visible and not unit.is_structure and unit.type_id not in {UnitTypeId.SCV, UnitTypeId.PROBE, UnitTypeId.DRONE}:
                self.enemy_sightings[unit.tag] = (unit.type_id, unit.is_flying, self.time + 60)

    def enemy_profile(self):
        sightings = getattr(self, "enemy_sightings", {}).values()
        armored = sum(kind in {UnitTypeId.IMMORTAL, UnitTypeId.STALKER, UnitTypeId.SIEGETANK, UnitTypeId.SIEGETANKSIEGED, UnitTypeId.ROACH}
                      for kind, _, _ in sightings)
        light = sum(kind in {UnitTypeId.ZEALOT, UnitTypeId.MARINE, UnitTypeId.ZERGLING, UnitTypeId.HYDRALISK, UnitTypeId.ADEPT}
                    for kind, _, _ in sightings)
        air = sum(flying for _, flying, _ in sightings)
        return armored, light, air

    def robotics_choice(self, planned=None):
        planned = planned or {}
        observer_target = 2 if self.townhalls.ready.amount >= 3 else 1
        if self.units(UnitTypeId.OBSERVER).amount + self.already_pending(UnitTypeId.OBSERVER) + planned.get(UnitTypeId.OBSERVER, 0) < observer_target:
            return UnitTypeId.OBSERVER
        if self.stalker_mode:
            return None
        armored, light, air = self.enemy_profile()
        if air >= 5 and air > armored + light:
            return None  # Save resources for units that can shoot up.
        colossus_limit = 5 if light >= 8 and light > armored and light > air else (1 if armored >= 4 and armored > light else 3)
        if self.structures(UnitTypeId.ROBOTICSBAY).ready:
            if self.units(UnitTypeId.COLOSSUS).amount + self.already_pending(UnitTypeId.COLOSSUS) + planned.get(UnitTypeId.COLOSSUS, 0) < colossus_limit:
                return UnitTypeId.COLOSSUS
        return UnitTypeId.IMMORTAL

    def desired_robotics_count(self):
        target = 1
        bases = self.townhalls.ready.amount
        if bases >= 2 and self.workers.amount >= 36 and self.vespene >= 100:
            target = 2
        if bases >= 3 and self.workers.amount >= 54 and self.vespene >= 250:
            target = 3
        if bases >= 4 and self.workers.amount >= 66 and self.minerals >= 1000 and self.vespene >= 500:
            target = 4
        return target

    def needs_more_robotics(self):
        if self.stalker_mode:
            return False
        armored, light, air = self.enemy_profile()
        if air >= 5 and air > armored + light:
            return False
        robos = self.structures(UnitTypeId.ROBOTICSFACILITY)
        total = robos.ready.amount + self.pending_count(UnitTypeId.ROBOTICSFACILITY)
        powered = robos.ready.filter(lambda b: b.is_powered)
        return (bool(powered) and total < self.desired_robotics_count()
                and (not powered.idle or self.minerals >= 500))

    async def manage_robotics_capacity(self):
        if self.needs_more_robotics():
            await self.build_safely(UnitTypeId.ROBOTICSFACILITY, max_pending=2)

    def reserved_minerals(self):
        if self.defense_priority:
            return 0
        if self.stalker_mode:
            return self.stalker_reserved_minerals() if self.structures(UnitTypeId.CYBERNETICSCORE).ready else 0
        return self.normal_reserved_minerals()

    async def produce_robotics(self):
        planned = {}
        for robo in self.structures(UnitTypeId.ROBOTICSFACILITY).ready.idle.filter(lambda b: b.is_powered):
            kind = self.robotics_choice(planned)
            if kind is None:
                break
            supply = self.calculate_supply_cost(kind)
            if self.supply_left >= supply and self.can_afford(kind):
                if robo.train(kind, can_afford_check=True):
                    self.supply_left -= supply
                    planned[kind] = planned.get(kind, 0) + 1

    async def build_pylons(self):
        if self.supply_cap >= 200:
            return
        first = not self.structures(UnitTypeId.PYLON)
        gates = self.structures.of_type({UnitTypeId.GATEWAY, UnitTypeId.WARPGATE}).amount
        threshold = max(5, 2 * gates + 2 * self.townhalls.amount)
        pending = self.pending_count(UnitTypeId.PYLON)
        # Include supply under construction; allow several pylons at high output.
        need_supply = self.supply_left + 8 * pending <= threshold
        unpowered = self.structures.filter(lambda b:
            b.type_id in {UnitTypeId.GATEWAY, UnitTypeId.WARPGATE, UnitTypeId.CYBERNETICSCORE, UnitTypeId.ROBOTICSFACILITY, UnitTypeId.ROBOTICSBAY, UnitTypeId.TWILIGHTCOUNCIL, UnitTypeId.FORGE, UnitTypeId.TEMPLARARCHIVE}
            and b.is_ready and not b.is_powered)
        if (first and self.supply_used >= 14) or (not first and need_supply) or (unpowered and pending == 0):
            await self.build_safely(UnitTypeId.PYLON, max_pending=3)

    async def build_tech(self):
        gates = self.structures.of_type({UnitTypeId.GATEWAY, UnitTypeId.WARPGATE})
        if not gates:
            await self.build_safely(UnitTypeId.GATEWAY)
            return
        if gates.ready and not self.structures(UnitTypeId.CYBERNETICSCORE):
            await self.build_safely(UnitTypeId.CYBERNETICSCORE)
        if self.stalker_mode:
            if self.structures(UnitTypeId.CYBERNETICSCORE) and self.gate_count_with_pending() < 2:
                await self.build_safely(UnitTypeId.GATEWAY)
            if self.units(UnitTypeId.STALKER).amount < 4:
                return
        cores = self.structures(UnitTypeId.CYBERNETICSCORE).ready.idle.filter(lambda b: b.is_powered)
        if cores and UpgradeId.WARPGATERESEARCH not in self.state.upgrades:
            if not self.already_pending_upgrade(UpgradeId.WARPGATERESEARCH) and self.can_afford(UpgradeId.WARPGATERESEARCH):
                cores.first.research(UpgradeId.WARPGATERESEARCH, can_afford_check=True)

    def powered_gate_count(self):
        return self.structures.of_type({UnitTypeId.GATEWAY, UnitTypeId.WARPGATE}).ready.filter(lambda b: b.is_powered).amount

    def gate_count_with_pending(self):
        gates = self.structures.of_type({UnitTypeId.GATEWAY, UnitTypeId.WARPGATE})
        return max(gates.amount, gates.ready.amount + self.pending_count(UnitTypeId.GATEWAY))

    async def add_production(self):
        if self.stalker_mode and self.townhalls.amount < 2:
            return  # build_tech owns the two opening gateways
        if self.minerals - 150 < self.reserved_minerals():
            return
        if not self.structures(UnitTypeId.CYBERNETICSCORE).ready:
            return
        gates = self.structures.of_type({UnitTypeId.GATEWAY, UnitTypeId.WARPGATE})
        # Scale with income and bank instead of stopping at two gates per base.
        desired = min(12, max(2, self.townhalls.ready.amount * 2) + int(self.minerals // 500))
        for _ in range(3):
            pending = self.pending_count(UnitTypeId.GATEWAY)
            total = gates.ready.amount + pending
            if total >= desired or not self.can_afford(UnitTypeId.GATEWAY):
                break
            if not await self.build_safely(UnitTypeId.GATEWAY, max_pending=3):
                break

    async def build_gas(self):
        if self.vespene >= 500 and self.minerals < 300:
            return
        if not self.structures.of_type({UnitTypeId.GATEWAY, UnitTypeId.WARPGATE}):
            return
        desired = 1 if self.workers.amount < 20 else min(8, self.townhalls.ready.amount * 2)
        pending = self.pending_count(UnitTypeId.ASSIMILATOR)
        if self.gas_buildings.ready.amount + pending >= desired or pending >= 2:
            return
        if not self.can_afford(UnitTypeId.ASSIMILATOR):
            return
        for base in self.townhalls.ready:
            for geyser in self.vespene_geyser.closer_than(12, base):
                if self.gas_buildings.closer_than(1, geyser):
                    continue
                if any(kind == UnitTypeId.ASSIMILATOR and pos.distance_to(geyser) < 1 for kind, pos, _, _ in self.build_orders):
                    continue
                worker = self.pick_builder(geyser.position)
                if worker and worker.build_gas(geyser, can_afford_check=True):
                    self.remember_build(UnitTypeId.ASSIMILATOR, geyser.position, worker)
                    return

    def choose_gateway_unit(self, planned=None):
        if self.stalker_mode:
            return self.stalker_gateway_unit()
        return self.normal_gateway_unit(planned)

    async def produce_army(self):
        planned = {}
        if self.minerals < self.reserved_minerals():
            return
        for gate in self.structures(UnitTypeId.GATEWAY).ready.idle.filter(lambda b: b.is_powered):
            if UpgradeId.WARPGATERESEARCH in self.state.upgrades:
                gate(AbilityId.MORPH_WARPGATE)
                continue
            if self.minerals < self.reserved_minerals():
                break
            unit_type = self.choose_gateway_unit(planned)
            if unit_type and self.supply_left >= 2 and gate.train(unit_type, can_afford_check=True):
                self.supply_left -= 2
                planned[unit_type] = planned.get(unit_type, 0) + 1
        pylons = self.structures(UnitTypeId.PYLON).ready
        gates = self.structures(UnitTypeId.WARPGATE).ready.filter(lambda b: b.is_powered)
        if not pylons or not gates:
            return
        # Query all cooldowns at once, then spend on every available gate.
        abilities_by_gate = await self.get_available_abilities(gates)
        reserved_warps = []
        for gate, abilities in zip(gates, abilities_by_gate):
            if self.minerals < self.reserved_minerals():
                break
            unit_type = self.choose_gateway_unit(planned)
            if not unit_type or self.supply_left < 2:
                break
            ability = {
                UnitTypeId.STALKER: AbilityId.WARPGATETRAIN_STALKER,
                UnitTypeId.ZEALOT: AbilityId.WARPGATETRAIN_ZEALOT,
                UnitTypeId.HIGHTEMPLAR: AbilityId.WARPGATETRAIN_HIGHTEMPLAR,
            }[unit_type]
            if ability not in abilities:
                continue
            placed = False
            for pylon in pylons.sorted_by_distance_to(self.game_info.map_center):
                points = [Point2((pylon.position.x + dx, pylon.position.y + dy))
                    for dx in (-4, -2, 0, 2, 4) for dy in (-4, -2, 0, 2, 4)
                    if dx * dx + dy * dy <= 25
                    and all(Point2((pylon.position.x + dx, pylon.position.y + dy)).distance_to(pos) >= 2 for pos in reserved_warps)]
                if not points:
                    continue
                valid = await self.can_place(ability, points)
                for pos, allowed in zip(points, valid):
                    if allowed and gate.warp_in(unit_type, pos, can_afford_check=True):
                        self.supply_left -= 2
                        reserved_warps.append(pos)
                        planned[unit_type] = planned.get(unit_type, 0) + 1
                        placed = True
                        break
                if placed:
                    break

    async def expand(self):
        if self.stalker_mode and self.townhalls.amount < 2:
            if self.units(UnitTypeId.STALKER).amount < 4 or self.powered_gate_count() < 2:
                return
        if self.pending_count(UnitTypeId.NEXUS):
            return
        desired = 1
        if self.workers.amount >= 20:
            desired = 2
        if self.workers.amount >= 36 or (self.time > 240 and self.minerals >= 700):
            desired = 3
        if self.workers.amount >= 52 or (self.time > 420 and self.minerals >= 1000):
            desired = 4
        if self.time > 600 and self.minerals >= 1200:
            desired = 5
        threats = self.enemy_units.filter(lambda u: not u.is_structure and (u.can_attack_ground or u.can_attack_air))
        if any(threats.closer_than(20, base) for base in self.townhalls):
            return
        if self.townhalls.amount >= desired or not self.can_afford(UnitTypeId.NEXUS):
            return
        location = await self.get_next_expansion()
        if location is None:
            return
        pos = await self.find_placement(UnitTypeId.NEXUS, location, max_distance=2, random_alternative=False, placement_step=1)
        if pos:
            worker = self.pick_builder(pos)
            if worker and worker.build(UnitTypeId.NEXUS, pos, can_afford_check=True):
                self.remember_build(UnitTypeId.NEXUS, pos, worker)

    async def chrono_boost(self):
        targets = self.structures(UnitTypeId.CYBERNETICSCORE).ready.filter(lambda u: not u.is_idle)
        targets = targets | self.townhalls.ready.filter(lambda u: not u.is_idle)
        targets = targets | self.structures.of_type({UnitTypeId.GATEWAY, UnitTypeId.ROBOTICSFACILITY, UnitTypeId.ROBOTICSBAY, UnitTypeId.TWILIGHTCOUNCIL, UnitTypeId.FORGE, UnitTypeId.TEMPLARARCHIVE}).ready.filter(lambda u: not u.is_idle)
        if self.stalker_mode and UpgradeId.BLINKTECH not in self.state.upgrades:
            targets = sorted(targets, key=lambda u: u.type_id != UnitTypeId.TWILIGHTCOUNCIL)
        for nexus in self.townhalls.ready:
            if nexus.energy < 50:
                continue
            for target in targets:
                if not target.has_buff(BuffId.CHRONOBOOSTENERGYCOST) and await self.can_cast(nexus, AbilityId.EFFECT_CHRONOBOOSTENERGYCOST, target):
                    nexus(AbilityId.EFFECT_CHRONOBOOSTENERGYCOST, target)
                    break
