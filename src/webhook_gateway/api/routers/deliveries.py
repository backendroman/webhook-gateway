from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from webhook_gateway.api.deps import DeliveryServiceDep
from webhook_gateway.api.schemas import (
    AttemptOut,
    DeliveryDetailsOut,
    DeliveryOut,
    DeliveryPageOut,
)
from webhook_gateway.domain.enums import DeliveryStatus
from webhook_gateway.repositories.deliveries import DeliveryFilter

router = APIRouter(prefix="/v1/deliveries", tags=["deliveries"])


@router.get("")
async def list_deliveries(
    svc: DeliveryServiceDep,
    *,
    status: DeliveryStatus | None = None,
    endpoint_id: UUID | None = None,
    event_id: UUID | None = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DeliveryPageOut:
    page = await svc.list(
        DeliveryFilter(status=status, endpoint_id=endpoint_id, event_id=event_id),
        cursor=cursor,
        limit=limit,
    )
    return DeliveryPageOut(
        items=[DeliveryOut.from_domain(d) for d in page.items], next_cursor=page.next_cursor
    )


@router.get("/{delivery_id}")
async def get_delivery(delivery_id: UUID, svc: DeliveryServiceDep) -> DeliveryDetailsOut:
    details = await svc.get(delivery_id)
    return DeliveryDetailsOut(
        **DeliveryOut.from_domain(details.delivery).model_dump(),
        attempts=[AttemptOut.from_domain(a) for a in details.attempts],
    )


@router.post("/{delivery_id}/replay", status_code=202)
async def replay_delivery(delivery_id: UUID, svc: DeliveryServiceDep) -> DeliveryOut:
    return DeliveryOut.from_domain(await svc.replay(delivery_id))
