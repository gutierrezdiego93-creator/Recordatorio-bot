"""
Bot de Telegram — maneja texto, audio, comandos y autenticación.
"""
import os
import re
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes, ConversationHandler
)
from sqlalchemy import select, and_
from database import AsyncSessionLocal, Recordatorio, Estado, Prioridad, Categoria, Cuadrante
from auth import (
    get_user_by_telegram, get_user_by_email,
    verify_password, link_telegram, create_token
)
from categories import parsear_recordatorio, detectar_cuadrante

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")

# Estados del ConversationHandler para login
ESPERANDO_EMAIL, ESPERANDO_PASSWORD = range(2)

# Emojis por categoría
CAT_EMOJI = {
    "trabajo":   "💼", "personal":  "👤", "familia":   "👨‍👩‍👧",
    "finanzas":  "💰", "salud":     "🏥", "legal":     "📋",
    "compras":   "🛒", "educacion": "📚", "otros":     "⚙️",
}
PRIO_EMOJI = {"alta": "🔴", "media": "🟡", "baja": "🟢"}

CUADRANTE_INFO = {
    "q1": {"emoji": "🔴", "label": "Q1 · Hacer ahora",   "desc": "Urgente + Importante"},
    "q2": {"emoji": "🟢", "label": "Q2 · Programar",     "desc": "No urgente + Importante"},
    "q3": {"emoji": "🟡", "label": "Q3 · Delegar",       "desc": "Urgente + No importante"},
    "q4": {"emoji": "⚪", "label": "Q4 · Eliminar",      "desc": "No urgente + No importante"},
}

def _teclado_cuadrantes(rec_id: int, actual: str) -> InlineKeyboardMarkup:
    """Teclado con los 4 cuadrantes para confirmar o cambiar."""
    filas = []
    for q, info in CUADRANTE_INFO.items():
        marca = " ✓" if q == actual else ""
        filas.append([InlineKeyboardButton(
            f"{info['emoji']} {info['label']}{marca}",
            callback_data=f"cuadrante_{rec_id}_{q}"
        )])
    return InlineKeyboardMarkup(filas)


# ── Utilidades ─────────────────────────────────────────────────────────────────
async def get_usuario_autenticado(chat_id: int):
    async with AsyncSessionLocal() as db:
        return await get_user_by_telegram(db, str(chat_id))


def _limpiar_fallback(texto: str) -> tuple[str, str]:
    """Limpieza básica sin IA: retorna (titulo, descripcion)."""
    t = texto
    # Quitar "Categoría X" donde sea que aparezca
    t = re.sub(r"[\.,]?\s*[Ee]s\s+la\s+categor[íi]a\s+\w+\.?", "", t).strip()
    t = re.sub(r"[\.,]?\s*[Cc]ategor[íi]a[:\s]+\w+\.?", "", t).strip()
    # Quitar frases al inicio
    t = re.sub(
        r"^(mañana\s+\w+\s+|hoy\s+)?"  # "Mañana jueves "
        r"(recuérdame\s*(que\s*)?|recuerdame\s*(que\s*)?|no\s+olvides\s*(que\s*)?|"
        r"acuérdate\s*(de\s*)?(que\s*)?|acuerdate\s*(de\s*)?(que\s*)?|"
        r"necesito\s+(que\s+me\s+recuerdes\s+)?(que\s+)?|"
        r"tengo\s+que\s+|debo\s+|quiero\s+|hay\s+que\s+)",
        "", t, flags=re.IGNORECASE
    ).strip()
    # Quitar referencias de tiempo del inicio
    t = re.sub(r"^(mañana|hoy|pasado mañana)\s+(\w+\s+)?", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"\s+a las \d{1,2}(\s*(am|pm|de la (mañana|tarde|noche)))?", "", t, flags=re.IGNORECASE).strip()
    # Quitar "No olvidar..." al final (es info de notas, no del título)
    notas_match = re.search(r"[\.!]\s*(no\s+olvidar.*)$", t, re.IGNORECASE)
    notas = notas_match.group(1).strip() if notas_match else ""
    if notas_match:
        t = t[:notas_match.start()].strip()
    descripcion = notas if notas else (t[:200] if t else texto[:200])
    titulo = (t[:80] if t else texto[:80]).strip()
    if titulo:
        titulo = titulo[0].upper() + titulo[1:]
    return titulo, descripcion


async def procesar_con_ia(texto: str) -> tuple[str, str]:
    """
    Llama a Groq LLM y retorna (titulo_corto, resumen).
    Usa formato de dos líneas para evitar errores de parseo JSON.
    Si Groq falla, usa limpieza regex de fallback.
    """
    if GROQ_API_KEY:
        try:
            import httpx
            prompt = (
                "Tienes este mensaje de recordatorio:\n"
                f'"{texto}"\n\n'
                "Responde EXACTAMENTE en dos líneas, sin nada más:\n"
                "TITULO: [máximo 5 palabras: solo qué es la tarea, sin fechas, horas, días ni categoría]\n"
                "NOTAS: [frase de 10-15 palabras: contexto útil — qué hay que hacer, con quién, por qué. "
                "Sin fechas, sin 'recuérdame', sin 'no olvidar', sin 'tengo que']"
            )
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "llama-3.1-8b-instant",
                        "messages": [
                            {"role": "system", "content": "Eres un asistente que resume recordatorios en español. Respondes SOLO con las dos líneas indicadas, sin explicaciones adicionales."},
                            {"role": "user", "content": prompt}
                        ],
                        "max_tokens": 120,
                        "temperature": 0.1
                    }
                )
            logger.info(f"Groq chat status: {resp.status_code}")
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"].strip()
                logger.info(f"Groq chat response: {content}")
                titulo = resumen = ""
                for line in content.splitlines():
                    line = line.strip()
                    if line.upper().startswith("TITULO:"):
                        titulo = line.split(":", 1)[1].strip()
                    elif line.upper().startswith("NOTAS:"):
                        resumen = line.split(":", 1)[1].strip()
                if titulo and resumen:
                    return titulo[:100], resumen[:250]
                logger.warning(f"Groq respuesta inesperada: {content}")
            else:
                logger.error(f"Groq chat error {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Error en procesar_con_ia: {e}")

    return _limpiar_fallback(texto)


async def transcribir_audio(file_path: str) -> str:
    """Transcribe un archivo de audio usando Groq Whisper (gratis) u OpenAI como fallback."""
    # Intentar con Groq primero (gratis)
    if GROQ_API_KEY:
        try:
            import httpx
            with open(file_path, "rb") as f:
                audio_data = f.read()
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                    files={"file": ("audio.ogg", audio_data, "audio/ogg")},
                    data={"model": "whisper-large-v3-turbo", "language": "es"}
                )
            if resp.status_code == 200:
                return resp.json().get("text", "")
            else:
                logger.error(f"Groq error {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Error transcribiendo con Groq: {e}")

    # Fallback a OpenAI si está configurado
    if OPENAI_API_KEY:
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=OPENAI_API_KEY)
            with open(file_path, "rb") as f:
                result = await client.audio.transcriptions.create(
                    model="whisper-1", file=f, language="es"
                )
            return result.text
        except Exception as e:
            logger.error(f"Error transcribiendo con OpenAI: {e}")

    return ""


def formatear_recordatorio(r: Recordatorio) -> str:
    fecha = r.fecha_limite.strftime("%d/%m %H:%M") if r.fecha_limite else "Sin fecha"
    cat   = CAT_EMOJI.get(r.categoria.value if r.categoria else "otros", "⚙️")
    prio  = PRIO_EMOJI.get(r.prioridad.value if r.prioridad else "media", "🟡")
    return f"{prio} {cat} *{r.titulo}*\n    📅 {fecha} · #{r.categoria.value}"


# ── Comandos ───────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    usuario = await get_usuario_autenticado(chat_id)
    if usuario:
        await update.message.reply_text(
            f"👋 Hola de nuevo, *{usuario.nombre}*\\!\n\n"
            "Puedes enviarme un recordatorio en texto o audio, o usar:\n"
            "• /pendientes — tus tareas pendientes\n"
            "• /hoy — lo urgente de hoy\n"
            "• /stats — tus indicadores\n"
            "• /completar — marcar tarea como lista",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "👋 Bienvenido a *RecordatorioBot*\\!\n\n"
            "Para empezar, inicia sesión con tu correo y contraseña.\n"
            "Usa el comando /login",
            parse_mode="Markdown"
        )


async def cmd_login_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    usuario = await get_usuario_autenticado(update.effective_chat.id)
    if usuario:
        await update.message.reply_text(f"✅ Ya estás autenticado como *{usuario.nombre}*\\.", parse_mode="Markdown")
        return ConversationHandler.END
    await update.message.reply_text("📧 Escribe tu *correo electrónico*:", parse_mode="Markdown")
    return ESPERANDO_EMAIL


async def recibir_email(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["email"] = update.message.text.strip()
    await update.message.reply_text("🔒 Ahora escribe tu *contraseña*:", parse_mode="Markdown")
    return ESPERANDO_PASSWORD


async def recibir_password(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    email    = ctx.user_data.get("email", "")
    password = update.message.text.strip()
    await update.message.delete()  # borrar contraseña del chat

    async with AsyncSessionLocal() as db:
        user = await get_user_by_email(db, email)
        if not user or not verify_password(password, user.password_hash):
            await update.message.reply_text("❌ Correo o contraseña incorrectos\\. Intenta con /login", parse_mode="Markdown")
            return ConversationHandler.END
        await link_telegram(db, user.id, update.effective_chat.id)

    await update.message.reply_text(
        f"✅ ¡Bienvenido, *{user.nombre}*\\!\n\n"
        "Ya puedes enviarme recordatorios en texto o audio.\n"
        "Prueba escribiendo: _Llamar al banco mañana a las 10am_",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def cmd_cancelar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operación cancelada.")
    return ConversationHandler.END


async def cmd_pendientes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    usuario = await get_usuario_autenticado(update.effective_chat.id)
    if not usuario:
        await update.message.reply_text("🔒 Primero inicia sesión con /login")
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Recordatorio)
            .where(and_(
                Recordatorio.usuario_id == usuario.id,
                Recordatorio.estado == Estado.pendiente
            ))
            .order_by(Recordatorio.prioridad, Recordatorio.fecha_limite)
        )
        recordatorios = result.scalars().all()

    if not recordatorios:
        await update.message.reply_text("🎉 ¡No tienes recordatorios pendientes\\!", parse_mode="Markdown")
        return

    lineas = [f"📋 *Tienes {len(recordatorios)} pendientes:*\n"]
    for r in recordatorios[:10]:
        lineas.append(formatear_recordatorio(r))
    await update.message.reply_text("\n".join(lineas), parse_mode="Markdown")


async def cmd_hoy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    usuario = await get_usuario_autenticado(update.effective_chat.id)
    if not usuario:
        await update.message.reply_text("🔒 Primero inicia sesión con /login")
        return

    hoy_inicio = datetime.now().replace(hour=0, minute=0, second=0)
    hoy_fin    = datetime.now().replace(hour=23, minute=59, second=59)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Recordatorio).where(and_(
                Recordatorio.usuario_id == usuario.id,
                Recordatorio.estado == Estado.pendiente,
                Recordatorio.fecha_limite >= hoy_inicio,
                Recordatorio.fecha_limite <= hoy_fin
            )).order_by(Recordatorio.fecha_limite)
        )
        hoy = result.scalars().all()

    if not hoy:
        await update.message.reply_text("✅ No tienes recordatorios para hoy\\.", parse_mode="Markdown")
        return

    lineas = [f"📅 *Recordatorios de hoy ({len(hoy)}):*\n"]
    for r in hoy:
        lineas.append(formatear_recordatorio(r))
    await update.message.reply_text("\n".join(lineas), parse_mode="Markdown")


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    usuario = await get_usuario_autenticado(update.effective_chat.id)
    if not usuario:
        await update.message.reply_text("🔒 Primero inicia sesión con /login")
        return

    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(Recordatorio).where(Recordatorio.usuario_id == usuario.id)
        )
        todos = res.scalars().all()

    pendientes  = sum(1 for r in todos if r.estado == Estado.pendiente)
    completados = sum(1 for r in todos if r.estado == Estado.completado)
    vencidos    = sum(1 for r in todos if r.estado == Estado.vencido)
    total       = len(todos)
    pct         = round((completados / total * 100)) if total else 0

    await update.message.reply_text(
        f"📊 *Tus estadísticas:*\n\n"
        f"⏳ Pendientes:  {pendientes}\n"
        f"✅ Completados: {completados}\n"
        f"❌ Vencidos:    {vencidos}\n"
        f"📈 Cumplimiento: {pct}%\n\n"
        f"Ver más detalles en el dashboard web\\.",
        parse_mode="Markdown"
    )


async def cmd_completar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    usuario = await get_usuario_autenticado(update.effective_chat.id)
    if not usuario:
        await update.message.reply_text("🔒 Primero inicia sesión con /login")
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Recordatorio).where(and_(
                Recordatorio.usuario_id == usuario.id,
                Recordatorio.estado == Estado.pendiente
            )).order_by(Recordatorio.fecha_limite).limit(5)
        )
        pendientes = result.scalars().all()

    if not pendientes:
        await update.message.reply_text("No tienes pendientes\\.", parse_mode="Markdown")
        return

    keyboard = []
    for r in pendientes:
        prio = PRIO_EMOJI.get(r.prioridad.value, "🟡")
        keyboard.append([InlineKeyboardButton(
            f"{prio} {r.titulo[:40]}",
            callback_data=f"completar_{r.id}"
        )])

    await update.message.reply_text(
        "✅ ¿Cuál quieres marcar como completada?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def callback_cuadrante(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, rec_id, q_val = query.data.split("_")
    usuario = await get_usuario_autenticado(query.message.chat_id)
    if not usuario:
        return
    rec_data = {}
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Recordatorio).where(and_(
                Recordatorio.id == int(rec_id),
                Recordatorio.usuario_id == usuario.id
            ))
        )
        rec = result.scalar_one_or_none()
        if rec:
            rec.cuadrante = Cuadrante(q_val)
            await db.commit()
            rec_data = {
                "titulo": rec.titulo,
                "descripcion": rec.descripcion,
                "fecha_limite": rec.fecha_limite,
                "categoria": rec.categoria.value if rec.categoria else "otros",
                "prioridad": rec.prioridad.value if rec.prioridad else "media",
            }
    q = CUADRANTE_INFO[q_val]
    titulo = rec_data.get("titulo", "")
    desc   = rec_data.get("descripcion", "")
    fecha  = rec_data.get("fecha_limite")
    fecha_str = fecha.strftime("%d/%m/%Y %H:%M") if fecha else "Sin fecha"

    lineas = [
        f"{q['emoji']} *{q['label']}* · _{q['desc']}_\n",
        f"📝 *{titulo}*",
    ]
    if desc:
        lineas.append(f"_{desc}_")
    lineas.append(f"📅 {fecha_str}")

    await query.edit_message_text(
        "\n".join(lineas),
        parse_mode="Markdown"
    )


async def callback_completar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    usuario = await get_usuario_autenticado(query.message.chat_id)
    if not usuario:
        return

    rec_id = int(query.data.split("_")[1])
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Recordatorio).where(and_(
                Recordatorio.id == rec_id,
                Recordatorio.usuario_id == usuario.id
            ))
        )
        rec = result.scalar_one_or_none()
        if rec:
            rec.estado = Estado.completado
            rec.completado_en = datetime.utcnow()
            await db.commit()
            await query.edit_message_text(f"✅ *{rec.titulo}* marcado como completado\\!", parse_mode="Markdown")
        else:
            await query.edit_message_text("No encontré ese recordatorio.")


# ── Mensajes de texto (lenguaje natural) ──────────────────────────────────────
async def manejar_texto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    usuario = await get_usuario_autenticado(update.effective_chat.id)
    if not usuario:
        await update.message.reply_text(
            "🔒 Primero inicia sesión con /login para guardar recordatorios\\.",
            parse_mode="Markdown"
        )
        return

    texto  = update.message.text
    parsed = parsear_recordatorio(texto)
    titulo, resumen = await procesar_con_ia(texto)
    cuadrante = parsed["cuadrante"]

    async with AsyncSessionLocal() as db:
        rec = Recordatorio(
            usuario_id   = usuario.id,
            titulo       = titulo,
            descripcion  = resumen,
            categoria    = parsed["categoria"],
            prioridad    = parsed["prioridad"],
            fecha_limite = parsed["fecha_limite"],
            cuadrante    = cuadrante,
            origen       = "telegram"
        )
        db.add(rec)
        await db.commit()
        await db.refresh(rec)

    fecha_str = parsed["fecha_limite"].strftime("%d/%m %H:%M") if parsed["fecha_limite"] else "Sin fecha"
    q = CUADRANTE_INFO[cuadrante.value]

    await update.message.reply_text(
        f"✅ *Guardado\\!*\n\n"
        f"📝 *{titulo}*\n"
        f"_{resumen}_\n\n"
        f"📅 {fecha_str}\n\n"
        f"{q['emoji']} Sugerido: *{q['label']}*\n"
        f"_{q['desc']}_\n\n"
        f"¿Confirmas o cambias el cuadrante?",
        reply_markup=_teclado_cuadrantes(rec.id, cuadrante.value),
        parse_mode="Markdown"
    )


# ── Mensajes de audio ──────────────────────────────────────────────────────────
async def manejar_audio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    usuario = await get_usuario_autenticado(update.effective_chat.id)
    if not usuario:
        await update.message.reply_text("🔒 Primero inicia sesión con /login", parse_mode="Markdown")
        return

    if not GROQ_API_KEY and not OPENAI_API_KEY:
        await update.message.reply_text(
            "⚠️ La transcripción de audio no está configurada todavía\\.\n"
            "Por ahora puedes enviar el recordatorio en texto\\.",
            parse_mode="Markdown"
        )
        return

    msg = await update.message.reply_text("🎙️ Transcribiendo tu audio\\.\\.\\.", parse_mode="Markdown")

    # Descargar audio
    voice = update.message.voice or update.message.audio
    file  = await ctx.bot.get_file(voice.file_id)
    path  = f"/tmp/audio_{update.effective_chat.id}.ogg"
    await file.download_to_drive(path)

    # Transcribir
    try:
        texto = await transcribir_audio(path)
    except Exception as e:
        logger.error(f"Error en transcripción: {e}")
        texto = ""

    if not texto:
        await msg.edit_text("❌ No pude transcribir el audio\\. Intenta enviarlo como texto\\.", parse_mode="Markdown")
        return

    # Procesar: extraer metadatos y generar título+resumen con IA
    parsed = parsear_recordatorio(texto)
    titulo, resumen = await procesar_con_ia(texto)
    cuadrante = parsed["cuadrante"]

    async with AsyncSessionLocal() as db:
        rec = Recordatorio(
            usuario_id   = usuario.id,
            titulo       = titulo,
            descripcion  = resumen,
            categoria    = parsed["categoria"],
            prioridad    = parsed["prioridad"],
            fecha_limite = parsed["fecha_limite"],
            cuadrante    = cuadrante,
            origen       = "telegram"
        )
        db.add(rec)
        await db.commit()
        await db.refresh(rec)

    fecha_str = parsed["fecha_limite"].strftime("%d/%m %H:%M") if parsed["fecha_limite"] else "Sin fecha"
    q = CUADRANTE_INFO[cuadrante.value]

    await msg.edit_text(
        f"✅ *Guardado\\!*\n\n"
        f"📝 *{titulo}*\n"
        f"_{resumen}_\n\n"
        f"📅 {fecha_str}\n\n"
        f"{q['emoji']} Sugerido: *{q['label']}*\n"
        f"_{q['desc']}_\n\n"
        f"¿Confirmas o cambias el cuadrante?",
        reply_markup=_teclado_cuadrantes(rec.id, cuadrante.value),
        parse_mode="Markdown"
    )


# ── Construcción de la app ─────────────────────────────────────────────────────
def build_bot() -> Application:
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # ConversationHandler para login
    login_handler = ConversationHandler(
        entry_points=[CommandHandler("login", cmd_login_start)],
        states={
            ESPERANDO_EMAIL:    [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_email)],
            ESPERANDO_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_password)],
        },
        fallbacks=[CommandHandler("cancelar", cmd_cancelar)],
    )

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("pendientes",cmd_pendientes))
    app.add_handler(CommandHandler("hoy",       cmd_hoy))
    app.add_handler(CommandHandler("stats",     cmd_stats))
    app.add_handler(CommandHandler("completar", cmd_completar))
    app.add_handler(login_handler)
    app.add_handler(CallbackQueryHandler(callback_cuadrante, pattern=r"^cuadrante_\d+_q\d$"))
    app.add_handler(CallbackQueryHandler(callback_completar, pattern=r"^completar_\d+$"))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, manejar_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_texto))

    return app
