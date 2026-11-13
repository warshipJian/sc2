"""Protoss match lifecycle and strategy scheduling."""

from sc2.bot_ai import BotAI
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.ids.ability_id import AbilityId

from loguru import logger

from sc2bot.sc2_compat import install_order_compatibility
from sc2bot.protoss.army_plan import ArmyPlan
from sc2bot.protoss.combat import Combat
from sc2bot.protoss.macro import Macro
from sc2bot.protoss.placement import Placement
from sc2bot.protoss.protoss_normal import NormalStrategy
from sc2bot.protoss.protoss_ladder import LadderStrategy
from sc2bot.protoss.protoss_stalker import StalkerStrategy
from sc2bot.apm_stats import CommandAPM
from sc2.action import combine_actions


class ProtossBot(StalkerStrategy, LadderStrategy, NormalStrategy, Macro, Placement, Combat, BotAI):
    def __init__(self, mode="normal", telemetry=None):
        super().__init__()
        self.mode = mode
        self.telemetry = telemetry
        self.harass_target = None
        self.harass_avoid = {}
        self.harass_retry = 0
        self.stalker_wave_seen = False
        self.stalker_counter_until = None

    @property
    def stalker_mode(self):
        return self.mode == "stalker"

    async def on_start(self):
        install_order_compatibility(self.game_data)
        self.client.game_step = 4
        self.command_apm = CommandAPM(self.time)
        self.pushing = False
        self.army_plan = ArmyPlan()
        self.defense_priority_until = 0
        self.worker_hits = {}
        self.raid_position = None
        self.recent_storms = []
        self.reinforcement_tags = set()
        self.defender_tags = set()
        self.enemy_sightings = {}
        self.advance_samples = {}
        self.advance_until = {}
        self.observer_scout_tag = None
        self.observer_scout_route = []
        self.observer_scout_index = 0
        self.observer_scout_started = None
        self.observer_scout_done = False
        self.reserved_build_positions = []
        self.build_orders = []
        self.used_builders = set()
        self.placement_blocked = False
        self.last_macro_log = -30
        self.ground_research_label = "-"
        self.available_forge_upgrades = set()
        self.wall_pylon = None
        self.wall_slots = []
        self.wall_gap = None
        ramp = self.main_base_ramp
        if len(ramp.upper) in {2, 5} and len(ramp.upper2_for_ramp_wall) == 2:
            self.wall_pylon = ramp.protoss_wall_pylon
            self.wall_slots = sorted(ramp.protoss_wall_buildings, key=lambda pos: (pos.x, pos.y))
            self.wall_gap = ramp.protoss_wall_warpin
        self.exit_corridor = [
            (self.start_location, ramp.top_center),
            (ramp.top_center, ramp.bottom_center),
            (ramp.bottom_center, ramp.bottom_center.towards(self.game_info.map_center, 9)),
        ]
        # A one-unit plug is too narrow for the robotics army.
        self.wall_slots = []
        self.wall_gap = None
        logger.info("Wall plan: pylon={} buildings={} gap={}", self.wall_pylon, self.wall_slots, self.wall_gap)

    async def on_step(self, iteration: int):
        if not self.townhalls:
            self.record_apm()
            return
        # Macro twice per game second; combat remains responsive.
        if iteration % 3 == 0:
            self.refresh_build_orders()
            self.update_enemy_composition()
            self.update_defense_priority()
            if self.stalker_mode:
                self.update_stalker_counterattack()
            if iteration % 12 == 0:
                if not self.evacuate_raided_workers() and not self.shift_gas_workers():
                    await self.distribute_workers(resource_ratio=3)
            await self.build_pylons()
            # Unlock production before spending the last minerals on probes.
            await self.build_tech()
            if self.stalker_mode or self.mode == "ladder":
                await self.build_stalker_opening_battery()
            if self.defense_priority:
                await self.produce_army()
                await self.produce_robotics()
                if sum(self.calculate_supply_cost(u.type_id) for u in self.main_army()) >= 30:
                    self.build_probes()
            else:
                await self.build_gas()
                await self.protect_expansion()
                if self.stalker_mode:
                    await self.advanced_tech()
                await self.expand()
                self.build_probes()
                if not self.stalker_mode:
                    await self.advanced_tech()
                if not self.ladder_pvt_opening():
                    await self.manage_ground_upgrades()
                    await self.manage_robotics_capacity()
                await self.produce_robotics()
                await self.produce_army()
                await self.add_production()
            if self.placement_blocked and self.pending_count(UnitTypeId.PYLON) < 2:
                await self.build_safely(UnitTypeId.PYLON, max_pending=2)
            await self.chrono_boost()
            self.log_macro()
        if iteration % 3 == 0:
            await self.cast_combat_spells()
        if iteration % 2 == 0:
            hold_opening = (UpgradeId.BLINKTECH not in self.state.upgrades
                            and self.townhalls.amount == 1 and self.raid_position is None)
            if self.stalker_mode and (not self.defense_priority or hold_opening):
                self.control_stalker_harass()
            else:
                self.control_army()
            self.control_observers()
            if self.ladder_pvt:
                self.control_ladder_prism()
        self.record_apm()
        if self.telemetry is not None:
            self.telemetry.observe(self)


    def record_apm(self):
        # Match the library's duplicate filtering and per-command batching.
        # These are submitted commands, not confirmed successful game actions.
        commands = [action for action in self.actions if self.prevent_double_actions(action)]
        count = sum(1 for _ in combine_actions(commands))
        self.command_apm.record(self.time, count)
        self.show_apm()

    def show_apm(self):
        recent, average = self.command_apm.rates(self.time)
        attack, armor, shields = self.ground_upgrade_levels()
        research = getattr(self, "ground_research_label", "-")
        self.client.debug_text_screen(
            f"Cmd APM: {recent:.0f} | Avg: {average:.0f}\nAtk/Def/Sh: {attack}/{armor}/{shields} | Upg: {research}",
            (0.015, 0.065), color=(255, 255, 255), size=12,
        )


    def log_macro(self):
        if self.time - self.last_macro_log < 30:
            return
        self.last_macro_log = self.time
        recent, average = self.command_apm.rates(self.time)
        logger.info("Bot command APM(60s)={:.0f} average={:.0f}", recent, average)
        gates = self.structures.of_type({UnitTypeId.GATEWAY, UnitTypeId.WARPGATE})
        logger.info("Macro {:.0f}s: minerals={} gas={} supply={}/{} probes={} bases={} BY={} gates={} powered={} pending_BG={}",
            self.time, self.minerals, self.vespene, self.supply_used, self.supply_cap,
            self.workers.amount, self.townhalls.amount,
            self.structures(UnitTypeId.CYBERNETICSCORE).amount, gates.amount,
            gates.ready.filter(lambda b: b.is_powered).amount, self.pending_count(UnitTypeId.GATEWAY))
        logger.info("Army: zealots={} stalkers={} immortals={} colossi={} observers={} robo={} bay={} upgrades={}",
            self.units(UnitTypeId.ZEALOT).amount, self.units(UnitTypeId.STALKER).amount,
            self.units(UnitTypeId.IMMORTAL).amount, self.units(UnitTypeId.COLOSSUS).amount,
            self.units(UnitTypeId.OBSERVER).amount, self.structures(UnitTypeId.ROBOTICSFACILITY).amount,
            self.structures(UnitTypeId.ROBOTICSBAY).amount, [u.name for u in self.state.upgrades])

        logger.info("Ground attack/armor/shields={}/{}/{} researching={}", *self.ground_upgrade_levels(), self.ground_research_label)
        logger.info("Templars={} storm={} army_mode={}", self.units(UnitTypeId.HIGHTEMPLAR).amount,
            UpgradeId.PSISTORMTECH in self.state.upgrades, self.army_plan.mode)
