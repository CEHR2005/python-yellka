import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx

from yellka.web_api import create_app


class WebApiTests(unittest.TestCase):
    def make_app(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return create_app(Path(temp.name) / "balance.sqlite3", token="secret")

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

    def client(self) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=self.make_app())
        return httpx.AsyncClient(transport=transport, base_url="http://test")


if __name__ == "__main__":
    unittest.main()
