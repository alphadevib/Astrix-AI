"""Accounts, sessions and the profile console.

Sign-up, sign-in, sign-out, profile, password change and session management.
The bearer token returned by `/auth/register` and `/auth/login` is the console's
only credential; there is no separate API key to paste anywhere.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..auth.service import AuthError
from .deps import AstrixDep, CurrentUser

router = APIRouter(prefix="/auth", tags=["accounts"])

# Pragmatic shape check, not RFC 5322. The only thing that proves an address is
# real is sending to it; this rejects the typos and keeps `email-validator` out
# of the dependency list.
EMAIL_PATTERN = r"^[^@\s]{1,64}@[^@\s.]+(\.[^@\s.]+)+$"
Email = Annotated[str, Field(pattern=EMAIL_PATTERN, max_length=254)]


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    email: Email
    password: str = Field(min_length=1, max_length=200)
    name: str = Field(default="", max_length=120)
    organisation: str = Field(default="", max_length=160)
    role: str = Field(default="flight-director", max_length=64)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    email: Email
    password: str = Field(min_length=1, max_length=200)


class ProfileRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = Field(default=None, max_length=120)
    organisation: str | None = Field(default=None, max_length=160)
    role: str | None = Field(default=None, max_length=64)
    preferences: dict | None = None


class PasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)
    sign_out_others: bool = True


def _fail(error: AuthError) -> HTTPException:
    return HTTPException(status_code=error.code, detail=error.message)


@router.post("/register", summary="Create an operator account and sign in")
async def register(body: RegisterRequest, request: Request, astrix: AstrixDep) -> dict:
    if not astrix.settings.allow_registration:
        raise HTTPException(
            status_code=403, detail="Sign-up is closed on this deployment. Ask an operator for an account."
        )
    agent = request.headers.get("user-agent", "")
    try:
        principal, token = await asyncio.to_thread(
            astrix.auth.register,
            body.email,
            body.password,
            body.name,
            body.organisation,
            body.role,
            agent,
        )
    except AuthError as error:
        raise _fail(error) from None
    return {"token": token, "user": astrix.auth.profile(principal.id)}


@router.post("/login", summary="Sign in and open a session")
async def login(body: LoginRequest, request: Request, astrix: AstrixDep) -> dict:
    try:
        principal, token = await asyncio.to_thread(
            astrix.auth.login, body.email, body.password, request.headers.get("user-agent", "")
        )
    except AuthError as error:
        raise _fail(error) from None
    return {"token": token, "user": astrix.auth.profile(principal.id)}


@router.post("/logout", summary="End this session")
async def logout(user: CurrentUser, astrix: AstrixDep) -> dict:
    await asyncio.to_thread(astrix.auth.logout, user.session_id)
    return {"signed_out": True}


@router.post("/logout-all", summary="End every session for this account")
async def logout_all(user: CurrentUser, astrix: AstrixDep) -> dict:
    count = await asyncio.to_thread(astrix.auth.logout_everywhere, user.id)
    return {"signed_out": count}


@router.get("/me", summary="The signed-in account")
async def me(user: CurrentUser, astrix: AstrixDep) -> dict:
    return {"user": astrix.auth.profile(user.id)}


@router.patch("/me", summary="Update profile details and console preferences")
async def update_me(body: ProfileRequest, user: CurrentUser, astrix: AstrixDep) -> dict:
    try:
        await asyncio.to_thread(
            astrix.auth.update_profile,
            user.id,
            name=body.name,
            organisation=body.organisation,
            role=body.role,
            preferences=body.preferences,
        )
    except AuthError as error:
        raise _fail(error) from None
    return {"user": astrix.auth.profile(user.id)}


@router.post("/password", summary="Change the account password")
async def change_password(body: PasswordRequest, user: CurrentUser, astrix: AstrixDep) -> dict:
    try:
        await asyncio.to_thread(
            astrix.auth.change_password,
            user.id,
            body.current_password,
            body.new_password,
            None if body.sign_out_others else user.session_id,
        )
    except AuthError as error:
        raise _fail(error) from None
    # This session was revoked along with the rest; the client signs in again.
    return {"changed": True, "sessions_ended": body.sign_out_others}


@router.get("/sessions", summary="Live sessions for this account")
async def sessions(user: CurrentUser, astrix: AstrixDep) -> dict:
    rows = await asyncio.to_thread(astrix.auth.sessions, user.id)
    for row in rows:
        row["current"] = row["id"] == user.session_id
    return {"sessions": rows}
