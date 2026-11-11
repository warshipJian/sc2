# 星际 2 本地 Bot

Python 3.12 + burnysc2。机器人使用神族，支持对战游戏内置电脑，也支持在两个本地窗口里和人对战。

## 安装和运行

```powershell
python -m pip install -r requirements.txt
python p.py --help
python p.py --mode stalker -r Z --fast
python p.py --human -r P
```

将 `AbyssalReefLE.SC2Map` 放进星际 2 安装目录下的 `Maps` 文件夹。脚本会尝试从 Windows 注册表找到游戏；如果自动识别失败，可以在 PowerShell 设置 `$env:SC2PATH = 'E:\game\StarCraft II'` 后再运行。

- `--mode normal`（默认）：混兵、科技和集结推进。
- `--mode stalker`：双 BG 开局、闪烁追猎和观察者。
- `-r P/T/Z/R`：默认随机；加 `--human` 时指定自己的种族，否则指定电脑种族。电脑难度为 VeryHard。
- `--fast`：非实时测试；不能与 `--human` 一起用。
- `--minutes N`：限制游戏时间，适合快速验证启动和开局。

## 项目结构

- `p.py`：命令行入口。
- `sc2bot/match.py`：参数、安装路径、对手和对局启动。
- `sc2bot/telemetry.py`、`apm_stats.py`：逐局归档和指令频率。
- `sc2bot/sc2_compat.py`：处理客户端返回的未知指令。
- `sc2bot/protoss/bot.py`：神族机器人初始化和每帧调度。
- `sc2bot/protoss/macro.py`、`placement.py`、`combat.py`：运营、建筑选址和通用战斗行为。
- `sc2bot/protoss/protoss_normal.py`、`protoss_stalker.py`：两种打法的决策。
- `tests/`：无需启动星际 2 的自动测试，运行 `python -m unittest -q`。

## 对局记录

新对局会写到项目根目录的 `runs/<时间>-<打法>-vs-<对手种族>-<唯一后缀>/`。启动时终端会打印路径。

- `match.log`：该局日志。
- `match.SC2Replay`：游戏成功保存时生成的录像。
- `match.json`：对局配置、机器人视角结果、实际对手种族和关键游戏时间。若客户端未返回实际种族，或某个事件没有发生，对应值为 null 或没有该条目。

旧版保存在根目录的日志和录像仍可单独查看。
