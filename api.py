"""
API REST con FastAPI — sirve el dashboard y los endpoints de datos.
"""
import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from database import (
    get_db, init_db, Usuario, Recordatorio,
    Estado, Prioridad, Categoria, Cuadrante
)
from auth import (
    get_user_by_email, get_user_by_id, create_user,
    verify_password, create_token, decode_token, hash_password
)

app = FastAPI(title="RecordatorioBot API", version="1.0.0")
bearer = HTTPBearer(auto_error=False)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth helper ────────────────────────────────────────────────────────────────
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db)
) -> Usuario:
    if not credentials:
        raise HTTPException(status_code=401, detail="No autenticado")
    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")
    user = await get_user_by_id(db, int(payload["sub"]))
    if not user or not user.activo:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    return user


# ── Schemas ────────────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    email: str
    password: str

class RegisterRequest(BaseModel):
    nombre: str
    email: str
    password: str

class RecordatorioCreate(BaseModel):
    titulo: str
    descripcion: Optional[str] = None
    categoria: Optional[str] = "otros"
    prioridad: Optional[str] = "media"
    fecha_limite: Optional[str] = None
    cuadrante: Optional[str] = None

class RecordatorioUpdate(BaseModel):
    titulo: Optional[str] = None
    descripcion: Optional[str] = None
    estado: Optional[str] = None
    prioridad: Optional[str] = None
    categoria: Optional[str] = None
    fecha_limite: Optional[str] = None
    cuadrante: Optional[str] = None

class UsuarioCreate(BaseModel):
    nombre: str
    email: str
    password: str
    es_admin: bool = False

class ChangePasswordRequest(BaseModel):
    password_actual: str
    password_nuevo: str


# ── Auth endpoints ─────────────────────────────────────────────────────────────
@app.post("/api/auth/login")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = await get_user_by_email(db, body.email)
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    token = create_token(user.id, user.email)
    return {"token": token, "nombre": user.nombre, "es_admin": user.es_admin}


@app.get("/api/auth/me")
async def me(user: Usuario = Depends(get_current_user)):
    return {
        "id": user.id, "nombre": user.nombre,
        "email": user.email, "es_admin": user.es_admin,
        "telegram_vinculado": bool(user.telegram_chat_id)
    }


@app.post("/api/auth/change-password")
async def change_password(
    body: ChangePasswordRequest,
    user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if not verify_password(body.password_actual, user.password_hash):
        raise HTTPException(status_code=400, detail="Contraseña actual incorrecta")
    if len(body.password_nuevo) < 6:
        raise HTTPException(status_code=400, detail="La nueva contraseña debe tener al menos 6 caracteres")
    user.password_hash = hash_password(body.password_nuevo)
    await db.commit()
    return {"ok": True}


# ── Recordatorios ──────────────────────────────────────────────────────────────
@app.get("/api/recordatorios")
async def listar_recordatorios(
    estado: Optional[str] = None,
    categoria: Optional[str] = None,
    user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    q = select(Recordatorio).where(Recordatorio.usuario_id == user.id)
    if estado:
        q = q.where(Recordatorio.estado == estado)
    if categoria:
        q = q.where(Recordatorio.categoria == categoria)
    q = q.order_by(Recordatorio.prioridad, Recordatorio.fecha_limite)
    result = await db.execute(q)
    recs = result.scalars().all()
    return [_rec_to_dict(r) for r in recs]


@app.post("/api/recordatorios")
async def crear_recordatorio(
    body: RecordatorioCreate,
    user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    fecha = None
    if body.fecha_limite:
        try:
            fecha = datetime.fromisoformat(body.fecha_limite)
        except ValueError:
            pass

    rec = Recordatorio(
        usuario_id=user.id,
        titulo=body.titulo,
        descripcion=body.descripcion,
        categoria=body.categoria or "otros",
        prioridad=body.prioridad or "media",
        fecha_limite=fecha,
        cuadrante=body.cuadrante or None,
        origen="web"
    )
    db.add(rec)
    await db.commit()
    await db.refresh(rec)
    return _rec_to_dict(rec)


@app.patch("/api/recordatorios/{rec_id}")
async def actualizar_recordatorio(
    rec_id: int,
    body: RecordatorioUpdate,
    user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Recordatorio).where(and_(
            Recordatorio.id == rec_id,
            Recordatorio.usuario_id == user.id
        ))
    )
    rec = result.scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="No encontrado")

    if body.titulo:
        rec.titulo = body.titulo
    if body.descripcion is not None:
        rec.descripcion = body.descripcion or None
    if body.estado:
        rec.estado = body.estado
        if body.estado == "completado":
            rec.completado_en = datetime.utcnow()
    if body.prioridad:
        rec.prioridad = body.prioridad
    if body.categoria:
        rec.categoria = body.categoria
    if body.fecha_limite:
        try:
            rec.fecha_limite = datetime.fromisoformat(body.fecha_limite)
        except ValueError:
            pass
    if body.cuadrante is not None:
        rec.cuadrante = body.cuadrante or None

    await db.commit()
    await db.refresh(rec)
    return _rec_to_dict(rec)


@app.delete("/api/recordatorios/{rec_id}")
async def eliminar_recordatorio(
    rec_id: int,
    user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Recordatorio).where(and_(
            Recordatorio.id == rec_id,
            Recordatorio.usuario_id == user.id
        ))
    )
    rec = result.scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="No encontrado")
    await db.delete(rec)
    await db.commit()
    return {"ok": True}


# ── Estadísticas ───────────────────────────────────────────────────────────────
@app.get("/api/stats")
async def estadisticas(
    user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Recordatorio).where(Recordatorio.usuario_id == user.id)
    )
    todos = result.scalars().all()

    pendientes  = [r for r in todos if r.estado == Estado.pendiente]
    completados = [r for r in todos if r.estado == Estado.completado]
    vencidos    = [r for r in todos if r.estado == Estado.vencido]
    total       = len(todos)

    # Tiempo promedio de cierre (en horas)
    tiempos = []
    for r in completados:
        if r.completado_en and r.creado_en:
            h = (r.completado_en - r.creado_en).total_seconds() / 3600
            tiempos.append(h)
    prom_cierre = round(sum(tiempos) / len(tiempos), 1) if tiempos else 0

    # Cumplimiento a tiempo
    a_tiempo = sum(
        1 for r in completados
        if r.fecha_limite and r.completado_en and r.completado_en <= r.fecha_limite
    )
    pct_tiempo = round(a_tiempo / len(completados) * 100) if completados else 0

    # Por categoría
    por_cat = {}
    for cat in Categoria:
        cat_recs = [r for r in todos if r.categoria == cat]
        cat_comp = [r for r in cat_recs if r.estado == Estado.completado]
        por_cat[cat.value] = {
            "total": len(cat_recs),
            "completados": len(cat_comp),
            "pct": round(len(cat_comp) / len(cat_recs) * 100) if cat_recs else 0
        }

    # Por prioridad
    por_prio = {}
    for prio in Prioridad:
        p_recs = [r for r in todos if r.prioridad == prio]
        p_comp = [r for r in p_recs if r.estado == Estado.completado]
        por_prio[prio.value] = {
            "total": len(p_recs),
            "completados": len(p_comp),
            "pct": round(len(p_comp) / len(p_recs) * 100) if p_recs else 0
        }

    score = min(100, round(
        (pct_tiempo * 0.5) +
        ((len(completados) / total * 100) * 0.3 if total else 0) +
        (max(0, 100 - len(vencidos) * 10) * 0.2)
    )) if total else 0

    return {
        "total": total,
        "pendientes": len(pendientes),
        "completados": len(completados),
        "vencidos": len(vencidos),
        "pct_a_tiempo": pct_tiempo,
        "prom_horas_cierre": prom_cierre,
        "score": score,
        "por_categoria": por_cat,
        "por_prioridad": por_prio,
    }


# ── Admin: gestión de usuarios ─────────────────────────────────────────────────
@app.get("/api/admin/usuarios")
async def listar_usuarios(
    user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if not user.es_admin:
        raise HTTPException(status_code=403, detail="Solo admins")
    result = await db.execute(select(Usuario))
    usuarios = result.scalars().all()
    return [{"id": u.id, "nombre": u.nombre, "email": u.email,
             "es_admin": u.es_admin, "activo": u.activo,
             "telegram": bool(u.telegram_chat_id)} for u in usuarios]


@app.post("/api/admin/usuarios")
async def crear_usuario(
    body: UsuarioCreate,
    user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if not user.es_admin:
        raise HTTPException(status_code=403, detail="Solo admins")
    existente = await get_user_by_email(db, body.email)
    if existente:
        raise HTTPException(status_code=400, detail="El correo ya está registrado")
    nuevo = await create_user(db, body.nombre, body.email, body.password, body.es_admin)
    return {"id": nuevo.id, "nombre": nuevo.nombre, "email": nuevo.email}


# ── Salud ──────────────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ── Dashboard (catch-all) ──────────────────────────────────────────────────────
@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404)
    return FileResponse("static/index.html")


# ── Helpers ────────────────────────────────────────────────────────────────────
def _rec_to_dict(r: Recordatorio) -> dict:
    return {
        "id":           r.id,
        "titulo":       r.titulo,
        "descripcion":  r.descripcion,
        "categoria":    r.categoria.value if r.categoria else "otros",
        "prioridad":    r.prioridad.value if r.prioridad else "media",
        "estado":       r.estado.value if r.estado else "pendiente",
        "fecha_limite": r.fecha_limite.isoformat() if r.fecha_limite else None,
        "origen":       r.origen,
        "cuadrante":    r.cuadrante.value if r.cuadrante else None,
        "creado_en":    r.creado_en.isoformat() if r.creado_en else None,
        "completado_en":r.completado_en.isoformat() if r.completado_en else None,
    }


# ── Startup ────────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    await init_db()
    # Crear usuario admin por defecto si no existe
    async with __import__("database").AsyncSessionLocal() as db:
        admin = await get_user_by_email(db, "admin@recordatorio.app")
        if not admin:
            await create_user(db, "Admin", "admin@recordatorio.app",
                              "admin1234", es_admin=True)
            print("✅ Usuario admin creado: admin@recordatorio.app / admin1234")
