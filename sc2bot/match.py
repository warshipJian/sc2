"""Command-line entry point for local StarCraft II matches."""

import argparse
import os
from pathlib import Path

from loguru import logger
from sc2 import maps
from sc2.data import Race, Difficulty
from sc2.main import run_game
from sc2.player import Bot, Computer, Human

from sc2bot.protoss.bot import ProtossBot
from sc2bot.telemetry import MatchArchive

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true", help="Run without real-time pacing")
    parser.add_argument("--minutes", type=int, default=0, help="Optional game-time limit")
    parser.add_argument("--human", action="store_true", help="Play against the bot in a second local SC2 window")
    parser.add_argument("--mode", choices=("normal", "stalker"), default="normal", help="Bot strategy: normal or blink stalker harassment")
    parser.add_argument("--race", "-r", type=str.upper, choices=("P", "T", "Z", "R"),
                        default="R", help="Your race with --human, otherwise computer race: P/T/Z/R (default R)")
    args = parser.parse_args()
    if args.human and args.fast:
        parser.error("--human requires real-time play; remove --fast")
    # Battle.net may install SC2 outside the library's default directory.
    if os.name == "nt" and "SC2PATH" not in os.environ:
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\StarCraft II",
            ) as key:
                install_path, _ = winreg.QueryValueEx(key, "InstallLocation")
            if (Path(install_path) / "Versions").is_dir():
                os.environ["SC2PATH"] = install_path
        except OSError:
            pass

    try:
        game_map = maps.get("AbyssalReefLE")
    except (KeyError, FileNotFoundError):
        from sc2.paths import Paths

        raise SystemExit(
            f"Missing map: AbyssalReefLE. Place AbyssalReefLE.SC2Map in {Paths.MAPS}"
        ) from None

    race = {"P": Race.Protoss, "T": Race.Terran, "Z": Race.Zerg, "R": Race.Random}[args.race]
    archive = MatchArchive(Path(__file__).resolve().parent.parent / "runs", mode=args.mode,
                           requested_race=race, human=args.human, map_name=game_map.name,
                           realtime=not args.fast, minutes=args.minutes)
    sink = logger.add(archive.log_path, encoding="utf-8", level="INFO")
    try:
        logger.info("Match archive: {}", archive.directory)
        logger.info("Bot strategy: {}", args.mode)
        bot = Bot(Race.Protoss, ProtossBot(mode=args.mode, telemetry=archive))
        if args.human:
            players = [Human(race, name="You"), bot]
            logger.info("Starting two local windows. Play in the window without the bot APM overlay.")
        else:
            players = [bot, Computer(race, Difficulty.VeryHard)]
        result = run_game(game_map, players, realtime=not args.fast,
                          game_time_limit=args.minutes * 60 if args.minutes else None,
                          save_replay_as=str(archive.replay_path))
        archive.finish(result[1] if args.human else result)
    except (Exception, KeyboardInterrupt) as error:
        logger.exception("Match interrupted or failed")
        archive.finish(error=error)
        raise
    finally:
        logger.remove(sink)


if __name__ == "__main__":
    main()
