"""Dependency accessors.

The `Astrix` container is built once in the app lifespan and stored on
`app.state`. Routes reach it through these helpers rather than importing a module
global, so tests can construct a container with a temporary database and mount the
same routers against it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

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
