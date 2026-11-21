"""One delayed progress message per turn, plus quiet typing indicators."""

import asyncio
from contextlib import suppress

from .presentation import progress_copy


class TurnProgress:
    def __init__(self, inbox, transport, row, stage, delay=5):
        self.inbox, self.transport, self.row = inbox, transport, row
        self.stage, self.delay = progress_copy(stage), delay
        self.task = None

    def update(self, stage):
        self.stage = progress_copy(stage)

    def start(self):
        self.task = asyncio.create_task(self.run())

    async def typing(self):
        if hasattr(self.transport, "typing"):
            with suppress(Exception):
                await asyncio.wait_for(self.transport.typing(self.row["id"]), timeout=3)

    async def run(self):
        await self.typing()
        await asyncio.sleep(self.delay)
        if self.inbox.window_open(self.row["sender"]):
            reply_id = self.inbox.reserve_progress(self.row, self.stage)
            if reply_id is not None:
                try:
                    remote = await self.transport.send(
                        self.row["sender"],
                        {
                            "type": "text",
                            "text": {"body": self.stage},
                        },
                    )
                    self.inbox.mark_reply(reply_id, "sent", remote)
                except BaseException:
                    self.inbox.mark_reply(reply_id, "uncertain")
                    raise
        while True:
            await asyncio.sleep(25)
            await self.typing()

    async def close(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self.task
