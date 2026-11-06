"""Drain application-owned streams before Uvicorn waits for active requests."""

import uvicorn

from hrs.runtime import lifecycle_lock, register_server


class Server(uvicorn.Server):
    async def startup(self, sockets=None):
        self.runtime_receipt = None
        settings = self.config.app.state.settings
        with lifecycle_lock(settings.project_root):
            try:
                await super().startup(sockets)
                if not self.started:
                    return
                listener = next(sock for server in self.servers for sock in server.sockets)
                host, port = listener.getsockname()[:2]
                host = "127.0.0.1" if host == "0.0.0.0" else "::1" if host == "::" else host
                origin = f"http://{'[' + host + ']' if ':' in host else host}:{port}"
                self.runtime_receipt = register_server(settings, origin)
            except BaseException:
                if self.started:
                    await self.shutdown(sockets)
                raise

    async def shutdown(self, sockets=None):
        self.config.app.state.shutdown_requested.set()
        try:
            await super().shutdown(sockets)
        finally:
            if getattr(self, "runtime_receipt", None):
                self.runtime_receipt.unlink(missing_ok=True)
