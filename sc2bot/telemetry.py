"""Per-match archives and game-time milestones."""

import json
import struct
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from mpyq import MPQArchive
from sc2.data import Race
from sc2.ids.upgrade_id import UpgradeId


def replay_opponent_race(path, player_id):
    """Read the assigned race when the live API omits it."""
    try:
        with path.open("rb") as stream:
            raw = MPQArchive(stream).read_file("replay.gamemetadata.json")
        metadata = json.loads(raw.decode("utf-8", errors="replace"))
        assigned = next((p.get("AssignedRace") for p in metadata.get("Players", [])
                         if p.get("PlayerID") == player_id), None)
        return {"Prot": "Protoss", "Terr": "Terran", "Zerg": "Zerg"}.get(assigned)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, struct.error):
        return None


class MatchArchive:
    def __init__(self, root, *, mode, requested_race, human, map_name, realtime, minutes):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        started = datetime.now().astimezone()
        prefix = f"{started:%Y%m%d-%H%M%S}-{mode}-vs-{requested_race.name}-"
        # Inherit the workspace ACL rather than using mkdtemp's private ACL.
        self.directory = root / f"{prefix}{uuid4().hex[:12]}"
        self.directory.mkdir()
        self.log_path = self.directory / "match.log"
        self.replay_path = self.directory / "match.SC2Replay"
        self.opponent_id = 1 if human else 2
        self.data = {
            "started_at": started.isoformat(),
            "mode": mode,
            "map": map_name,
            "opponent": "human" if human else "computer",
            "difficulty": None if human else "VeryHard",
            "requested_race": requested_race.name,
            "actual_race": None,
            "realtime": realtime,
            "limit_minutes": minutes or None,
            "status": "starting",
            "result": None,
            "milestones_seconds": {},
        }
        self.write()

    def write(self):
        temp = self.directory / "match.json.tmp"
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.directory / "match.json")

    def observe(self, bot):
        changed = self.data["status"] == "starting"
        if changed:
            self.data["status"] = "in_game"
        if self.data["actual_race"] is None:
            race = next((p.actual_race for p in bot.game_info.players
                         if p.id != bot.player_id and p.actual_race in (Race.Protoss, Race.Terran, Race.Zerg)), None)
            if race is not None:
                self.data["actual_race"] = race.name
                changed = True
        milestones = self.data["milestones_seconds"]
        checks = (
            ("second_base_started", bot.townhalls.amount >= 2),
            ("second_base_ready", bot.townhalls.ready.amount >= 2),
            ("blink_completed", UpgradeId.BLINKTECH in bot.state.upgrades),
            ("first_attack", bot.army_plan.mode == "ATTACK"),
        )
        for key, happened in checks:
            if happened and key not in milestones:
                milestones[key] = round(bot.time, 1)
                changed = True
        if changed:
            self.write()

    def finish(self, result=None, *, error=None):
        self.data["finished_at"] = datetime.now().astimezone().isoformat()
        self.data["status"] = ("interrupted" if isinstance(error, KeyboardInterrupt)
                               else "failed" if error is not None else "finished")
        self.data["result"] = result.name if result is not None else None
        self.data["replay_saved"] = self.replay_path.is_file()
        if self.data["replay_saved"] and self.data["actual_race"] is None:
            self.data["actual_race"] = replay_opponent_race(self.replay_path, self.opponent_id)
        if error is not None:
            self.data["error"] = str(error) or type(error).__name__
        self.write()
