from __future__ import annotations

import argparse
import os
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from .catalog import CATALOG_ITEMS, VECTORS
from .discord_bot import run_discord_bot
from .money import format_ap, parse_ap
from .service import EconomyError, EconomyService


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
    buy_subparsers.add_parser("cashback", help="Upgrade cashback by +5%%")
    buy_subparsers.add_parser("retro", help="Buy retroactive indexing")

    tasks = subparsers.add_parser("tasks", help="Show completed tasks")
    tasks.add_argument("--limit", type=int, default=20)

    transactions = subparsers.add_parser("transactions", help="Show transaction log")
    transactions.add_argument("--limit", type=int, default=20)

    upgrades = subparsers.add_parser("upgrades", help="Show bought upgrades")
    upgrades.add_argument("--limit", type=int, default=20)

    catalog = subparsers.add_parser("catalog", help="Show task catalog")
    catalog.add_argument("query", nargs="?")

    discord = subparsers.add_parser("discord", help="Run Discord bot")
    discord.add_argument("--token", default=os.environ.get("DISCORD_BOT_TOKEN"))
    discord.add_argument("--prefix", default=os.environ.get("YELLKA_DISCORD_PREFIX", "!"))

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    service = EconomyService(args.db)

    try:
        if args.command == "init":
            state = service.initialize(
                initial_balance=parse_ap(args.initial_balance),
                update_bonus=args.update_bonus,
            )
            print_state(state)
        elif args.command == "balance":
            print_state(service.get_state())
        elif args.command == "earn":
            service.add_income(Decimal(args.amount.replace(",", ".")), args.note)
            print_state(service.get_state())
        elif args.command == "spend":
            service.add_expense(
                Decimal(args.amount.replace(",", ".")),
                args.note,
                allow_negative=args.allow_negative,
            )
            print_state(service.get_state())
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
                print(f"Ретроспективная индексация: +{format_ap(result.retro_bonus)} AP")
            print_state(service.get_state())
        elif args.command == "buy":
            result = buy_upgrade(service, args)
            print(f"Улучшение #{result.id}: -{format_ap(result.cost)} AP")
            if result.cashback:
                print(f"Кэшбек: +{format_ap(result.cashback)} AP")
            print_state(service.get_state())
        elif args.command == "tasks":
            print_rows(
                service.list_tasks(limit=args.limit),
                ["id", "created_at", "title", "vector", "units", "reward"],
            )
        elif args.command == "transactions":
            print_rows(
                service.list_transactions(limit=args.limit),
                ["id", "created_at", "amount", "kind", "note"],
            )
        elif args.command == "upgrades":
            print_rows(
                service.list_upgrades(limit=args.limit),
                ["id", "created_at", "upgrade_type", "target", "level_after", "cost", "cashback"],
            )
        elif args.command == "catalog":
            for item in CATALOG_ITEMS:
                if args.query and args.query.casefold() not in item.title.casefold() and args.query.casefold() not in item.key.casefold():
                    continue
                print(f"{item.key:22} {format_ap(item.value):>6}  {item.title}")
        elif args.command == "discord":
            run_discord_bot(args.token, Path(args.db), command_prefix=args.prefix)
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


def print_state(state) -> None:
    print(f"Баланс: {format_ap(state.balance)} AP")
    print(f"База Ядра: {format_ap(state.base_rate)} AP")
    print(f"Кэшбек: {state.cashback_level * 5}%")
    print(
        "Ретроспективная индексация: "
        + ("включена" if state.retroactive_indexing_enabled else "выключена")
    )
    vectors = ", ".join(
        f"{key}=+{level * 10}%" for key, level in state.vector_levels.items()
    )
    print(f"Векторы: {vectors}")


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
