import os
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime,
    Boolean, ForeignKey, Enum as SAEnum, text
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, relationship
import enum

# ── URL de base de datos ───────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./recordatorio.db")

# Railway usa postgres://, SQLAlchemy necesita postgresql+asyncpg://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# ── Base ───────────────────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass

# ── Enums ──────────────────────────────────────────────────────────────────────
class Prioridad(str, enum.Enum):
    alta   = "alta"
    media  = "media"
    baja   = "baja"

class Estado(str, enum.Enum):
    pendiente  = "pendiente"
    completado = "completado"
    vencido    = "vencido"

class Categoria(str, enum.Enum):
    trabajo    = "trabajo"
    personal   = "personal"
    familia    = "familia"
    finanzas   = "finanzas"
    salud      = "salud"
    legal      = "legal"
    compras    = "compras"
    educacion  = "educacion"
    otros      = "otros"

class Cuadrante(str, enum.Enum):
    q1 = "q1"  # Urgente + Importante → Hacer ahora
    q2 = "q2"  # No urgente + Importante → Programar
    q3 = "q3"  # Urgente + No importante → Delegar
    q4 = "q4"  # No urgente + No importante → Eliminar

# ── Modelos ────────────────────────────────────────────────────────────────────
class Usuario(Base):
    __tablename__ = "usuarios"

    id            = Column(Integer, primary_key=True, index=True)
    nombre        = Column(String(100), nullable=False)
    email         = Column(String(200), unique=True, index=True, nullable=False)
    password_hash = Column(String(200), nullable=False)
    telegram_chat_id = Column(String(50), unique=True, nullable=True)
    es_admin      = Column(Boolean, default=False)
    activo        = Column(Boolean, default=True)
    creado_en     = Column(DateTime, default=datetime.utcnow)

    recordatorios = relationship("Recordatorio", back_populates="usuario", cascade="all, delete")


class Recordatorio(Base):
    __tablename__ = "recordatorios"

    id           = Column(Integer, primary_key=True, index=True)
    usuario_id   = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    titulo       = Column(String(300), nullable=False)
    descripcion  = Column(Text, nullable=True)
    categoria    = Column(SAEnum(Categoria), default=Categoria.otros)
    prioridad    = Column(SAEnum(Prioridad), default=Prioridad.media)
    estado       = Column(SAEnum(Estado), default=Estado.pendiente)
    fecha_limite = Column(DateTime, nullable=True)
    origen       = Column(String(20), default="telegram")  # telegram | web
    cuadrante    = Column(SAEnum(Cuadrante), nullable=True)
    creado_en    = Column(DateTime, default=datetime.utcnow)
    completado_en = Column(DateTime, nullable=True)

    usuario = relationship("Usuario", back_populates="recordatorios")


# ── Helpers ────────────────────────────────────────────────────────────────────
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migraciones: agregar columnas nuevas si no existen (seguro ejecutar múltiples veces)
        await conn.execute(text(
            "ALTER TABLE recordatorios ADD COLUMN IF NOT EXISTS cuadrante VARCHAR(2)"
        ))
        await conn.execute(text(
            "ALTER TABLE recordatorios ADD COLUMN IF NOT EXISTS completado_en TIMESTAMP"
        ))
    print("✅ Base de datos lista")
