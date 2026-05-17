import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yellka.discord_bot import DiscordCommandHandler, is_task_result_response
from yellka.service import EconomyService


class DiscordCommandHandlerTests(unittest.TestCase):
    def make_handler(self) -> DiscordCommandHandler:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        service = EconomyService(Path(temp.name) / "balance.sqlite3")
        return DiscordCommandHandler(service)

    def test_ignores_messages_without_prefix(self) -> None:
        handler = self.make_handler()

        self.assertIsNone(handler.handle_message("balance"))

    def test_discord_prefix_commands_update_balance(self) -> None:
        handler = self.make_handler()

        self.assertEqual(handler.handle_message("!earn 3.5 старт"), "Баланс: 3.5 AP")
        balance = handler.handle_message("!balance")

        self.assertIsNotNone(balance)
        self.assertIn("Баланс: 3.5 AP", balance)
        self.assertIn("База: 0.2 AP", balance)

    def test_command_errors_are_returned_as_messages(self) -> None:
        handler = self.make_handler()

        response = handler.handle_message("!spend 1")

        self.assertEqual(response, "Ошибка: Not enough AP: need 1.000, balance 0.000")

    def test_start_panel_commands_are_detected(self) -> None:
        handler = self.make_handler()

        self.assertTrue(handler.is_panel_command("!start"))
        self.assertTrue(handler.is_panel_command("!старт"))
        self.assertFalse(handler.is_panel_command("!balance"))

    def test_prompt_mode_accepts_minimal_amount_messages(self) -> None:
        handler = self.make_handler()

        prompt = handler.start_prompt("earn", user_id=1, channel_id=10)
        response = handler.handle_user_message("8,55 старт", user_id=1, channel_id=10)

        self.assertIn("сумму", prompt)
        self.assertEqual(response, "Баланс: 8.55 AP")

    def test_prompt_mode_accepts_minimal_task_messages(self) -> None:
        handler = self.make_handler()

        handler.start_prompt("complete", user_id=1, channel_id=10)
        response = handler.handle_user_message(
            "Цепь и возврат 3",
            user_id=1,
            channel_id=10,
        )

        self.assertIsNotNone(response)
        self.assertIn("Задача #1", response)
        self.assertIn("Баланс: 0.6 AP", response)

    def test_premium_command_lists_and_marks_pending_tasks(self) -> None:
        handler = self.make_handler()
        handler.handle_message("!complete Тест")

        pending = handler.handle_message("!premium")
        marked = handler.handle_message("!premium mark 1")
        empty = handler.handle_message("!premium")

        self.assertIsNotNone(pending)
        self.assertIn("без премии", pending)
        self.assertEqual(marked, "Премия по задаче #1 отмечена полученной")
        self.assertEqual(empty, "Нет задач без премии")

    def test_category_done_awards_pending_premium(self) -> None:
        handler = self.make_handler()
        handler.handle_message('!complete "Модификаторы силы: рывок"')
        handler.handle_message('!complete "Модификаторы силы: удар"')

        done = handler.handle_message("!categories done Модификаторы силы")
        repeated = handler.handle_message("!categories done Модификаторы силы")
        balance = handler.handle_message("!balance")

        self.assertIsNotNone(done)
        self.assertIn("Категория завершена: Модификаторы силы", done)
        self.assertIn("Премия: +0.2 AP", done)
        self.assertEqual(
            repeated,
            "Категория завершена: Модификаторы силы\nНовых премий нет",
        )
        self.assertIsNotNone(balance)
        self.assertIn("Баланс: 0.6 AP", balance)

    def test_vectors_command_shows_next_upgrade_prices(self) -> None:
        handler = self.make_handler()
        handler.handle_message("!earn 20")
        handler.handle_message("!buy_cashback")

        response = handler.handle_message("!vectors")

        self.assertIsNotNone(response)
        self.assertIn("code: +0% -> +10% | 0.475 AP", response)

    def test_task_result_includes_retro_breakdown(self) -> None:
        handler = self.make_handler()
        handler.handle_message("!earn 60")
        handler.handle_message("!complete Старый 40")
        handler.handle_message("!buy_core")
        retro = handler.handle_message("!buy_retro")

        response = handler.handle_message("!complete Новый")

        self.assertIsNotNone(retro)
        self.assertIn("Улучшение #2", retro)
        self.assertIn("Баланс: 67 AP", retro)
        self.assertIsNotNone(response)
        self.assertIn("Задача #2", response)
        self.assertNotIn("Ретро:", response)
        self.assertIn("Итого начислено: 0.25 AP", response)

    def test_task_result_response_detection(self) -> None:
        self.assertTrue(is_task_result_response("Задача #12: +0.2 AP"))
        self.assertFalse(is_task_result_response("Баланс: 3 AP"))

    def test_crew_ability_command_updates_dominant_level_by_id(self) -> None:
        handler = self.make_handler()
        handler.service.create_cabin(
            name="Асуна Юкио",
            universe="SAO.V1",
            rank="S",
            sedative_dose="7",
            dominants=[
                {"name": "Суб-Администратор", "level": 1},
                {"name": "Скорость Вспышки", "level": 1},
            ],
        )

        listed = handler.handle_message("!crew")
        updated = handler.handle_message("!crew_ability 1 1 2")
        updated_by_name = handler.handle_message('!crew_ability 1 "Скорость Вспышки" 3')

        self.assertIsNotNone(listed)
        self.assertIn("#1 Асуна Юкио", listed)
        self.assertIsNotNone(updated)
        self.assertIn("1. Суб-Администратор ур.2", updated)
        self.assertIsNotNone(updated_by_name)
        self.assertIn("2. Скорость Вспышки ур.3", updated_by_name)
        self.assertTrue(handler.last_message_changed_state)


if __name__ == "__main__":
    unittest.main()
