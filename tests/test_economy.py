import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yellka.catalog import find_catalog_item
from yellka.service import EconomyService, InsufficientBalanceError


class EconomyServiceTests(unittest.TestCase):
    def make_service(self) -> EconomyService:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return EconomyService(Path(temp.name) / "balance.sqlite3")

    def test_manual_transactions_are_logged_and_totaled(self) -> None:
        service = self.make_service()

        service.add_income(Decimal("10"), "Стартовый баланс")
        service.add_expense(Decimal("2.5"), "Покупка")

        self.assertEqual(service.get_state().balance, Decimal("7.500"))
        history = service.list_transactions(limit=10)
        self.assertEqual([row["kind"] for row in history], ["expense", "income"])
        self.assertEqual(history[0]["note"], "Покупка")

    def test_task_completion_uses_base_vector_priority_and_full_close_bonus(self) -> None:
        service = self.make_service()
        service.add_income(Decimal("20"), "Старт")
        service.buy_vector("code")

        task = service.complete_task(
            title="Закрыть поиск игрока",
            vector="code",
            units=3,
            catalog_key="player_search",
            priority=True,
            full_close=True,
        )

        # 3 units * base 0.2 * catalog 0.77 * vector 1.1 * priority 2 * full close 1.5
        self.assertEqual(task.reward, Decimal("1.525"))
        self.assertEqual(service.get_state().balance, Decimal("21.025"))
        self.assertEqual(service.list_tasks(limit=10)[0]["title"], "Закрыть поиск игрока")

    def test_core_vector_and_cashback_upgrades_follow_document_prices(self) -> None:
        service = self.make_service()
        service.add_income(Decimal("20"), "Старт")

        cashback = service.buy_cashback()
        first_core = service.buy_core()
        first_vector = service.buy_vector("code")

        self.assertEqual(cashback.cost, Decimal("3.000"))
        self.assertEqual(first_core.cost, Decimal("2.000"))
        self.assertEqual(first_core.cashback, Decimal("0.100"))
        self.assertEqual(first_vector.cost, Decimal("0.500"))
        self.assertEqual(first_vector.cashback, Decimal("0.025"))

        state = service.get_state()
        self.assertEqual(state.base_rate, Decimal("0.250"))
        self.assertEqual(state.cashback_level, 1)
        self.assertEqual(state.vector_levels["code"], 1)
        self.assertEqual(state.balance, Decimal("14.625"))

    def test_retroactive_indexing_pays_previous_task_delta_after_new_task(self) -> None:
        service = self.make_service()
        service.add_income(Decimal("40"), "Старт")
        original = service.complete_task(title="Первый таск", vector="code", units=4)

        service.buy_retroactive_indexing()
        service.buy_core()
        followup = service.complete_task(title="Новый таск", vector="code", units=1)

        self.assertEqual(original.reward, Decimal("0.800"))
        self.assertEqual(followup.reward, Decimal("0.250"))

        transactions = service.list_transactions(limit=10)
        retro = [row for row in transactions if row["kind"] == "retro_bonus"]
        self.assertEqual(len(retro), 1)
        # Previous task delta: 4 units * (0.25 - 0.20).
        self.assertEqual(Decimal(retro[0]["amount"]), Decimal("0.200"))

    def test_expenses_cannot_overdraw_by_default(self) -> None:
        service = self.make_service()

        with self.assertRaises(InsufficientBalanceError):
            service.add_expense(Decimal("1"), "Too much")

    def test_catalog_lookup_supports_keys_and_russian_titles(self) -> None:
        by_key = find_catalog_item("chain")
        by_title = find_catalog_item("Цепь")

        self.assertEqual(by_key.key, "chain")
        self.assertEqual(by_title.key, "chain")
        self.assertEqual(by_title.value, Decimal("2.100"))


if __name__ == "__main__":
    unittest.main()
