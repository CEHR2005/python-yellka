import asyncio
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx

from yellka.service import EconomyService
from yellka.web_api import create_app


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, content: str) -> None:
        self.messages.append(content)


class WebApiTests(unittest.TestCase):
    def make_app(self, **kwargs):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        kwargs.setdefault("discord_notifier", None)
        return create_app(
            Path(temp.name) / "balance.sqlite3",
            token="secret",
            **kwargs,
        )

    def run_async(self, coro):
        return asyncio.run(coro)

    def auth(self) -> dict[str, str]:
        return {"Authorization": "Bearer secret"}

    def test_requires_bearer_token(self) -> None:
        async def scenario():
            async with self.client() as client:
                return await client.get("/api/tasks")

        response = self.run_async(scenario())
        self.assertEqual(response.status_code, 401)

    def test_task_lifecycle_endpoints_update_balance(self) -> None:
        async def scenario():
            async with self.client() as client:
                created = await client.post(
                    "/api/tasks",
                    headers=self.auth(),
                    json={
                        "title": "поиск игрока",
                        "category": "ИИ врагов",
                        "units": 2,
                    },
                )
                done = await client.post(
                    f"/api/tasks/{created.json()['id']}/done",
                    headers=self.auth(),
                )
                submitted = await client.post(
                    f"/api/tasks/{created.json()['id']}/submit",
                    headers=self.auth(),
                )
                duplicate = await client.post(
                    f"/api/tasks/{created.json()['id']}/submit",
                    headers=self.auth(),
                )
                balance = await client.get("/api/balance", headers=self.auth())
                return created, done, submitted, duplicate, balance

        created, done, submitted, duplicate, balance = self.run_async(scenario())
        self.assertEqual(created.status_code, 200)
        self.assertEqual(done.json()["status"], "done")
        self.assertEqual(submitted.json()["status"], "submitted")
        self.assertEqual(submitted.json()["submitted_reward"], "0.400")
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(balance.json()["balance"], "0.4")

    def test_task_submit_history_entry_can_revert_submission(self) -> None:
        notifier = FakeNotifier()
        app = self.make_app(discord_notifier=notifier)

        async def scenario():
            async with self.client(app) as client:
                created = await client.post(
                    "/api/tasks",
                    headers=self.auth(),
                    json={
                        "title": "поиск игрока",
                        "category": "ИИ врагов",
                        "units": 2,
                    },
                )
                await client.post(
                    f"/api/tasks/{created.json()['id']}/done",
                    headers=self.auth(),
                )
                submitted = await client.post(
                    f"/api/tasks/{created.json()['id']}/submit",
                    headers=self.auth(),
                )
                history = await client.get("/api/history", headers=self.auth())
                reverted = await client.post(
                    f"/api/tasks/{created.json()['id']}/revert-submit",
                    headers=self.auth(),
                )
                balance = await client.get("/api/balance", headers=self.auth())
                return submitted, history, reverted, balance

        submitted, history, reverted, balance = self.run_async(scenario())

        self.assertEqual(submitted.status_code, 200)
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()[0]["kind"], "task_submit")
        self.assertEqual(history.json()[0]["title"], "ИИ врагов: поиск игрока")
        self.assertEqual(history.json()[0]["amount"], "0.400")
        self.assertTrue(history.json()[0]["revertible"])
        self.assertEqual(reverted.json()["status"], "done")
        self.assertIsNone(reverted.json()["economy_task_id"])
        self.assertEqual(reverted.json()["reverted_total"], "0.400")
        self.assertEqual(balance.json()["balance"], "0")
        self.assertEqual(len(notifier.messages), 2)
        self.assertIn("Yellka: сдача задачи", notifier.messages[0])
        self.assertIn("Yellka: откат сдачи задачи", notifier.messages[1])
        self.assertIn("Откат начисления: -0.4 AP", notifier.messages[1])
        self.assertIn("Баланс AP: 0", notifier.messages[1])

    def test_category_endpoints_create_and_close_empty_category(self) -> None:
        async def scenario():
            async with self.client() as client:
                created = await client.post(
                    "/api/categories",
                    headers=self.auth(),
                    json={"category": "Модификаторы силы"},
                )
                closed = await client.post(
                    "/api/categories/Модификаторы силы/complete",
                    headers=self.auth(),
                )
                categories = await client.get("/api/categories", headers=self.auth())
                return created, closed, categories

        created, closed, categories = self.run_async(scenario())
        self.assertEqual(created.status_code, 200)
        self.assertEqual(closed.json()["premium_awarded"], "0.000")
        self.assertEqual(categories.json()[0]["category"], "Модификаторы силы")

    def test_bootstrap_contains_ui_reference_data(self) -> None:
        async def scenario():
            async with self.client() as client:
                return await client.get("/api/bootstrap", headers=self.auth())

        response = self.run_async(scenario())
        body = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("balance", body)
        self.assertIn("tasks", body)
        self.assertIn("categories", body)
        self.assertIn("catalog", body)
        self.assertIn("vectors", body)
        self.assertIn("wallet", body)
        self.assertIn("shop_catalog", body)
        self.assertIn("history", body)
        self.assertIn("retro_buffer", body)

    def test_retro_buffer_endpoint_lists_and_activates_buffer(self) -> None:
        app = self.make_app()
        service = EconomyService(app.state.db_path)
        service.add_income("60", "Старт")
        service.complete_task(title="Первый таск", units=40)
        service.buy_core()

        async def scenario():
            async with self.client(app) as client:
                listed = await client.get("/api/retro-buffer", headers=self.auth())
                activated = await client.post(
                    "/api/retro-buffer/activate",
                    headers=self.auth(),
                )
                return listed, activated

        listed, activated = self.run_async(scenario())

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["gross"], "2.000")
        self.assertEqual(listed.json()["fee"], "1.000")
        self.assertEqual(listed.json()["net"], "1.000")
        self.assertEqual(listed.json()["tasks"][0]["net_delta"], "1.000")
        self.assertEqual(activated.status_code, 200)
        self.assertEqual(activated.json()["tasks"], [])

    def test_shop_quote_purchase_and_wallet_endpoints(self) -> None:
        async def scenario():
            async with self.client() as client:
                await client.post(
                    "/api/tasks",
                    headers=self.auth(),
                    json={"title": "seed", "units": 60},
                )
                await client.post("/api/tasks/1/done", headers=self.auth())
                await client.post("/api/tasks/1/submit", headers=self.auth())
                quote = await client.post(
                    "/api/shop/quote",
                    headers=self.auth(),
                    json={"item_key": "terminal.core"},
                )
                purchase = await client.post(
                    "/api/shop/purchase",
                    headers=self.auth(),
                    json={"item_key": "terminal.core", "note": "api"},
                )
                wallet = await client.get("/api/wallet", headers=self.auth())
                history = await client.get("/api/shop/purchases", headers=self.auth())
                return quote, purchase, wallet, history

        quote, purchase, wallet, history = self.run_async(scenario())
        self.assertEqual(quote.status_code, 200)
        self.assertEqual(quote.json()["final_cost"], "2.000")
        self.assertEqual(purchase.json()["item_key"], "terminal.core")
        self.assertEqual(wallet.json()["currencies"]["ap"], "10.000")
        self.assertEqual(history.json()[0]["title"], "Ядро Вычислений")

    def test_discord_notifications_for_task_submit_and_purchase(self) -> None:
        notifier = FakeNotifier()
        app = self.make_app(discord_notifier=notifier)

        async def scenario():
            async with self.client(app) as client:
                created = await client.post(
                    "/api/tasks",
                    headers=self.auth(),
                    json={
                        "title": "лендинг",
                        "category": "Сайты",
                        "units": 10,
                    },
                )
                await client.post(
                    f"/api/tasks/{created.json()['id']}/done",
                    headers=self.auth(),
                )
                submitted = await client.post(
                    f"/api/tasks/{created.json()['id']}/submit",
                    headers=self.auth(),
                )
                purchase = await client.post(
                    "/api/shop/purchase",
                    headers=self.auth(),
                    json={"item_key": "terminal.core"},
                )
                return submitted, purchase

        submitted, purchase = self.run_async(scenario())

        self.assertEqual(submitted.status_code, 200)
        self.assertEqual(purchase.status_code, 200)
        self.assertEqual(len(notifier.messages), 2)
        self.assertIn("Yellka: сдача задачи", notifier.messages[0])
        self.assertIn("Сайты: лендинг", notifier.messages[0])
        self.assertIn("Начислено: +2 AP", notifier.messages[0])
        self.assertIn("Расчет:", notifier.messages[0])
        self.assertIn("База: 0.2 AP", notifier.messages[0])
        self.assertIn("Основное: 10u * 0.2 AP = 2 AP", notifier.messages[0])
        self.assertIn("Итого: 2 AP", notifier.messages[0])
        self.assertIn("Yellka: покупка", notifier.messages[1])
        self.assertIn("Ядро Вычислений", notifier.messages[1])
        self.assertIn("Списано: -2 ap", notifier.messages[1])
        self.assertIn("Баланс ap: 0", notifier.messages[1])

    def test_discord_task_submit_shows_crew_bonus_math(self) -> None:
        notifier = FakeNotifier()
        app = self.make_app(discord_notifier=notifier)
        EconomyService(app.state.db_path).create_cabin(
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

        async def scenario():
            async with self.client(app) as client:
                created = await client.post(
                    "/api/tasks",
                    headers=self.auth(),
                    json={"title": "ТЗ", "full_close": True},
                )
                await client.post(
                    f"/api/tasks/{created.json()['id']}/done",
                    headers=self.auth(),
                )
                return await client.post(
                    f"/api/tasks/{created.json()['id']}/submit",
                    headers=self.auth(),
                )

        response = self.run_async(scenario())

        self.assertEqual(response.status_code, 200)
        message = notifier.messages[0]
        self.assertIn("База: 0.225 AP = 0.2 AP Ядро + 0.025 AP crew", message)
        self.assertIn("Вектор: x1.123 = x1 куплено + x0.123 crew", message)
        self.assertIn("Вектор Асуна Юкио (Суб-Администратор): +0.123 = 0.1 * (130 - 7 СД)% = 0.123", message)
        self.assertIn("База Асуна Юкио (Скорость Вспышки): +0.025 = 0.02 * (130 - 7 СД)% = 0.025", message)
        self.assertIn("Основное: 1u * 0.225 AP * x1.123 = 0.253 AP", message)
        self.assertNotIn("Каталог", message)

    def test_discord_notification_for_site_prestige_transactions(self) -> None:
        notifier = FakeNotifier()
        app = self.make_app(discord_notifier=notifier)

        async def scenario():
            async with self.client(app) as client:
                created = await client.post(
                    "/api/tasks",
                    headers=self.auth(),
                    json={"title": "Большая задача", "units": 1000},
                )
                await client.post(
                    f"/api/tasks/{created.json()['id']}/done",
                    headers=self.auth(),
                )
                await client.post(
                    f"/api/tasks/{created.json()['id']}/submit",
                    headers=self.auth(),
                )
                await client.post(
                    "/api/shop/purchase",
                    headers=self.auth(),
                    json={"item_key": "prime.subscription", "quantity": 5},
                )
                return await client.post(
                    "/api/prestige",
                    headers=self.auth(),
                    json={"prime": False},
                )

        response = self.run_async(scenario())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(notifier.messages), 3)
        self.assertIn("Yellka: сингулярный коллапс", notifier.messages[-1])
        self.assertIn("Возврат:", notifier.messages[-1])
        self.assertIn("Осколки:", notifier.messages[-1])

    def test_cabin_dominant_upgrade_endpoint_notifies_discord(self) -> None:
        notifier = FakeNotifier()
        app = self.make_app(discord_notifier=notifier)
        EconomyService(app.state.db_path).add_income("10", "Старт")

        async def scenario():
            async with self.client(app) as client:
                cabin = await client.post(
                    "/api/cabins",
                    headers=self.auth(),
                    json={
                        "sample_code": "02",
                        "name": "Асуна Юкио",
                        "universe": "SAO.V1",
                        "rank": "S",
                        "dominants": [{"name": "Скорость Вспышки", "level": 1}],
                    },
                )
                return await client.post(
                    f"/api/cabins/{cabin.json()['id']}/dominants/1/upgrade",
                    headers=self.auth(),
                )

        response = self.run_async(scenario())

        self.assertEqual(response.status_code, 200)
        self.assertIn('"level": 2', response.json()["dominants"])
        self.assertEqual(response.json()["upgrade_cost"], "3.000")
        self.assertEqual(response.json()["balance_before"], "10.000")
        self.assertEqual(response.json()["balance_after"], "7.000")
        self.assertEqual(EconomyService(app.state.db_path).get_wallet()["currencies"]["ap"], "7.000")
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("Yellka: прокачка черты экипажа", notifier.messages[0])
        self.assertIn("#02 Асуна Юкио (SAO.V1)", notifier.messages[0])
        self.assertIn("Скорость Вспышки: Lv.1 -> Lv.2", notifier.messages[0])
        self.assertIn("Списано: -3 AP", notifier.messages[0])
        self.assertIn("Баланс AP: 10 -> 7", notifier.messages[0])

    def test_cabin_dominant_upgrade_endpoint_uses_shadow_ap_when_real_ap_empty(self) -> None:
        notifier = FakeNotifier()
        app = self.make_app(discord_notifier=notifier)
        service = EconomyService(app.state.db_path)
        with service._connect() as conn:
            service._insert_transaction(
                conn,
                Decimal("5"),
                "seed_shadow",
                "Shadow seed",
                currency="shadow_ap",
            )

        async def scenario():
            async with self.client(app) as client:
                cabin = await client.post(
                    "/api/cabins",
                    headers=self.auth(),
                    json={
                        "sample_code": "02",
                        "name": "Асуна Юкио",
                        "universe": "SAO.V1",
                        "rank": "S",
                        "dominants": [{"name": "Суб-Администратор", "level": 1}],
                    },
                )
                return await client.post(
                    f"/api/cabins/{cabin.json()['id']}/dominants/1/upgrade",
                    headers=self.auth(),
                )

        response = self.run_async(scenario())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["balance_before"], "0.000")
        self.assertEqual(response.json()["balance_after"], "0.000")
        self.assertEqual(response.json()["shadow_balance_before"], "5.000")
        self.assertEqual(response.json()["shadow_balance_after"], "2.000")
        self.assertEqual(EconomyService(app.state.db_path).get_wallet()["currencies"]["ap"], "0.000")
        self.assertEqual(EconomyService(app.state.db_path).get_wallet()["currencies"]["shadow_ap"], "2.000")
        self.assertIn("Баланс AP: 0 -> 0", notifier.messages[0])
        self.assertIn("Баланс shadow_ap: 5 -> 2", notifier.messages[0])

    def test_cabin_defect_excision_endpoint_notifies_discord(self) -> None:
        notifier = FakeNotifier()
        app = self.make_app(discord_notifier=notifier)
        EconomyService(app.state.db_path).add_income("20", "Старт")

        async def scenario():
            async with self.client(app) as client:
                cabin = await client.post(
                    "/api/cabins",
                    headers=self.auth(),
                    json={
                        "sample_code": "01",
                        "name": "Химико Тога",
                        "rank": "S",
                        "recessive_name": "Кровавая Ревность",
                        "recessive_description": "Опасный недостаток.",
                    },
                )
                return await client.post(
                    f"/api/cabins/{cabin.json()['id']}/defect/excise",
                    headers=self.auth(),
                )

        response = self.run_async(scenario())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["recessive_name"], "")
        self.assertEqual(response.json()["excision_cost"], "10.000")
        self.assertEqual(EconomyService(app.state.db_path).get_wallet()["currencies"]["ap"], "10.000")
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("Yellka: иссечение недостатка", notifier.messages[0])
        self.assertIn("Кровавая Ревность", notifier.messages[0])
        self.assertIn("Списано: -10 ap", notifier.messages[0])
        self.assertIn("Баланс ap: 20 -> 10", notifier.messages[0])

    def test_cabin_sr_promotion_endpoint_uses_shadow_ap_and_notifies(self) -> None:
        notifier = FakeNotifier()
        app = self.make_app(discord_notifier=notifier)
        service = EconomyService(app.state.db_path)
        with service._connect() as conn:
            service._set_meta(conn, "prime_active", "1")
            service._insert_transaction(
                conn,
                Decimal("5"),
                "seed_shadow",
                "Shadow seed",
                currency="shadow_ap",
            )

        async def scenario():
            async with self.client(app) as client:
                cabin = await client.post(
                    "/api/cabins",
                    headers=self.auth(),
                    json={
                        "sample_code": "01",
                        "name": "Химико Тога",
                        "rank": "S",
                        "dominants": [
                            {"name": "Мимикрия ДНК", "level": 4},
                            {"name": "Жажда Крови", "level": 4},
                            {"name": "Эмпатия Искажения", "level": 4},
                        ],
                    },
                )
                return await client.post(
                    f"/api/cabins/{cabin.json()['id']}/promote/sr",
                    headers=self.auth(),
                )

        response = self.run_async(scenario())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rank"], "SR")
        self.assertEqual(response.json()["promotion_cost"], "2.250")
        self.assertEqual(EconomyService(app.state.db_path).get_wallet()["currencies"]["shadow_ap"], "2.750")
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("Yellka: повышение ранга", notifier.messages[0])
        self.assertIn("S -> SR, подписка: -2.25 AP", notifier.messages[0])
        self.assertIn("Баланс shadow_ap: 5 -> 2.75", notifier.messages[0])

    def test_manual_domain_endpoints(self) -> None:
        async def scenario():
            async with self.client() as client:
                expedition = await client.post(
                    "/api/expeditions",
                    headers=self.auth(),
                    json={"title": "World A", "difficulty": "hard"},
                )
                cabin = await client.post(
                    "/api/cabins",
                    headers=self.auth(),
                    json={"name": "Cabin A", "rank": "B", "tags": "focus,calm"},
                )
                effects = await client.get("/api/effects", headers=self.auth())
                prime = await client.get("/api/prime", headers=self.auth())
                return expedition, cabin, effects, prime

        expedition, cabin, effects, prime = self.run_async(scenario())
        self.assertEqual(expedition.json()["title"], "World A")
        self.assertEqual(cabin.json()["rank"], "B")
        self.assertEqual(effects.status_code, 200)
        self.assertEqual(prime.json()["active"], False)

    def client(self, app=None) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=app or self.make_app())
        return httpx.AsyncClient(transport=transport, base_url="http://test")


if __name__ == "__main__":
    unittest.main()
