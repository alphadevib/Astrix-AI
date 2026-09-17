"""Dependency accessors.

The `Astrix` container is built once in the app lifespan and stored on
`app.state`. Routes reach it through these helpers rather than importing a module
global, so tests can construct a container with a temporary database and mount the
same routers against it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from ..auth.service import Principal
from ..bootstrap import Astrix
from ..services.mission_runner import MissionRunner
from ..services.pipeline import AstrixPipeline


def get_astrix(request: Request) -> Astrix:
    astrix: Astrix | None = getattr(request.app.state, "astrix", None)
    if astrix is None:  # pragma: no cover — only if the lifespan did not run
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ASTRIX is still starting up",
        )
    return astrix


def get_pipeline(astrix: Annotated[Astrix, Depends(get_astrix)]) -> AstrixPipeline:
    return astrix.pipeline


def get_runner(astrix: Annotated[Astrix, Depends(get_astrix)]) -> MissionRunner:
    runner = astrix.runner
    if runner is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="mission runner is not initialised",
        )
    return runner


AstrixDep = Annotated[Astrix, Depends(get_astrix)]
PipelineDep = Annotated[AstrixPipeline, Depends(get_pipeline)]
RunnerDep = Annotated[MissionRunner, Depends(get_runner)]


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get("x-astrix-token", "").strip()


def get_current_user(request: Request, astrix: Annotated[Astrix, Depends(get_astrix)]) -> Principal:
    """The signed-in operator, or 401.

    Every console route that touches an account's data depends on this, so an
    unauthenticated request can never reach a query that would have to filter by
    user afterwards.
    """
    principal = astrix.auth.resolve(_bearer(request))
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to continue.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def get_optional_user(
    request: Request, astrix: Annotated[Astrix, Depends(get_astrix)]
) -> Principal | None:
    """The signed-in operator if there is one. Used where a route works either way."""
    return astrix.auth.resolve(_bearer(request))


CurrentUser = Annotated[Principal, Depends(get_current_user)]
OptionalUser = Annotated[Principal | None, Depends(get_optional_user)]
