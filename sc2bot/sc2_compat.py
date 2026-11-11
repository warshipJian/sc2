"""Tolerate observed orders newer than burnysc2's generated ability IDs."""

from loguru import logger
from s2clientprotocol import data_pb2
from sc2.game_data import AbilityData
from sc2.ids.ability_id import AbilityId

KNOWN_ABILITY_IDS = frozenset(ability.value for ability in AbilityId)


class UnknownOrderAbility(AbilityData):
    """An observation placeholder, never an ability to send to the client.

    Keep an order rather than dropping it: dropping it would make a busy
    worker appear idle. NULL cannot match any building/training ability.
    The original number remains available on raw_id and the protobuf.
    """

    def __init__(self, game_data, raw_id):
        self._game_data = game_data
        self.raw_id = raw_id
        self._proto = data_pb2.AbilityData(
            ability_id=raw_id,
            button_name=f"UnknownObservedOrder_{raw_id}",
        )

    @property
    def id(self):
        return AbilityId.NULL_NULL

    @property
    def exact_id(self):
        return AbilityId.NULL_NULL


class OrderAbilityTable(dict):
    def __init__(self, game_data):
        super().__init__(game_data.abilities)
        self.game_data = game_data

    def __missing__(self, raw_id):
        # AbilityId._missing_ returns NULL rather than raising ValueError.
        if raw_id not in KNOWN_ABILITY_IDS:
            ability = UnknownOrderAbility(self.game_data, raw_id)
            self[raw_id] = ability
            logger.warning(
                "Client order ability {} is unknown to burnysc2; "
                "preserving it as a busy order without interpreting its action.",
                raw_id,
            )
            return ability
        # A missing KNOWN ability may indicate another problem. Do not hide it.
        raise KeyError(raw_id)


def install_order_compatibility(game_data):
    """Patch only this match's ability table; leave installed packages intact."""
    if not isinstance(game_data.abilities, OrderAbilityTable):
        game_data.abilities = OrderAbilityTable(game_data)
