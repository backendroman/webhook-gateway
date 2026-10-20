from typing import Annotated

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

from webhook_gateway.api.deps import EventServiceDep
from webhook_gateway.api.schemas import EventAcceptedOut, EventIn

router = APIRouter(prefix="/v1/events", tags=["events"])

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]


@router.post("", status_code=202, response_model=EventAcceptedOut)
async def ingest_event(
    body: EventIn, idempotency_key: IdempotencyKey, svc: EventServiceDep
) -> JSONResponse:
    result = await svc.ingest(idempotency_key, body.type, body.payload)
    headers = {"Idempotent-Replayed": "true"} if result.replayed else None
    return JSONResponse(status_code=result.status_code, content=result.body, headers=headers)
