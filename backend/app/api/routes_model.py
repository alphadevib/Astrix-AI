"""Astrix-LM: training corpus and Astrix's own models."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from .deps import AstrixDep

router = APIRouter(prefix="/model", tags=["astrix-lm"])


class TrainRequest(BaseModel):
    only_verified: bool = False


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    severity: str = "WARNING"
    ml_score: float = Field(default=0.6, ge=0.0, le=1.0)
    final_score: float = Field(default=0.6, ge=0.0, le=1.0)
    persistence_seconds: float = 0.0
    deviating_parameters: list[str] = Field(default_factory=list)
    context_factors: dict[str, float] = Field(default_factory=dict)


class ExportRequest(BaseModel):
    only_verified: bool = True


def _lab(astrix):
    if astrix.model_lab is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="training corpus is disabled")
    return astrix.model_lab


@router.get("/status", summary="Corpus size, model versions and shadow agreement")
async def model_status(astrix: AstrixDep) -> dict:
    return _lab(astrix).status()


@router.post("/train", summary="Train a new version of the Astrix-LM nano model")
async def model_train(body: TrainRequest, astrix: AstrixDep) -> dict:
    return await asyncio.to_thread(_lab(astrix).nano.train, body.only_verified)


@router.post("/verify", summary="Verify corpus encryption and hash-chain integrity")
async def model_verify(astrix: AstrixDep) -> dict:
    return await asyncio.to_thread(_lab(astrix).corpus.verify)


@router.post("/predict", summary="Ask the nano model for a diagnosis and action")
async def model_predict(body: PredictRequest, astrix: AstrixDep) -> dict:
    prediction = _lab(astrix).nano.predict(body.model_dump())
    if prediction is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="no trained model yet — train one first")
    return prediction


@router.post(
    "/export",
    summary="Decrypt and export the corpus as chat-format JSONL for LLM fine-tuning",
    response_class=PlainTextResponse,
)
async def model_export(body: ExportRequest, astrix: AstrixDep) -> PlainTextResponse:
    records = await asyncio.to_thread(_lab(astrix).corpus.export_sft, body.only_verified)
    text = "\n".join(json.dumps(r) for r in records)
    return PlainTextResponse(
        text,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="astrix-lm-sft.jsonl"'},
    )


# --------------------------------------------------------------------------
# Astrix Small LLM (On-board Neural Decision Model)
# --------------------------------------------------------------------------


class SolveRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    query: str | None = None
    situation: PredictRequest | None = None


class TrainSmallLMRequest(BaseModel):
    sync_corpus: bool = True


def _small_lm(astrix):
    lm = getattr(astrix, "small_lm", None)
    if lm is not None:
        return lm
    if astrix.model_lab and getattr(astrix.model_lab, "small_lm", None):
        return astrix.model_lab.small_lm
    from ..training.small_llm import AstrixSmallLM

    return AstrixSmallLM()


@router.get("/small-llm/status", summary="Status, accuracy and heads of the on-board Small LLM")
async def small_llm_status(astrix: AstrixDep) -> dict:
    return _small_lm(astrix).status()


@router.post("/small-llm/predict", summary="Inference through the on-board neural decision Small LLM")
async def small_llm_predict(body: PredictRequest, astrix: AstrixDep) -> dict:
    return _small_lm(astrix).predict(body.model_dump())


@router.post("/small-llm/solve", summary="Solve spacecraft anomalies and resolve sensor spoofing")
async def small_llm_solve(body: SolveRequest, astrix: AstrixDep) -> dict:
    lm = _small_lm(astrix)
    if body.query:
        return lm.solve_issue(body.query)
    if body.situation:
        return lm.solve_issue(body.situation.model_dump())
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provide either 'query' or 'situation'")


@router.post("/small-llm/train", summary="Retrain AstrixSmallLM on the 100 real-time operational issues")
async def small_llm_train(body: TrainSmallLMRequest, astrix: AstrixDep) -> dict:
    lm = _small_lm(astrix)
    return await asyncio.to_thread(lm.train, body.sync_corpus)

