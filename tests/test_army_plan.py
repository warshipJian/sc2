import unittest

from sc2bot.protoss.army_plan import ArmyPlan, storm_target, needs_full_defense


class ArmyPlanTests(unittest.TestCase):
    def update(self, plan, **changes):
        args = dict(now=400, army_supply=44, power=60, enemy_power=0,
                    cohesion=0.9, shields=0.9, advanced=True)
        args.update(changes)
        plan.update(**args)

    def test_six_units_do_not_trigger_a_late_suicide_attack(self):
        plan = ArmyPlan()
        self.update(plan, now=700, army_supply=12)
        self.assertEqual(plan.mode, 'RALLY')

    def test_scattered_army_must_assemble_first(self):
        plan = ArmyPlan()
        self.update(plan, cohesion=0.5)
        self.assertEqual(plan.mode, 'RALLY')

    def test_assembled_army_attacks(self):
        plan = ArmyPlan()
        self.update(plan)
        self.assertEqual(plan.mode, 'ATTACK')

    def test_retreat_when_outmatched(self):
        plan = ArmyPlan(mode='ATTACK', peak_power=60)
        self.update(plan, enemy_power=100)
        self.assertEqual(plan.mode, 'ATTACK')
        self.update(plan, now=402, enemy_power=100)
        self.assertEqual(plan.mode, 'RETREAT')
        self.assertEqual(plan.retreat_until, 427)

    def test_heavy_losses_trigger_retreat(self):
        plan = ArmyPlan(mode='ATTACK', peak_supply=70, last_enemy_time=399)
        self.update(plan, army_supply=44)
        self.assertEqual(plan.mode, 'RETREAT')

    def test_spread_out_formation_is_not_mistaken_for_losses(self):
        plan = ArmyPlan(mode='ATTACK', peak_power=100, peak_supply=44)
        self.update(plan, power=45, enemy_power=5)
        self.assertEqual(plan.mode, 'ATTACK')

    def test_slightly_stronger_enemy_does_not_trigger_retreat(self):
        plan = ArmyPlan(mode='ATTACK')
        self.update(plan, enemy_power=65)
        self.update(plan, now=410, enemy_power=65)
        self.assertEqual(plan.mode, 'ATTACK')

    def test_brief_threat_resets_retreat_confirmation(self):
        plan = ArmyPlan(mode='ATTACK')
        self.update(plan, enemy_power=100)
        self.update(plan, now=401, enemy_power=5)
        self.update(plan, now=402, enemy_power=100)
        self.assertEqual(plan.mode, 'ATTACK')

    def test_small_harassment_does_not_pull_main_army(self):
        self.assertFalse(needs_full_defense(4, 80))
        plan = ArmyPlan(mode='ATTACK')
        self.update(plan, base_threat=needs_full_defense(4, 80))
        self.assertEqual(plan.mode, 'ATTACK')

    def test_large_invasion_or_critical_base_requires_full_defense(self):
        self.assertTrue(needs_full_defense(30, 80))
        self.assertTrue(needs_full_defense(4, 80, critical_base=True))
        self.assertFalse(needs_full_defense(0, 80, critical_base=True))

    def test_reinforcements_do_not_cancel_retreat_timer(self):
        plan = ArmyPlan(mode='RETREAT', retreat_until=450)
        self.update(plan, now=420, power=100, army_supply=70)
        self.assertEqual(plan.mode, 'RETREAT')

    def test_wait_for_shields_before_leaving_retreat(self):
        plan = ArmyPlan(mode='RETREAT', retreat_until=390)
        self.update(plan, shields=0.3)
        self.assertEqual(plan.mode, 'RETREAT')

    def test_recovered_army_returns_to_rally_before_attacking(self):
        plan = ArmyPlan(mode='RETREAT', retreat_until=390)
        self.update(plan)
        self.assertEqual(plan.mode, 'RALLY')

    def test_fog_does_not_trigger_immediate_rematch_with_weaker_army(self):
        plan = ArmyPlan(mode='ATTACK', peak_power=60)
        self.update(plan, enemy_power=100)
        self.update(plan, now=402, enemy_power=100)
        self.update(plan, now=428, enemy_power=0)
        self.assertEqual(plan.mode, 'RALLY')
        self.update(plan, now=429, power=75, enemy_power=0)
        self.assertEqual(plan.mode, 'RALLY')
        self.update(plan, now=450, power=125, enemy_power=0)
        self.assertEqual(plan.mode, 'ATTACK')

    def test_base_under_attack_takes_priority(self):
        plan = ArmyPlan()
        self.update(plan, base_threat=True)
        self.assertEqual(plan.mode, 'DEFEND')


class StormTests(unittest.TestCase):
    enemies = [(10, 10), (10.5, 10), (10, 10.5), (20, 20)]

    def test_dense_cluster_is_targeted(self):
        self.assertIsNotNone(storm_target(self.enemies, [], []))

    def test_two_enemies_do_not_waste_storm(self):
        self.assertIsNone(storm_target(self.enemies[:2], [], []))

    def test_friendly_army_in_cluster_prevents_storm(self):
        self.assertIsNone(storm_target(self.enemies, [(10, 10)], []))

    def test_avoid_overlapping_storms(self):
        self.assertIsNone(storm_target(self.enemies, [], [(10, 10)]))


if __name__ == '__main__':
    unittest.main()
