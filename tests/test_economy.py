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
        self.assertEqual(first_core.cost, Decimal("1.520"))
        self.assertEqual(first_core.cashback, Decimal("0.080"))
        self.assertEqual(first_vector.cost, Decimal("0.475"))
        self.assertEqual(first_vector.cashback, Decimal("0.025"))

        state = service.get_state()
        self.assertEqual(state.base_rate, Decimal("0.250"))
        self.assertEqual(state.cashback_level, 1)
        self.assertEqual(state.vector_levels["code"], 1)
        self.assertEqual(state.balance, Decimal("15.005"))

    def test_vector_upgrade_quote_includes_discounted_next_price(self) -> None:
        service = self.make_service()
        service.add_income(Decimal("20"), "Старт")
        service.buy_cashback()

        quote = service.quote_vector_upgrade("code")

        self.assertEqual(quote.level_before, 0)
        self.assertEqual(quote.level_after, 1)
        self.assertEqual(quote.full_cost, Decimal("0.500"))
        self.assertEqual(quote.discount, Decimal("0.025"))
        self.assertEqual(quote.final_cost, Decimal("0.475"))

    def test_core_upgrade_quote_includes_discounted_next_price(self) -> None:
        service = self.make_service()
        service.add_income(Decimal("20"), "Старт")
        service.buy_cashback()

        quote = service.quote_core_upgrade()

        self.assertEqual(quote.level_before, Decimal("0.200"))
        self.assertEqual(quote.level_after, Decimal("0.250"))
        self.assertEqual(quote.full_cost, Decimal("1.600"))
        self.assertEqual(quote.discount, Decimal("0.080"))
        self.assertEqual(quote.final_cost, Decimal("1.520"))

    def test_historical_upgrade_spend_matches_spreadsheet_totals(self) -> None:
        service = self.make_service()
        with service._connect() as conn:
            service._set_meta(conn, "base_rate", "1.900")
            service._set_meta(conn, "cashback_level", "5")
            service._set_meta(conn, "retroactive_indexing_enabled", "1")
            service._set_meta(conn, "vector_level:code", "10")

        estimate = service.estimate_upgrade_spend()

        # Historical spend: core 220.5, vectors 27.5, discount levels 15, retro 20.
        self.assertEqual(estimate.discount_spent, Decimal("15.000"))
        self.assertEqual(estimate.discount_saved, Decimal("58.300"))
        self.assertEqual(estimate.retroactive_indexing_spent, Decimal("20.000"))
        self.assertEqual(estimate.core_spent, Decimal("220.500"))
        self.assertEqual(estimate.vector_spent_by_key["code"], Decimal("27.500"))
        self.assertEqual(estimate.total_spent, Decimal("283.000"))

    def test_earnings_stats_derive_total_from_balance_and_historical_spend(self) -> None:
        service = self.make_service()
        with service._connect() as conn:
            service._set_meta(conn, "base_rate", "1.900")
            service._set_meta(conn, "cashback_level", "5")
            service._set_meta(conn, "retroactive_indexing_enabled", "1")
            service._set_meta(conn, "vector_level:code", "10")
            service._set_meta(conn, "historical_starting_balance", "24.000")
        service.add_income(Decimal("10"), "Премия")
        service.complete_task(title="Таск", units=2)

        stats = service.get_earnings_stats()

        self.assertEqual(stats.total_earned, Decimal("318.870"))
        self.assertEqual(stats.starting_balance, Decimal("24.000"))
        self.assertEqual(stats.task_earned, Decimal("7.600"))
        self.assertEqual(stats.retro_earned, Decimal("0.000"))
        self.assertEqual(stats.discount_gross, Decimal("18.270"))
        self.assertEqual(stats.discount_net, Decimal("3.270"))
        self.assertEqual(stats.premium_and_other_earned, Decimal("269.000"))

    def test_earnings_stats_split_task_and_retro_from_task_reward_columns(self) -> None:
        service = self.make_service()
        now = service._now()
        with service._connect() as conn:
            for title, reward, current_reward in [
                ("Историческая задача 1", "10.000", "15.000"),
                ("Историческая задача 2", "2.500", "3.750"),
            ]:
                cur = conn.execute(
                    """
                    INSERT INTO tasks (
                        created_at, title, vector, units, base_rate, vector_level,
                        vector_multiplier, priority_multiplier, full_close_bonus,
                        catalog_weight, reward, current_reward,
                        retro_paid_base_rate, premium_received, note
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        title,
                        "code",
                        1,
                        "1.000",
                        0,
                        "1.000",
                        "1.000",
                        "1.000",
                        "1.000",
                        reward,
                        current_reward,
                        "1.000",
                        0,
                        "",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO transactions (
                        created_at, amount, kind, note, task_id, upgrade_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        current_reward,
                        "task_reward",
                        f"Импортирована задача: {title}",
                        cur.lastrowid,
                        None,
                    ),
                )

        stats = service.get_earnings_stats()

        self.assertEqual(stats.total_earned, Decimal("18.750"))
        self.assertEqual(stats.task_earned, Decimal("12.500"))
        self.assertEqual(stats.retro_earned, Decimal("6.250"))
        self.assertEqual(stats.discount_gross, Decimal("0.000"))
        self.assertEqual(stats.discount_net, Decimal("0.000"))
        self.assertEqual(stats.premium_and_other_earned, Decimal("0.000"))

    def test_retroactive_indexing_pays_previous_task_delta_after_new_task(self) -> None:
        service = self.make_service()
        service.add_income(Decimal("40"), "Старт")
        original = service.complete_task(title="Первый таск", vector="code", units=4)

        service.buy_retroactive_indexing()
        service.buy_core()
        followup = service.complete_task(title="Новый таск", vector="code", units=1)

        self.assertEqual(original.reward, Decimal("0.800"))
        self.assertEqual(followup.reward, Decimal("0.250"))
        self.assertEqual(followup.retro_bonus, Decimal("0.200"))
        self.assertEqual(len(followup.retro_details), 1)
        self.assertEqual(followup.retro_details[0].task_id, original.id)
        self.assertEqual(followup.retro_details[0].delta, Decimal("0.200"))

        transactions = service.list_transactions(limit=10)
        retro = [row for row in transactions if row["kind"] == "retro_bonus"]
        self.assertEqual(len(retro), 1)
        # Previous task delta: 4 units * (0.25 - 0.20).
        self.assertEqual(Decimal(retro[0]["amount"]), Decimal("0.200"))

        original_task = [row for row in service.list_tasks(limit=10) if row["id"] == original.id][0]
        self.assertEqual(Decimal(original_task["reward"]), Decimal("0.800"))
        self.assertEqual(Decimal(original_task["current_reward"]), Decimal("1.000"))

    def test_premium_queue_tracks_tasks_without_changing_original_reward(self) -> None:
        service = self.make_service()
        first = service.complete_task(title="Первый таск", units=2)
        second = service.complete_task(title="Второй таск", units=1)

        service.mark_task_premium_received(first.id)

        pending = service.list_tasks(limit=10, premium_pending=True)
        self.assertEqual([row["id"] for row in pending], [second.id])
        self.assertEqual(Decimal(pending[0]["reward"]), Decimal("0.200"))

    def test_task_title_can_include_category_prefix(self) -> None:
        service = self.make_service()

        task = service.complete_task(title="ИИ врагов: поиск игрока", units=1)

        row = service.list_tasks(limit=1)[0]
        self.assertEqual(row["id"], task.id)
        self.assertEqual(row["category"], "ИИ врагов")
        self.assertEqual(row["title"], "поиск игрока")

    def test_categories_can_be_marked_completed(self) -> None:
        service = self.make_service()
        service.complete_task(title="ИИ врагов: поиск игрока", units=1)
        service.complete_task(title="ИИ врагов: движение к игроку", units=1)

        updated = service.set_category_completed("ИИ врагов", True)
        categories = service.list_categories()

        self.assertEqual(updated["category"], "ИИ врагов")
        self.assertEqual(int(updated["completed"]), 1)
        self.assertEqual(len(categories), 1)
        self.assertEqual(categories[0]["category"], "ИИ врагов")
        self.assertEqual(int(categories[0]["completed"]), 1)
        self.assertEqual(int(categories[0]["task_count"]), 2)
        self.assertEqual(int(categories[0]["premium_pending_count"]), 2)
        self.assertEqual(categories[0]["reward_total"], Decimal("0.400"))
        self.assertEqual(categories[0]["reward_formula"], "0.2x2 = 0.4")
        self.assertEqual(categories[0]["premium_total"], Decimal("0.200"))
        self.assertEqual(categories[0]["premium_pending_total"], Decimal("0.200"))

    def test_category_premium_is_half_of_total_original_reward(self) -> None:
        service = self.make_service()
        service.complete_task(title="ИИ врагов: поиск игрока", catalog_value="0.770")
        service.complete_task(title="ИИ врагов: специальные атаки", catalog_value="0.825")

        category = service.list_categories()[0]

        self.assertEqual(category["reward_total"], Decimal("0.319"))
        self.assertEqual(category["reward_formula"], "0.154 + 0.165 = 0.319")
        self.assertEqual(category["premium_total"], Decimal("0.160"))

    def test_expenses_cannot_overdraw_by_default(self) -> None:
        service = self.make_service()

        with self.assertRaises(InsufficientBalanceError):
            service.add_expense(Decimal("1"), "Too much")

    def test_expense_amounts_are_always_recorded_as_negative(self) -> None:
        service = self.make_service()
        service.add_income(Decimal("20"), "Старт")

        service.add_expense(Decimal("-7.2"), "Списание")

        self.assertEqual(service.get_state().balance, Decimal("12.800"))
        self.assertEqual(
            Decimal(service.list_transactions(limit=1)[0]["amount"]),
            Decimal("-7.200"),
        )

    def test_catalog_lookup_supports_keys_and_russian_titles(self) -> None:
        by_key = find_catalog_item("chain")
        by_title = find_catalog_item("Цепь")

        self.assertEqual(by_key.key, "chain")
        self.assertEqual(by_title.key, "chain")
        self.assertEqual(by_title.value, Decimal("2.100"))


if __name__ == "__main__":
    unittest.main()
