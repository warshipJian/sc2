import unittest
from types import SimpleNamespace
from sc2bot.protoss.protoss_ladder import LadderStrategy

class LadderTimingTests(unittest.TestCase):
    def test_waits_for_second_base_and_support_before_pushing(self):
        strategy = LadderStrategy()
        strategy.mode = "ladder"
        strategy.townhalls = SimpleNamespace(ready=SimpleNamespace(amount=1))
        self.assertFalse(strategy.ladder_ready_to_push(48, True))
        strategy.townhalls.ready.amount = 2
        self.assertFalse(strategy.ladder_ready_to_push(48, False))
        self.assertFalse(strategy.ladder_ready_to_push(40, True))
        self.assertTrue(strategy.ladder_ready_to_push(48, True))
