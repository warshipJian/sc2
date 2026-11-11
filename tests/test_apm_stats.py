import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from sc2bot.apm_stats import CommandAPM
from sc2bot.protoss.bot import ProtossBot, AbilityId
from sc2.position import Point2


class APMTests(unittest.TestCase):
    def test_first_minute_and_average(self):
        stats = CommandAPM()
        stats.record(10, 30)
        self.assertEqual(stats.rates(30), (60, 60))

    def test_idle_time_reduces_rolling_rate_to_zero(self):
        stats = CommandAPM()
        stats.record(10, 30)
        self.assertEqual(stats.rates(70), (0, 1800 / 70))

    def test_window_uses_game_time_and_drops_old_events(self):
        stats = CommandAPM()
        stats.record(10, 30)
        stats.record(50, 20)
        self.assertEqual(stats.rates(80), (20, 37.5))

    def test_empty_start_is_zero(self):
        self.assertEqual(CommandAPM().rates(0), (0, 0))

    def test_overlay_omits_native_data(self):
        bot = ProtossBot()
        bot.state = SimpleNamespace(game_loop=30 * 22.4,
            score=SimpleNamespace(current_apm=0, current_effective_apm=0))
        bot.command_apm = CommandAPM()
        bot.command_apm.record(10, 30)
        bot.client = SimpleNamespace(debug_text_screen=Mock())
        bot.show_apm()
        text = bot.client.debug_text_screen.call_args.args[0]
        self.assertIn('Cmd APM: 60', text)
        self.assertNotIn('Client', text)

    def test_commands_are_filtered_and_batched(self):
        bot = ProtossBot()
        bot.state = SimpleNamespace(game_loop=60 * 22.4)
        bot.command_apm = CommandAPM()
        bot.show_apm = Mock()
        # Two units receiving the same move become one protocol command.
        key = (AbilityId.MOVE_MOVE, Point2((10, 10)), False, True)
        bot.actions = [SimpleNamespace(combining_tuple=key,
            unit=SimpleNamespace(tag=i), keep=True) for i in (1, 2)]
        bot.actions.append(SimpleNamespace(keep=False))
        bot.prevent_double_actions = lambda action: action.keep
        bot.record_apm()
        self.assertEqual(bot.command_apm.total, 1)
        self.assertEqual(bot.command_apm.rates(60), (1, 1))


if __name__ == '__main__':
    unittest.main()
