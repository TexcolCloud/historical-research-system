"""Drain application-owned streams before Uvicorn waits for active requests."""

import uvicorn


class Server(uvicorn.Server):
    async def shutdown(self, sockets=None):
        self.config.app.state.shutdown_requested.set()
        await super().shutdown(sockets)
