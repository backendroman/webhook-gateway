from uuid import UUID

from fastapi import APIRouter, status

from webhook_gateway.api.deps import EndpointServiceDep
from webhook_gateway.api.schemas import (
    EndpointCreatedOut,
    EndpointCreateIn,
    EndpointOut,
    EndpointPatchIn,
)
from webhook_gateway.domain.enums import EndpointStatus
from webhook_gateway.services.endpoints import EndpointCreate, EndpointPatch

router = APIRouter(prefix="/v1/endpoints", tags=["endpoints"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_endpoint(body: EndpointCreateIn, svc: EndpointServiceDep) -> EndpointCreatedOut:
    endpoint = await svc.create(
        EndpointCreate(
            url=body.url, description=body.description, event_types=tuple(body.event_types)
        )
    )
    return EndpointCreatedOut.from_domain_with_secret(endpoint)


@router.get("/{endpoint_id}")
async def get_endpoint(endpoint_id: UUID, svc: EndpointServiceDep) -> EndpointOut:
    return EndpointOut.from_domain(await svc.get(endpoint_id))


@router.patch("/{endpoint_id}")
async def patch_endpoint(
    endpoint_id: UUID, body: EndpointPatchIn, svc: EndpointServiceDep
) -> EndpointOut:
    patch = EndpointPatch(
        url=body.url,
        description=body.description,
        event_types=tuple(body.event_types) if body.event_types is not None else None,
        status=EndpointStatus(body.status) if body.status is not None else None,
    )
    return EndpointOut.from_domain(await svc.update(endpoint_id, patch))


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint(endpoint_id: UUID, svc: EndpointServiceDep) -> None:
    await svc.delete(endpoint_id)
