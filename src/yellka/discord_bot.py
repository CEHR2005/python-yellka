from __future__ import annotations

from contextlib import contextmanager
import fcntl
import shlex
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .money import format_ap
from .service import (
    CASHBACK_MAX_LEVEL,
    VECTOR_MAX_LEVEL,
    EconomyError,
    EconomyService,
    RetroBonusDetail,
    UpgradeQuote,
)

DEFAULT_COMMAND_PREFIX = "!"


def run_discord_bot(
    token: str | None,
    db_path: Path,
    *,
    command_prefix: str = DEFAULT_COMMAND_PREFIX,
    startup_guild_id: int | None = None,
    startup_channel_id: int | None = None,
) -> None:
    if not token:
        raise ValueError("DISCORD_BOT_TOKEN or --token is required")
    with discord_bot_lock(db_path):
        client = create_discord_client(
            EconomyService(db_path),
            command_prefix=command_prefix,
            startup_guild_id=startup_guild_id,
            startup_channel_id=startup_channel_id,
        )
        client.run(token)


@contextmanager
def discord_bot_lock(db_path: Path):
    lock_path = db_path.with_suffix(db_path.suffix + ".discord.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                f"Discord bot is already running for database: {db_path}"
            ) from exc
        yield


def create_discord_client(
    service: EconomyService,
    *,
    command_prefix: str = DEFAULT_COMMAND_PREFIX,
    startup_guild_id: int | None = None,
    startup_channel_id: int | None = None,
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
    terminal_messages: dict[int, Any] = {}
    startup_panel_sent = False

    def status_embed() -> Any:
        state = service.get_state()
        embed = discord.Embed(
            title="Yellka Terminal",
            description="Панель быстрых действий для AP-баланса.",
            color=0x2F80ED,
        )
        embed.add_field(name="Баланс", value=f"{format_ap(state.balance)} AP")
        embed.add_field(name="База", value=f"{format_ap(state.base_rate)} AP")
        embed.add_field(
            name="Следующее Ядро",
            value=format_upgrade_quote(service.quote_core_upgrade()),
        )
        embed.add_field(name="Скидка", value=f"{state.cashback_level * 5}%")
        embed.add_field(
            name="Ретро",
            value="включено" if state.retroactive_indexing_enabled else "выключено",
        )
        embed.add_field(
            name="Потрачено на ядра/векторы",
            value=handler.upgrade_spend_text(),
            inline=False,
        )
        embed.add_field(
            name="Исторический заработок",
            value=handler.earnings_stats_text(),
            inline=False,
        )
        vectors = ", ".join(
            f"{key} +{level * 10}%" for key, level in state.vector_levels.items()
        )
        embed.add_field(name="Векторы", value=vectors or "нет", inline=False)
        embed.add_field(
            name="Следующие улучшения",
            value=handler.vector_prices_text(),
            inline=False,
        )
        return embed

    async def send_terminal(channel: Any) -> None:
        message = await channel.send(embed=status_embed(), view=YellkaControlView())
        terminal_messages[channel.id] = message

    async def delete_old_terminals(channel: Any, bot_user: Any) -> None:
        async for message in channel.history(limit=50):
            if message.author != bot_user:
                continue
            if not message.embeds:
                continue
            if message.embeds[0].title != "Yellka Terminal":
                continue
            try:
                await message.delete()
            except discord.HTTPException:
                pass
        terminal_messages.pop(channel.id, None)

    async def refresh_terminal(channel: Any) -> None:
        message = terminal_messages.get(channel.id)
        if message is None:
            return
        try:
            await message.edit(embed=status_embed(), view=YellkaControlView())
        except discord.HTTPException:
            terminal_messages.pop(channel.id, None)
            await send_terminal(channel)

    class YellkaControlView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            state = service.get_state()
            if state.cashback_level >= CASHBACK_MAX_LEVEL:
                self.remove_item(self.cashback_button)
            if all(level >= VECTOR_MAX_LEVEL for level in state.vector_levels.values()):
                self.remove_item(self.vector_button)

        @discord.ui.button(label="Баланс", style=discord.ButtonStyle.primary)
        async def balance_button(self, interaction: Any, button: Any) -> None:
            await interaction.response.send_message(
                handler.dispatch(f"{command_prefix}balance"),
                ephemeral=True,
            )
            await refresh_terminal(interaction.channel)

        @discord.ui.button(label="Доход", style=discord.ButtonStyle.success)
        async def earn_button(self, interaction: Any, button: Any) -> None:
            prompt = handler.start_prompt(
                "earn",
                interaction.user.id,
                interaction.channel_id,
            )
            await interaction.response.send_message(prompt, ephemeral=True)

        @discord.ui.button(label="Расход", style=discord.ButtonStyle.danger)
        async def spend_button(self, interaction: Any, button: Any) -> None:
            prompt = handler.start_prompt(
                "spend",
                interaction.user.id,
                interaction.channel_id,
            )
            await interaction.response.send_message(prompt, ephemeral=True)

        @discord.ui.button(label="Задача", style=discord.ButtonStyle.success)
        async def complete_button(self, interaction: Any, button: Any) -> None:
            prompt = handler.start_prompt(
                "complete",
                interaction.user.id,
                interaction.channel_id,
            )
            await interaction.response.send_message(prompt, ephemeral=True)

        @discord.ui.button(label="История", style=discord.ButtonStyle.secondary)
        async def history_button(self, interaction: Any, button: Any) -> None:
            await interaction.response.send_message(
                handler.dispatch(f"{command_prefix}history"),
                ephemeral=True,
            )

        @discord.ui.button(label="Задачи", style=discord.ButtonStyle.secondary)
        async def tasks_button(self, interaction: Any, button: Any) -> None:
            await interaction.response.send_message(
                handler.dispatch(f"{command_prefix}tasks"),
                ephemeral=True,
            )

        @discord.ui.button(label="Премии", style=discord.ButtonStyle.secondary)
        async def premium_button(self, interaction: Any, button: Any) -> None:
            await interaction.response.send_message(
                handler.dispatch(f"{command_prefix}premium"),
                ephemeral=True,
            )

        @discord.ui.button(label="Ядро", style=discord.ButtonStyle.secondary)
        async def core_button(self, interaction: Any, button: Any) -> None:
            await self._send_result(interaction, f"{command_prefix}buy_core")

        @discord.ui.button(label="Скидка", style=discord.ButtonStyle.secondary)
        async def cashback_button(self, interaction: Any, button: Any) -> None:
            await self._send_result(interaction, f"{command_prefix}buy_cashback")

        @discord.ui.button(label="Вектор", style=discord.ButtonStyle.secondary)
        async def vector_button(self, interaction: Any, button: Any) -> None:
            prompt = handler.start_prompt(
                "vector",
                interaction.user.id,
                interaction.channel_id,
            )
            await interaction.response.send_message(prompt, ephemeral=True)

        async def _send_result(self, interaction: Any, command: str) -> None:
            await interaction.response.send_message(
                handler.handle_message(command) or "Готово",
                ephemeral=True,
            )
            await refresh_terminal(interaction.channel)

    class YellkaDiscordClient(discord.Client):
        async def on_ready(self) -> None:
            nonlocal startup_panel_sent
            print(f"Discord bot logged in as {self.user}")
            if startup_panel_sent or startup_channel_id is None:
                return
            if startup_guild_id is not None:
                guild = self.get_guild(startup_guild_id)
                if guild is None:
                    print(f"Startup guild not found: {startup_guild_id}")
                    return
                channel = guild.get_channel(startup_channel_id)
            else:
                channel = self.get_channel(startup_channel_id)
            if channel is None:
                print(f"Startup channel not found: {startup_channel_id}")
                return
            await delete_old_terminals(channel, self.user)
            await send_terminal(channel)
            startup_panel_sent = True

        async def on_message(self, message: Any) -> None:
            if message.author == self.user:
                return
            if handler.is_panel_command(message.content):
                await send_terminal(message.channel)
                return
            response = handler.handle_user_message(
                message.content,
                message.author.id,
                message.channel.id,
            )
            if response is None:
                return
            await message.channel.send(response)
            if handler.last_message_changed_state:
                await refresh_terminal(message.channel)

    return YellkaDiscordClient(intents=intents)


@dataclass(frozen=True)
class PendingPrompt:
    action: str


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
        self.pending_prompts: dict[tuple[int, int], PendingPrompt] = {}
        self.last_message_changed_state = False

    def is_panel_command(self, content: str) -> bool:
        text = content.strip().lower()
        return text in {
            f"{self.command_prefix}start",
            f"{self.command_prefix}panel",
            f"{self.command_prefix}панель",
            f"{self.command_prefix}старт",
        }

    def handle_message(self, content: str) -> str | None:
        self.last_message_changed_state = False
        text = content.strip()
        if not text.startswith(self.command_prefix):
            return None
        try:
            return self.dispatch(text)
        except (EconomyError, KeyError, ValueError) as exc:
            return f"Ошибка: {exc}"

    def handle_user_message(
        self,
        content: str,
        user_id: int,
        channel_id: int,
    ) -> str | None:
        self.last_message_changed_state = False
        key = (user_id, channel_id)
        text = content.strip()
        if text.lower() in {"cancel", "отмена"}:
            self.pending_prompts.pop(key, None)
            return "Действие отменено."
        if key in self.pending_prompts and not text.startswith(self.command_prefix):
            prompt = self.pending_prompts.pop(key)
            try:
                return self.complete_prompt(prompt.action, text)
            except (EconomyError, KeyError, ValueError) as exc:
                return f"Ошибка: {exc}"
        return self.handle_message(content)

    def start_prompt(self, action: str, user_id: int, channel_id: int) -> str:
        self.pending_prompts[(user_id, channel_id)] = PendingPrompt(action)
        if action == "earn":
            return "Напиши сумму и заметку: `10 Ручная корректировка`."
        if action == "spend":
            return "Напиши сумму и заметку: `2.5 покупка ассета`."
        if action == "complete":
            return "Напиши название задачи и, если нужно, units: `Цепь и возврат 3`."
        if action == "vector":
            return "Напиши вектор: `code`, `modeling`, `animation`, `sfx` или `gamedesign`."
        raise ValueError(f"Unknown prompt action: {action}")

    def complete_prompt(self, action: str, text: str) -> str:
        if action == "earn":
            amount, note = self._amount_note([f"{self.command_prefix}earn", *shlex.split(text)])
            self.service.add_income(amount, note)
            self.last_message_changed_state = True
            return self._balance_line()
        if action == "spend":
            amount, note = self._amount_note([f"{self.command_prefix}spend", *shlex.split(text)])
            self.service.add_expense(amount, note)
            self.last_message_changed_state = True
            return self._balance_line()
        if action == "complete":
            args = shlex.split(text)
            if not args:
                raise ValueError("Нужно название задачи")
            units = 1
            title_parts = args
            if len(args) > 1:
                try:
                    units = int(args[-1])
                    title_parts = args[:-1]
                except ValueError:
                    pass
            title = " ".join(title_parts)
            result = self.service.complete_task(title=title, units=units)
            self.last_message_changed_state = True
            return self._task_result_line(result)
        if action == "vector":
            result = self.service.buy_vector(text.strip())
            self.last_message_changed_state = True
            return self._upgrade_line(result)
        raise ValueError(f"Unknown prompt action: {action}")

    def dispatch(self, text: str) -> str:
        args = shlex.split(text)
        if not args:
            return self._help()
        command = args[0]
        if not command.startswith(self.command_prefix):
            raise ValueError(f"Command must start with {self.command_prefix}")
        command = command[len(self.command_prefix) :].lower()

        if command in {"start", "старт", "panel", "панель"}:
            return "Открываю панель."
        if command == "help":
            return self._help()
        if command == "balance":
            state = self.service.get_state()
            return (
                f"Баланс: {format_ap(state.balance)} AP\n"
                f"База: {format_ap(state.base_rate)} AP\n"
                f"Следующее Ядро: {format_upgrade_quote(self.service.quote_core_upgrade())}\n"
                f"Скидка: {state.cashback_level * 5}%\n"
                f"Ретро: {'включено' if state.retroactive_indexing_enabled else 'выключено'}\n"
                f"Потрачено на ядра/векторы: {self.upgrade_spend_text()}\n"
                f"Исторический заработок: {self.earnings_stats_text()}\n"
                f"\nСледующие улучшения векторов:\n{self.vector_prices_text()}"
            )
        if command == "vectors":
            return self.vector_prices_text()
        if command == "earn":
            amount, note = self._amount_note(args)
            self.service.add_income(amount, note)
            self.last_message_changed_state = True
            return self._balance_line()
        if command == "spend":
            amount, note = self._amount_note(args)
            self.service.add_expense(amount, note)
            self.last_message_changed_state = True
            return self._balance_line()
        if command == "complete":
            if len(args) < 2:
                raise ValueError(
                    f"Usage: {self.command_prefix}complete <title> [units]"
                )
            units = int(args[2]) if len(args) > 2 else 1
            result = self.service.complete_task(title=args[1], units=units)
            self.last_message_changed_state = True
            return self._task_result_line(result)
        if command == "buy_core":
            result = self.service.buy_core()
            self.last_message_changed_state = True
            return self._upgrade_line(result)
        if command == "buy_vector":
            vector = args[1] if len(args) > 1 else "code"
            result = self.service.buy_vector(vector)
            self.last_message_changed_state = True
            return self._upgrade_line(result)
        if command == "buy_cashback":
            result = self.service.buy_cashback()
            self.last_message_changed_state = True
            return self._upgrade_line(result)
        if command == "buy_retro":
            result = self.service.buy_retroactive_indexing()
            self.last_message_changed_state = True
            return self._upgrade_line(result)
        if command == "tasks":
            rows = self.service.list_tasks(limit=10)
            if not rows:
                return "Нет выполненных задач"
            return "\n".join(
                self._task_line(row)
                for row in rows
            )
        if command == "premium":
            if len(args) > 1 and args[1] == "mark":
                if len(args) < 3:
                    raise ValueError(
                        f"Usage: {self.command_prefix}premium mark <task_id>"
                    )
                task = self.service.mark_task_premium_received(int(args[2]))
                self.last_message_changed_state = True
                return f"Премия по задаче #{task['id']} отмечена полученной"
            rows = self.service.list_tasks(limit=10, premium_pending=True)
            if not rows:
                return "Нет задач без премии"
            return "\n".join(self._task_line(row) for row in rows)
        if command == "categories":
            if len(args) > 2 and args[1] in {"done", "open"}:
                completed = args[1] == "done"
                row = self.service.set_category_completed(" ".join(args[2:]), completed)
                self.last_message_changed_state = True
                status = "завершена" if completed else "открыта"
                return f"Категория {status}: {row['category']}"
            rows = self.service.list_categories()
            if not rows:
                return "Нет категорий"
            return "\n".join(self._category_line(row) for row in rows)
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

    def vector_prices_text(self) -> str:
        state = self.service.get_state()
        lines = []
        for key, quote in self.service.quote_vector_upgrades().items():
            level = state.vector_levels[key]
            if quote.maxed:
                price = "макс"
            else:
                price = format_upgrade_quote(quote)
            lines.append(f"{key}: +{level * 10}% -> +{quote.level_after * 10}% | {price}")
        return "\n".join(lines)

    def upgrade_spend_text(self) -> str:
        estimate = self.service.estimate_upgrade_spend()
        vectors = ", ".join(
            f"{key} {format_ap(amount)}"
            for key, amount in estimate.vector_spent_by_key.items()
            if amount
        )
        if not vectors:
            vectors = "0"
        return (
            f"{format_ap(estimate.total_spent)} AP "
            f"(уровни скидки {format_ap(estimate.discount_spent)}, "
            f"ретро {format_ap(estimate.retroactive_indexing_spent)}, "
            f"ядра {format_ap(estimate.core_spent)}, "
            f"векторы {format_ap(estimate.vector_spent)}: {vectors}; "
            f"скидка на ядра с базы {format_ap(estimate.discount_start_base)}, "
            "векторы без скидки)"
        )

    def earnings_stats_text(self) -> str:
        stats = self.service.get_earnings_stats()
        return (
            f"{format_ap(stats.total_earned)} AP "
            f"(старт {format_ap(stats.starting_balance)}, "
            f"задачи {format_ap(stats.task_earned)}, "
            f"ретро {format_ap(stats.retro_earned)}, "
            f"кэшбек скидки {format_ap(stats.discount_gross)} / "
            f"чистыми {format_ap(stats.discount_net)}, "
            f"премии {format_ap(stats.premium_earned)}, "
            f"прочее {format_ap(stats.other_earned)})"
        )

    def _upgrade_line(self, result) -> str:
        line = f"Улучшение #{result.id}: -{format_ap(result.cost)} AP"
        return line + "\n" + self._balance_line()

    def _task_result_line(self, result) -> str:
        lines = [f"Задача #{result.id}: +{format_ap(result.reward)} AP"]
        if result.retro_details:
            lines.append(format_retro_bonus(result.retro_details))
        lines.append(f"Итого начислено: {format_ap(result.reward + result.retro_bonus)} AP")
        lines.append(self._balance_line())
        return "\n".join(lines)

    def _task_line(self, row: dict[str, Any]) -> str:
        premium = "премия получена" if int(row["premium_received"]) else "без премии"
        task_name = row["title"]
        if row.get("category"):
            task_name = f"{row['category']}: {task_name}"
        return (
            f"#{row['id']} +{format_ap(row['reward'])} AP"
            f" / ретро {format_ap(row['current_reward'])} AP"
            f" [{premium}] {task_name}"
        )

    def _category_line(self, row: dict[str, Any]) -> str:
        status = "закрыта" if int(row["completed"]) else "открыта"
        return (
            f"{row['category']}: {status}, "
            f"задач {row['task_count']}, "
            f"без премии {row['premium_pending_count']}, "
            f"исходно {row['reward_formula']} AP, "
            f"премия {format_ap(row['premium_total'])} AP "
            f"(к получению {format_ap(row['premium_pending_total'])})"
        )

    def _help(self) -> str:
        return help_text(self.command_prefix)


def help_text(command_prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return f"""Команды:
{command_prefix}balance
{command_prefix}earn <amount> [note]
{command_prefix}spend <amount> [note]
{command_prefix}complete <title> [units]
{command_prefix}tasks
{command_prefix}vectors
{command_prefix}premium
{command_prefix}premium mark <task_id>
{command_prefix}categories
{command_prefix}categories done <category>
{command_prefix}categories open <category>
{command_prefix}history
{command_prefix}buy_core
{command_prefix}buy_vector [code|modeling|animation|sfx|gamedesign]
{command_prefix}buy_cashback
{command_prefix}buy_retro"""


def format_upgrade_quote(quote: UpgradeQuote) -> str:
    if quote.maxed:
        return "макс"
    if quote.discount:
        return (
            f"{format_ap(quote.final_cost)} AP "
            f"(полная {format_ap(quote.full_cost)}, скидка {format_ap(quote.discount)})"
        )
    return f"{format_ap(quote.final_cost)} AP"


def format_retro_bonus(details: list[RetroBonusDetail]) -> str:
    total = sum((detail.delta for detail in details), Decimal("0.000"))
    total_units = sum(detail.units for detail in details)
    first = details[0]
    base_delta = first.current_base_rate - first.paid_base_rate
    vector_bonus = first.vector_multiplier - Decimal("1.000")
    return "\n".join(
        [
            f"Ретро: +{format_ap(total)} AP",
            f"Задач: {len(details)} | units: {total_units}",
            (
                f"Базовая разница: {format_ap(first.current_base_rate)} - "
                f"{format_ap(first.paid_base_rate)} = {format_ap(base_delta)} AP"
            ),
            f"Вектор: +{format_ap(vector_bonus * 100)}% (x{format_ap(first.vector_multiplier)})",
            f"Формула: `{total_units} * {format_ap(base_delta)} * {format_ap(first.vector_multiplier)} = {format_ap(total)}`",
        ]
    )
