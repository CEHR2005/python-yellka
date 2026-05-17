import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yellka.service import EconomyError, EconomyService


class TrackerTaskTests(unittest.TestCase):
    def make_service(self) -> EconomyService:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return EconomyService(Path(temp.name) / "balance.sqlite3")

    def test_categories_can_be_created_without_completed_tasks(self) -> None:
        service = self.make_service()

        created = service.create_task_category("ИИ врагов")
        closed = service.set_category_completed("ИИ врагов", True)
        categories = service.list_categories()

        self.assertEqual(created["category"], "ИИ врагов")
        self.assertEqual(closed["premium_awarded"], Decimal("0.000"))
        self.assertEqual(categories[0]["category"], "ИИ врагов")
        self.assertEqual(categories[0]["task_count"], 0)
        self.assertEqual(categories[0]["premium_total"], Decimal("0.000"))

    def test_tracker_task_lifecycle_submits_once_for_ap(self) -> None:
        service = self.make_service()
        task = service.create_tracker_task(
            title="поиск игрока",
            category="ИИ врагов",
            units=2,
            vector="code",
            catalog_value="1",
        )

        self.assertEqual(task["status"], "draft")
        self.assertEqual(service.get_state().balance, Decimal("0.000"))

        done = service.mark_tracker_task_done(task["id"])
        submitted = service.submit_tracker_task(task["id"])

        self.assertEqual(done["status"], "done")
        self.assertEqual(submitted["status"], "submitted")
        self.assertEqual(submitted["economy_task_id"], 1)
        self.assertEqual(Decimal(submitted["submitted_reward"]), Decimal("0.400"))
        self.assertEqual(service.get_state().balance, Decimal("0.400"))
        self.assertEqual(service.list_tasks(limit=1)[0]["category"], "ИИ врагов")

        with self.assertRaises(EconomyError):
            service.submit_tracker_task(task["id"])

    def test_submitted_tracker_task_appears_in_history_and_can_be_reverted(self) -> None:
        service = self.make_service()
        task = service.create_tracker_task(
            title="поиск игрока",
            category="ИИ врагов",
            units=2,
        )
        service.mark_tracker_task_done(task["id"])
        submitted = service.submit_tracker_task(task["id"])

        history = service.list_history_entries()

        self.assertEqual(history[0]["kind"], "task_submit")
        self.assertEqual(history[0]["title"], "ИИ врагов: поиск игрока")
        self.assertEqual(history[0]["amount"], "0.400")
        self.assertTrue(history[0]["revertible"])
        self.assertEqual(history[0]["tracker_task_id"], task["id"])

        reverted = service.revert_tracker_task_submission(task["id"])

        self.assertEqual(reverted["status"], "done")
        self.assertIsNone(reverted["economy_task_id"])
        self.assertEqual(reverted["submitted_reward"], "0.000")
        self.assertEqual(service.get_state().balance, Decimal("0.000"))
        self.assertEqual(service.list_tasks(limit=1), [])
        self.assertEqual(service.list_transactions(limit=1), [])

    def test_tracker_task_must_be_done_before_submit(self) -> None:
        service = self.make_service()
        task = service.create_tracker_task(title="Черновик")

        with self.assertRaises(EconomyError):
            service.submit_tracker_task(task["id"])

    def test_submitted_tracker_task_cannot_be_edited(self) -> None:
        service = self.make_service()
        task = service.create_tracker_task(title="Готово")
        service.mark_tracker_task_done(task["id"])
        service.submit_tracker_task(task["id"])

        with self.assertRaises(EconomyError):
            service.update_tracker_task(task["id"], title="Поздно")


if __name__ == "__main__":
    unittest.main()
