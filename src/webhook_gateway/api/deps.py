from typing import Annotated, cast

from fastapi import Depends, Request

from webhook_gateway.api.container import Container
from webhook_gateway.services.deliveries import DeliveryService
from webhook_gateway.services.endpoints import EndpointService
from webhook_gateway.services.events import EventService


def get_container(request: Request) -> Container:
    return cast(Container, request.app.state.container)


ContainerDep = Annotated[Container, Depends(get_container)]


def get_endpoint_service(c: ContainerDep) -> EndpointService:
    return EndpointService(c.sessionmaker, c.url_validator, c.clock)


EndpointServiceDep = Annotated[EndpointService, Depends(get_endpoint_service)]


def get_event_service(c: ContainerDep) -> EventService:
    return EventService(c.sessionmaker, c.clock)


EventServiceDep = Annotated[EventService, Depends(get_event_service)]


def get_delivery_service(c: ContainerDep) -> DeliveryService:
    return DeliveryService(c.sessionmaker, c.clock)


DeliveryServiceDep = Annotated[DeliveryService, Depends(get_delivery_service)]
