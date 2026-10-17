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

    def is_online(self, user_id: int) -> bool:
        return bool(self.connections.get(user_id))

    async def send_to_user(self, user_id: int, payload: dict) -> None:
        # Every delivery checks durable account/session state. This also
        # covers a socket registered in a different process from deletion.
        from app.core.deps import get_ws_user
        from app.database import SessionLocal

        for ws in list(self.connections.get(user_id, ())):
            try:
                with SessionLocal() as db:
                    user = await get_ws_user(ws, db, required_role=None)
                    if user is None or user.id != user_id:
                        self.disconnect(user_id, ws)
                        continue
                await ws.send_json(payload)
            except Exception:
                self.disconnect(user_id, ws)


manager = ChatConnectionManager()
