import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yellka.discord_bot import DiscordCommandHandler
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


if __name__ == "__main__":
    unittest.main()
