"""Application factory. Everything security-relevant is wired here and covered by tests."""

import logging

from fastapi import APIRouter, Depends, FastAPI
from kb_contracts import CONTRACT_VERSION
from kb_contracts.config import Settings
from kb_contracts.logging import REDACTOR
from kb_contracts.providers import Clock
from kb_core.db.protocols import UnitOfWorkFactory
from starlette.middleware.trustedhost import TrustedHostMiddleware

from kb_api import health
from kb_api.auth import make_token_dependency
from kb_api.context import CorrelationFilter
from kb_api.deps import build_services
from kb_api.errors import install_error_handlers
from kb_api.middleware import correlation_middleware, reject_browser_origin
from kb_api.routes import ROUTER_MODULES

# Only loopback names are valid Host headers: blocks DNS-rebinding (ADR-0014).
ALLOWED_HOSTS = ["127.0.0.1"]  # Electron main always dials the literal loopback IP


def create_app(
    settings: Settings,
    token: str,
    probes: list[health.NamedProbe] | None = None,
    uow_factory: UnitOfWorkFactory | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    REDACTOR.register_secret(token)  # the token can never appear in a log line
    logging.getLogger().addFilter(CorrelationFilter())
    for h in logging.getLogger().handlers:
        h.addFilter(CorrelationFilter())

    app = FastAPI(
        title="Ken Backlink OS API",
        version=CONTRACT_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,  # no unauthenticated docs/schema surface in the packaged app
    )
    app.state.settings = settings
    # Without a unit-of-work factory the persistence-backed routes answer 501 (see deps).
    app.state.services = (
        build_services(settings, uow_factory, clock) if uow_factory is not None else None
    )
    app.state.probes = probes if probes is not None else health.default_probes(settings)

    # Starlette runs middleware in reverse registration order: last added = outermost.
    app.middleware("http")(reject_browser_origin)
    app.middleware("http")(correlation_middleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
    # Deliberately NO CORSMiddleware: CORS is deny-all.
    install_error_handlers(app)

    app.include_router(health.public_router)
    private = APIRouter(dependencies=[Depends(make_token_dependency(token))])
    private.include_router(health.private_router)
    for module in ROUTER_MODULES:
        private.include_router(module.router)
    app.include_router(private)
    return app
