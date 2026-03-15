"""
Image prompt generation for social content.
Builds structure detection and LLM instructions for image_prompt / carousel_slides.
"""

from typing import Dict, Any, Optional, Tuple


def detect_structure_type(topic: str, post_type: str, weekday: str = None) -> Tuple[str, str]:
    """
    Detect infographic structure type from topic, post_type, and weekday.
    Returns (structure_type, structure_guide) for use in image prompt instructions.

    Args:
        topic: Topic string
        post_type: Post type string
        weekday: Day of week (e.g., "Monday", "Thursday") - used to avoid problem-solution framing on non-Thursday days
    """
    topic_lower = (topic or "").lower()
    post_type_lower = (post_type or "").lower()

    # Only use problem-solution comparison structure on Thursday
    # On other days, use educational/informative comparison structure instead
    is_thursday = weekday == "Thursday" if weekday else False

    if "compar" in topic_lower or " vs " in topic_lower or "tradicional" in topic_lower:
        if is_thursday:
            # Thursday: Problem & Solution day - use problem-solution comparison
            structure_type = "COMPARATIVA_CURIOSITY"
            structure_guide = """
ESTRUCTURA: Comparativa curiosa (Problema intrigante → Promesa visual)
⚠️ CRÍTICO PARA ALCANCE FB: NO CERRAR LA VENTA EN LA IMAGEN. Generar CURIOSIDAD.
- Diseño suave dividido (50/50), NO usar rojo agresivo - usar tonos neutros/tierra con acentos sutiles
- Panel izquierdo (50% espacio, fondo suave beige/gris claro): [SITUACIÓN COMÚN]
  * UNA pregunta o frase intrigante (ej. "¿Sigues perdiendo agua así?")
  * UNA imagen o icono simple que muestre el problema de forma visual (NO texto con cifras)
  * SIN porcentajes, SIN números de pérdida, SIN listados de problemas
- Panel derecho (50% espacio, fondo verde suave): [INSINUACIÓN DE SOLUCIÓN]
  * UNA frase corta de promesa (ej. "Hay una forma mejor")
  * UNA imagen o icono que sugiera la solución sin explicarla completamente
  * SIN cifras exactas, SIN listados de beneficios detallados
- NO incluir tabla comparativa en la imagen - mover esos datos al caption
- Objetivo: Imagen debe generar la pregunta "¿Cómo?" o "¿Cuánto?" - la respuesta está en el caption
- Máximo 2 frases cortas por lado (10-15 palabras total por lado)
"""
        else:
            # Other days: Use educational/informative comparison (not problem-solution)
            structure_type = "COMPARATIVA_EDUCATIVA"
            structure_guide = """
ESTRUCTURA: Comparativa educativa (Opción A ↔ Opción B)
- Diseño limpio dividido (50/50), usar tonos profesionales con acentos en verde/azul IMPAG
- Panel izquierdo (50% espacio, fondo neutral claro): [OPCIÓN/MÉTODO A]
  * Título descriptivo claro (ej. "Método Tradicional", "Sistema Manual")
  * 2-3 características principales con iconos
  * Enfoque neutral, informativo (NO negativo)
- Panel derecho (50% espacio, fondo verde/azul suave): [OPCIÓN/MÉTODO B]
  * Título descriptivo claro (ej. "Método Moderno", "Sistema Automatizado")
  * 2-3 características principales con iconos
  * Enfoque informativo, educativo
- Objetivo: Educar sobre diferentes opciones o enfoques disponibles
- Tono: Neutral, profesional, informativo (NO usar lenguaje de problema/solución)
- Máximo 3-4 puntos por lado con iconos visuales
"""
    elif "paso" in topic_lower or "cómo" in topic_lower or "instalación" in topic_lower or "tutorial" in post_type_lower:
        structure_type = "TUTORIAL"
        structure_guide = """
ESTRUCTURA: Tutorial paso a paso
- Título principal (20% altura, fondo verde/azul IMPAG): "[Nombre del Proceso]"
- 4-6 pasos numerados (60% altura, cada paso en panel separado):
  * Número grande (150px, color verde IMPAG): "1", "2", "3"...
  * Título del paso (texto bold, 60px)
  * Ilustración mostrando la acción
  * Especificación técnica (medidas exactas)
  * Indicador visual del resultado esperado
- Sección de tips (20% altura, fondo azul claro con borde verde):
  * Icono 💡 grande (40px)
  * Texto: Consejos prácticos destacados
"""
    elif any(k in topic_lower for k in ("sistema", "instalación completa", "diagrama", "bomba solar", "pozo", "sistema de riego", "sistema solar", "instalación de riego")):
        structure_type = "DIAGRAMA DE SISTEMA"
        structure_guide = """
ESTRUCTURA: Diagrama de sistema técnico
- Vista superior (50% espacio): Sistema completo en paisaje agrícola Durango
- Vista en corte (50% espacio): Sección técnica mostrando:
  * Componentes subterráneos visibles
  * Flujos con flechas de color (azul=agua, verde=nutrientes, naranja=energía)
  * Dimensiones específicas etiquetadas (ej: "30-50 cm", "1-4 m")
  * Materiales y conexiones visibles
- Tabla de especificaciones (inferior): Materiales, dimensiones, capacidades
"""
    elif any(k in topic_lower for k in ("qué está atacando", "hongo", "virus", "plagas", "diagnóstico", "qué está atacando")):
        structure_type = "QUICK_GUIDE_3"
        structure_guide = """
ESTRUCTURA: Guía rápida diagnóstica (3 paneles horizontales)
- 3 paneles: uno por tipo de problema (ej. hongo, virus, plagas). Cada panel: subtítulo, ilustración pequeña, 1-2 bullets de síntomas + tip de manejo.
- Plantilla simple: un visual por panel + headline + 2 bullets por panel + footer.
"""
    elif any(k in topic_lower for k in ("planifica", "pasos", "camino al éxito", "4 pasos")):
        structure_type = "STEP_PATH_4"
        structure_guide = """
ESTRUCTURA: Proceso en 4 pasos (cuadrantes unidos por camino)
- 4 cuadrantes conectados por una ruta; cada uno: número, título, texto corto, icono (ej. suelo, planta, calendario, pala).
- Plantilla simple: número grande + título + 1-2 frases + icono por paso.
"""
    elif any(k in topic_lower for k in ("los 5", "5 mejores", "5 cultivos", "5 errores", "cinco ")):
        structure_type = "LIST_CIRCULAR_5"
        structure_guide = """
ESTRUCTURA: Lista circular (5 ítems)
- Título central; 5 ítems en círculo con borde/viña; cada ítem: nombre, tagline, 1-2 specs o tips.
- Plantilla simple: un headline central + 5 bloques con título + 1-2 bullets.
"""
    elif any(k in topic_lower for k in ("plantas indicadoras", "tu suelo te habla", "indicador")):
        structure_type = "INDICATOR_SECTIONS_3"
        structure_guide = """
ESTRUCTURA: Secciones por indicador (3 secciones)
- 3 secciones: cada una = problema (ej. compactación) + 2 plantas indicadoras + solución corta.
- Plantilla simple: un visual por sección + headline + 2 bullets por sección + footer.
"""
    elif any(k in topic_lower for k in ("fases lunares", "luna y agricultura", "luna")):
        structure_type = "LUNAR_4_COLUMNS"
        structure_guide = """
ESTRUCTURA: 4 columnas lunares
- 4 columnas: Luna nueva, Creciente, Llena, Menguante; cada una: icono luna, lista de actividades, ilustración pequeña.
- Plantilla simple: 4 columnas con icono + lista + visual.
"""
    else:
        structure_type = "CURIOSITY_DRIVEN_SIMPLE"
        structure_guide = """
ESTRUCTURA: Visual simple centrado en curiosidad (NO multi-panel denso)
⚠️ IMPORTANTE: Menos es más para alcance orgánico FB
- Área principal (60% altura): Visual fuerte + 1 pregunta o frase intrigante (max 15 palabras)
- Área secundaria (20% altura): 1 concepto de apoyo visual o insinuación de beneficio (sin detalles)
- Pie (20% altura): Contacto IMPAG
- NO incluir: paneles múltiples con texto denso, tablas de especificaciones, listados de 4+ bullets
- Especificaciones técnicas detalladas → van en CAPTION, no en la imagen
- Objetivo: Una imagen limpia que genere la pregunta '¿Cómo funciona?' o '¿Qué es esto?'
"""

    return structure_type, structure_guide


def get_weekday_image_style_guidance(weekday_theme: Dict[str, Any]) -> str:
    """
    Return day-specific visual style guidance for image_prompt generation.
    Aligns image style with the weekday theme and content type (same as strategy prompt).
    """
    day_name = weekday_theme.get("day_name", "")
    theme = weekday_theme.get("theme", "")
    content_type = weekday_theme.get("content_type", "")
    sector_rotation = weekday_theme.get("sector_rotation")

    # Day-specific visual style: mood, layout emphasis, and imagery hints
    style_by_day = {
        "Monday": (
            "✊ LUNES - ESTILO MOTIVACIONAL / INSPIRACIONAL:\n"
            "- Imagen debe transmitir inspiración o motivación: paisaje agrícola con buena luz, persona en campo, frase destacada con tipografía inspiradora.\n"
            "- Colores: tonos cálidos (dorado, verde suave, cielo), ambiente positivo. Evitar fondos fríos o técnicos.\n"
            "- Si es frase/cita: texto como elemento central, fondo limpio o paisaje difuminado, tipografía legible y emotiva.\n"
            "- Si es foto de la semana: imagen de campo realista, golden hour o amanecer, sensación de logro o esperanza.\n"
        ),
        "Tuesday": (
            "💸 MARTES - ESTILO PRODUCTO (sutilmente promocional):\n"
            "- ⚠️ EVITAR apariencia de anuncio - debe verse como post orgánico\n"
            "- Imagen debe destacar el PRODUCTO en uso real: persona usando/instalando el producto en campo, ambiente auténtico.\n"
            "- NO incluir precio en la imagen; NO badges de 'OFERTA'; NO diseño tipo flyer\n"
            "- Si hay promoción, mencionarla sutilmente en 1 frase corta o dejarla para el caption\n"
            "- Colores: mantener IMPAG natural, evitar naranjas/amarillos promocionales agresivos\n"
            "- Si es kit: mostrar en contexto de uso real, no estilo catálogo tipo 'knolling'\n"
            "- Objetivo: Mostrar el producto de forma aspiracional y auténtica, no vendedora\n"
        ),
        "Wednesday": (
            "📚 MIÉRCOLES - ESTILO EDUCATIVO / TIPS:\n"
            "- Imagen debe ser clara y didáctica: infografía limpia, pasos numerados, iconos y viñetas legibles.\n"
            "- Estilo: flyer técnico o infografía ilustrada; colores tierra/verde/azul, no fotorealista si es infografía.\n"
            "- Priorizar legibilidad: título + 2-3 bullets por sección, tipografía técnica pero amigable.\n"
            "- Si es Pro Tip o Sabías que: un concepto central con apoyo visual (icono, ilustración pequeña) y texto corto.\n"
        ),
        "Thursday": (
            "🛠️ JUEVES - ESTILO PROBLEMA Y SOLUCIÓN (curiosidad-driven):\n"
            "- ⚠️ EVITAR contraste agresivo rojo vs verde - usar tonos sutiles (beige/gris claro vs verde suave)\n"
            "- Mostrar el problema de forma visual e intrigante, NO con texto explicativo extenso\n"
            "- Insinuar la solución sin dar todos los detalles en la imagen\n"
            "- NO incluir porcentajes o cifras financieras en la imagen - guardarlos para el caption\n"
            "- Máximo 1 pregunta o frase curiosa por lado (ej. '¿Reconoces este error?' vs 'Así se resuelve')\n"
            "- Datos concretos y resultados específicos → CAPTION, no imagen\n"
            "- Objetivo: Generar curiosidad sobre el problema y la solución, no cerrar la historia en la imagen\n"
        ),
        "Friday": (
            "📅 VIERNES - ESTILO ESTACIONAL:\n"
            "- Imagen debe evocar la TEMPORADA o época: calendario, clima, ciclo de cultivo, alertas (heladas, lluvia).\n"
            "- Elementos visuales: calendario agrícola, íconos de clima, paisaje según estación (siembra, cosecha, etc.).\n"
            "- Colores: adaptar sutilmente a la época (ej. tonos otoñales, verdes de temporada de lluvias) manteniendo identidad IMPAG.\n"
            "- Si es checklist o recordatorio: ítems numerados, íconos por actividad, sensación de planificación.\n"
        ),
        "Saturday": (
            "👩‍🌾 SÁBADO - ESTILO SEGMENTO DE PRODUCTOR:\n"
            "- Imagen debe ser específica para el segmento de la semana (forestal 🌲, plantas/cultivos 🌾, ganadería 🐄).\n"
            f"- Segmento esta semana: {sector_rotation or 'general'}. "
            + (
                "Usar escenas, íconos o productos asociados a ese segmento (viveros/árboles, cultivos/riego, ganado/abrevaderos).\n"
                if sector_rotation
                else "Variar entre escenas agrícolas, forestales o ganaderas según el tema.\n"
            )
            + "- Estilo: educativo y práctico; FAQ, Pro Tip o Interesting Fact con visual claro y texto corto.\n"
            "- Mantener tono profesional y útil para ese tipo de productor.\n"
        ),
        "Sunday": (
            "📊 DOMINGO - ESTILO INNOVACIÓN / REPORTES:\n"
            "- Imagen debe verse actual e informativa: instalación real, dato clave, novedad de industria o trivia.\n"
            "- ⚠️ BASE SIEMPRE en fotografía fotorrealista de instalación real (invernadero, sistema de riego, "
            "parcela tecnificada). NO renders 3D futuristas, NO luces LED neón, NO ciencia ficción.\n"
            "- Si hay estadística o dato: overlay limpio con número grande sobre la fotografía real — "
            "no en fondo blanco vacío.\n"
            "- Colores: mantener IMPAG natural; badges ('Nuevo', 'Tendencia 2026') solo si añaden contexto, máximo 1.\n"
            "- Si es trivia: pregunta o dato como centro visual sobre foto real agrícola difuminada.\n"
        ),
    }

    block = style_by_day.get(day_name)
    if not block:
        return ""

    return (
        "🎨 ESTILO SEGÚN DÍA (CRÍTICO - alinea la imagen al tema del día):\n"
        f"DÍA: {day_name} | TEMA: {theme}\n"
        f"TIPO DE CONTENIDO DEL DÍA: {content_type}\n\n"
        f"{block}\n"
        "⚠️ El image_prompt que generes DEBE reflejar este estilo del día además de la estructura y el canal.\n\n"
    )


def get_visual_style_for_post(post_type: str, structure_type: str, weekday: str = None) -> str:
    """
    Return the visual rendering style directive for the main element of the image.
    Maps post_type + structure_type to a concrete visual style (3D render, photo, illustration, etc.)
    instead of always defaulting to 'person holding product'.
    """
    pt = (post_type or "").lower()
    st = (structure_type or "").lower()

    # ── 3D TECHNICAL RENDER ──────────────────────────────────────────────────
    # For educational, installation, how-to, system diagram posts.
    # Shows the technology/system itself — no person needed.
    if st in ("tutorial", "diagrama de sistema") or pt in ("tutorial", "checklist"):
        return (
            "🎨 ESTILO VISUAL: RENDER 3D TÉCNICO\n"
            "- Genera una ilustración 3D técnica de alta calidad (estilo render arquitectónico/industrial moderno).\n"
            "- Vista isométrica o en perspectiva que muestra el sistema, proceso o instalación completa.\n"
            "- Componentes claramente visibles y etiquetados con líneas de llamada (callouts).\n"
            "- Fondo: gradiente azul-gris muy claro o blanco roto; iluminación de estudio, sombras suaves.\n"
            "- Materiales realistas: metal, plástico, tuberías, cables representados con texturas precisas.\n"
            "- SIN personas — el protagonista es el sistema técnico.\n"
            "- Colores de acento IMPAG (verde #2E7D32, azul #1565C0) para resaltar componentes clave.\n"
            "- Si es proceso de instalación: mostrar las etapas en vista de corte o explosionada (exploded view).\n"
            "- Ejemplo de prompt: 'Isometric 3D technical render of a solar-powered water pump system, "
            "exploded view showing solar panel, pump, pipes and drip irrigation network, "
            "clean labels, soft studio lighting, white background, IMPAG green accents'\n"
        )

    # ── 3D / ILLUSTRATED INFOGRAPHIC ────────────────────────────────────────
    if pt in ("infografías", "infografias", "infographic") or st in ("quick_guide_3", "step_path_4", "list_circular_5", "indicator_sections_3", "lunar_4_columns"):
        return (
            "🎨 ESTILO VISUAL: ILUSTRACIÓN TÉCNICA / INFOGRAFÍA 3D\n"
            "- Estilo ilustración editorial moderna con elementos 3D ligeros (no flat design, no fotorealista).\n"
            "- Iconografía detallada: plantas, herramientas, maquinaria agrícola representados como ilustraciones limpias.\n"
            "- Paleta: tonos tierra (ocre, verde olivo, marrón cálido) con acentos IMPAG verde-azul.\n"
            "- Composición modular y organizada — cada sección tiene su ícono o ilustración representativa.\n"
            "- SIN personas — los elementos visuales son íconos e ilustraciones del tema.\n"
            "- Tipografía bold para títulos, regular para subtexto; mucho espacio blanco entre secciones.\n"
        )

    # ── SPLIT PANEL / CURIOSITY (Thursday problem-solution) ─────────────────
    if st in ("comparativa_curiosity",):
        return (
            "🎨 ESTILO VISUAL: SPLIT PANEL CURIOSITY (visual intrigante)\n"
            "- Dos paneles lado a lado (50/50), tonos neutros beige/gris claro vs verde suave.\n"
            "- Panel izquierdo: fotografía o ilustración que muestra la situación problemática de forma visual, "
              "SIN texto explicativo largo — solo una imagen evocadora y 1 frase corta.\n"
            "- Panel derecho: fotografía o ilustración que insinúa la solución sin revelarla completamente.\n"
            "- Las imágenes pueden ser fotorrealistas (campo real, planta real, maquinaria real).\n"
            "- Si hay personas: solo una, en plano medio o detalle de manos/herramienta — NO retrato frontal.\n"
            "- Objetivo visual: generar la pregunta '¿Qué está pasando aquí?' — NO cerrar la historia.\n"
        )

    # ── EDUCATIONAL COMPARISON (other days, not Thursday) ───────────────────
    if st in ("comparativa_educativa",):
        return (
            "🎨 ESTILO VISUAL: COMPARATIVA ILUSTRADA\n"
            "- Diseño en dos columnas con ilustraciones o íconos 3D ligeros para cada opción.\n"
            "- Estilo limpio y profesional, paleta IMPAG verde-azul sobre fondo blanco o gris muy claro.\n"
            "- Cada columna tiene su ilustración representativa (NO foto de persona) + título + 2-3 bullets cortos.\n"
            "- SIN personas — usar íconos, plantas, equipos representados como ilustraciones.\n"
        )

    # ── DATA VISUALIZATION (ROI, Sunday innovation/stats) ────────────────────
    if pt in ("roi",):
        return (
            "🎨 ESTILO VISUAL: VISUALIZACIÓN DE DATOS / REPORT\n"
            "- Diseño tipo dashboard o reporte ejecutivo limpio.\n"
            "- Número o estadística clave: tipografía muy grande (display), color IMPAG verde o azul.\n"
            "- Gráfica simple (barra, pastel, línea) si hay datos comparativos — estilo flat design moderno.\n"
            "- Íconos de tendencia, innovación o crecimiento como elementos de apoyo.\n"
            "- Fondo blanco o gris muy claro; sin fotografías de personas ni paisajes.\n"
            "- Badges mínimos ('Nuevo', 'Tendencia 2026') si aplica.\n"
        )

    if weekday == "Sunday":
        return (
            "🎨 ESTILO VISUAL: FOTOGRAFÍA REAL DE INNOVACIÓN AGRÍCOLA\n"
            "- ⚠️ CRÍTICO: Fotografía fotorrealista de una instalación REAL — invernadero, sistema hidropónico, "
            "parcela tecnificada, rancho ganadero moderno — según el tema del post.\n"
            "- NO renders 3D futuristas. NO ciencia ficción. NO luces LED de colores (morado, azul neón). "
            "NO estética de videojuego ni laboratorio sci-fi.\n"
            "- La instalación debe verse como algo que EXISTE en México hoy, no en el futuro.\n"
            "- Luz natural preferida: sol de mediodía o golden hour entrando por el techo del invernadero.\n"
            "- Si el tema incluye datos o estadísticas: overlay limpio con 1 número grande + contexto breve "
            "sobre la fotografía real (no en fondo blanco vacío).\n"
            "- Gráficas simples (barra, línea) solo si son imprescindibles — siempre sobre foto real difuminada, "
            "NO en fondo blanco.\n"
            "- Badges ('Tendencia 2026', 'Nuevo') solo si añaden contexto; máximo 1.\n"
            "- Ejemplos de referencias: hileras de torres hidropónicas en invernadero con luz natural, "
            "sistema de riego por goteo en parcela de Durango, invernadero de hortalizas con plantas reales y verdes.\n"
        )

    # ── MOTIVATIONAL / LIFESTYLE (Monday, La Vida en el Rancho) ─────────────
    if weekday == "Monday":
        return (
            "🎨 ESTILO VISUAL: FOTOGRAFÍA CINEMATOGRÁFICA / PAISAJE EMOCIONAL\n"
            "- Fotografía fotorrealista de paisaje agrícola del norte de México (Durango), "
              "toma amplia o aérea (drone shot), golden hour o amanecer.\n"
            "- Si hay persona: silueta de productor en el campo al contraluz, "
              "plano general — NO retrato frontal mirando a cámara.\n"
            "- Ambiente: emocional, poético, auténtico. Cielo dramático, hileras de cultivo o ganado en pastizal.\n"
            "- Colores: tonos cálidos (dorado, naranja suave, verde natural), sin filtros artificiales.\n"
            "- Texto mínimo o ninguno — la imagen habla sola.\n"
        )

    # ── SEASONAL / CALENDAR (Friday) ────────────────────────────────────────
    if weekday == "Friday":
        return (
            "🎨 ESTILO VISUAL: ILUSTRACIÓN ESTACIONAL / CALENDARIO\n"
            "- Composición que evoca la temporada: lluvia, heladas, cosecha, siembra — según el tema.\n"
            "- Elementos visuales: íconos de clima (☁️❄️🌧️☀️), calendario agrícola, cultivos en etapa relevante.\n"
            "- Estilo ilustración editorial moderna — no fotorrealista, pero detallada y cálida.\n"
            "- Paleta adaptada a la época: verdes frescos para siembra, dorados para cosecha, azules para heladas.\n"
            "- SIN personas — los protagonistas son los elementos naturales y las plantas/cultivos.\n"
        )

    # ── PROMOTIONAL WITH PRODUCT + PERSON (Tuesday/promo/kit/new-arrivals) ──
    if pt in ("promo", "kit", "new-arrivals", "new_arrivals") or weekday == "Tuesday":
        return (
            "🎨 ESTILO VISUAL: FOTOGRAFÍA DE PRODUCTO EN CONTEXTO REAL\n"
            "- Fotografía fotorrealista del producto EN USO REAL en campo, rancho o vivero.\n"
            "- Contexto auténtico: ambiente natural de Durango, NOT fondo blanco tipo catálogo.\n"
            "- Persona SOLO si añade autenticidad: técnico o productor usando/instalando el producto "
              "en plano medio — NO retrato frontal mirando a cámara con producto en mano.\n"
            "- Iluminación natural preferida (golden hour). Colores reales, sin saturación artificial.\n"
            "- Para kits en FB/IG: composición en contexto de uso; para Stories/Status: knolling limpio está OK.\n"
        )

    # ── BEFORE/AFTER ────────────────────────────────────────────────────────
    if pt in ("before-after", "before_after"):
        return (
            "🎨 ESTILO VISUAL: ANTES / DESPUÉS (fotografía o render comparativo)\n"
            "- Dos imágenes lado a lado o arriba/abajo mostrando el estado antes y el estado después.\n"
            "- Puede ser fotografía real o render 3D según el tema (campo antes/después de riego, suelo seco/húmedo, etc.).\n"
            "- Etiquetas simples 'ANTES' / 'DESPUÉS' con tipografía bold; colores neutros para ANTES, verde IMPAG para DESPUÉS.\n"
            "- SIN personas — protagoniza el resultado visible en el cultivo, suelo o instalación.\n"
        )

    # ── IMPORTANT DATE / CELEBRATION ────────────────────────────────────────
    if pt in ("important-date", "important_date"):
        return (
            "🎨 ESTILO VISUAL: DISEÑO EDITORIAL DE CELEBRACIÓN\n"
            "- Composición festiva pero elegante, tipografía expresiva como elemento principal.\n"
            "- Elementos visuales: flores, campos, símbolos culturales mexicanos según la fecha.\n"
            "- Paleta cálida y celebratoria; puede incluir ilustraciones de personas en estilo editorial "
              "(no fotorrealista) si la fecha lo amerita.\n"
            "- NO usar diseño tipo póster escolar ni imágenes de banco de fotos genéricas.\n"
        )

    # ── DEFAULT: SECTOR-SPECIFIC TECHNICAL PHOTO ────────────────────────────
    return (
        "🎨 ESTILO VISUAL: FOTOGRAFÍA TÉCNICA AGRÍCOLA AUTÉNTICA\n"
        "- Fotografía fotorrealista del cultivo, sistema, maquinaria o situación descrita en el tema.\n"
        "- Contexto real de campo en el norte de México (Durango): parcelas, invernaderos, ranchos.\n"
        "- Persona SOLO si es imprescindible para mostrar escala o uso — plano medio o detalle, "
          "NO retrato frontal mirando a cámara sosteniendo algo.\n"
        "- Priorizar el ELEMENTO TÉCNICO o NATURAL como protagonista: la planta enferma, "
          "el sistema de riego, el suelo, la maquinaria, el cultivo.\n"
        "- Iluminación natural. Sin personas decorativas ni modelos de stock.\n"
    )


def build_image_prompt_instructions(
    strat_data: Dict[str, Any],
    structure_type: str,
    structure_guide: str,
    contact_info: Dict[str, str],
    selected_product_id: Optional[str] = None,
    weekday_theme: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Build the image_prompt-specific section appended to the content creation prompt.
    If weekday_theme is provided, injects day-specific image style guidance (same logic as strategy prompt).
    """
    channel = strat_data.get("channel", "fb-post")
    topic = strat_data.get("topic", "")
    post_type = (strat_data.get("post_type") or "").lower()
    weekday = weekday_theme.get("day_name") if weekday_theme else None

    out = (
        "--- INSTRUCCIONES ESPECÍFICAS PARA image_prompt ---\n"
        f"ESTRUCTURA DETECTADA: {structure_type}\n"
        f"{structure_guide}\n\n"
    )

    if weekday_theme:
        out += get_weekday_image_style_guidance(weekday_theme)

    out += (
        "🚨 REGLA DE ORO PARA ALCANCE ORGÁNICO FACEBOOK:\n"
        "La imagen debe hacer que el usuario se DETENGA y pregunte '¿Cómo?' o '¿Qué es esto?' - NO debe cerrar la venta.\n"
        "EVITAR en imagen (especialmente FB/IG posts):\n"
        "  ❌ Cifras financieras específicas ($X/día, $X ahorrado, etc.) → moverlas al caption\n"
        "  ❌ Tablas de comparación detalladas → moverlas al caption\n"
        "  ❌ Listas de 4+ bullets con specs → moverlas al caption\n"
        "  ❌ Fondos rojos agresivos o diseño tipo flyer promocional\n"
        "  ❌ Textos densos que expliquen todo - la imagen debe intrigar, el caption explica\n"
        "  ❌ Apariencia de anuncio pagado o catálogo\n"
        "PRIORIZAR en imagen:\n"
        "  ✅ Visual fuerte y limpio (producto en uso real, persona auténtica, paisaje)\n"
        "  ✅ Máximo 1-2 frases cortas que generen curiosidad (10-20 palabras total)\n"
        "  ✅ Colores suaves y naturales (verde IMPAG, tierra, beige, grises)\n"
        "  ✅ Apariencia orgánica, como si fuera compartido por un experto, no vendido\n\n"
        "El campo 'image_prompt' DEBE ser un prompt detallado y técnico para generación de imágenes (estilo IMPAG).\n"
        "Sigue este formato estructurado:\n\n"
        "⚠️⚠️⚠️ ADAPTACIÓN POR CANAL (CRÍTICO) ⚠️⚠️⚠️:\n"
        "- Para wa-status, stories, tiktok, reels: La imagen DEBE ser AUTOEXPLICATIVA con TEXTO GRANDE Y VISIBLE.\n"
        "  El usuario debe entender el mensaje SOLO viendo la imagen, sin leer el caption.\n"
        "- Para fb-post, ig-post: ⚠️ NUEVA REGLA ALCANCE ORGÁNICO:\n"
        "  * La imagen debe GENERAR CURIOSIDAD, NO explicar todo\n"
        "  * MÁXIMO 1-2 frases cortas en la imagen (10-20 palabras total)\n"
        "  * NO incluir: tablas de comparación, listados largos de specs, cifras financieras exactas ($X/día), porcentajes múltiples\n"
        "  * Specs técnicas detalladas → van en el CAPTION, no en la imagen\n"
        "  * Diseño debe verse orgánico, NO como anuncio o flyer promocional\n"
        "  * Evitar fondos rojos agresivos - preferir tonos neutros, tierra, verdes suaves\n"
        "  * Objetivo: Que el usuario pregunte '¿Cómo?' o '¿Cuánto?' - la respuesta está en el caption\n\n"
        "FORMATO REQUERIDO (adaptar dimensiones al canal):\n"
        "- wa-status/stories/tiktok/reels: Vertical 1080×1920 px\n"
        "- fb-post/ig-post: Cuadrado 1080×1080 px\n"
        "Estilo IMPAG: diseño limpio, moderno y profesional. Acentos verde–azul IMPAG, tipografías gruesas para títulos.\n"
    )

    # ── Inject post-type-aware visual style (replaces generic 'person holding product') ──
    out += "\n" + get_visual_style_for_post(post_type, structure_type, weekday) + "\n"

    web = contact_info.get("web", "")
    whatsapp = contact_info.get("whatsapp", "")
    location = contact_info.get("location", "")

    out += (
        "Instrucciones de diseño detalladas:\n"
        "1. LOGOS (OBLIGATORIO - §7 IMPAG only):\n"
        "   - Usar SOLO branding IMPAG. Logo oficial 'IMPAG Agricultura Inteligente' en esquina superior derecha, sin deformarlo.\n"
        "   - No incluir otros nombres ni logos en la imagen.\n\n"
        "2. ELEMENTO PRINCIPAL:\n"
        "   - Seguir EXACTAMENTE el estilo visual indicado arriba (🎨 ESTILO VISUAL).\n"
        "   - El estilo visual define si usar render 3D, fotografía, ilustración, etc. según el tipo de post.\n"
        "   - NO ignorar el estilo visual para poner una foto genérica de 'persona sosteniendo producto'.\n"
        "   ⚠️ PARA STORIES/STATUS/TIKTOK/REELS: Agrega TEXTO GRANDE Y VISIBLE en la imagen que comunique el mensaje principal.\n"
        "   El texto debe ser legible desde lejos, con buen contraste, tamaño mínimo 60-80px.\n"
        "   ⚠️ PARA FB-POST/IG-POST: REDUCIR TEXTO. Máximo 1-2 frases cortas (10-20 palabras total). Generar curiosidad.\n\n"
        "3. ESPECIFICACIONES TÉCNICAS:\n"
        "   - Para FB-POST/IG-POST: NO incluir bloque de especificaciones técnicas en la imagen.\n"
        "   - Specs técnicas detalladas → van en el CAPTION\n"
        "   - Para STORIES/STATUS/TIKTOK/REELS: Puede incluir 2-3 specs clave con viñetas.\n\n"
        "4. PIE DEL FLYER (mantener estilo IMPAG):\n"
        f"   - {web}\n"
        "   - Envíos a todo México\n"
        f"   - WhatsApp: {whatsapp}\n"
        f"   - 📍 {location}\n\n"
        "OUTPUT JSON:\n"
        "- TODOS los strings JSON deben estar entre comillas dobles y CERRADOS correctamente\n"
        "- Si un string contiene saltos de línea (\\n), escápalos como \\\\n\n"
        "- Si un string contiene comillas, escápalas como \\\"\n"
        "- NUNCA dejes strings sin cerrar - cada \" debe tener su \" de cierre\n"
        "- El JSON debe ser válido y parseable\n"
        "⚠️ REGLA CRÍTICA: 'image_prompt' es SIEMPRE OBLIGATORIO (nunca null). Si es carrusel, proporciona el prompt de la imagen de portada o primera slide.\n"
        "suggested_hashtags: cuando sea útil, incluye 5-8 hashtags en español (ej. #Riego #Agricultura #Campo).\n\n"
        "RESPONDE SOLO CON EL JSON (sin texto adicional):\n"
        "{\n"
        '  "selected_category": "...",\n'
        '  "selected_product_id": "...",\n'
        f'  "channel": "{channel}",\n'
        f'  "topic": "{topic}",\n'
        '  "caption": "... (RESPETA: wa-status/stories/tiktok/reels = MUY CORTO, fb-post = puede ser largo)",\n'
        '  "image_prompt": "PROMPT DETALLADO OBLIGATORIO para generación de imagen (SIEMPRE requerido). Debe describir el estilo visual indicado (3D render / fotografía / ilustración / etc.) según el tipo de post. Si es carrusel, usa el prompt de la imagen de portada o primera slide. SIEMPRE incluye logos IMPAG y dimensiones correctas (1080×1920 para vertical, 1080×1080 para cuadrado).",\n'
        '  "carousel_slides": ["Slide 1 CON TEXTO GRANDE...", "Slide 2 CON TEXTO...", ...] (SOLO si es carrusel: TikTok 2-3, FB/IG 2-10. Si es carrusel, image_prompt debe ser la portada o primera slide),\n'
        '  "needs_music": true/false,\n'
        '  "posting_time": "...",\n'
        '  "notes": "...",\n'
        '  "suggested_hashtags": ["#Riego", "#Agricultura", ...] (opcional: 5-8 hashtags en español)\n'
        "}\n\n"
        f"REGLAS FINALES: Producto ID {selected_product_id or 'ninguno'}. Incluye logos IMPAG. Sigue el estilo visual (🎨) indicado — NO sustituir por foto genérica de persona con producto."
    )

    return out
