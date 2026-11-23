"""WebSocket progress events with cancellation and project-scoped orchestration."""

import asyncio
import base64
import hmac
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.routes.agent import AgentRequest

log = logging.getLogger(__name__)


def create_websocket_router():
    router = APIRouter()

    @router.websocket("/projects/{project_id}/agent/ws")
    async def stream(websocket: WebSocket, project_id: str):
        token = websocket.app.state.settings.api_token
        supplied = websocket.headers.get("x-api-key", "")
        protocols = websocket.scope.get("subprotocols", [])
        if not supplied:
            # Browser WebSocket cannot set X-API-Key. Keep the app token out of
            # query strings/access logs and accept it as a base64url subprotocol.
            encoded = next((p[11:] for p in protocols if p.startswith("volt-token.")), "")
            if encoded and len(encoded) <= 8192:
                try:
                    supplied = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode(
                        "utf-8"
                    )
                except (ValueError, UnicodeDecodeError):
                    supplied = ""
        if token and not hmac.compare_digest(
            supplied.encode("utf-8"), token.get_secret_value().encode("utf-8")
        ):
            await websocket.close(code=1008)
            return
        try:
            websocket.app.state.memory.get(project_id)
        except KeyError:
            await websocket.close(code=1008)
            return
        await websocket.accept(subprotocol="volt-demo" if "volt-demo" in protocols else None)
        task = None

        async def run(payload):
            try:
                request = AgentRequest.model_validate(payload)
                result = await websocket.app.state.orchestrator.run(
                    project_id,
                    request.message,
                    feedback=request.feedback,
                    on_event=websocket.send_json,
                )
                await websocket.send_json({"type": "done", **result})
            except asyncio.CancelledError:
                await websocket.send_json({"type": "cancelled"})
                raise
            except Exception as error:
                log.exception("Agent WebSocket run failed")
                await websocket.send_json({"type": "error", "code": type(error).__name__})

        try:
            while True:
                payload = await websocket.receive_json()
                if not isinstance(payload, dict):
                    await websocket.send_json({"type": "error", "code": "INVALID_REQUEST"})
                    continue
                if payload.get("action") == "cancel":
                    if task and not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                elif task and not task.done():
                    await websocket.send_json({"type": "error", "code": "AGENT_BUSY"})
                else:
                    task = asyncio.create_task(run(payload))
        except WebSocketDisconnect:
            pass
        finally:
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    return router
