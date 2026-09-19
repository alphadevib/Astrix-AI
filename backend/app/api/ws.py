"""WebSocket feed for the mission-control dashboard.

One socket carries every event type — telemetry, detection, diagnosis, risk, plan,
safety, simulation, approval_required, executed, outcome, learning — and the
client filters on `type`. A single connection keeps ordering intact, which matters
because the dashboard is showing a causal chain, not independent widgets.

On connect the server replays the recent non-telemetry events so a browser that
joins mid-demo sees the agent activity that led to the current state.
"""

from __future__ import annotations

import asyncio
import hmac
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from .deps import get_astrix

router = APIRouter(tags=["stream"])

log = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 20.0


@router.websocket("/ws/telemetry")
async def telemetry_socket(websocket: WebSocket) -> None:
    astrix = get_astrix(websocket)  # WebSocket exposes .app like Request does

    # The browser WebSocket API cannot set an Authorization header, so the session
    # token arrives as a query parameter. It is a bearer credential either way; the
    # socket is rejected before `accept()` so an unauthenticated client never sees
    # a frame of telemetry.
    settings = astrix.settings
    if settings.require_auth:
        token = websocket.query_params.get("token", "").strip()
        machine = settings.api_token
        ok = bool(token) and (
            (bool(machine) and hmac.compare_digest(token.encode(), machine.encode())) or astrix.auth.resolve(token) is not None
        )
        if not ok:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Sign in to continue.")
            return

    await websocket.accept()
    bus = astrix.bus
    queue = bus.subscribe()

    try:
        await websocket.send_json(
            {
                "type": "hello",
                "payload": {
                    "app": astrix.settings.app_name,
                    "tagline": astrix.settings.tagline,
                    "health": astrix.health(),
                    "pipeline": astrix.pipeline.status(),
                    "mission": astrix.runner.status() if astrix.runner else {"running": False},
                },
            }
        )
        # Replayed events are history, not news: the client uses them to rebuild the
        # activity feed but must not re-apply them to live state (a replayed
        # `mission_started` would otherwise wipe the charts it just loaded).
        for event in bus.recent(40):
            await websocket.send_json({**event, "replay": True})

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                # Keeps intermediaries from closing an idle socket between faults.
                await websocket.send_json({"type": "heartbeat", "payload": {}})
                continue
            await websocket.send_json(event)

    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 — a dead socket must not take down the bus
        log.debug("websocket closed on error", exc_info=True)
    finally:
        bus.unsubscribe(queue)
