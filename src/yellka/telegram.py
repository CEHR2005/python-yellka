from __future__ import annotations

import json
import shlex
import time
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .money import format_ap
from .service import EconomyError, EconomyService


def run_telegram_bot(token: str | None, db_path: Path) -> None:
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN or --token is required")
    bot = TelegramBalanceBot(token, EconomyService(db_path))
    bot.run()


class TelegramBalanceBot:
    def __init__(self, token: str, service: EconomyService):
        self.token = token
        self.service = service
        self.offset = 0

    def run(self) -> None:
        print("Telegram bot polling started")
        while True:
            for update in self._updates():
                self._handle_update(update)
            time.sleep(1)

    def _updates(self) -> list[dict]:
        data = self._api(
            "getUpdates",
            {"timeout": 30, "offset": self.offset, "allowed_updates": json.dumps(["message"])},
        )
        updates = data.get("result", [])
        for update in updates:
            self.offset = max(self.offset, int(update["update_id"]) + 1)
        return updates

    def _handle_update(self, update: dict) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = (message.get("text") or "").strip()
        if not chat_id or not text:
            return
        try:
            response = self._dispatch(text)
        except (EconomyError, KeyError, ValueError) as exc:
            response = f"Ошибка: {exc}"
        self._api("sendMessage", {"chat_id": chat_id, "text": response})

    def _dispatch(self, text: str) -> str:
        args = shlex.split(text)
        command = args[0].lower()
        if command in {"/start", "/help"}:
            return HELP
        if command == "/balance":
            state = self.service.get_state()
            return (
                f"Баланс: {format_ap(state.balance)} AP\n"
                f"База: {format_ap(state.base_rate)} AP\n"
                f"Кэшбек: {state.cashback_level * 5}%\n"
                f"Ретро: {'включено' if state.retroactive_indexing_enabled else 'выключено'}"
            )
        if command == "/earn":
            amount, note = self._amount_note(args)
            self.service.add_income(amount, note)
            return self._balance_line()
        if command == "/spend":
            amount, note = self._amount_note(args)
            self.service.add_expense(amount, note)
            return self._balance_line()
        if command == "/complete":
            if len(args) < 2:
                raise ValueError("Usage: /complete <title> [units]")
            units = int(args[2]) if len(args) > 2 else 1
            result = self.service.complete_task(title=args[1], units=units)
            response = f"Задача #{result.id}: +{format_ap(result.reward)} AP"
            if result.retro_bonus:
                response += f"\nРетро: +{format_ap(result.retro_bonus)} AP"
            return response + "\n" + self._balance_line()
        if command == "/buy_core":
            result = self.service.buy_core()
            return self._upgrade_line(result)
        if command == "/buy_vector":
            vector = args[1] if len(args) > 1 else "code"
            result = self.service.buy_vector(vector)
            return self._upgrade_line(result)
        if command == "/buy_cashback":
            result = self.service.buy_cashback()
            return self._upgrade_line(result)
        if command == "/buy_retro":
            result = self.service.buy_retroactive_indexing()
            return self._upgrade_line(result)
        if command == "/tasks":
            rows = self.service.list_tasks(limit=10)
            if not rows:
                return "Нет выполненных задач"
            return "\n".join(
                f"#{row['id']} +{format_ap(row['reward'])} AP {row['title']}"
                for row in rows
            )
        if command == "/history":
            rows = self.service.list_transactions(limit=10)
            if not rows:
                return "Нет транзакций"
            return "\n".join(
                f"#{row['id']} {format_ap(row['amount'])} AP {row['kind']}: {row['note']}"
                for row in rows
            )
        raise ValueError(f"Unknown command: {command}")

    def _amount_note(self, args: list[str]) -> tuple[Decimal, str]:
        if len(args) < 2:
            raise ValueError(f"Usage: {args[0]} <amount> [note]")
        amount = Decimal(args[1].replace(",", "."))
        note = " ".join(args[2:]) if len(args) > 2 else args[0].lstrip("/")
        return amount, note

    def _balance_line(self) -> str:
        state = self.service.get_state()
        return f"Баланс: {format_ap(state.balance)} AP"

    def _upgrade_line(self, result) -> str:
        line = f"Улучшение #{result.id}: -{format_ap(result.cost)} AP"
        if result.cashback:
            line += f"\nКэшбек: +{format_ap(result.cashback)} AP"
        return line + "\n" + self._balance_line()

    def _api(self, method: str, params: dict) -> dict:
        body = urlencode(params).encode("utf-8")
        request = Request(
            f"https://api.telegram.org/bot{self.token}/{method}",
            data=body,
            method="POST",
        )
        with urlopen(request, timeout=35) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not data.get("ok"):
            raise ValueError(data)
        return data


HELP = """Команды:
/balance
/earn <amount> [note]
/spend <amount> [note]
/complete <title> [units]
/tasks
/history
/buy_core
/buy_vector [code|modeling|animation|sfx|gamedesign]
/buy_cashback
/buy_retro"""
