import os
from datetime import datetime, timedelta
from typing import Optional
import bcrypt
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import Usuario

SECRET_KEY  = os.getenv("SECRET_KEY", "cambia_esto_en_produccion_1234567890abcdef")
ALGORITHM   = "HS256"
TOKEN_EXPIRE_DAYS = 30


# ── Contraseñas ────────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ── JWT ────────────────────────────────────────────────────────────────────────
def create_token(user_id: int, email: str) -> str:
    expire = datetime.utcnow() + timedelta(days=TOKEN_EXPIRE_DAYS)
    return jwt.encode(
        {"sub": str(user_id), "email": email, "exp": expire},
        SECRET_KEY, algorithm=ALGORITHM
    )


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


# ── Usuarios ───────────────────────────────────────────────────────────────────
async def get_user_by_email(db: AsyncSession, email: str) -> Optional[Usuario]:
    result = await db.execute(select(Usuario).where(Usuario.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[Usuario]:
    result = await db.execute(select(Usuario).where(Usuario.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_telegram(db: AsyncSession, chat_id: str) -> Optional[Usuario]:
    result = await db.execute(
        select(Usuario).where(Usuario.telegram_chat_id == str(chat_id))
    )
    return result.scalar_one_or_none()


async def create_user(db: AsyncSession, nombre: str, email: str,
                      password: str, es_admin: bool = False) -> Usuario:
    user = Usuario(
        nombre=nombre,
        email=email,
        password_hash=hash_password(password),
        es_admin=es_admin
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def login(db: AsyncSession, email: str, password: str) -> Optional[str]:
    """Verifica credenciales y retorna token JWT, o None si falla."""
    user = await get_user_by_email(db, email)
    if not user or not user.activo:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return create_token(user.id, user.email)


async def link_telegram(db: AsyncSession, user_id: int, chat_id: str):
    """Vincula un chat_id de Telegram a un usuario."""
    user = await get_user_by_id(db, user_id)
    if user:
        user.telegram_chat_id = str(chat_id)
        await db.commit()
