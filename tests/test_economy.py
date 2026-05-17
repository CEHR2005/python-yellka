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
        self.assertEqual(first_core.cost, Decimal("1.900"))
        self.assertEqual(first_core.cashback, Decimal("0.100"))
        self.assertEqual(first_vector.cost, Decimal("0.475"))
        self.assertEqual(first_vector.cashback, Decimal("0.025"))

        state = service.get_state()
        self.assertEqual(state.base_rate, Decimal("0.250"))
        self.assertEqual(state.cashback_level, 1)
        self.assertEqual(state.vector_levels["code"], 1)
        self.assertEqual(state.balance, Decimal("14.625"))

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
        self.assertEqual(quote.full_cost, Decimal("2.000"))
        self.assertEqual(quote.discount, Decimal("0.100"))
        self.assertEqual(quote.final_cost, Decimal("1.900"))

    def test_historical_upgrade_spend_matches_spreadsheet_totals(self) -> None:
        service = self.make_service()
        with service._connect() as conn:
            service._set_meta(conn, "base_rate", "1.900")
            service._set_meta(conn, "cashback_level", "5")
            service._set_meta(conn, "retroactive_indexing_enabled", "1")
            service._set_meta(conn, "vector_level:code", "10")

        estimate = service.estimate_upgrade_spend()

        # Shop 3.0 spend: core cost uses current_base * 10, cashback levels cost 3+i.
        self.assertEqual(estimate.discount_spent, Decimal("25.000"))
        self.assertEqual(estimate.discount_saved, Decimal("72.875"))
        self.assertEqual(estimate.retroactive_indexing_spent, Decimal("20.000"))
        self.assertEqual(estimate.core_spent, Decimal("275.625"))
        self.assertEqual(estimate.vector_spent_by_key["code"], Decimal("27.500"))
        self.assertEqual(estimate.total_spent, Decimal("348.125"))

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

        self.assertEqual(stats.total_earned, Decimal("383.995"))
        self.assertEqual(stats.starting_balance, Decimal("24.000"))
        self.assertEqual(stats.task_earned, Decimal("7.600"))
        self.assertEqual(stats.retro_earned, Decimal("0.000"))
        self.assertEqual(stats.discount_gross, Decimal("18.270"))
        self.assertEqual(stats.discount_net, Decimal("-6.730"))
        self.assertEqual(stats.premium_and_other_earned, Decimal("334.125"))

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
        service.add_income(Decimal("60"), "Старт")
        original = service.complete_task(title="Первый таск", vector="code", units=40)
        service.buy_core()
        retro = service.buy_retroactive_indexing()
        followup = service.complete_task(title="Новый таск", vector="code", units=1)

        self.assertEqual(original.reward, Decimal("8.000"))
        self.assertEqual(retro.cost, Decimal("1.000"))
        self.assertEqual(retro.cashback, Decimal("1.000"))
        self.assertEqual(followup.reward, Decimal("0.250"))
        self.assertEqual(followup.retro_bonus, Decimal("0.000"))
        self.assertEqual(len(followup.retro_details), 0)

        transactions = service.list_transactions(limit=10)
        retro = [row for row in transactions if row["kind"] == "retro_bonus"]
        self.assertEqual(len(retro), 1)
        # Previous task delta: 40 units * (0.25 - 0.20), minus the 1 AP buffer fee.
        self.assertEqual(Decimal(retro[0]["amount"]), Decimal("1.000"))

        original_task = [row for row in service.list_tasks(limit=10) if row["id"] == original.id][0]
        self.assertEqual(Decimal(original_task["reward"]), Decimal("8.000"))
        self.assertEqual(Decimal(original_task["current_reward"]), Decimal("10.000"))

    def test_retro_buffer_lists_taxed_tasks_and_burns_after_activation(self) -> None:
        service = self.make_service()
        service.add_income(Decimal("60"), "Старт")
        service.complete_task(title="Первый таск", vector="code", units=40)
        service.buy_core()

        buffer = service.get_retro_buffer()

        self.assertEqual(buffer["eligible_count"], 1)
        self.assertEqual(buffer["gross"], "2.000")
        self.assertEqual(buffer["fee"], "1.000")
        self.assertEqual(buffer["net"], "1.000")
        self.assertTrue(buffer["activation_allowed"])
        self.assertEqual(buffer["tasks"][0]["gross_delta"], "2.000")
        self.assertEqual(buffer["tasks"][0]["fee_share"], "1.000")
        self.assertEqual(buffer["tasks"][0]["net_delta"], "1.000")

        service.buy_retroactive_indexing()

        burned = service.get_retro_buffer()
        self.assertEqual(burned["eligible_count"], 0)
        self.assertEqual(burned["tasks"], [])

    def test_prestige_resets_fact_ap_and_clears_retro_buffer_start(self) -> None:
        service = self.make_service()
        service.complete_task(title="Старая задача", vector="code", units=10)

        prestige = service.run_prestige()

        self.assertEqual(prestige["refund_currency"], "shadow_ap")
        self.assertEqual(service.get_wallet()["currencies"]["ap"], "0.000")
        self.assertEqual(service.get_retro_buffer()["tasks"], [])

    def test_shop_quote_and_purchase_record_history(self) -> None:
        service = self.make_service()
        service.add_income(Decimal("20"), "Старт")

        quote = service.quote_shop_purchase("terminal.core")
        purchase = service.buy_shop_item("terminal.core", note="Shop UI")

        self.assertEqual(quote["full_cost"], "2.000")
        self.assertEqual(purchase["item_key"], "terminal.core")
        self.assertEqual(purchase["final_cost"], "2.000")
        self.assertEqual(service.get_state().base_rate, Decimal("0.250"))
        self.assertEqual(service.get_wallet()["currencies"]["ap"], "18.000")

    def test_noctur_shard_purchase_and_prime_purchase_use_new_wallets(self) -> None:
        service = self.make_service()
        service.add_income(Decimal("25"), "Старт")
        with service._connect() as conn:
            service._insert_transaction(
                conn,
                Decimal("4"),
                "seed_shards",
                "Shard seed",
                currency="singularity_shard",
            )

        noctur = service.buy_shop_item("noctur.core_rewrite")
        prime = service.buy_shop_item("prime.subscription")

        self.assertEqual(noctur["currency"], "singularity_shard")
        self.assertEqual(prime["final_cost"], "20.000")
        wallet = service.get_wallet()["currencies"]
        self.assertEqual(wallet["singularity_shard"], "0.000")
        self.assertEqual(wallet["ap"], "5.000")

    def test_shop_catalog_exposes_shop_35_noctur_rebalance(self) -> None:
        service = self.make_service()
        catalog = {item["key"]: item for item in service.list_shop_catalog()}

        self.assertEqual(catalog["terminal.cashback"]["section"], "legacy")
        self.assertEqual(catalog["hub.optimization"]["section"], "legacy")
        self.assertEqual(catalog["noctur.cascade"]["section"], "legacy")
        self.assertEqual(catalog["noctur.absolute_limit"]["section"], "legacy")
        self.assertEqual(catalog["noctur.devaluation"]["section"], "noctur")
        self.assertEqual(catalog["noctur.devaluation"]["base_cost"], "2.000")
        self.assertEqual(catalog["noctur.shadow_investment"]["base_cost"], "6.000")
        self.assertEqual(catalog["noctur.quantum_archive"]["base_cost"], "1.000")
        self.assertEqual(catalog["noctur.author_right"]["title"], "Право Редактора")

    def test_shop_35_devaluation_discount_applies_to_global_shop_items(self) -> None:
        service = self.make_service()
        with service._connect() as conn:
            service._set_shop_level(conn, "noctur.devaluation", 3)

        prime = service.quote_shop_purchase("prime.subscription")
        target_world = service.quote_shop_purchase("expedition.target_request")
        hub = service.quote_shop_purchase("hub.scanning")

        self.assertEqual(prime["full_cost"], "20.000")
        self.assertEqual(prime["discount"], "6.000")
        self.assertEqual(prime["final_cost"], "14.000")
        self.assertEqual(target_world["final_cost"], "2.100")
        self.assertEqual(hub["final_cost"], "10.000")

    def test_shop_35_core_rewrite_uses_shadow_base_for_future_core_costs(self) -> None:
        service = self.make_service()
        with service._connect() as conn:
            service._set_shop_level(conn, "noctur.core_rewrite", 1)

        first = service.quote_core_upgrade()
        service.add_income(Decimal("20"), "Старт")
        service.buy_core()
        second = service.quote_core_upgrade()

        self.assertEqual(first.full_cost, Decimal("2.000"))
        self.assertEqual(first.level_after, Decimal("0.300"))
        self.assertEqual(second.full_cost, Decimal("2.500"))
        self.assertEqual(second.level_before, Decimal("0.300"))
        self.assertEqual(second.level_after, Decimal("0.400"))

    def test_crew_samples_store_traits_and_can_be_managed(self) -> None:
        service = self.make_service()

        cabin = service.create_cabin(
            sample_code="01",
            name="Химико Тога",
            universe="BnHA",
            rank="S",
            tags="[Современность], [Авангард], [Нестабильность]",
            full_tags="[Современность], [Авангард], [Нестабильность]",
            sedative_dose="37",
            upkeep="1.2",
            subscription_tier="S",
            subscription_started_at="2026-05-13",
            recessive_name="Кровавая Ревность",
            recessive_description="Изоляция каюты стоит 10 AP.",
            dominants=[
                {"name": "Мимикрия ДНК", "level": 1},
                {"name": "Жажда Крови", "level": 1},
            ],
            note="ASSIR PRIME upkeep.",
        )

        self.assertEqual(cabin["sample_code"], "01")
        self.assertEqual(cabin["sedative_dose"], "37.000")
        self.assertEqual(cabin["base_upkeep"], "1.200")
        self.assertEqual(cabin["effective_upkeep"], "1.200")
        self.assertEqual(cabin["subscription_tier"], "S")
        self.assertIn("Мимикрия ДНК", cabin["dominants"])

        updated = service.update_cabin(
            cabin["id"],
            rank="SR",
            dominants=[{"name": "Мимикрия ДНК", "level": 2}],
            active=False,
        )

        self.assertEqual(updated["rank"], "SR")
        self.assertEqual(updated["active"], 0)
        self.assertIn('"level": 2', updated["dominants"])
        self.assertEqual(len(service.list_cabins()), 1)

        deleted = service.delete_cabin(cabin["id"])
        self.assertEqual(deleted["name"], "Химико Тога")
        self.assertEqual(service.list_cabins(), [])

    def test_prime_discount_applies_to_crew_upkeep_summary(self) -> None:
        service = self.make_service()
        for name in ["Химико Тога", "Асуна Юкио", "Тай Ли"]:
            service.create_cabin(name=name, rank="S", upkeep="1.2", subscription_tier="S")

        inactive = service.crew_upkeep_summary()
        self.assertEqual(inactive["base_total"], "3.600")
        self.assertEqual(inactive["discount_total"], "0.000")
        self.assertEqual(inactive["effective_total"], "3.600")

        with service._connect() as conn:
            service._set_meta(conn, "prime_active", "1")
            service._set_meta(conn, "prime_active_since", "2026-05-12")

        active = service.crew_upkeep_summary()
        self.assertEqual(service.prime_status()["active_since"], "2026-05-12")
        self.assertEqual(active["base_total"], "3.600")
        self.assertEqual(active["discount_total"], "0.900")
        self.assertEqual(active["effective_total"], "2.700")

    def test_cabin_defect_excision_spends_ap_and_clears_recessive(self) -> None:
        service = self.make_service()
        service.add_income("20", "Старт")
        cabin = service.create_cabin(
            name="Химико Тога",
            rank="S",
            recessive_name="Кровавая Ревность",
            recessive_description="Опасный недостаток.",
        )

        updated = service.excise_cabin_defect(cabin["id"])

        self.assertEqual(updated["excised_defect"], "Кровавая Ревность")
        self.assertEqual(updated["excision_cost"], "10.000")
        self.assertEqual(updated["recessive_name"], "")
        self.assertEqual(updated["recessive_description"], "")
        self.assertEqual(service.get_wallet()["currencies"]["ap"], "10.000")
        self.assertEqual(service.list_shop_purchases(limit=1)[0]["item_key"], "genesis.defect_excision")

    def test_sr_promotion_requires_level_four_dominants_and_uses_shadow_ap(self) -> None:
        service = self.make_service()
        with service._connect() as conn:
            service._set_meta(conn, "prime_active", "1")
            service._insert_transaction(
                conn,
                Decimal("5"),
                "seed_shadow",
                "Shadow seed",
                currency="shadow_ap",
            )
        cabin = service.create_cabin(
            name="Химико Тога",
            rank="S",
            upkeep="1.2",
            dominants=[
                {"name": "Мимикрия ДНК", "level": 4},
                {"name": "Жажда Крови", "level": 4},
                {"name": "Эмпатия Искажения", "level": 4},
            ],
        )

        promoted = service.promote_cabin_to_sr(cabin["id"])

        self.assertEqual(promoted["rank"], "SR")
        self.assertEqual(promoted["upkeep"], "3.000")
        self.assertEqual(promoted["effective_upkeep"], "2.250")
        self.assertEqual(promoted["promotion_cost"], "2.250")
        wallet = service.get_wallet()["currencies"]
        self.assertEqual(wallet["ap"], "0.000")
        self.assertEqual(wallet["shadow_ap"], "2.750")
        rows = service.list_transactions(limit=2)
        self.assertEqual(rows[0]["kind"], "shop_purchase")
        self.assertEqual(rows[0]["amount"], "-2.250")

    def test_terminal_upgrades_can_spend_shadow_ap_when_real_ap_is_empty(self) -> None:
        service = self.make_service()
        with service._connect() as conn:
            service._insert_transaction(
                conn,
                Decimal("5"),
                "seed_shadow",
                "Shadow seed",
                currency="shadow_ap",
            )

        result = service.buy_core()

        self.assertEqual(result.cost, Decimal("2.000"))
        wallet = service.get_wallet()["currencies"]
        self.assertEqual(wallet["ap"], "0.000")
        self.assertEqual(wallet["shadow_ap"], "3.000")

    def test_cabin_dominant_upgrade_spends_shadow_ap_when_real_ap_is_empty(self) -> None:
        service = self.make_service()
        with service._connect() as conn:
            service._insert_transaction(
                conn,
                Decimal("5"),
                "seed_shadow",
                "Shadow seed",
                currency="shadow_ap",
            )
        cabin = service.create_cabin(
            name="Асуна Юкио",
            rank="S",
            dominants=[{"name": "Суб-Администратор", "level": 1}],
        )

        updated = service.upgrade_cabin_dominant(cabin["id"], 1)

        self.assertEqual(updated["balance_before"], "0.000")
        self.assertEqual(updated["balance_after"], "0.000")
        self.assertEqual(updated["shadow_balance_before"], "5.000")
        self.assertEqual(updated["shadow_balance_after"], "2.000")
        wallet = service.get_wallet()["currencies"]
        self.assertEqual(wallet["ap"], "0.000")
        self.assertEqual(wallet["shadow_ap"], "2.000")

    def test_dominant_upgrade_cannot_exceed_rank_limit(self) -> None:
        service = self.make_service()
        service.add_income("10", "Старт")
        cabin = service.create_cabin(
            name="Асуна Юкио",
            rank="S",
            dominants=[{"name": "Суб-Администратор", "level": 3}],
        )

        with self.assertRaises(Exception):
            service.upgrade_cabin_dominant(cabin["id"], 1)

    def test_prime_extends_dominant_limit_by_one(self) -> None:
        service = self.make_service()
        service.add_income("10", "Старт")
        with service._connect() as conn:
            service._set_meta(conn, "prime_active", "1")
        cabin = service.create_cabin(
            name="Асуна Юкио",
            rank="S",
            dominants=[{"name": "Суб-Администратор", "level": 3}],
        )

        upgraded = service.upgrade_cabin_dominant(cabin["id"], 1)

        self.assertEqual(upgraded["level_after"], 4)
        self.assertEqual(upgraded["dominant_max_level"], 4)
        with self.assertRaises(Exception):
            service.upgrade_cabin_dominant(cabin["id"], 1)

    def test_create_cabin_rejects_dominant_above_rank_limit(self) -> None:
        service = self.make_service()

        with self.assertRaises(ValueError):
            service.create_cabin(
                name="Асуна Юкио",
                rank="S",
                dominants=[{"name": "Суб-Администратор", "level": 4}],
            )

    def test_update_cabin_rejects_dominant_above_rank_limit(self) -> None:
        service = self.make_service()
        cabin = service.create_cabin(
            name="Асуна Юкио",
            rank="S",
            dominants=[{"name": "Суб-Администратор", "level": 3}],
        )

        with self.assertRaises(ValueError):
            service.update_cabin(
                cabin["id"],
                dominants=[{"name": "Суб-Администратор", "level": 4}],
            )

    def test_active_crew_dominants_apply_to_task_rewards(self) -> None:
        service = self.make_service()
        service.create_cabin(
            sample_code="02",
            name="Асуна Юкио",
            universe="SAO.V1",
            rank="S",
            sedative_dose="7",
            dominants=[
                {"name": "Суб-Администратор", "level": 1},
                {"name": "Скорость Вспышки", "level": 1},
            ],
        )

        task = service.complete_task(title="Кодовая задача", vector="code", units=1)

        # S-rank cap 130% - 7% SD = 123% efficiency.
        # Base: 0.2 + (0.02 * 1.23) => 0.225.
        # Code multiplier: 1 + (0.10 * 1.23) => 1.123.
        self.assertEqual(task.reward, Decimal("0.253"))
        row = service.list_tasks(limit=1)[0]
        self.assertEqual(Decimal(row["base_rate"]), Decimal("0.225"))
        self.assertEqual(Decimal(row["vector_multiplier"]), Decimal("1.123"))
        self.assertEqual(Decimal(row["crew_vector_bonus"]), Decimal("0.123"))

    def test_crew_vector_bonus_is_part_of_vector_multiplier(self) -> None:
        service = self.make_service()
        service.create_cabin(
            sample_code="02",
            name="Асуна Юкио",
            universe="SAO.V1",
            rank="S",
            sedative_dose="0",
            dominants=[{"name": "Суб-Администратор", "level": 1}],
        )

        task = service.complete_task(
            title="Закрыть ТЗ",
            vector="code",
            units=1,
            full_close=True,
        )

        self.assertEqual(task.reward, Decimal("0.339"))
        row = service.list_tasks(limit=1)[0]
        self.assertEqual(Decimal(row["vector_multiplier"]), Decimal("1.130"))
        self.assertEqual(Decimal(row["full_close_bonus"]), Decimal("1.500"))
        self.assertEqual(Decimal(row["crew_vector_bonus"]), Decimal("0.130"))

    def test_active_crew_dominants_apply_shop_flat_discounts(self) -> None:
        service = self.make_service()
        service.create_cabin(
            sample_code="01",
            name="Химико Тога",
            rank="S",
            sedative_dose="37",
            dominants=[{"name": "Мимикрия ДНК", "level": 1}],
        )
        service.create_cabin(
            sample_code="03",
            name="Тай Ли",
            rank="S",
            sedative_dose="0.5",
            dominants=[{"name": "Блокировка Точек", "level": 1}],
        )

        infiltrator = service.quote_shop_purchase("world.infiltrator")
        skip = service.quote_shop_purchase("world.skip", options={"obstacles": 1})

        self.assertEqual(infiltrator["full_cost"], "0.500")
        self.assertEqual(infiltrator["discount"], "0.400")
        self.assertEqual(infiltrator["final_cost"], "0.100")
        self.assertEqual(skip["full_cost"], "1.000")
        self.assertEqual(skip["discount"], "0.300")
        self.assertEqual(skip["final_cost"], "0.700")

    def test_active_crew_full_close_bonus_applies_to_task_rewards(self) -> None:
        service = self.make_service()
        service.create_cabin(
            sample_code="03",
            name="Тай Ли",
            rank="S",
            sedative_dose="0.5",
            dominants=[{"name": "Чтение Ауры", "level": 1}],
        )

        task = service.complete_task(title="Закрыть ТЗ", vector="code", units=1, full_close=True)

        self.assertEqual(task.reward, Decimal("0.308"))
        self.assertEqual(Decimal(service.list_tasks(limit=1)[0]["full_close_bonus"]), Decimal("1.539"))

    def test_crew_tag_set_bonuses_apply_to_shop_quotes_and_rewards(self) -> None:
        service = self.make_service()
        for index, tags in enumerate(
            [
                "[Киберпространство], [Фэнтези], [Эрудит], [Нестабильность], [Постапокалипсис]",
                "[Киберпространство], [Фэнтези], [Эрудит], [Нестабильность], [Постапокалипсис]",
                "[Киберпространство], [Фэнтези], [Эрудит], [Нестабильность], [Постапокалипсис]",
            ],
            start=1,
        ):
            service.create_cabin(
                sample_code=f"T{index}",
                name=f"Тест #{index}",
                rank="S",
                tags=tags,
            )

        vector = service.quote_vector_upgrade("code")
        adaptation = service.quote_shop_purchase("world.adaptation")
        prerequisite = service.quote_shop_purchase("world.prerequisite")
        intel = service.quote_shop_purchase("expedition.intel")
        extractor = service.quote_shop_purchase("hub.extractor")
        task = service.complete_task(title="Закрыть ТЗ", vector="code", units=1, full_close=True)

        self.assertEqual(vector.full_cost, Decimal("0.500"))
        self.assertEqual(vector.discount, Decimal("0.075"))
        self.assertEqual(vector.final_cost, Decimal("0.425"))
        self.assertEqual(adaptation["final_cost"], "0.700")
        self.assertEqual(prerequisite["final_cost"], "0.700")
        self.assertEqual(intel["final_cost"], "0.000")
        self.assertEqual(extractor["final_cost"], "0.250")
        self.assertEqual(task.reward, Decimal("0.360"))
        self.assertEqual(Decimal(service.list_tasks(limit=1)[0]["full_close_bonus"]), Decimal("1.800"))

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
        repeated = service.set_category_completed("ИИ врагов", True)
        categories = service.list_categories()

        self.assertEqual(updated["category"], "ИИ врагов")
        self.assertEqual(int(updated["completed"]), 1)
        self.assertEqual(updated["premium_awarded"], Decimal("0.200"))
        self.assertEqual(updated["premium_task_count"], 2)
        self.assertEqual(repeated["premium_awarded"], Decimal("0.000"))
        self.assertEqual(repeated["premium_task_count"], 0)
        self.assertEqual(service.get_state().balance, Decimal("0.600"))
        self.assertEqual(len(categories), 1)
        self.assertEqual(categories[0]["category"], "ИИ врагов")
        self.assertEqual(int(categories[0]["completed"]), 1)
        self.assertEqual(int(categories[0]["task_count"]), 2)
        self.assertEqual(int(categories[0]["premium_pending_count"]), 0)
        self.assertEqual(categories[0]["reward_total"], Decimal("0.400"))
        self.assertEqual(categories[0]["reward_formula"], "0.2x2 = 0.4")
        self.assertEqual(categories[0]["premium_total"], Decimal("0.200"))
        self.assertEqual(categories[0]["premium_pending_total"], Decimal("0.000"))

    def test_category_premium_is_half_of_total_original_reward(self) -> None:
        service = self.make_service()
        service.complete_task(title="ИИ врагов: поиск игрока", catalog_value="0.770")
        service.complete_task(title="ИИ врагов: специальные атаки", catalog_value="0.825")

        category = service.list_categories()[0]

        self.assertEqual(category["reward_total"], Decimal("0.319"))
        self.assertEqual(category["reward_formula"], "0.154 + 0.165 = 0.319")
        self.assertEqual(category["premium_total"], Decimal("0.160"))

    def test_reading_aura_adds_to_category_premium_rate(self) -> None:
        service = self.make_service()
        service.create_cabin(
            name="Тай Ли",
            rank="SR",
            dominants=[{"name": "Чтение Ауры", "level": 4}],
        )
        service.complete_task(title="ИИ врагов: поиск игрока", units=1)
        service.complete_task(title="ИИ врагов: движение к игроку", units=1)

        category = service.list_categories()[0]
        updated = service.set_category_completed("ИИ врагов", True)

        self.assertEqual(category["premium_rate"], Decimal("0.550"))
        self.assertEqual(category["premium_bonus_rate"], Decimal("0.050"))
        self.assertEqual(category["premium_total"], Decimal("0.220"))
        self.assertEqual(updated["premium_awarded"], Decimal("0.220"))
        self.assertEqual(updated["premium_rate"], Decimal("0.550"))
        self.assertEqual(updated["premium_bonus_details"][0]["trait"], "Чтение Ауры")

    def test_earned_ap_timeline_tracks_positive_ap_transactions(self) -> None:
        service = self.make_service()
        service.complete_task(title="Первый", units=2)
        service.add_expense("0.1", "Tax")
        service.complete_task(title="Второй", units=1)

        timeline = service.earned_ap_timeline()

        self.assertEqual(timeline["total"], "0.600")
        self.assertEqual([point["cumulative"] for point in timeline["points"]], ["0.400", "0.600"])

    def test_upgrade_efficiency_compares_core_and_selected_vector_impact(self) -> None:
        service = self.make_service()

        report = service.quote_upgrade_efficiency()

        self.assertEqual(report["vector"], "code")
        self.assertEqual(report["units"], 1)
        self.assertEqual(report["base_rate"], "0.200")
        self.assertEqual(report["vector_multiplier"], "1.000")
        self.assertEqual(report["core_step"], "0.050")
        code = next(entry for entry in report["entries"] if entry["target"] == "code")
        core = next(entry for entry in report["entries"] if entry["kind"] == "core")
        self.assertEqual(code["impact"], "0.020")
        self.assertEqual(code["cost"], "0.500")
        self.assertEqual(code["impact_per_ap"], "0.040")
        self.assertEqual(core["impact"], "0.050")
        self.assertEqual(core["cost"], "2.000")
        self.assertEqual(core["impact_per_ap"], "0.025")

    def test_upgrade_efficiency_can_switch_vector(self) -> None:
        service = self.make_service()

        report = service.quote_upgrade_efficiency("modeling")

        self.assertEqual(report["vector"], "modeling")
        self.assertEqual(
            [entry["target"] for entry in report["entries"]],
            ["modeling", "Ядро"],
        )

    def test_upgrade_efficiency_uses_actual_core_step(self) -> None:
        service = self.make_service()
        with service._connect() as conn:
            service._set_shop_level(conn, "noctur.core_rewrite", 1)
            service._set_meta(conn, "vector_level:code", "10")

        report = service.quote_upgrade_efficiency("code")

        core = next(entry for entry in report["entries"] if entry["kind"] == "core")
        self.assertEqual(report["core_step"], "0.100")
        self.assertEqual(report["vector_multiplier"], "2.000")
        self.assertEqual(core["impact"], "0.200")

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
