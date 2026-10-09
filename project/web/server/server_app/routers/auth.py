# -*- coding: utf-8 -*-

import asyncio
import secrets
from datetime import timedelta, datetime, date, timezone
import bcrypt
from fastapi import FastAPI, Depends, HTTPException, status, Query, Response, UploadFile, File, Form, WebSocket, \
    WebSocketDisconnect
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from sqlalchemy.orm import Session
from jose import JWTError, jwt

from server.database.models import *

from fastapi import APIRouter

from ..config import (ACCESS_TOKEN_EXPIRE_MINUTES, ADMIN_FALLBACK_ENABLED,
                      ADMIN_FALLBACK_USER_ID, ALGORITHM,
                      LOGIN_ADMIN, MIN_PASSWORD_LENGTH, PASSWORD_ADMIN,
                      RESET_CODE_MAX_ATTEMPTS, RESET_CODE_TTL_MINUTES,
                      RESET_REQUEST_COOLDOWN_SEC, SECRET_KEY, logger)
from ..core import fm, generate_reset_code, get_db, get_user, verify_password
from ..schemas import (PasswordResetConfirm, PasswordResetRequest, PasswordResetResponse,
                       PasswordResetVerify, Token)
from ..security import LoginThrottle, ResetCodeStore


router = APIRouter()


reset_store = ResetCodeStore(
    ttl_seconds=RESET_CODE_TTL_MINUTES * 60,
    max_attempts=RESET_CODE_MAX_ATTEMPTS,
    cooldown_seconds=RESET_REQUEST_COOLDOWN_SEC,
)

login_throttle = LoginThrottle()


# Служебные домены: письмо на такой адрес физически некуда доставить, а
# pydantic на них падает с ошибкой валидации
_RESERVED_DOMAINS = ("local", "localhost", "localdomain", "internal", "intranet",
                     "test", "example", "invalid", "lan", "home", "corp")


def mail_address_problem(address: str):
    """Возвращает причину, по которой на адрес нельзя отправить письмо, или None."""
    if not address or address.count("@") != 1:
        return "адрес заполнен неверно"
    local, _, domain = address.partition("@")
    domain = domain.strip().lower().rstrip(".")
    if not local.strip() or not domain or "." not in domain:
        return "адрес заполнен неверно"
    if domain.split(".")[-1] in _RESERVED_DOMAINS or domain in _RESERVED_DOMAINS:
        return "служебный домен, письма туда не доставляются"
    return None


# Один и тот же ответ для существующего и несуществующего логина:
# иначе форма сброса превращается в проверялку «есть ли такой пользователь»
GENERIC_RESET_SENT = "Если такой пользователь есть, код отправлен на привязанную почту"


_RESET_MESSAGES = {
    "missing": "Код не запрашивался или уже использован",
    "expired": f"Срок действия кода истёк ({RESET_CODE_TTL_MINUTES} мин). Запросите новый",
    "locked": "Слишком много неверных попыток. Запросите новый код",
    "invalid": "Неверный код сброса",
}


def _reset_failure(state: str, attempts_left: int = 0) -> PasswordResetResponse:
    message = _RESET_MESSAGES.get(state, "Неверный код сброса")
    if state == "invalid" and attempts_left > 0:
        message = f"{message}. Осталось попыток: {attempts_left}"
    return PasswordResetResponse(
        success=False,
        message=message,
        attempts_left=attempts_left,
    )


def _issue_token(user_id: int, is_admin: bool) -> dict:
    now = datetime.now(timezone.utc)
    to_encode = {
        "sub": str(user_id),
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat": now,
        "is_admin": is_admin,
    }
    return {
        "access_token": jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM),
        "token_type": "bearer",
        "user_id": user_id,
    }


@router.post("/reset-password/request", response_model=PasswordResetResponse)
async def request_password_reset(request: PasswordResetRequest, db: Session = Depends(get_db)):
    """Выдаёт код сброса. Сам код уходит только на почту сотрудника."""
    username = (request.username or "").strip()

    # Пауза считается по логину, в том числе несуществующему
    if not reset_store.can_request(username):
        return PasswordResetResponse(
            success=False,
            message=f"Код уже отправлен. Повторный запрос через {RESET_REQUEST_COOLDOWN_SEC} с",
        )

    generic = PasswordResetResponse(success=True, message=GENERIC_RESET_SENT)

    user = db.query(User).filter_by(username=username).first()
    if not user:
        return generic

    employee = db.query(Employee).filter_by(id=user.id).first()
    if not employee or not employee.email:
        logger.warning("Сброс пароля для %r невозможен: в карточке сотрудника не указана почта", username)
        return generic

    address = (employee.email or "").strip()
    problem = mail_address_problem(address)
    if problem:
        # Раньше такой адрес доходил до MessageSchema и валил запрос с 500:
        # pydantic отвергает служебные домены вроде .local, .internal, .test
        logger.warning("Сброс пароля для %r невозможен: адрес %r — %s. "
                       "Укажите сотруднику рабочую почту в карточке.", username, address, problem)
        return generic

    reset_code = generate_reset_code()
    reset_store.issue(username, reset_code)

    # Старое поле больше не используется: код в базе не хранится
    if user.reset_code:
        user.reset_code = None
        db.commit()

    try:
        message = MessageSchema(
            subject="Код сброса пароля",
            recipients=[address],
            body=(f"Ваш код для сброса пароля: {reset_code}\n"
                  f"Код действует {RESET_CODE_TTL_MINUTES} минут."),
            subtype="plain"
        )
        await fm.send_message(message)
    except Exception as e:
        logger.error("Не удалось отправить код сброса на %r: %s", address, e)
        reset_store.clear(username)
        return PasswordResetResponse(success=False, message="Ошибка при отправке кода сброса")

    return generic


@router.post("/reset-password/verify", response_model=PasswordResetResponse)
async def verify_password_reset(payload: PasswordResetVerify):
    """Проверка кода до ввода нового пароля. Код не расходуется."""
    state, attempts_left = reset_store.verify(payload.username, payload.reset_code, consume=False)
    if state != "ok":
        return _reset_failure(state, attempts_left)
    return PasswordResetResponse(success=True, message="Код подтверждён",
                                 attempts_left=attempts_left)


@router.post("/reset-password/confirm", response_model=PasswordResetResponse)
async def confirm_password_reset(confirm: PasswordResetConfirm, db: Session = Depends(get_db)):
    state, attempts_left = reset_store.verify(confirm.username, confirm.reset_code, consume=True)
    if state != "ok":
        return _reset_failure(state, attempts_left)

    new_password = confirm.new_password or ""
    if len(new_password) < MIN_PASSWORD_LENGTH:
        # Код уже израсходован, поэтому выдаём новый цикл целиком
        return PasswordResetResponse(
            success=False,
            message=f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов. Запросите код заново",
        )

    user = db.query(User).filter_by(username=(confirm.username or "").strip()).first()
    if not user:
        return PasswordResetResponse(success=False, message="Не удалось сменить пароль")

    user.password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    user.reset_code = None
    db.commit()

    reset_store.clear(confirm.username)
    login_throttle.reset(confirm.username)

    return PasswordResetResponse(success=True, message="Пароль успешно изменён")


_DUMMY = []


def _dummy_hash() -> str:
    """Хэш bcrypt со стоимостью по умолчанию — для сверки при несуществующем логине."""
    if not _DUMMY:
        _DUMMY.append(bcrypt.hashpw(secrets.token_hex(16).encode("ascii"), bcrypt.gensalt()).decode("ascii"))
    return _DUMMY[0]


@router.post("/login", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    username = (form_data.username or "").strip()
    password = form_data.password or ""

    # Задержка растёт с числом неудач по этой учётной записи
    delay = login_throttle.delay_for(username)
    if delay:
        await asyncio.sleep(delay)

    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Неверный логин или пароль",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Аварийный вход из .env — только для одного, административного логина.
    # Пароли остальных сотрудников проверяются исключительно по базе; раньше
    # в лог на каждый вход падала строка «логин не совпал с LOGIN_ADMIN»,
    # из-за чего казалось, будто .env-пароль сверяют для всех.
    if (ADMIN_FALLBACK_ENABLED and LOGIN_ADMIN and PASSWORD_ADMIN
            and secrets.compare_digest(username, LOGIN_ADMIN)):
        if secrets.compare_digest(password, PASSWORD_ADMIN):
            login_throttle.reset(username)
            logger.warning("Аварийный вход администратора из .env, логин %r", username)
            return _issue_token(ADMIN_FALLBACK_USER_ID, True)
        logger.warning("Аварийный вход: административный логин совпал, пароль нет")

    user = get_user(db, username)
    if not user:
        # нет такого логина — пароль всё равно сверяется (с пустышкой той же стоимости), чтобы по времени
        # ответа нельзя было понять, существует ли учётная запись
        verify_password(password, _dummy_hash())
        login_throttle.register_failure(username)
        raise unauthorized
    if not verify_password(password, user.password):
        login_throttle.register_failure(username)
        raise unauthorized

    login_throttle.reset(username)
    return _issue_token(user.id, False)
