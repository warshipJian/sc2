"""Two-gate expand into a supported two-base push."""

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.data import Race


class LadderStrategy:
    def control_ladder_prism(self):
        army = self.main_army()
        if not army:
            return
        center = self.largest_group(army).center
        home = self.townhalls.closest_to(center).position
        anti_air = (self.enemy_units | self.enemy_structures).filter(lambda unit: unit.can_attack_air)
        for prism in self.units(UnitTypeId.WARPPRISM):
            target = home if anti_air.closer_than(10, prism) else center.towards(home, 7, limit=True)
            if prism.distance_to(target) > 3:
                prism.move(target)

    @property
    def ladder_pvt(self):
        return self.mode == "ladder" and getattr(self, "enemy_race", None) == Race.Terran

    def ladder_can_expand(self):
        if self.ladder_pvt:
            return self.powered_gate_count() >= 1 and bool(self.gas_buildings.ready)
        return self.powered_gate_count() >= 2 and self.main_army().amount >= 4

    def ladder_ready_to_push(self, supply, advanced):
        if self.ladder_pvt and self.townhalls.amount < 3:
            return (self.townhalls.ready.amount >= 2 and supply >= 24
                    and UpgradeId.BLINKTECH in self.state.upgrades
                    and bool(self.units(UnitTypeId.WARPPRISM)))
        return self.townhalls.ready.amount >= 2 and supply >= 44 and advanced

    def ladder_pvt_opening(self):
        return self.ladder_pvt and self.townhalls.amount < 3

    async def ladder_advanced_tech(self):
        if self.townhalls.amount < 2:
            return
        if self.ladder_pvt and self.townhalls.amount < 3:
            twilight = UnitTypeId.TWILIGHTCOUNCIL
            if not self.structures(twilight):
                await self.build_safely(twilight)
                return
            self.upgrade_once(twilight, UpgradeId.BLINKTECH)
            if not self.structures(UnitTypeId.ROBOTICSFACILITY):
                await self.build_safely(UnitTypeId.ROBOTICSFACILITY)
            return
        if not self.structures(UnitTypeId.ROBOTICSFACILITY):
            await self.build_safely(UnitTypeId.ROBOTICSFACILITY)
            return
        await self.normal_advanced_tech()
