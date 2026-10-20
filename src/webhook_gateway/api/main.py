import uvicorn

from webhook_gateway.api.app import create_app
from webhook_gateway.config import Settings
from webhook_gateway.logging import configure_logging


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level, json=settings.log_json)
    uvicorn.run(create_app(settings), host="0.0.0.0", port=8000, access_log=False)  # noqa: S104


if __name__ == "__main__":
    main()
