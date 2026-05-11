from __future__ import annotations

import argparse
import os
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from .catalog import CATALOG_ITEMS, VECTORS
from .discord_bot import run_discord_bot
from .money import format_ap, parse_ap
from .service import EconomyError, EconomyService, RetroBonusDetail, UpgradeQuote


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def default_db_path() -> Path:
    env_path = os.environ.get("YELLKA_DB")
    if env_path:
        return Path(env_path).expanduser()
    return Path.home() / ".local" / "share" / "yellka" / "balance.sqlite3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yellka",
        description="ASSIR/Yellka balance manager with task logs and upgrade formulas.",
    )
    parser.add_argument("--db", default=str(default_db_path()), help="SQLite DB path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Initialize a balance database")
    init.add_argument("--initial-balance", default="0", help="Existing AP balance")
    init.add_argument(
        "--update-bonus",
        action="store_true",
        help="Apply the +2.5 AP ASSIR update bonus from the document",
    )

    subparsers.add_parser("balance", help="Show current balance and terminal state")

    income = subparsers.add_parser("earn", help="Add a manual income transaction")
    income.add_argument("amount")
    income.add_argument("note", nargs="?", default="Доход")

    expense = subparsers.add_parser("spend", help="Add a manual expense transaction")
    expense.add_argument("amount")
    expense.add_argument("note", nargs="?", default="Расход")
    expense.add_argument("--allow-negative", action="store_true")

    complete = subparsers.add_parser("complete", help="Log a completed task")
    complete.add_argument("title", nargs="?", help="Task title")
    complete.add_argument("--catalog", help="Catalog key or Russian title")
    complete.add_argument("--value", default="1", help="Manual task weight")
    complete.add_argument("--units", type=int, default=1)
    complete.add_argument("--vector", default="code", choices=sorted(VECTORS))
    complete.add_argument("--priority", action="store_true")
    complete.add_argument("--full-close", action="store_true")
    complete.add_argument("--note", default="")

    buy = subparsers.add_parser("buy", help="Buy a terminal upgrade")
    buy_subparsers = buy.add_subparsers(dest="upgrade", required=True)
    buy_subparsers.add_parser("core", help="Upgrade Ядро Вычислений by +0.05 AP")
    vector = buy_subparsers.add_parser("vector", help="Upgrade a vector multiplier")
    vector.add_argument("vector", choices=sorted(VECTORS))
    buy_subparsers.add_parser("cashback", help="Upgrade discount by +5%%")
    buy_subparsers.add_parser("retro", help="Buy retroactive indexing")

    tasks = subparsers.add_parser("tasks", help="Show completed tasks")
    tasks.add_argument("--limit", type=int, default=20)
    tasks.add_argument(
        "--premium-pending",
        action="store_true",
        help="Show only tasks without received premium",
    )

    premium = subparsers.add_parser("premium", help="Manage task premium status")
    premium_subparsers = premium.add_subparsers(dest="premium_command", required=True)
    premium_list = premium_subparsers.add_parser(
        "list", help="Show tasks without received premium"
    )
    premium_list.add_argument("--limit", type=int, default=20)
    premium_mark = premium_subparsers.add_parser(
        "mark", help="Mark a task premium as received"
    )
    premium_mark.add_argument("task_id", type=int)

    categories = subparsers.add_parser("categories", help="Manage task categories")
    category_subparsers = categories.add_subparsers(
        dest="category_command",
        required=True,
    )
    category_subparsers.add_parser("list", help="Show task categories")
    category_done = category_subparsers.add_parser(
        "done", help="Mark a category as completed"
    )
    category_done.add_argument("category")
    category_open = category_subparsers.add_parser(
        "open", help="Mark a category as not completed"
    )
    category_open.add_argument("category")

    transactions = subparsers.add_parser("transactions", help="Show transaction log")
    transactions.add_argument("--limit", type=int, default=20)

    upgrades = subparsers.add_parser("upgrades", help="Show bought upgrades")
    upgrades.add_argument("--limit", type=int, default=20)

    history_settings = subparsers.add_parser(
        "history-settings",
        help="Configure historical upgrade spend assumptions",
    )
    history_settings.add_argument("--discount-start-base")
    history_settings.add_argument("--discount-purchase-cost")
    history_settings.add_argument("--discount-cashback")
    history_settings.add_argument("--retro-purchase-cost")
    history_settings.add_argument("--starting-balance")

    catalog = subparsers.add_parser("catalog", help="Show task catalog")
    catalog.add_argument("query", nargs="?")

    discord = subparsers.add_parser("discord", help="Run Discord bot")
    discord.add_argument("--token", default=os.environ.get("DISCORD_BOT_TOKEN"))
    discord.add_argument("--prefix", default=os.environ.get("YELLKA_DISCORD_PREFIX", "!"))
    discord.add_argument(
        "--startup-guild-id",
        type=int,
        default=os.environ.get("YELLKA_DISCORD_STARTUP_GUILD_ID"),
    )
    discord.add_argument(
        "--startup-channel-id",
        type=int,
        default=os.environ.get("YELLKA_DISCORD_STARTUP_CHANNEL_ID"),
    )

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    service = EconomyService(args.db)

    try:
        if args.command == "init":
            state = service.initialize(
                initial_balance=parse_ap(args.initial_balance),
                update_bonus=args.update_bonus,
            )
            print_state(service)
        elif args.command == "balance":
            print_state(service)
        elif args.command == "earn":
            service.add_income(Decimal(args.amount.replace(",", ".")), args.note)
            print_state(service)
        elif args.command == "spend":
            service.add_expense(
                Decimal(args.amount.replace(",", ".")),
                args.note,
                allow_negative=args.allow_negative,
            )
            print_state(service)
        elif args.command == "complete":
            result = service.complete_task(
                title=args.title,
                vector=args.vector,
                units=args.units,
                catalog_key=args.catalog,
                catalog_value=args.value,
                priority=args.priority,
                full_close=args.full_close,
                note=args.note,
            )
            print(f"Задача #{result.id}: +{format_ap(result.reward)} AP")
            if result.retro_bonus:
                print(format_retro_bonus(result.retro_details))
            print(f"Итого начислено: {format_ap(result.reward + result.retro_bonus)} AP")
            print_state(service)
        elif args.command == "buy":
            result = buy_upgrade(service, args)
            print(f"Улучшение #{result.id}: -{format_ap(result.cost)} AP")
            print_state(service)
        elif args.command == "tasks":
            print_rows(
                service.list_tasks(
                    limit=args.limit,
                    premium_pending=args.premium_pending,
                ),
                [
                    "id",
                    "created_at",
                    "category",
                    "title",
                    "vector",
                    "units",
                    "reward",
                    "current_reward",
                    "premium_received",
                ],
            )
        elif args.command == "premium":
            if args.premium_command == "list":
                print_rows(
                    service.list_tasks(limit=args.limit, premium_pending=True),
                    ["id", "created_at", "category", "title", "reward", "current_reward"],
                )
            elif args.premium_command == "mark":
                task = service.mark_task_premium_received(args.task_id)
                print(f"Премия по задаче #{task['id']} отмечена полученной")
            else:
                parser.error(f"Unknown premium command: {args.premium_command}")
        elif args.command == "categories":
            if args.category_command == "list":
                print_rows(
                    service.list_categories(),
                    [
                        "category",
                        "completed",
                        "task_count",
                        "premium_pending_count",
                        "reward_formula",
                        "reward_total",
                        "premium_total",
                        "premium_pending_total",
                    ],
                )
            elif args.category_command == "done":
                row = service.set_category_completed(args.category, True)
                print(f"Категория отмечена завершенной: {row['category']}")
            elif args.category_command == "open":
                row = service.set_category_completed(args.category, False)
                print(f"Категория снова открыта: {row['category']}")
            else:
                parser.error(f"Unknown categories command: {args.category_command}")
        elif args.command == "transactions":
            print_rows(
                service.list_transactions(limit=args.limit),
                ["id", "created_at", "amount", "kind", "note"],
            )
        elif args.command == "upgrades":
            print_rows(
                service.list_upgrades(limit=args.limit),
                ["id", "created_at", "upgrade_type", "target", "level_after", "cost", "discount"],
            )
        elif args.command == "history-settings":
            service.set_upgrade_history_settings(
                discount_start_base=args.discount_start_base,
                discount_purchase_cost=args.discount_purchase_cost,
                historical_discount_cashback=args.discount_cashback,
                retroactive_indexing_purchase_cost=args.retro_purchase_cost,
                historical_starting_balance=args.starting_balance,
            )
            print(format_upgrade_spend(service))
        elif args.command == "catalog":
            for item in CATALOG_ITEMS:
                if args.query and args.query.casefold() not in item.title.casefold() and args.query.casefold() not in item.key.casefold():
                    continue
                print(f"{item.key:22} {format_ap(item.value):>6}  {item.title}")
        elif args.command == "discord":
            run_discord_bot(
                args.token,
                Path(args.db),
                command_prefix=args.prefix,
                startup_guild_id=args.startup_guild_id,
                startup_channel_id=args.startup_channel_id,
            )
        else:
            parser.error(f"Unknown command: {args.command}")
    except (EconomyError, KeyError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    return 0


def buy_upgrade(service: EconomyService, args: argparse.Namespace):
    if args.upgrade == "core":
        return service.buy_core()
    if args.upgrade == "vector":
        return service.buy_vector(args.vector)
    if args.upgrade == "cashback":
        return service.buy_cashback()
    if args.upgrade == "retro":
        return service.buy_retroactive_indexing()
    raise ValueError(f"Unknown upgrade: {args.upgrade}")


def print_state(service: EconomyService) -> None:
    state = service.get_state()
    print(f"Баланс: {format_ap(state.balance)} AP")
    print(f"База Ядра: {format_ap(state.base_rate)} AP")
    print(f"Следующее Ядро: {format_upgrade_quote(service.quote_core_upgrade())}")
    print(f"Скидка: {state.cashback_level * 5}%")
    print(
        "Ретроспективная индексация: "
        + ("включена" if state.retroactive_indexing_enabled else "выключена")
    )
    print(format_upgrade_spend(service))
    print(format_earnings_stats(service))
    print("Векторы:")
    for key, quote in service.quote_vector_upgrades().items():
        level = state.vector_levels[key]
        if quote.maxed:
            next_price = "макс"
        else:
            next_price = format_upgrade_quote(quote)
        print(f"  {key}=+{level * 10}% | следующий апгрейд: {next_price}")


def format_upgrade_quote(quote: UpgradeQuote) -> str:
    if quote.maxed:
        return "макс"
    if quote.discount:
        return (
            f"{format_ap(quote.final_cost)} AP "
            f"(полная {format_ap(quote.full_cost)}, скидка {format_ap(quote.discount)})"
        )
    return f"{format_ap(quote.final_cost)} AP"


def format_upgrade_spend(service: EconomyService) -> str:
    estimate = service.estimate_upgrade_spend()
    vectors = ", ".join(
        f"{key}={format_ap(amount)}"
        for key, amount in estimate.vector_spent_by_key.items()
        if amount
    )
    if not vectors:
        vectors = "0"
    return (
        "Потрачено на ядра/векторы/скидку: "
        f"{format_ap(estimate.total_spent)} AP "
        f"(уровни скидки {format_ap(estimate.discount_spent)}, "
        f"ретро {format_ap(estimate.retroactive_indexing_spent)}, "
        f"ядра {format_ap(estimate.core_spent)}, "
        f"векторы {format_ap(estimate.vector_spent)}: {vectors}; "
        f"скидка на ядра с базы {format_ap(estimate.discount_start_base)}, "
        "векторы без скидки)"
    )


def format_earnings_stats(service: EconomyService) -> str:
    stats = service.get_earnings_stats()
    return (
        "Исторический заработок: "
        f"{format_ap(stats.total_earned)} AP "
        f"(старт {format_ap(stats.starting_balance)}, "
        f"задачи {format_ap(stats.task_earned)}, "
        f"ретро {format_ap(stats.retro_earned)}, "
        f"кэшбек скидки {format_ap(stats.discount_gross)} / "
        f"чистыми {format_ap(stats.discount_net)}, "
        f"премии {format_ap(stats.premium_earned)}, "
        f"прочее {format_ap(stats.other_earned)})"
    )


def format_retro_bonus(details: list[RetroBonusDetail]) -> str:
    if not details:
        return ""
    return format_retro_summary(details, title="Ретроспективная индексация")


def format_retro_summary(details: list[RetroBonusDetail], *, title: str) -> str:
    total = sum((detail.delta for detail in details), Decimal("0.000"))
    total_units = sum(detail.units for detail in details)
    first = details[0]
    vector_bonus = first.vector_multiplier - Decimal("1.000")
    return (
        f"{title}: +{format_ap(total)} AP\n"
        f"Задач: {len(details)} | units: {total_units}\n"
        f"Базовая разница: {format_ap(first.current_base_rate)} - "
        f"{format_ap(first.paid_base_rate)} = "
        f"{format_ap(first.current_base_rate - first.paid_base_rate)} AP\n"
        f"Вектор: +{format_ap(vector_bonus * 100)}% "
        f"(x{format_ap(first.vector_multiplier)})\n"
        f"Формула: {total_units} * "
        f"{format_ap(first.current_base_rate - first.paid_base_rate)} * "
        f"{format_ap(first.vector_multiplier)} = {format_ap(total)} AP"
    )


def print_rows(rows: list[dict], columns: list[str]) -> None:
    if not rows:
        print("Нет записей")
        return
    widths = {
        column: max(len(column), *(len(str(row[column])) for row in rows))
        for column in columns
    }
    print("  ".join(column.ljust(widths[column]) for column in columns))
    print("  ".join("-" * widths[column] for column in columns))
    for row in rows:
        print("  ".join(str(row[column]).ljust(widths[column]) for column in columns))


if __name__ == "__main__":
    raise SystemExit(main())
