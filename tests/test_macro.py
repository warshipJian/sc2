import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from sc2.position import Point2
from sc2.units import Units
from sc2bot.protoss.bot import ProtossBot, UnitTypeId, UpgradeId, AbilityId
from sc2bot.protoss.army_plan import ArmyPlan


class MacroTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = ProtossBot()
        self.bot.state = SimpleNamespace(game_loop=600 * 22.4, upgrades=set())
        self.bot.build_orders = []
        self.bot.used_builders = set()
        self.bot.worker_hits = {}
        self.bot.raid_position = None
        self.bot.reserved_build_positions = []
        self.bot.unit_tags_received_action = set()
        self.bot.structures = Units([], self.bot)
        self.bot.units = Units([], self.bot)
        self.bot.workers = Units([], self.bot)
        self.bot.enemy_units = Units([], self.bot)
        self.bot.townhalls = Units([], self.bot)
        self.bot.already_pending = lambda kind: 0
        self.bot.already_pending_upgrade = lambda kind: 0
        self.bot.can_afford = lambda kind: True
        self.bot.supply_left = 10
        self.bot.minerals = 1000
        self.bot.vespene = 0
        self.bot.game_info = SimpleNamespace(map_center=Point2((50, 50)))

    def unit(self, kind, x=20, y=20, tag=1, **extra):
        class FakeUnit(SimpleNamespace):
            def distance_to(self, other):
                return self.position.distance_to(other.position if hasattr(other, 'position') else other)
            def __call__(self, ability, target=None):
                self.cast_calls.append((ability, target))
                return True
            def __getitem__(self, index):
                return self.position[index]
        values = dict(type_id=kind, position=Point2((x, y)), position_tuple=(x, y), tag=tag,
                      is_ready=True, is_idle=True, is_powered=True,
                      is_flying=False, is_structure=False)
        values.update(extra)
        return FakeUnit(**values)

    async def test_low_gas_trains_zealots_in_every_gateway(self):
        calls = []
        self.bot.can_afford = lambda kind: kind == UnitTypeId.ZEALOT
        def train(kind, **kwargs):
            calls.append(kind)
            return True
        self.bot.structures = Units([
            self.unit(UnitTypeId.GATEWAY, tag=i, train=train) for i in range(3)
        ], self.bot)
        # Existing zealots must no longer impose the old four-unit cap.
        self.bot.units = Units([self.unit(UnitTypeId.ZEALOT, tag=i) for i in range(6)], self.bot)
        await self.bot.produce_army()
        self.assertEqual(calls, [UnitTypeId.ZEALOT] * 3)
        self.assertEqual(self.bot.supply_left, 4)

    async def test_stalkers_preferred_with_core_and_gas(self):
        calls = []
        def train(kind, **kwargs):
            calls.append(kind)
            return True
        self.bot.structures = Units([
            self.unit(UnitTypeId.GATEWAY, train=train),
            self.unit(UnitTypeId.CYBERNETICSCORE, tag=2),
        ], self.bot)
        await self.bot.produce_army()
        self.assertEqual(calls, [UnitTypeId.STALKER])

    async def test_save_125_minerals_instead_of_buying_another_zealot(self):
        self.bot.structures = Units([self.unit(UnitTypeId.CYBERNETICSCORE)], self.bot)
        self.bot.minerals = 100
        self.bot.vespene = 400
        self.bot.can_afford = lambda kind: kind == UnitTypeId.ZEALOT
        self.assertIsNone(self.bot.choose_gateway_unit())

    async def test_stalker_mode_never_falls_back_to_zealots(self):
        self.bot.mode = 'stalker'
        self.bot.structures = Units([self.unit(UnitTypeId.CYBERNETICSCORE)], self.bot)
        self.bot.can_afford = lambda kind: kind == UnitTypeId.ZEALOT
        self.assertIsNone(self.bot.choose_gateway_unit())
        self.bot.can_afford = lambda kind: True
        self.assertEqual(self.bot.choose_gateway_unit(), UnitTypeId.STALKER)

    async def test_stalker_mode_builds_twilight_before_robotics(self):
        self.bot.mode = 'stalker'
        self.bot.structures = Units([self.unit(UnitTypeId.CYBERNETICSCORE)] + [
            self.unit(UnitTypeId.GATEWAY, tag=i) for i in (2, 3)
        ], self.bot)
        self.bot.build_safely = AsyncMock()
        await self.bot.advanced_tech()
        self.bot.build_safely.assert_not_awaited()
        self.bot.units = Units([self.unit(UnitTypeId.STALKER, tag=i) for i in range(4)], self.bot)
        await self.bot.advanced_tech()
        self.bot.build_safely.assert_awaited_once_with(UnitTypeId.TWILIGHTCOUNCIL)

    async def test_stalker_opening_adds_second_gate_before_warpgate(self):
        self.bot.mode = 'stalker'
        self.bot.structures = Units([self.unit(UnitTypeId.GATEWAY),
            self.unit(UnitTypeId.CYBERNETICSCORE, tag=2, research=Mock())], self.bot)
        self.bot.build_safely = AsyncMock()
        await self.bot.build_tech()
        self.bot.build_safely.assert_awaited_once_with(UnitTypeId.GATEWAY)
        self.bot.structures(UnitTypeId.CYBERNETICSCORE).first.research.assert_not_called()

    async def test_stalker_opening_waits_for_army_before_expanding(self):
        self.bot.mode = 'stalker'
        self.bot.townhalls = Units([self.unit(UnitTypeId.NEXUS)], self.bot)
        self.bot.workers = Units([self.unit(UnitTypeId.PROBE, tag=i) for i in range(22)], self.bot)
        self.bot.get_next_expansion = AsyncMock()
        await self.bot.expand()
        self.bot.get_next_expansion.assert_not_awaited()

    async def test_first_wave_clear_enables_counterattack_and_expansion_reserve(self):
        self.bot.mode = 'stalker'
        self.bot.townhalls = Units([self.unit(UnitTypeId.NEXUS)], self.bot)
        self.bot.units = Units([self.unit(UnitTypeId.STALKER, tag=i) for i in range(4)], self.bot)
        self.bot.structures = Units([self.unit(UnitTypeId.CYBERNETICSCORE, tag=3)] + [
            self.unit(UnitTypeId.GATEWAY, tag=i) for i in (1, 2)], self.bot)
        self.bot.defense_priority_until = self.bot.time + 12
        self.bot.update_stalker_counterattack()
        self.assertFalse(self.bot.stalker_counterattack_window)
        self.bot.state.game_loop += 13 * 22.4
        self.bot.update_stalker_counterattack()
        self.assertTrue(self.bot.stalker_counterattack_window)
        self.assertEqual(self.bot.reserved_minerals(), 400)

    async def test_stalker_mode_saves_gas_for_blink_except_during_emergency(self):
        self.bot.mode = 'stalker'
        self.bot.units = Units([self.unit(UnitTypeId.STALKER, tag=i) for i in range(4)], self.bot)
        self.bot.structures = Units([self.unit(UnitTypeId.CYBERNETICSCORE),
            self.unit(UnitTypeId.TWILIGHTCOUNCIL, tag=2)], self.bot)
        self.bot.calculate_cost = lambda kind: SimpleNamespace(vespene=150 if kind == UpgradeId.BLINKTECH else 50)
        self.bot.vespene = 150
        self.assertIsNone(self.bot.choose_gateway_unit())
        self.bot.defense_priority_until = self.bot.time + 12
        self.assertEqual(self.bot.choose_gateway_unit(), UnitTypeId.STALKER)

    async def test_stalker_robotics_produces_only_observers(self):
        self.bot.mode = 'stalker'
        self.bot.townhalls = Units([self.unit(UnitTypeId.NEXUS, tag=i) for i in range(3)], self.bot)
        self.bot.units = Units([self.unit(UnitTypeId.OBSERVER)], self.bot)
        self.assertEqual(self.bot.robotics_choice(), UnitTypeId.OBSERVER)
        self.assertIsNone(self.bot.robotics_choice({UnitTypeId.OBSERVER: 1}))
        self.assertFalse(self.bot.needs_more_robotics())

    async def test_emergency_spends_reserved_minerals_on_affordable_units(self):
        self.bot.defense_priority_until = self.bot.time + 12
        self.bot.structures = Units([self.unit(UnitTypeId.CYBERNETICSCORE)], self.bot)
        self.bot.minerals = 100
        self.bot.vespene = 400
        self.bot.can_afford = lambda kind: kind == UnitTypeId.ZEALOT
        self.assertEqual(self.bot.reserved_minerals(), 0)
        self.assertEqual(self.bot.choose_gateway_unit(), UnitTypeId.ZEALOT)

    async def test_ground_attack_near_base_triggers_emergency_but_scout_does_not(self):
        self.bot.townhalls = Units([self.unit(UnitTypeId.NEXUS, 10, 10,
            health_percentage=1)], self.bot)
        self.bot.enemy_structures = Units([], self.bot)
        self.bot.combat_power = lambda unit: 5
        self.bot.enemy_units = Units([self.unit(UnitTypeId.PROBE, 12, 10,
            can_attack_ground=True)], self.bot)
        self.bot.update_defense_priority()
        self.assertFalse(self.bot.defense_priority)
        self.bot.enemy_units = Units([self.unit(UnitTypeId.STALKER, 12 + i, 10,
            tag=i, can_attack_ground=True) for i in range(2)], self.bot)
        self.bot.update_defense_priority()
        self.assertTrue(self.bot.defense_priority)

    async def test_worker_damage_detects_raid_without_visible_attacker(self):
        self.bot.townhalls = Units([self.unit(UnitTypeId.NEXUS, 10, 10)], self.bot)
        self.bot.enemy_structures = Units([], self.bot)
        for tag in range(3):
            worker = self.unit(UnitTypeId.PROBE, 12 + tag, 10, tag=tag)
            await self.bot.on_unit_took_damage(worker, 10)
        self.bot.update_defense_priority()
        self.assertEqual(self.bot.raid_position, Point2((12, 10)))
        self.assertTrue(self.bot.defense_priority)

    async def test_excess_gas_workers_shift_without_emptying_assimilator(self):
        self.bot.minerals, self.bot.vespene = 100, 800
        mineral = self.unit(UnitTypeId.MINERALFIELD, 10, 10)
        self.bot.mineral_field = Units([mineral], self.bot)
        self.bot.gas_buildings = Units([self.unit(UnitTypeId.ASSIMILATOR, tag=8, assigned_harvesters=2)], self.bot)
        workers = [self.unit(UnitTypeId.PROBE, tag=i, is_idle=False,
            order_target=8, is_carrying_resource=False, gather=Mock()) for i in range(2)]
        self.bot.workers = Units(workers, self.bot)
        self.assertTrue(self.bot.shift_gas_workers())
        workers[0].gather.assert_called_once_with(mineral)
        workers[1].gather.assert_not_called()

    async def test_exact_wall_position_is_preserved(self):
        pos = Point2((30.5, 20.5))
        self.bot.wall_slots = [pos, Point2((33.5, 23.5))]
        self.bot.can_place_single = AsyncMock(return_value=True)
        built = []
        worker = self.unit(UnitTypeId.PROBE, tag=8)
        worker.build = lambda kind, target, **kwargs: built.append(target) or True
        self.bot.pick_builder = lambda _: worker
        self.assertTrue(await self.bot.try_wall_building(UnitTypeId.GATEWAY))
        self.assertEqual(built, [pos])

    async def test_robotics_detection_then_immortal_then_colossus(self):
        self.assertEqual(self.bot.robotics_choice(), UnitTypeId.OBSERVER)
        self.bot.units = Units([self.unit(UnitTypeId.OBSERVER)], self.bot)
        self.assertEqual(self.bot.robotics_choice(), UnitTypeId.IMMORTAL)
        self.bot.structures = Units([self.unit(UnitTypeId.ROBOTICSBAY)], self.bot)
        self.assertEqual(self.bot.robotics_choice(), UnitTypeId.COLOSSUS)

    async def test_two_base_economy_adds_second_busy_robotics(self):
        self.bot.townhalls = Units([self.unit(UnitTypeId.NEXUS, tag=i) for i in (1, 2)], self.bot)
        self.bot.workers = Units([self.unit(UnitTypeId.PROBE, tag=i) for i in range(36)], self.bot)
        self.bot.vespene = 200
        self.bot.structures = Units([self.unit(UnitTypeId.ROBOTICSFACILITY, is_idle=False)], self.bot)
        self.bot.build_safely = AsyncMock(return_value=True)
        await self.bot.manage_robotics_capacity()
        self.bot.build_safely.assert_awaited_once_with(UnitTypeId.ROBOTICSFACILITY, max_pending=2)
        self.bot.already_pending = lambda kind: 1 if kind == UnitTypeId.ROBOTICSFACILITY else 0
        self.assertFalse(self.bot.needs_more_robotics())

    async def test_multiple_robotics_do_not_duplicate_observer(self):
        calls = []
        def train(kind, **kwargs):
            calls.append(kind)
            return True
        self.bot.structures = Units([self.unit(UnitTypeId.ROBOTICSFACILITY,
            tag=i, train=train) for i in range(3)], self.bot)
        self.bot.supply_left = 20
        self.bot.calculate_supply_cost = lambda kind: 1 if kind == UnitTypeId.OBSERVER else 4
        await self.bot.produce_robotics()
        self.assertEqual(calls, [UnitTypeId.OBSERVER, UnitTypeId.IMMORTAL, UnitTypeId.IMMORTAL])

    async def test_parallel_colossus_orders_respect_composition_limit(self):
        calls = []
        def train(kind, **kwargs):
            calls.append(kind)
            return True
        self.bot.structures = Units([self.unit(UnitTypeId.ROBOTICSFACILITY,
            tag=i, train=train) for i in range(3)] + [self.unit(UnitTypeId.ROBOTICSBAY)], self.bot)
        self.bot.units = Units([self.unit(UnitTypeId.OBSERVER),
            self.unit(UnitTypeId.COLOSSUS, tag=20), self.unit(UnitTypeId.COLOSSUS, tag=21)], self.bot)
        self.bot.supply_left = 20
        self.bot.calculate_supply_cost = lambda kind: 6 if kind == UnitTypeId.COLOSSUS else 4
        await self.bot.produce_robotics()
        self.assertEqual(calls, [UnitTypeId.COLOSSUS, UnitTypeId.IMMORTAL, UnitTypeId.IMMORTAL])

    async def test_two_base_robotics_purchase_is_reserved(self):
        self.bot.structures = Units([self.unit(UnitTypeId.CYBERNETICSCORE)], self.bot)
        self.bot.townhalls = Units([self.unit(UnitTypeId.NEXUS, tag=i) for i in (1, 2)], self.bot)
        self.assertEqual(self.bot.reserved_minerals(), 150)

    async def test_ground_upgrade_queue_respects_previous_levels_and_twilight(self):
        candidates = list(self.bot.ground_upgrade_candidates())
        self.assertEqual(candidates, [UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1,
                                     UpgradeId.PROTOSSGROUNDARMORSLEVEL1])
        self.bot.state.upgrades = {UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1,
                                   UpgradeId.PROTOSSGROUNDARMORSLEVEL1}
        self.assertEqual(list(self.bot.ground_upgrade_candidates()), [UpgradeId.PROTOSSSHIELDSLEVEL1])
        self.bot.structures = Units([self.unit(UnitTypeId.TWILIGHTCOUNCIL)], self.bot)
        self.assertEqual(list(self.bot.ground_upgrade_candidates()), [
            UpgradeId.PROTOSSSHIELDSLEVEL1, UpgradeId.PROTOSSGROUNDWEAPONSLEVEL2, UpgradeId.PROTOSSGROUNDARMORSLEVEL2])
        self.bot.state.upgrades.update({UpgradeId.PROTOSSGROUNDWEAPONSLEVEL2,
                                        UpgradeId.PROTOSSGROUNDARMORSLEVEL2})
        self.assertEqual(list(self.bot.ground_upgrade_candidates()), [
            UpgradeId.PROTOSSSHIELDSLEVEL1, UpgradeId.PROTOSSGROUNDWEAPONSLEVEL3, UpgradeId.PROTOSSGROUNDARMORSLEVEL3])

    async def test_immortal_heavy_enemy_shifts_production(self):
        self.bot.enemy_sightings = {i: (UnitTypeId.IMMORTAL, False, 660) for i in range(5)}
        self.bot.units = Units([self.unit(UnitTypeId.OBSERVER)] + [
            self.unit(UnitTypeId.STALKER, tag=i + 2) for i in range(6)
        ] + [self.unit(UnitTypeId.COLOSSUS, tag=20)], self.bot)
        self.bot.structures = Units([self.unit(UnitTypeId.ROBOTICSBAY)], self.bot)
        self.assertEqual(self.bot.choose_gateway_unit(), UnitTypeId.ZEALOT)
        self.assertEqual(self.bot.robotics_choice(), UnitTypeId.IMMORTAL)

    async def test_air_heavy_enemy_pauses_ground_robotics(self):
        self.bot.enemy_sightings = {i: (UnitTypeId.VOIDRAY, True, 660) for i in range(6)}
        self.bot.units = Units([self.unit(UnitTypeId.OBSERVER)], self.bot)
        self.assertIsNone(self.bot.robotics_choice())

    async def test_ramp_corridor_rejects_buildings(self):
        self.bot.exit_corridor = [(Point2((10, 10)), Point2((20, 10)))]
        self.assertFalse(self.bot.safe_build_position(Point2((15, 12))))

    async def test_unaffordable_forge_upgrade_reserves_its_minerals(self):
        self.bot.structures = Units([self.unit(UnitTypeId.FORGE)], self.bot)
        self.bot.available_forge_upgrades = {UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1}
        self.bot.calculate_cost = lambda _: SimpleNamespace(minerals=150, vespene=150)
        self.assertEqual(self.bot.reserved_minerals(), 150)

    async def test_two_forges_research_attack_and_armor_in_parallel(self):
        calls = []
        def research(upgrade, **kwargs):
            calls.append(upgrade)
            return True
        self.bot.structures = Units([
            self.unit(UnitTypeId.FORGE, tag=i, research=research) for i in (1, 2)
        ], self.bot)
        self.bot.game_data = SimpleNamespace(upgrades={
            UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1.value: SimpleNamespace(
                research_ability=SimpleNamespace(exact_id=AbilityId.SMART)),
            UpgradeId.PROTOSSGROUNDARMORSLEVEL1.value: SimpleNamespace(
                research_ability=SimpleNamespace(exact_id=AbilityId.MOVE_MOVE)),
        })
        self.bot.get_available_abilities = AsyncMock(return_value=[
            [AbilityId.SMART], [AbilityId.MOVE_MOVE]])
        await self.bot.manage_ground_upgrades()
        self.assertEqual(calls, [UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1,
                                UpgradeId.PROTOSSGROUNDARMORSLEVEL1])

    async def test_client_rejected_upgrade_is_not_researched(self):
        forge = self.unit(UnitTypeId.FORGE, research=Mock())
        self.bot.structures = Units([forge], self.bot)
        self.bot.game_data = SimpleNamespace(upgrades={
            upgrade.value: SimpleNamespace(research_ability=SimpleNamespace(exact_id=AbilityId.SMART))
            for upgrade in (UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1, UpgradeId.PROTOSSGROUNDARMORSLEVEL1)})
        self.bot.get_available_abilities = AsyncMock(return_value=[[]])
        await self.bot.manage_ground_upgrades()
        forge.research.assert_not_called()

    async def test_storm_tech_enables_templar_production(self):
        self.bot.structures = Units([self.unit(UnitTypeId.TEMPLARARCHIVE)], self.bot)
        self.bot.state.upgrades = {UpgradeId.PSISTORMTECH}
        self.bot.units = Units([self.unit(UnitTypeId.STALKER, tag=i) for i in range(6)], self.bot)
        self.assertEqual(self.bot.choose_gateway_unit(), UnitTypeId.HIGHTEMPLAR)

    async def test_templar_casts_storm_and_suppresses_second_cast(self):
        self.bot.state.upgrades = {UpgradeId.PSISTORMTECH}
        self.bot.recent_storms = []
        templar = self.unit(UnitTypeId.HIGHTEMPLAR, 7, 7, energy=100, cast_calls=[])
        self.bot.units = Units([self.unit(UnitTypeId.STALKER, 0, 0), templar], self.bot)
        self.bot.enemy_units = Units([
            self.unit(UnitTypeId.MARINE, 10, 10),
            self.unit(UnitTypeId.MARINE, 10.5, 10),
            self.unit(UnitTypeId.MARINE, 10, 10.5),
        ], self.bot)
        self.bot.can_cast = AsyncMock(return_value=True)
        await self.bot.cast_combat_spells()
        self.assertEqual(templar.cast_calls[0][0], AbilityId.PSISTORM_PSISTORM)
        await self.bot.cast_combat_spells()
        self.assertEqual(len(templar.cast_calls), 1)

    async def test_low_gas_warps_zealots_at_distinct_positions(self):
        positions = []
        def warp(kind, pos, **kwargs):
            self.assertEqual(kind, UnitTypeId.ZEALOT)
            positions.append(pos)
            return True
        self.bot.can_afford = lambda kind: kind == UnitTypeId.ZEALOT
        self.bot.structures = Units([
            self.unit(UnitTypeId.PYLON),
            self.unit(UnitTypeId.WARPGATE, tag=2, warp_in=warp),
            self.unit(UnitTypeId.WARPGATE, tag=3, warp_in=warp),
        ], self.bot)
        self.bot.get_available_abilities = AsyncMock(return_value=[
            [AbilityId.WARPGATETRAIN_ZEALOT], [AbilityId.WARPGATETRAIN_ZEALOT]])
        async def placement(_, points):
            return [True] * len(points)
        self.bot.can_place = placement
        await self.bot.produce_army()
        self.assertEqual(len(positions), 2)
        self.assertGreaterEqual(positions[0].distance_to(positions[1]), 2)

    async def test_by_is_requested_after_ready_gateway(self):
        self.bot.structures = Units([self.unit(UnitTypeId.GATEWAY)], self.bot)
        self.bot.build_safely = AsyncMock(return_value=True)
        await self.bot.build_tech()
        self.bot.build_safely.assert_awaited_once_with(UnitTypeId.CYBERNETICSCORE)

    async def test_large_bank_adds_multiple_gateways(self):
        self.bot.structures = Units([self.unit(UnitTypeId.CYBERNETICSCORE)], self.bot)
        self.bot.townhalls = Units([self.unit(UnitTypeId.NEXUS)], self.bot)
        self.bot.build_safely = AsyncMock(return_value=True)
        await self.bot.add_production()
        self.assertEqual(self.bot.build_safely.await_count, 3)

    async def test_travelling_builder_prevents_duplicate_by(self):
        self.bot.build_orders = [(UnitTypeId.CYBERNETICSCORE, Point2((20, 20)), 615, 9)]
        self.assertEqual(self.bot.pending_count(UnitTypeId.CYBERNETICSCORE), 1)
        self.assertFalse(await self.bot.build_safely(UnitTypeId.CYBERNETICSCORE))

    async def test_builders_are_not_reused(self):
        workers = [self.unit(UnitTypeId.PROBE, tag=i, is_collecting=True) for i in (1, 2)]
        self.bot.workers = Units(workers, self.bot)
        first = self.bot.pick_builder(Point2((20, 20)))
        self.bot.remember_build(UnitTypeId.GATEWAY, Point2((20, 20)), first)
        second = self.bot.pick_builder(Point2((20, 20)))
        self.assertNotEqual(first.tag, second.tag)

    def prepare_observer_scout(self):
        observer = self.unit(UnitTypeId.OBSERVER, 5, 5, tag=99, is_flying=True, move=Mock())
        self.bot.units = Units([observer], self.bot)
        self.bot.townhalls = Units([self.unit(UnitTypeId.NEXUS, 0, 0)], self.bot)
        self.bot.enemy_structures = Units([], self.bot)
        self.bot.army_plan = ArmyPlan()
        self.bot.observer_scout_tag = observer.tag
        self.bot.observer_scout_started = self.bot.time
        self.bot.observer_scout_route = [Point2((80, 80)), Point2((60, 70))]
        self.bot.observer_scout_index = 0
        self.bot.observer_scout_done = False
        return observer

    async def test_first_observer_follows_scout_route_without_an_army(self):
        observer = self.prepare_observer_scout()
        self.bot.control_observers()
        observer.move.assert_called_once_with(Point2((80, 80)))

    async def test_observer_avoids_visible_anti_air(self):
        observer = self.prepare_observer_scout()
        turret = self.unit(UnitTypeId.MISSILETURRET, 8, 5, can_attack_air=True,
                           is_detector=True, air_range=7, detect_range=11)
        self.bot.enemy_structures = Units([turret], self.bot)
        self.bot.game_info.map_size = Point2((100, 100))
        self.bot.control_observers()
        destination = observer.move.call_args.args[0]
        self.assertGreater(destination.distance_to(turret.position), observer.distance_to(turret))

    async def test_completed_scout_returns_to_army(self):
        observer = self.prepare_observer_scout()
        self.bot.observer_scout_index = 2
        self.bot.control_observers()
        self.assertTrue(self.bot.observer_scout_done)
        observer.move.assert_called_once_with(Point2((0, 0)))

    async def test_assimilator_builder_returns_to_minerals_after_placement(self):
        gas = self.unit(UnitTypeId.ASSIMILATOR, 5, 5, tag=90, is_ready=False)
        worker = self.unit(UnitTypeId.PROBE, 5, 5, tag=8, gather=Mock())
        mineral = self.unit(UnitTypeId.MINERALFIELD, 2, 2, tag=70)
        self.bot.workers = Units([worker], self.bot)
        self.bot.townhalls = Units([self.unit(UnitTypeId.NEXUS, 0, 0)], self.bot)
        self.bot.mineral_field = Units([mineral], self.bot)
        self.bot.build_orders = [(UnitTypeId.ASSIMILATOR, gas.position, 615, 8)]
        await self.bot.on_building_construction_started(gas)
        worker.gather.assert_called_once_with(mineral)

    async def test_completed_gas_fills_only_vacancies_from_mineral_workers(self):
        gas = self.unit(UnitTypeId.ASSIMILATOR, 5, 5, tag=90,
            has_vespene=True, ideal_harvesters=3, assigned_harvesters=1)
        workers = [self.unit(UnitTypeId.PROBE, tag=i, is_idle=False,
            is_collecting=True, order_target=70, is_carrying_resource=False,
            gather=Mock()) for i in range(4)]
        # This probe is already working at another gas and must stay there.
        other_gas_worker = self.unit(UnitTypeId.PROBE, tag=5, is_idle=False,
            is_collecting=True, order_target=91, is_carrying_resource=False, gather=Mock())
        self.bot.workers = Units(workers + [other_gas_worker], self.bot)
        self.bot.mineral_field = Units([self.unit(UnitTypeId.MINERALFIELD, tag=70)], self.bot)
        await self.bot.on_building_construction_complete(gas)
        self.assertEqual(sum(worker.gather.call_count for worker in workers), 2)
        other_gas_worker.gather.assert_not_called()

    async def test_build_lease_clears_when_structure_appears(self):
        self.bot.build_orders = [(UnitTypeId.GATEWAY, Point2((20, 20)), 615, 9)]
        self.bot.structures = Units([self.unit(UnitTypeId.GATEWAY, is_ready=False)], self.bot)
        self.bot.refresh_build_orders()
        self.assertEqual(self.bot.build_orders, [])

    async def test_unplaceable_by_requests_more_power_space(self):
        self.bot.placement_blocked = False
        self.bot.structures = Units([self.unit(UnitTypeId.PYLON)], self.bot)
        self.bot.safe_build_position = lambda _: True
        async def cannot_place(_, positions):
            return [False] * len(positions)
        self.bot.can_place = cannot_place
        result = await self.bot.build_safely(UnitTypeId.CYBERNETICSCORE)
        self.assertFalse(result)
        self.assertTrue(self.bot.placement_blocked)

    async def test_high_income_allows_two_supply_pylons_in_flight(self):
        self.bot.structures = Units([self.unit(UnitTypeId.PYLON)] + [
            self.unit(UnitTypeId.GATEWAY, tag=i + 2) for i in range(8)
        ], self.bot)
        self.bot.supply_cap = 100
        self.bot.supply_used = 98
        self.bot.supply_left = 2
        self.bot.already_pending = lambda kind: 1 if kind == UnitTypeId.PYLON else 0
        self.bot.build_safely = AsyncMock(return_value=True)
        await self.bot.build_pylons()
        self.bot.build_safely.assert_awaited_once_with(UnitTypeId.PYLON, max_pending=3)

    async def test_large_bank_opens_third_without_eight_stalkers(self):
        self.bot.townhalls = Units([self.unit(UnitTypeId.NEXUS, tag=i) for i in (1, 2)], self.bot)
        self.bot.workers = Units([self.unit(UnitTypeId.PROBE, tag=i) for i in range(24)], self.bot)
        location = Point2((60, 60))
        self.bot.get_next_expansion = AsyncMock(return_value=location)
        self.bot.find_placement = AsyncMock(return_value=location)
        built = []
        worker = self.unit(UnitTypeId.PROBE, tag=99)
        worker.build = lambda kind, pos, **kwargs: built.append(kind) or True
        self.bot.pick_builder = lambda _: worker
        await self.bot.expand()
        self.assertEqual(built, [UnitTypeId.NEXUS])


if __name__ == '__main__':
    unittest.main()
