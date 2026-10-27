from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from app.api.deps import DB, CurrentUser, SettingsDep, auth_rate_limit
from app.schemas.requests import LoginIn, PasswordChangeIn, ProfileUpdateIn, RegisterIn
from app.services import accounts

router = APIRouter(tags=["auth"])


def session_cookie(response, token, settings):
    response.set_cookie("hesabatim_session", token, httponly=True, secure=settings.environment == "production",
                        samesite="lax", max_age=settings.jwt_ttl_minutes * 60, path="/")


@router.post("/auth/register", status_code=201, dependencies=[Depends(auth_rate_limit)])
def register(body: RegisterIn, response: Response, db: DB, settings: SettingsDep):
    profile, token = accounts.register(db, settings, email=body.email, password=body.password, full_name=body.full_name,
                                       business_name=body.business_name, currency=body.currency)
    session_cookie(response, token, settings)
    return {"access_token": token, "token_type": "bearer", "user": accounts.serialize_me(db, profile)}


@router.post("/auth/login", dependencies=[Depends(auth_rate_limit)])
def login(body: LoginIn, response: Response, db: DB, settings: SettingsDep):
    profile, token = accounts.login(db, settings, email=body.email, password=body.password)
    session_cookie(response, token, settings)
    return {"access_token": token, "token_type": "bearer", "user": accounts.serialize_me(db, profile)}


@router.post("/auth/logout-all", status_code=204)
def logout_all(db: DB, user: CurrentUser):
    """Invalidate every token issued for this account (all devices)."""
    accounts.revoke_sessions(db, user)
    response = Response(status_code=204)
    response.delete_cookie("hesabatim_session", path="/")
    return response


@router.post("/auth/password")
def change_password(body: PasswordChangeIn, response: Response, db: DB, user: CurrentUser, settings: SettingsDep):
    token = accounts.change_password(db, settings, user, current=body.current_password, new=body.new_password)
    session_cookie(response, token, settings)
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
def me(db: DB, user: CurrentUser):
    return accounts.serialize_me(db, user)


@router.patch("/me")
def update_me(body: ProfileUpdateIn, db: DB, user: CurrentUser):
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(user, k, v)
    db.commit()
    return accounts.serialize_me(db, user)
