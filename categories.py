"""
Detección automática de categoría, prioridad y fecha
a partir de texto en español (sin API externa).
"""
import re
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Tuple
from database import Categoria, Prioridad, Cuadrante

TZ = ZoneInfo(os.getenv("TZ", "America/Mexico_City"))

def _ahora() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)

# ── Palabras clave por categoría ───────────────────────────────────────────────
KEYWORDS: dict[Categoria, list[str]] = {
    Categoria.trabajo: [
        "trabajo", "oficina", "reunión", "reunion", "junta", "cliente",
        "proyecto", "reporte", "informe", "presentación", "presentacion",
        "correo", "email", "llamada", "llamar", "proveedor", "factura",
        "contrato", "propuesta", "entrega", "sprint", "equipo", "jefe",
        "gerente", "colega", "compañero", "empresa", "negocio", "ventas",
        "soporte", "ticket", "tarea laboral", "capacitación", "capacitacion",
    ],
    Categoria.finanzas: [
        "pagar", "pago", "factura", "banco", "tarjeta", "crédito", "credito",
        "débito", "debito", "transferencia", "depósito", "deposito", "cobro",
        "cobrar", "dinero", "presupuesto", "gasto", "inversión", "inversion",
        "impuesto", "declaración", "declaracion", "renta", "nómina", "nomina",
        "sueldo", "salario", "deuda", "préstamo", "prestamo", "hipoteca",
        "seguro", "servicio", "cuenta", "recibo",
    ],
    Categoria.salud: [
        "médico", "medico", "doctor", "cita médica", "cita medica",
        "hospital", "clínica", "clinica", "farmacia", "medicamento",
        "medicina", "pastilla", "ejercicio", "gym", "gimnasio", "correr",
        "dentista", "odontólogo", "odontologo", "psicólogo", "psicologo",
        "terapia", "análisis", "analisis", "examen médico", "vacuna",
        "dieta", "nutricionista", "cirugía", "cirugia", "salud",
    ],
    Categoria.familia: [
        "familia", "mamá", "mama", "papá", "papa", "hermano", "hermana",
        "hijo", "hija", "esposa", "esposo", "pareja", "cumpleaños",
        "aniversario", "boda", "graduación", "graduacion", "familiar",
        "reunión familiar", "visitar", "visita", "abuelo", "abuela",
        "tío", "tia", "primo", "prima", "sobrino", "sobrina",
    ],
    Categoria.legal: [
        "contrato", "legal", "abogado", "notario", "documento", "firma",
        "escritura", "poder", "demanda", "juicio", "trámite", "tramite",
        "registro", "licencia", "permiso", "certificado", "apostille",
        "visa", "pasaporte", "acta", "constitución", "constitucion",
        "sociedad", "legal", "ley", "reglamento", "normativa",
    ],
    Categoria.compras: [
        "comprar", "compra", "tienda", "supermercado", "mercado",
        "shopping", "pedido", "pedir", "encargar", "encargo", "lista",
        "producto", "artículo", "articulo", "tela", "ropa", "zapatos",
        "electrodoméstico", "electrodomestico", "mueble", "repuesto",
        "material", "insumo", "stock", "inventario",
    ],
    Categoria.educacion: [
        "estudiar", "estudio", "curso", "clase", "tarea", "examen",
        "universidad", "escuela", "colegio", "lectura", "leer", "libro",
        "capacitación", "capacitacion", "certificación", "certificacion",
        "diploma", "título", "titulo", "webinar", "seminario", "taller",
        "aprender", "aprendizaje", "práctica", "practica", "inglés",
    ],
    Categoria.personal: [
        "personal", "yo", "mi", "casa", "hogar", "descanso", "vacaciones",
        "viaje", "viajar", "amigo", "amiga", "cumple", "celebrar",
        "renovar", "arreglar", "limpiar", "organizar", "hobby",
        "película", "pelicula", "restaurante", "cena", "almuerzo",
    ],
}

# ── Palabras clave de prioridad ────────────────────────────────────────────────
PRIORIDAD_ALTA = [
    "urgente", "urgentemente", "ya", "ahora", "inmediato", "inmediatamente",
    "crítico", "critico", "importante", "hoy", "emergencia", "asap",
    "prioritario", "priority", "alta", "high", "prioridad alta",
]
PRIORIDAD_BAJA = [
    "cuando pueda", "sin prisa", "eventualmente", "a futuro",
    "baja prioridad", "low priority", "baja", "low", "no urgente",
    "luego", "después", "despues", "tranquilo",
]

# ── Tablas de referencia ───────────────────────────────────────────────────────
DIAS_SEMANA = {
    "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2,
    "jueves": 3, "viernes": 4, "sábado": 5, "sabado": 5, "domingo": 6,
}
MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
    "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12,
}


# ── Detección de categoría ─────────────────────────────────────────────────────
def detectar_categoria(texto: str) -> Categoria:
    texto_lower = texto.lower()
    scores = {cat: 0 for cat in Categoria}
    for cat, keywords in KEYWORDS.items():
        for kw in keywords:
            if kw in texto_lower:
                scores[cat] += 1
    mejor = max(scores, key=lambda c: scores[c])
    return mejor if scores[mejor] > 0 else Categoria.otros


# ── Detección de prioridad ─────────────────────────────────────────────────────
def detectar_prioridad(texto: str) -> Prioridad:
    texto_lower = texto.lower()
    for kw in PRIORIDAD_ALTA:
        if kw in texto_lower:
            return Prioridad.alta
    for kw in PRIORIDAD_BAJA:
        if kw in texto_lower:
            return Prioridad.baja
    return Prioridad.media


# ── Extracción de hora ─────────────────────────────────────────────────────────
def _extraer_hora(texto: str) -> Tuple[int, int]:
    """
    Extrae (hora, minuto) de texto en español.
    Soporta todos los formatos: HH:MM, HH.MM, X y Y, X y media,
    X y cuarto, mediodía, medianoche, de la tarde/mañana/noche, am/pm.
    Default: 9:00
    """
    # Indicadores AM/PM
    es_pm = bool(re.search(r"de la tarde|de la noche|pasado el medio\s*d[íi]a|\bpm\b", texto))
    es_am = bool(re.search(r"de la mañana|de la madrugada|\bam\b", texto))

    def aplicar_ampm(h: int) -> int:
        if es_pm and h != 12 and h < 12:
            return h + 12
        if es_am and h == 12:
            return 0
        return h

    # Palabras especiales
    if re.search(r"\bmedianoche\b", texto):
        return (0, 0)
    if re.search(r"\bmedio\s*d[íi]a\b|\bmediodia\b", texto):
        return (12, 0)

    # "en la mañana" sin hora específica → 9:00
    # "en la tarde" sin hora específica → 15:00
    # "en la noche" sin hora específica → 20:00
    # (solo aplica si no hay número de hora)

    # Formato HH:MM o HH.MM (ej: 14:30, 2.30, 17.00)
    m = re.search(r"\b(\d{1,2})[:.](\d{2})\b\s*(am|pm)?", texto)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        if m.group(3) == "pm" and h < 12:
            h += 12
        elif m.group(3) == "am" and h == 12:
            h = 0
        else:
            h = aplicar_ampm(h)
        return (h, mn)

    # "X y cuarto" → X:15
    m = re.search(r"(?:a las\s+)?(\d{1,2})\s+y\s+cuarto", texto)
    if m:
        h = int(m.group(1))
        return (aplicar_ampm(h), 15)

    # "X y media" → X:30
    m = re.search(r"(?:a las\s+)?(\d{1,2})\s+y\s+media", texto)
    if m:
        h = int(m.group(1))
        return (aplicar_ampm(h), 30)

    # "X y Y" → X:Y (ej: "5 y 30", "a las 5 y 30")
    m = re.search(r"(?:a las\s+)?(\d{1,2})\s+y\s+(\d{2})\b", texto)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        return (aplicar_ampm(h), mn)

    # "a las X am/pm" o "a las X"
    m = re.search(r"a las\s+(\d{1,2})\s*(am|pm)?", texto)
    if m:
        h = int(m.group(1))
        if m.group(2) == "pm" and h < 12:
            h += 12
        elif m.group(2) == "am" and h == 12:
            h = 0
        else:
            h = aplicar_ampm(h)
        return (h, 0)

    # "X am/pm" suelto
    m = re.search(r"\b(\d{1,2})\s*(am|pm)\b", texto)
    if m:
        h = int(m.group(1))
        if m.group(2) == "pm" and h < 12:
            h += 12
        elif m.group(2) == "am" and h == 12:
            h = 0
        return (h, 0)

    # "11 de la noche", "3 de la tarde", "8 de la mañana"
    m = re.search(r"\b(\d{1,2})\s+de la (tarde|noche|mañana|madrugada)\b", texto)
    if m:
        h = int(m.group(1))
        periodo = m.group(2)
        if periodo in ("tarde", "noche") and h < 12:
            h += 12
        elif periodo == "madrugada" and h == 12:
            h = 0
        return (h, 0)

    # Sin hora pero con indicador de parte del día
    if re.search(r"de la noche|en la noche|por la noche", texto):
        return (20, 0)
    if re.search(r"de la tarde|en la tarde|por la tarde", texto):
        return (15, 0)
    if re.search(r"de la mañana|en la mañana|por la mañana", texto):
        return (9, 0)

    return (9, 0)  # default


# ── Detección de fecha ─────────────────────────────────────────────────────────
def detectar_fecha(texto: str) -> Optional[datetime]:
    """
    Extrae fecha y hora del texto en español.
    Cubre todos los formatos comunes.
    """
    t = texto.lower()
    ahora = _ahora()

    def con_hora(base: datetime) -> datetime:
        h, mn = _extraer_hora(t)
        return base.replace(hour=h, minute=mn, second=0, microsecond=0)

    # ── Relativas simples ──────────────────────────────────────────────────────

    # "ahora mismo", "en este momento"
    if re.search(r"\bahora mismo\b|\ben este momento\b", t):
        return ahora

    # "hoy"
    if re.search(r"\bhoy\b", t):
        return con_hora(ahora)

    # "pasado mañana"
    if re.search(r"pasado\s*mañana", t):
        return con_hora(ahora + timedelta(days=2))

    # "mañana" como fecha (no como "de la mañana", "por la mañana", "en la mañana")
    if re.search(r"\bmañana\b", t) and not re.search(r"(?:de|por|en)\s+la\s+mañana", t):
        return con_hora(ahora + timedelta(days=1))

    # ── Días de la semana ──────────────────────────────────────────────────────
    # "el próximo viernes", "el viernes", "este viernes", "próximo viernes"
    for dia_nombre, dia_num in DIAS_SEMANA.items():
        if re.search(rf"\b{dia_nombre}\b", t):
            dias_hasta = (dia_num - ahora.weekday()) % 7
            if dias_hasta == 0:
                dias_hasta = 7  # si es hoy, va al próximo
            # "este X" puede ser el más cercano aunque sea 0 días
            if re.search(rf"este\s+{dia_nombre}", t) and (dia_num - ahora.weekday()) % 7 == 0:
                dias_hasta = 0
            return con_hora(ahora + timedelta(days=dias_hasta))

    # ── Relativas con número ───────────────────────────────────────────────────

    # "en X semanas"
    m = re.search(r"en\s+(\d+)\s+semanas?", t)
    if m:
        return con_hora(ahora + timedelta(weeks=int(m.group(1))))

    # "en X días"
    m = re.search(r"en\s+(\d+)\s+d[íi]as?", t)
    if m:
        return con_hora(ahora + timedelta(days=int(m.group(1))))

    # "en X horas"
    m = re.search(r"en\s+(\d+)\s+horas?", t)
    if m:
        return ahora + timedelta(hours=int(m.group(1)))

    # "en X minutos"
    m = re.search(r"en\s+(\d+)\s+minutos?", t)
    if m:
        return ahora + timedelta(minutes=int(m.group(1)))

    # ── Fechas específicas ─────────────────────────────────────────────────────

    # "el 21 del 6", "el 3 del 12", "el 5 del 7 del 2027" (mes numérico con "del")
    m = re.search(r"(?:el\s+)?(\d{1,2})\s+del\s+(\d{1,2})(?:\s+(?:del?\s+)?(\d{2,4}))?", t)
    if m:
        dia, mes_num = int(m.group(1)), int(m.group(2))
        año = int(m.group(3)) if m.group(3) else ahora.year
        if año < 100: año += 2000
        if 1 <= dia <= 31 and 1 <= mes_num <= 12:
            h, mn = _extraer_hora(t)
            try:
                fecha_tentativa = datetime(año, mes_num, dia, h, mn)
                if fecha_tentativa < ahora and not m.group(3):
                    fecha_tentativa = datetime(año + 1, mes_num, dia, h, mn)
                return fecha_tentativa
            except ValueError:
                pass

    # "el 15 de junio", "15 de junio", "fecha 25 de junio", "el próximo 15 de junio"
    m = re.search(r"(?:el\s+|fecha\s+|próximo\s+|proximo\s+)?(\d{1,2})\s+de\s+([a-záéíóúü]+)", t)
    if m:
        dia = int(m.group(1))
        mes = MESES.get(m.group(2))
        if mes and 1 <= dia <= 31:
            h, mn = _extraer_hora(t)
            año = ahora.year
            # Si la fecha ya pasó este año, ir al próximo año
            try:
                fecha_tentativa = datetime(año, mes, dia, h, mn)
                if fecha_tentativa < ahora:
                    fecha_tentativa = datetime(año + 1, mes, dia, h, mn)
                return fecha_tentativa
            except ValueError:
                pass

    # "15/06", "15/06/2026", "15-06", "15-06-2026"
    m = re.search(r"\b(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?\b", t)
    if m:
        dia, mes_num = int(m.group(1)), int(m.group(2))
        año = int(m.group(3)) if m.group(3) else ahora.year
        if año < 100:
            año += 2000
        h, mn = _extraer_hora(t)
        try:
            fecha_tentativa = datetime(año, mes_num, dia, h, mn)
            if fecha_tentativa < ahora and not m.group(3):
                fecha_tentativa = datetime(año + 1, mes_num, dia, h, mn)
            return fecha_tentativa
        except ValueError:
            pass

    # Sin fecha explícita pero con hora → asumir hoy o mañana
    hora_match = re.search(
        r"\d{1,2}[:. ]\d{2}|a las \d|de la tarde|de la noche|de la mañana|\d am|\d pm",
        t
    )
    if hora_match:
        h, mn = _extraer_hora(t)
        base = ahora.replace(hour=h, minute=mn, second=0, microsecond=0)
        if base < ahora:  # si la hora ya pasó hoy, va mañana
            base += timedelta(days=1)
        return base

    return None


# ── Detección de cuadrante (Matriz de Eisenhower) ─────────────────────────────
PALABRAS_IMPORTANTE = [
    "cliente", "jefe", "gerente", "reunión", "reunion", "entrega", "presentación",
    "presentacion", "contrato", "médico", "medico", "doctor", "hospital", "cirugía",
    "cirugia", "legal", "abogado", "juicio", "declaración", "declaracion", "impuesto",
    "nómina", "nomina", "proyecto", "sprint", "importante", "crítico", "critico",
    "urgente", "emergencia", "visa", "pasaporte", "vuelo", "examen", "tesis",
    "factura", "pago", "vencimiento", "deuda", "hipoteca",
]

def detectar_cuadrante(texto: str, fecha_limite=None) -> Cuadrante:
    """
    Clasifica en la Matriz de Eisenhower según urgencia e importancia.
    - Urgente: fecha hoy o mañana, o palabras de urgencia en el texto
    - Importante: palabras clave de alto impacto
    """
    from datetime import datetime, timedelta
    t = texto.lower()

    # ── Detectar urgencia ──────────────────────────────────────────────────────
    urgente = False
    if fecha_limite:
        ahora = datetime.now()
        dias_restantes = (fecha_limite - ahora).days
        if dias_restantes <= 1:
            urgente = True
    if re.search(r"\b(urgente|ahora|ya|hoy|emergencia|inmediato|asap)\b", t):
        urgente = True

    # ── Detectar importancia ───────────────────────────────────────────────────
    importante = any(kw in t for kw in PALABRAS_IMPORTANTE)
    # Alta prioridad también implica importancia
    if detectar_prioridad(texto) == Prioridad.alta:
        importante = True

    # ── Clasificar ────────────────────────────────────────────────────────────
    if urgente and importante:
        return Cuadrante.q1
    if not urgente and importante:
        return Cuadrante.q2
    if urgente and not importante:
        return Cuadrante.q3
    return Cuadrante.q4


# ── Parsear recordatorio completo ──────────────────────────────────────────────
def parsear_recordatorio(texto: str) -> dict:
    """
    Recibe texto libre y retorna dict con:
    titulo, categoria, prioridad, fecha_limite
    """
    titulo = re.sub(r"#\w+", "", texto).strip()
    titulo = re.sub(r"\s+", " ", titulo)

    # Categoría: hashtag explícito primero
    categoria = None
    for cat in Categoria:
        if f"#{cat.value}" in texto.lower():
            categoria = cat
            break
    if not categoria:
        m = re.search(r"categor[íi]a\s+(\w+)", texto.lower())
        if m:
            try:
                categoria = Categoria(m.group(1))
            except ValueError:
                pass
    if not categoria:
        categoria = detectar_categoria(texto)

    prioridad = detectar_prioridad(texto)
    fecha     = detectar_fecha(texto)

    cuadrante = detectar_cuadrante(texto, fecha)

    return {
        "titulo":       titulo[:280] if titulo else texto[:280],
        "categoria":    categoria,
        "prioridad":    prioridad,
        "fecha_limite": fecha,
        "cuadrante":    cuadrante,
    }
