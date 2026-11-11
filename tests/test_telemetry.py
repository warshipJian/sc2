import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS

from sc2.data import Race, Result
from sc2.ids.upgrade_id import UpgradeId
from sc2bot.telemetry import MatchArchive


class ArchiveTests(unittest.TestCase):
    def make_archive(self, root):
        return MatchArchive(root, mode="stalker", requested_race=Race.Random, human=False,
                            map_name="AbyssalReefLE", realtime=False, minutes=0)

    def test_consecutive_matches_get_distinct_archives(self):
        with TemporaryDirectory() as tmp:
            a = self.make_archive(tmp)
            b = self.make_archive(tmp)
            self.assertNotEqual(a.directory, b.directory)
            self.assertTrue((a.directory / "match.json").is_file())

    def test_archive_records_actual_race_milestones_and_result(self):
        with TemporaryDirectory() as tmp:
            archive = self.make_archive(tmp)
            bot = NS(game_info=NS(players=[NS(id=2, actual_race=Race.Zerg)]), player_id=1,
                     townhalls=NS(amount=2, ready=NS(amount=1)),
                     state=NS(upgrades={UpgradeId.BLINKTECH}), army_plan=NS(mode="ATTACK"), time=200.4)
            archive.observe(bot)
            bot.townhalls.ready.amount = 2
            bot.time = 220
            archive.observe(bot)
            archive.replay_path.write_bytes(b"test replay")
            archive.finish(Result.Victory)
            data = json.loads((archive.directory / "match.json").read_text(encoding="utf-8"))
            self.assertEqual(data["actual_race"], "Zerg")
            self.assertEqual(data["result"], "Victory")
            self.assertTrue(data["replay_saved"])
            self.assertEqual(data["milestones_seconds"], {
                "second_base_started": 200.4, "second_base_ready": 220,
                "first_attack": 200.4, "blink_completed": 200.4,
            })

    def test_failed_match_retains_error_without_claiming_a_replay(self):
        with TemporaryDirectory() as tmp:
            archive = self.make_archive(tmp)
            archive.finish(error=RuntimeError("startup failed"))
            self.assertEqual(archive.data["status"], "failed")
            self.assertEqual(archive.data["error"], "startup failed")
            self.assertFalse(archive.data["replay_saved"])
