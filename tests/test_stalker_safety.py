import unittest

from sc2.position import Point2
from sc2bot.protoss.protoss_stalker import safer_stalker_step


class StalkerSafetyTests(unittest.TestCase):
    def test_step_avoids_second_enemy_instead_of_retreating_straight_home(self):
        pos, home = Point2((10, 10)), Point2((0, 10))
        threats = [(Point2((13, 10)), 5), (Point2((6, 10)), 5)]
        step = safer_stalker_step(pos, home, threats, lambda _: True)
        self.assertIsNotNone(step)
        self.assertNotEqual(step.y, 10)

    def test_no_step_when_out_of_range_or_terrain_blocks_all_options(self):
        pos, home = Point2((10, 10)), Point2((0, 10))
        self.assertIsNone(safer_stalker_step(pos, home, [], lambda _: True))
        self.assertIsNone(safer_stalker_step(pos, home, [(pos, 5)], lambda _: False))
