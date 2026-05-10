import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def run_cli(self, db_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        return subprocess.run(
            [sys.executable, "-m", "yellka", "--db", str(db_path), *args],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

    def test_help_renders(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        result = subprocess.run(
            [sys.executable, "-m", "yellka", "--help"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertIn("ASSIR/Yellka balance manager", result.stdout)
        self.assertIn("discord", result.stdout)
        self.assertIn("Run Discord bot", result.stdout)
        self.assertNotIn("telegram", result.stdout.lower())

    def test_discord_command_requires_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "balance.sqlite3"
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src")
            env.pop("DISCORD_BOT_TOKEN", None)

            result = subprocess.run(
                [sys.executable, "-m", "yellka", "--db", str(db_path), "discord"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("DISCORD_BOT_TOKEN or --token is required", result.stderr)

    def test_cli_workflow_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "balance.sqlite3"

            self.run_cli(db_path, "init", "--initial-balance", "10")
            self.run_cli(db_path, "buy", "cashback")
            self.run_cli(db_path, "buy", "core")
            completed = self.run_cli(
                db_path,
                "complete",
                "Цепь",
                "--catalog",
                "chain",
                "--units",
                "2",
                "--full-close",
            )
            tasks = self.run_cli(db_path, "tasks")

            self.assertIn("Задача #1", completed.stdout)
            self.assertIn("Цепь", tasks.stdout)


if __name__ == "__main__":
    unittest.main()
