import unittest
from types import SimpleNamespace

from s2clientprotocol import raw_pb2
from sc2.ids.ability_id import AbilityId
from sc2.unit import UnitOrder
from sc2.bot_ai import BotAI
from sc2.ids.unit_typeid import UnitTypeId

from sc2bot.sc2_compat import install_order_compatibility


class OrderCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.known = object()
        self.data = SimpleNamespace(abilities={AbilityId.SMART.value: self.known})
        install_order_compatibility(self.data)
        self.bot = SimpleNamespace(game_data=self.data)

    def test_reported_4135_order_keeps_target_and_progress(self):
        proto = raw_pb2.UnitOrder(
            ability_id=4135, target_unit_tag=4355784705, progress=0.25
        )
        order = UnitOrder.from_proto(proto, self.bot)
        self.assertEqual(order.ability.raw_id, 4135)
        self.assertEqual(order.ability.exact_id, AbilityId.NULL_NULL)
        self.assertEqual(order.target, 4355784705)
        self.assertEqual(order.progress, 0.25)
        self.assertIs(self.data.abilities[4135], order.ability)

    def test_known_order_is_unchanged(self):
        order = UnitOrder.from_proto(raw_pb2.UnitOrder(ability_id=1), self.bot)
        self.assertIs(order.ability, self.known)

    def test_pending_pylon_count_survives_unknown_worker_order(self):
        ability = SimpleNamespace(exact_id=AbilityId.PROTOSSBUILD_PYLON)
        self.data.abilities[AbilityId.PROTOSSBUILD_PYLON.value] = ability
        self.data.units = {
            UnitTypeId.PYLON.value: SimpleNamespace(creation_ability=ability)
        }
        bot = BotAI()
        bot.game_data = self.data
        bot.state = SimpleNamespace(game_loop=100)
        orders = [UnitOrder.from_proto(proto, self.bot) for proto in (
            raw_pb2.UnitOrder(ability_id=4135),
            raw_pb2.UnitOrder(ability_id=AbilityId.PROTOSSBUILD_PYLON.value),
        )]
        bot.units = [SimpleNamespace(orders=orders, is_ready=True)]
        bot.structures = []
        self.assertEqual(bot.already_pending(UnitTypeId.PYLON), 1)

    def test_mixed_queue_keeps_unknown_order_and_position(self):
        unknown = raw_pb2.UnitOrder(ability_id=4135)
        unknown.target_world_space_pos.x = 10
        unknown.target_world_space_pos.y = 20
        orders = [UnitOrder.from_proto(p, self.bot) for p in (
            unknown, raw_pb2.UnitOrder(ability_id=1)
        )]
        self.assertEqual(len(orders), 2)
        self.assertEqual(tuple(orders[0].target), (10, 20))
        self.assertIs(orders[1].ability, self.known)

    def test_missing_known_ability_still_raises(self):
        with self.assertRaises(KeyError):
            self.data.abilities[AbilityId.MOVE_MOVE.value]

    def test_installation_is_idempotent(self):
        table = self.data.abilities
        install_order_compatibility(self.data)
        self.assertIs(self.data.abilities, table)


if __name__ == "__main__":
    unittest.main()
