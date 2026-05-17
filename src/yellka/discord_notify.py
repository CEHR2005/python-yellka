from __future__ import annotations

import os
from dataclasses import dataclass

import httpx


DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_MESSAGE_LIMIT = 2000


@dataclass(frozen=True)
class DiscordTransactionNotifier:
    token: str
    channel_id: str

    @classmethod
    def from_env(cls) -> DiscordTransactionNotifier | None:
        token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        channel_id = (
            os.environ.get("YELLKA_DISCORD_TRANSACTION_CHANNEL_ID", "").strip()
            or os.environ.get("YELLKA_DISCORD_STARTUP_CHANNEL_ID", "").strip()
        )
        if not token or not channel_id:
            return None
        return cls(token=token, channel_id=channel_id)

    async def send(self, content: str) -> None:
        content = content.strip()
        if not content:
            return
        if len(content) > DISCORD_MESSAGE_LIMIT:
            content = content[: DISCORD_MESSAGE_LIMIT - 3] + "..."
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.post(
                f"{DISCORD_API_BASE}/channels/{self.channel_id}/messages",
                headers={
                    "Authorization": f"Bot {self.token}",
                    "Content-Type": "application/json",
                },
                json={
                    "content": content,
                    "allowed_mentions": {"parse": []},
                },
            )
            response.raise_for_status()
