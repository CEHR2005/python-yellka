from __future__ import annotations

import shlex
from decimal import Decimal
from pathlib import Path
from typing import Any

from .money import format_ap
from .service import EconomyError, EconomyService

DEFAULT_COMMAND_PREFIX = "!"


def run_discord_bot(
    token: str | None,
    db_path: Path,
    *,
    command_prefix: str = DEFAULT_COMMAND_PREFIX,
) -> None:
    if not token:
        raise ValueError("DISCORD_BOT_TOKEN or --token is required")
    client = create_discord_client(
        EconomyService(db_path),
        command_prefix=command_prefix,
    )
    client.run(token)


def create_discord_client(
    service: EconomyService,
    *,
    command_prefix: str = DEFAULT_COMMAND_PREFIX,
) -> Any:
    try:
        import discord
    except ImportError as exc:
        message = (
            "discord.py is required to run the Discord bot. "
            "Install package dependencies first."
        )
        raise ValueError(message) from exc

    intents = discord.Intents.default()
    intents.message_content = True
    handler = DiscordCommandHandler(service, command_prefix=command_prefix)

    class YellkaDiscordClient(discord.Client):
        async def on_ready(self) -> None:
            print(f"Discord bot logged in as {self.user}")

        async def on_message(self, message: Any) -> None:
            if message.author == self.user:
                return
            response = handler.handle_message(message.content)
            if response is None:
                return
            await message.channel.send(response)

    return YellkaDiscordClient(intents=intents)


class DiscordCommandHandler:
    def __init__(
        self,
        service: EconomyService,
        *,
        command_prefix: str = DEFAULT_COMMAND_PREFIX,
    ):
        if not command_prefix:
            raise ValueError("command_prefix must not be empty")
        self.service = service
        self.command_prefix = command_prefix

    def handle_message(self, content: str) -> str | None:
        text = content.strip()
        if not text.startswith(self.command_prefix):
            return None
        try:
            return self.dispatch(text)
        except (EconomyError, KeyError, ValueError) as exc:
            return f"Ошибка: {exc}"

    def dispatch(self, text: str) -> str:
        args = shlex.split(text)
        if not args:
            return self._help()
        command = args[0]
        if not command.startswith(self.command_prefix):
            raise ValueError(f"Command must start with {self.command_prefix}")
        command = command[len(self.command_prefix) :].lower()

        if command in {"start", "help"}:
            return self._help()
        if command == "balance":
            state = self.service.get_state()
            return (
                f"Баланс: {format_ap(state.balance)} AP\n"
                f"База: {format_ap(state.base_rate)} AP\n"
                f"Кэшбек: {state.cashback_level * 5}%\n"
                f"Ретро: {'включено' if state.retroactive_indexing_enabled else 'выключено'}"
            )
        if command == "earn":
            amount, note = self._amount_note(args)
            self.service.add_income(amount, note)
            return self._balance_line()
        if command == "spend":
            amount, note = self._amount_note(args)
            self.service.add_expense(amount, note)
            return self._balance_line()
        if command == "complete":
            if len(args) < 2:
                raise ValueError(
                    f"Usage: {self.command_prefix}complete <title> [units]"
                )
            units = int(args[2]) if len(args) > 2 else 1
            result = self.service.complete_task(title=args[1], units=units)
            response = f"Задача #{result.id}: +{format_ap(result.reward)} AP"
            if result.retro_bonus:
                response += f"\nРетро: +{format_ap(result.retro_bonus)} AP"
            return response + "\n" + self._balance_line()
        if command == "buy_core":
            result = self.service.buy_core()
            return self._upgrade_line(result)
        if command == "buy_vector":
            vector = args[1] if len(args) > 1 else "code"
            result = self.service.buy_vector(vector)
            return self._upgrade_line(result)
        if command == "buy_cashback":
            result = self.service.buy_cashback()
            return self._upgrade_line(result)
        if command == "buy_retro":
            result = self.service.buy_retroactive_indexing()
            return self._upgrade_line(result)
        if command == "tasks":
            rows = self.service.list_tasks(limit=10)
            if not rows:
                return "Нет выполненных задач"
            return "\n".join(
                f"#{row['id']} +{format_ap(row['reward'])} AP {row['title']}"
                for row in rows
            )
        if command == "history":
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
            command = args[0] if args else f"{self.command_prefix}command"
            raise ValueError(f"Usage: {command} <amount> [note]")
        amount = Decimal(args[1].replace(",", "."))
        note = (
            " ".join(args[2:])
            if len(args) > 2
            else args[0][len(self.command_prefix) :]
        )
        return amount, note

    def _balance_line(self) -> str:
        state = self.service.get_state()
        return f"Баланс: {format_ap(state.balance)} AP"

    def _upgrade_line(self, result) -> str:
        line = f"Улучшение #{result.id}: -{format_ap(result.cost)} AP"
        if result.cashback:
            line += f"\nКэшбек: +{format_ap(result.cashback)} AP"
        return line + "\n" + self._balance_line()

    def _help(self) -> str:
        return help_text(self.command_prefix)


def help_text(command_prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return f"""Команды:
{command_prefix}balance
{command_prefix}earn <amount> [note]
{command_prefix}spend <amount> [note]
{command_prefix}complete <title> [units]
{command_prefix}tasks
{command_prefix}history
{command_prefix}buy_core
{command_prefix}buy_vector [code|modeling|animation|sfx|gamedesign]
{command_prefix}buy_cashback
{command_prefix}buy_retro"""
