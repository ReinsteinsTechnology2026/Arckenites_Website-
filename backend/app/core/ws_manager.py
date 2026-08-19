from fastapi import WebSocket

# In-memory, per-process connection registry — same "fine at current single-
# process scale, revisit with a shared backend if the API ever runs multiple
# worker processes" trade-off already documented in rate_limit.py.


class ChatConnectionManager:
    def __init__(self) -> None:
        self.connections: dict[int, set[WebSocket]] = {}

    def connect(self, user_id: int, ws: WebSocket) -> None:
        self.connections.setdefault(user_id, set()).add(ws)

    def disconnect(self, user_id: int, ws: WebSocket) -> None:
        sockets = self.connections.get(user_id)
        if not sockets:
            return
        sockets.discard(ws)
        if not sockets:
            self.connections.pop(user_id, None)

    async def send_to_user(self, user_id: int, payload: dict) -> None:
        for ws in list(self.connections.get(user_id, ())):
            try:
                await ws.send_json(payload)
            except Exception:
                self.disconnect(user_id, ws)


manager = ChatConnectionManager()
