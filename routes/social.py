from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import anthropic
import os
import json
import random
from datetime import datetime, date as date_type
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from models import get_db, Product, ProductCategory, SocialPost, SupplierProduct
from auth import verify_google_token

# Import new modules (same directory)
import sys
from pathlib import Path
routes_dir = Path(__file__).parent
if str(routes_dir) not in sys.path:
    sys.path.insert(0, str(routes_dir))
import social_context
import social_products
import social_llm
import social_rate_limit
import social_logging
import social_topic
import social_image_prompt
# New multi-step pipeline modules
import social_config
import social_helpers
import social_topic_engine
import social_strategy_engine
import social_content_engine

router = APIRouter()

claude_api_key = os.getenv("CLAUDE_API_KEY")


# --- Configuration Constants (Moved from Frontend) ---

CONTACT_INFO = {
    "web": "todoparaelcampo.com.mx",
    "whatsapp": "677-119-7737",
    "location": "Nuevo Ideal, Durango",
    "social": "@impag.tech",
    "email": "ventas@impag.tech"
}

# Topic examples for broad-topic days (Wed/Sat/Sun) — inspiration only, §11
BROAD_TOPIC_EXAMPLES_EXTRA = (
    # Plagas, enfermedades y manejo integrado (40)
    "Trampas de feromona vs trampas cromáticas: cuándo usar cada una",
    "Insecticidas de contacto vs sistémicos: ventajas y riesgos",
    "Rotación de ingredientes activos: cómo evitar resistencia",
    "Manejo de trips en chile: diagnóstico y control por etapa",
    "Manejo de mosca blanca en jitomate: umbrales y acciones",
    "Gusano cogollero en maíz: detección temprana y control",
    "Araña roja en berries: signos y control sin quemar el cultivo",
    "Pulgones en hortalizas: control biológico con crisopas y catarinas",
    "Minador de la hoja: cómo identificar galerías y reducir daño",
    "Nematodos: cuándo sospecharlos y cómo confirmarlos",
    "Damping-off en semilleros: causas, prevención y qué hacer",
    "Fusarium vs Phytophthora: diferencias clave en raíces",
    "Oídio vs mildiu: cómo distinguirlos en campo",
    "Mancha bacteriana vs mancha fúngica en chile: guía rápida",
    "Tizón temprano vs tardío en papa: señales y manejo",
    "Roya en frijol: manejo preventivo en temporal",
    "Carbón de la espiga en maíz: prevención y manejo de semilla",
    "Antracnosis en mango: calendario de control por floración",
    "Moniliasis en cacao: prácticas culturales que sí funcionan",
    "Sigatoka en plátano: control cultural + químico correcto",
    "Broca del café: trampas y control integrado",
    "Picudo del algodón: monitoreo y control regional",
    "Mosca de la fruta en cítricos: trampas, atrayentes y ventanas",
    "Pudrición apical en jitomate: calcio vs riego (qué es real)",
    "\"Quemado\" por herbicidas: síntomas y recuperación",
    "Manejo de malezas resistentes: glifosato ya no basta",
    "Preemergentes vs postemergentes: elección según cultivo",
    "Control de zacate Johnson: estrategias por temporada",
    "Control de conyza (rama negra): errores comunes",
    "Control de cuscuta: prevención en alfalfa",
    "MIP en invernadero: rutina semanal que evita desastres",
    "Bioplaguicidas: compatibilidades y mezclas peligrosas",
    "Jabón potásico vs aceite neem: cuándo sí y cuándo no",
    "Trichoderma: dónde funciona y dónde es puro marketing",
    "Bacillus subtilis vs cobre: prevención de enfermedades foliares",
    "Extractos vegetales: eficacia real y limitaciones",
    "Barreras vivas contra plagas: cómo diseñarlas",
    "Manejo de plagas nocturnas: monitoreo con lámparas y trampas",
    "Reingreso al lote tras aplicación: seguridad y práctica",
    "Cómo leer una etiqueta de agroquímicos sin regarla",
    # Suelo, nutrición y diagnósticos (35)
    "Prueba de pH con tiras vs medidor: precisión y costo",
    "Muestreo de suelo correcto: profundidad, zigzag y errores comunes",
    "Textura del suelo con \"prueba del frasco\": interpretación práctica",
    "Materia orgánica: cómo subirla sin arruinarte",
    "CEC (capacidad de intercambio catiónico): qué te dice de verdad",
    "Salinidad (EC): síntomas en plantas y correcciones viables",
    "Sodicidad: por qué tu suelo \"se hace plástico\" y cómo arreglarlo",
    "Cal agrícola vs yeso: cuándo usar cada uno",
    "Azufre elemental vs ácido: bajar pH sin matar el suelo",
    "Nitrógeno ureico vs amoniacal: comportamiento en suelo",
    "Fósforo bloqueado: causas y cómo liberarlo",
    "Potasio: deficiencia vs \"consumo oculto\"",
    "Calcio vs magnesio: equilibrio y síntomas parecidos",
    "Micronutrientes: boro, zinc, hierro (síntomas clave)",
    "Quelatos vs sales: cuándo convienen",
    "Fertilización de arranque vs mantenimiento: estrategia por cultivo",
    "Fertirriego vs aplicación al voleo: eficiencia real",
    "Enmiendas orgánicas: compost maduro vs inmaduro",
    "Gallinaza vs estiércol bovino: nutrientes y riesgos sanitarios",
    "Lixiviación: cómo pierdes fertilizante sin darte cuenta",
    "Nitrificación: por qué el N \"desaparece\"",
    "Curva de absorción de nutrientes por etapa fenológica",
    "Interpretación básica de un análisis de suelo (sin química pesada)",
    "Interpretación básica de análisis foliar",
    "Cómo detectar toxicidad por sales antes del colapso",
    "Biochar: cuándo sí mejora el suelo y cuándo es humo",
    "Mulch orgánico vs plástico: impacto en suelo y malezas",
    "Compactación por maquinaria: cómo medirla y reducirla",
    "Labranza mínima vs convencional: efectos en rendimiento",
    "Coberturas verdes en temporal: especies y fechas recomendadas",
    "Manejo de suelos calizos: hierro, zinc y bloqueos",
    "Suelos arenosos: cómo retener agua y fertilizante",
    "Suelos arcillosos: cómo mejorar infiltración y aireación",
    "Cómo calcular dosis de fertilizante según objetivo de rendimiento",
    "Errores comunes al mezclar fertilizantes en tanque",
    # Riego, hidráulica y agua (35)
    "Riego por goteo superficial vs subterráneo: pros y contras",
    "Cintilla vs manguera con gotero integrado: cuál conviene",
    "Presión nominal vs presión real en campo: cómo medir",
    "Pérdida de carga: por qué tu final de línea riega menos",
    "Filtración: malla vs disco vs arena (cuándo usar cada una)",
    "Lavado de líneas: rutina para evitar taponamientos",
    "Cloración en riego: dosis segura y señales de exceso",
    "Ácidos para limpiar riego: riesgos y alternativas",
    "pH del agua de riego: cómo afecta fertilizantes y goteros",
    "Bicarbonatos altos: síntomas y manejo",
    "Riego en suelos pesados: cómo evitar asfixia radicular",
    "Riego en suelos ligeros: pulsos cortos vs riegos largos",
    "Riego nocturno vs diurno: evaporación y enfermedad",
    "Riego por aspersión: cuándo genera más enfermedades",
    "Microaspersión en frutales: uniformidad y manejo",
    "Uniformidad de riego: cómo evaluarla sin laboratorio",
    "Cálculo de caudal total del sistema: método rápido",
    "Cómo seleccionar bomba según caudal y altura dinámica",
    "Energía solar para bombeo: dimensionamiento básico",
    "Variadores de frecuencia en bombas: cuándo valen la pena",
    "Programación de riego por evapotranspiración (ET) simplificada",
    "Sensores de humedad: calibración por tipo de suelo",
    "Tensiometros vs capacitivos: cuál conviene en hortalizas",
    "Riego deficitario en vid: cuándo mejora calidad",
    "Riego deficitario en mango: riesgos en floración",
    "Manejo de riego en papa: etapa crítica y humedad objetivo",
    "Riego en alfalfa: frecuencia por temporada",
    "Diseño de camas elevadas con cinta: distancias óptimas",
    "Geomembrana en bordos: instalación y errores típicos",
    "Captación de lluvia: cálculo de volumen y tamaño de bordo",
    "Canales y zanjas de infiltración: control de escorrentía",
    "Drenaje parcelario: cuándo es indispensable en lluvias",
    "Reuso de agua tratada: riesgos y buenas prácticas",
    "Calidad de agua para ganado: sales y efectos en consumo",
    "Riego y heladas: estrategias de protección (qué sí funciona)",
    # Cultivos específicos por región/temporada (40)
    "Maíz de temporal en Durango/Zacatecas: manejo por lluvia errática",
    "Frijol en altiplano: ventana de siembra y control de malezas",
    "Chile seco (guajillo/ancho): secado, manejo y pérdidas típicas",
    "Chile jalapeño: manejo de floración y caída por calor",
    "Jitomate campo abierto: tutorado vs rastrero (costos y rendimiento)",
    "Tomatillo: control de virosis y manejo de vectores",
    "Cebolla: manejo de bulbo y prevención de \"cuello grueso\"",
    "Ajo: vernalización y selección de semilla",
    "Papa: aporque, humedad, y control de tizones",
    "Zanahoria: suelos ideales y deformaciones por compactación",
    "Lechuga: tip burn y manejo de calcio/temperatura",
    "Pepino: amarre y manejo de polinización",
    "Calabaza: polinización y cuajado (abejas vs manual)",
    "Sandía: manejo de cuajado y control de oídio",
    "Melón: calidad, grados brix y riego en maduración",
    "Fresa: establecimiento, acolchado y control de pudriciones",
    "Arándano: acidificación de suelo y agua (mitos vs realidad)",
    "Vid: poda, brotación y manejo de canopia",
    "Mango: floración, alternancia y nutrición",
    "Aguacate: raíz, Phytophthora y drenaje",
    "Limón: manejo de brotes y control de psílido",
    "Naranja: manejo de fruta chica vs raleo",
    "Plátano: fertilización y control de sigatoka",
    "Café: sombra vs sol y productividad real",
    "Cacao: manejo de sombra y moniliasis",
    "Sorgo: tolerancia a sequía y fertilización",
    "Trigo: densidad, macollaje y manejo de riego",
    "Avena forrajera: corte óptimo para calidad",
    "Alfalfa: manejo de corona y persistencia",
    "Pastos mejorados: establecimiento en temporal",
    "Nopal: densidad, plagas y usos comerciales",
    "Maguey: plantación, manejo y proyección a mezcal",
    "Amaranto: manejo básico y mercado nicho",
    "Cártamo: manejo en zonas secas y comercialización",
    "Girasol: densidad, plagas y mercado",
    "Cebada: manejo para malta vs forraje",
    "Hortalizas de invierno en Bajío: calendario y riesgos",
    "Hortalizas en trópico húmedo: manejo de exceso de agua",
    "Siembra tardía: riesgos y cómo reducir pérdidas",
    "Cultivos de ciclo corto para \"caja rápida\" en 60–90 días",
    # Forestal, silvopastoril y recursos naturales (25)
    "Encino vs pino: diferencias de establecimiento y crecimiento",
    "Pinus patula: plagas y enfermedades comunes",
    "Pinus greggii: ventajas en reforestación productiva",
    "Pinus arizonica: sanidad y manejo en norte de México",
    "Plantación de eucalipto: manejo hídrico y controversias",
    "Producción de carbón vegetal: costos, permisos y mercado",
    "Resina de pino: técnicas de extracción y rentabilidad",
    "Manejo de leña: corte sostenible vs depredación",
    "Control de incendios: brechas corta fuego y mantenimiento",
    "Restauración de suelos erosionados con barreras vivas",
    "Reforestación con nativas: tasa de supervivencia realista",
    "Vivero forestal: sustratos, riego y sanidad",
    "Micorrizas en reforestación: cuándo ayudan de verdad",
    "Sistemas silvopastoriles con mezquite: sombra + forraje",
    "Cercos vivos: especies útiles por región",
    "Captura de carbono en sistemas agroforestales: humo vs realidad",
    "Manejo de agostadero: carga animal y recuperación",
    "Pastoreo rotacional: diseño de potreros y agua",
    "Bancos de proteína (leucaena): beneficios y riesgos",
    "Manejo de maleza en reforestación: químico vs manual",
    "Aprovechamiento de piñón: manejo y mercado",
    "Plantación de nogal pecanero: agua, suelo y retorno de inversión",
    "Manejo de plagas descortezadoras: prevención y monitoreo",
    "Enfermedades en encinos (seca): signos y respuesta",
    "Permisos forestales: lo básico para no meterte en broncas",
    # Ganadería (razas, sistemas, números) (25)
    "Beefmaster vs Brahman vs Angus: cuál conviene en calor",
    "Charolais vs Limousin: engorda y rendimiento en canal",
    "Ganado doble propósito: cruces comunes y resultados",
    "Producción de leche: Holstein vs Jersey vs Pardo Suizo",
    "Sombra y agua en ganado: impacto en ganancia diaria",
    "Destete temprano vs tradicional: costos y beneficios",
    "Suplementación en sequía: qué dar y cuánto",
    "Sales minerales: formulación básica por región",
    "Parásitos internos: desparasitación estratégica",
    "Garrapata: control integrado y rotación de productos",
    "Mastitis: prevención en ordeña pequeña",
    "Calidad de leche: bacterias, enfriamiento y pérdidas",
    "Pastoreo rotacional: cálculos de carga animal",
    "Producción de becerros: calendario reproductivo anual",
    "Engorda en corral: dieta base y errores caros",
    "Ensilaje de maíz vs sorgo: comparación de costos",
    "Henificación: cuándo conviene vs ensilar",
    "Gallinas ponedoras: números reales por 100 aves",
    "Pollo de engorda: ciclo, mortalidad y margen",
    "Porcino traspatio vs tecnificado: diferencia de rentabilidad",
    "Razas de cerdo (Yorkshire, Landrace, Duroc): pros y contras",
    "Borrego Pelibuey vs Katahdin: adaptación y mercado",
    "Cabra Saanen vs Alpina: producción de leche y manejo",
    "Queso artesanal: rendimiento por litro (expectativas reales)",
    "Bioseguridad básica: protocolos simples que sí reducen pérdidas",
    # Agroindustria, valor agregado y emprendimiento rural (40)
    "Chile seco: empaque premium vs granel (márgenes)",
    "Salsa artesanal: costos, vida de anaquel y etiqueta",
    "Mermeladas de fruta local: mercado y estacionalidad",
    "Deshidratado solar vs eléctrico: calidad y costo",
    "Harina de maíz criollo: storytelling + nicho premium",
    "Tortillería rural: números, permisos y demanda",
    "Queso fresco vs madurado: inversión y retorno",
    "Yogurt artesanal: proceso, inocuidad y margen",
    "Carne seca/machaca: requisitos y mercado regional",
    "Miel: diferenciación por floración y precio",
    "Polinización como servicio: cómo cobrar y operar",
    "Venta directa: canales cortos y logística real",
    "Cajas \"del huerto\" (CSA): modelo y retención de clientes",
    "Centro de acopio pequeño: qué equipo sí necesitas",
    "Empaque y clasificación: cómo sube el precio por calidad",
    "Marca local: cuándo vale la pena registrar",
    "Etiquetado NOM: lo básico para no fallar",
    "Trazabilidad con QR: qué poner y cómo usarlo",
    "Certificación orgánica: costos y alternativas (Sistemas Participativos)",
    "Buenas prácticas de manejo (BPM): checklist para agroindustria",
    "Inocuidad: por qué la gente enferma y cómo evitarlo",
    "Refrigeración: cuándo se paga sola en perecederos",
    "Transporte de perecederos: pérdidas por mala logística",
    "Subproductos: cáscaras, bagazo y compost comercial",
    "Forraje ensilado como negocio: vender \"bolsas\" por temporada",
    "Venta de plántula: vivero de hortalizas como emprendimiento",
    "Servicio de aplicación de riego/fertirriego: cómo cobrar",
    "Servicio de análisis de suelo \"con interpretación\": paquete rentable",
    "Paquetes por cultivo: \"kit de establecimiento\" y upsell",
    "Cómo fijar precios sin competir por lo más barato",
    "Coyotes vs contrato: negociación y riesgo",
    "Agricultura por contrato: cuándo conviene",
    "Seguro agrícola: qué cubre y qué no",
    "Financiamiento rural: errores que hunden proyectos",
    "Cooperativa: ventajas reales y trampas comunes",
    "Almacenamiento de grano: control de plagas y humedad",
    "Secado de grano: humedad objetivo y pérdidas",
    "Silos vs bodegas: comparación de inversión",
    "Agroturismo: granja educativa como negocio",
    "Producción de semilla certificada: requisitos y mercado",
    # Tecnología moderna aplicada (AgTech) (40)
    "Sensores de humedad: dónde colocarlos y cuántos necesitas",
    "Estación meteo: variables clave para decisiones reales",
    "Pronóstico hiperlocal vs apps genéricas: cuál confiar",
    "IA para diagnóstico por foto: cómo evitar falsos positivos",
    "Drones: mapas NDVI para decidir riego/fertilizante",
    "Satélite gratuito: cómo interpretarlo sin \"ser ingeniero\"",
    "Prescripción variable: fertilización por zonas en parcela",
    "Monitoreo de bombas: consumo eléctrico y fallas",
    "Válvulas inteligentes: automatización por sector",
    "Energía solar para bombeo: cálculo rápido de paneles",
    "Baterías vs sin baterías: diseño de sistema solar de riego",
    "Filtrado inteligente: sensores de presión diferencial",
    "Medición de caudal: cómo detectar fugas con datos",
    "Control de inventario rural con WhatsApp + Sheets",
    "ERP simple para agrotienda: qué módulos importan",
    "Trazabilidad digital: del lote al cliente con QR",
    "Blockchain en alimentos: casos donde sí sirve",
    "Control de frío con sensores: alertas y pérdidas evitadas",
    "Diagnóstico de mastitis con pruebas rápidas: qué comprar",
    "Collares para ganado: celo, rumia y salud (qué sí predicen)",
    "Básculas inteligentes: control de ganancia diaria",
    "Cámaras en corrales: detección de cojeras por IA",
    "Riego basado en ET: automatización con datos meteorológicos",
    "Modelos de predicción de precios: cómo usarlos sin apostar",
    "Marketplace rural: vender directo sin intermediario",
    "Pagos digitales en campo: reducir morosidad",
    "Microseguros paramétricos: cómo funcionan (lluvia/temperatura)",
    "Bioinsumos comerciales: cómo elegir proveedores confiables",
    "Fermentación de bioinsumos en sitio: control de calidad básico",
    "Control biológico en invernadero: esquema de liberaciones",
    "Trampas inteligentes: conteo automático de plagas",
    "Robots agrícolas: qué existe y qué es humo",
    "Hidroponía básica: cuándo sí es rentable en México",
    "Sustratos: coco vs perlita vs tezontle (comparativa práctica)",
    "Invernadero: túnel, macrotúnel, multitúnel (retorno de inversión)",
    "Riego en invernadero: pulsos, drenaje y salinidad",
    "Iluminación suplementaria: cuándo vale la pena",
    "Postcosecha: atmósfera modificada en pequeña escala",
    "Calidad con sensores: Brix, firmeza y temperatura",
    "Gestión agrícola (FMIS): qué registrar para que sirva",
)

POST_TYPES_DEFINITIONS = """
- Infografías: Explicar rápido (riego, acolchado). Versión resumida para Reels.
- Fechas importantes: Anclar promos o recordatorios (Día del Agricultor, heladas).
- Memes/tips rápidos: Humor educativo (errores comunes).
- Promoción puntual: Liquidar overstock o empujar alta rotación.
- Kits: Combo de productos (solución completa, ej. kit riego).
- Caso de éxito / UGC: Prueba social (instalaciones, resultados).
- Antes / Después: Demostrar impacto visual.
- Checklist operativo: Guía de acciones por temporada (previo a helada, arranque riego).
- Tutorial corto / "Cómo se hace": Educar en 30–45s.
- "Lo que llegó hoy": Novedades y entradas de inventario.
- FAQ / Mitos: Remover objeciones (costos, duración).
- Seguridad y prevención: Cuidado de personal/equipo.
- ROI / números rápidos: Justificar inversión con datos.
- Convocatoria a UGC: Pedir fotos/video de clientes.
- Recordatorio de servicio: Mantenimiento (lavado filtros, revisión bomba).
- Cómo pedir / logística: Simplificar proceso de compra.
"""

CHANNEL_FORMATS = """
FORMATOS POR CANAL (CRÍTICO - ADAPTA EL CONTENIDO):

📱 WA STATUS (wa-status):
  - Aspecto: Vertical 9:16 (1080×1920)
  - Música: ✅ OBLIGATORIO (corridos mexicanos, regional)
  - ⚠️ CAPTION: MÍNIMO O VACÍO (máximo 50 caracteres). El contenido visual/imagen debe comunicar TODO.
  - ⚠️ PRIORIDAD: La imagen/video es lo más importante, NO el texto.
  - Duración: 15-30 segundos si es video
  - Efímero: Desaparece en 24h
  - Ejemplo: Alerta urgente, "Llegó X producto", UGC rápido

📨 WA BROADCAST (wa-broadcast):
  - Aspecto: Cuadrado 1:1 (1080×1080)
  - Música: ❌ No aplica
  - Caption: Corto pero informativo (~200 chars)
  - Ejemplo: Oferta VIP, aviso de stock

📲 WA MENSAJE (wa-message):
  - Texto conversacional, personal
  - Se puede incluir imagen cuadrada

📸 FB + IG POST (fb-post, ig-post):
  - Aspecto: Cuadrado 1:1 (1080×1080)
  - Carrusel: ✅ Hasta 10 slides
  - Música: ❌ No
  - Caption: LARGO permitido (hasta 2000 chars)
  - Se replica automáticamente FB → IG
  - Ejemplo: Infografía, carrusel educativo, caso de éxito

🎬 FB + IG REEL (fb-reel, ig-reel):
  - Aspecto: Vertical 9:16 (1080×1920)
  - Video: ✅ 15-90 segundos
  - Música: ✅ OBLIGATORIO (trending o mexicana)
  - ⚠️ CAPTION: MUY CORTO (máximo 100 caracteres). El texto principal va EN EL VIDEO con subtítulos.
  - ⚠️ PRIORIDAD: El video y su contenido visual es lo más importante, NO el caption.
  - Se replica automáticamente FB → IG
  - Hook en primeros 3 segundos
  - Ejemplo: Instalación rápida, antes/después, tip del día

📱 FB + IG STORIES (fb-story, ig-story):
  - Aspecto: Vertical 9:16 (1080×1920)
  - ⚠️ CAPTION: MÍNIMO O VACÍO (máximo 50 caracteres). El contenido visual/imagen debe comunicar TODO.
  - ⚠️ PRIORIDAD: La imagen/video es lo más importante, NO el texto.
  - Efímero: Desaparece en 24h
  - Ejemplo: Alerta urgente, promoción flash, behind-the-scenes

🎵 TIKTOK (tiktok) - ⚠️ FORMATO ESPECIAL:
  - Aspecto: Vertical 9:16 (1080×1920)
  - ⚠️ CARRUSEL DE 2-3 IMÁGENES (NO video)
  - El usuario DESLIZA para ver siguiente imagen
  - Música: ✅ OBLIGATORIO (corridos mexicanos, regional popular)
  - ⚠️ CAPTION: MUY CORTO (máximo 150 caracteres). SOLO hashtags o texto mínimo.
  - ⚠️ PRIORIDAD: TODO EL TEXTO PRINCIPAL VA EN LAS IMÁGENES DEL CARRUSEL, NO en caption.
  - ⚠️ CRÍTICO: El caption es secundario, las imágenes con texto grande son lo importante.
  - Estructura típica 3 slides:
    1. HOOK/Problema (primera imagen engancha con texto grande visible)
    2. CONTENIDO/Solución (texto en imagen)
    3. CTA/Contacto (texto en imagen)
  - Ejemplo: "3 errores al instalar" / "Antes→Después→Precio"
"""

# --- Models ---

class SocialGenRequest(BaseModel):
    date: str # YYYY-MM-DD
    # Optional overrides allow testing specific scenarios, but defaults are autonomous
    category: Optional[str] = None
    suggested_topic: Optional[str] = None # User-suggested topic for the post

class SocialGenResponse(BaseModel):
    caption: str
    image_prompt: Optional[str] = None  # Optional - carousel posts use carousel_slides instead
    posting_time: Optional[str] = None
    notes: Optional[str] = None
    format: Optional[str] = None
    cta: Optional[str] = None
    selected_product_id: Optional[str] = None
    selected_category: Optional[str] = None # New field for AI decision
    selected_product_details: Optional[Dict[str, Any]] = None # Full product object for frontend
    post_type: Optional[str] = None # Post type from strategy phase (e.g., "Infografías", "Memes/tips rápidos", "Kits")
    content_tone: Optional[str] = None # Content tone: Motivational, Technical, Humor, Educational, Inspirational, etc.
    # Channel-specific fields
    channel: Optional[str] = None # wa-status, wa-broadcast, fb-post, fb-reel, tiktok, etc.
    carousel_slides: Optional[List[str]] = None # For TikTok carousels: list of 2-3 image prompts
    needs_music: Optional[bool] = None # Whether this content needs background music
    aspect_ratio: Optional[str] = None # 1:1, 9:16, 4:5
    # Topic-based deduplication fields (CRITICAL)
    topic: Optional[str] = None # Topic in format "Error → Daño concreto → Solución" (canonical unit of deduplication)
    problem_identified: Optional[str] = None # Problem description from strategy phase
    saved_post_id: Optional[int] = None # ID of the automatically saved post in database
    # Viral angle fields (from pre-strategy phase)
    viral_angle: Optional[Dict[str, str]] = None # Viral hook data: hook_type, primary_trigger, hook_sentence, visual_concept, curiosity_gap
    suggested_hashtags: Optional[List[str]] = None  # §5: 5-8 hashtags from content phase
    # Multiple posts support (e.g., Monday generates 2 posts)
    second_post: Optional['SocialGenResponse'] = None  # Optional second post (for days that generate multiple posts)

# Update forward references for Pydantic v2
SocialGenResponse.model_rebuild()

# --- Logic ---

def identify_agricultural_problems(
    month: Optional[int],
    phase: Optional[str],
    nearby_dates: list,
    durango_context: str
) -> dict:
    """
    Returns empty structure - AI will identify problems dynamically in the strategy phase.
    This function is kept for backward compatibility but no longer returns hardcoded problems.
    
    Args:
        month: Month number (1-12) or None if seasonal context not available
        phase: Agricultural phase string or None if seasonal context not available
        nearby_dates: List of nearby dates (only populated on Fridays)
        durango_context: Regional context string
    """
    # Check nearby dates for urgency hints (e.g., heladas coming soon)
    # Only check if we have seasonal context (month and phase not None)
    urgency_hints = []
    if month is not None and phase is not None:
        for date in nearby_dates:
            if isinstance(date, dict) and date.get("type") == "seasonal":
                date_name = str(date.get("name", "")).lower()
                if "helada" in date_name or "frío" in date_name:
                    urgency_hints.append({
                        "type": "helada_risk",
                        "days_until": date.get("daysUntil", 0),
                        "name": date.get("name", "")
                    })
    
    return {
        "problems": [],
        "most_urgent": [],
        "high_priority": [],
        "month": month,
        "phase": phase,
        "urgency_hints": urgency_hints  # Pass hints to AI for context
    }

def get_saturday_sector(dt: datetime) -> str:
    """
    Rotate sector for Saturday posts: forestry, plant, animal.
    Uses week number to determine rotation.
    """
    week_num = dt.isocalendar()[1]  # ISO week number
    sectors = ['forestry', 'plant', 'animal']
    return sectors[week_num % 3]

def get_default_tone_for_weekday(day_name: str) -> str:
    """
    Returns default content tone based on weekday theme.
    Used as fallback if LLM doesn't provide tone.
    """
    tone_map = {
        'Monday': 'Motivational',
        'Tuesday': 'Promotional',
        'Wednesday': 'Educational',
        'Thursday': 'Problem-Solving',
        'Friday': 'Seasonal',
        'Saturday': 'Educational',
        'Sunday': 'Informative'
    }
    return tone_map.get(day_name, 'Educational')

def get_weekday_theme(dt: datetime) -> Dict[str, Any]:
    """
    Returns the weekly theme and recommended post types for a given date.
    
    Returns:
        {
            'day_name': 'Monday',
            'theme': '✊ Motivational / Inspirational',
            'content_type': 'Inspiring quote or message...',
            'recommended_post_types': ['Memes/tips rápidos', 'Infografías', ...],
            'sector_rotation': None or 'forestry'|'plant'|'animal' (for Saturday)
        }
    """
    weekday = dt.weekday()  # 0=Monday, 6=Sunday
    
    themes = {
        0: {  # Monday
            'day_name': 'Monday',
            'theme': '✊ Motivational / Inspirational',
            'content_type': 'Inspiring quote or message for agriculture/forestry producers',
            'recommended_post_types': [
                'Motivational Phrase or Quote of the Week',
                'Memes/tips rápidos',
                'Image / Photo of the Week'
            ],
            'sector_rotation': None
        },
        1: {  # Tuesday
            'day_name': 'Tuesday',
            'theme': '💸 Promotion / Deals',
            'content_type': 'Highlight a product with a special price, bundle, or seasonal offer',
            'recommended_post_types': [
                'Promoción puntual',
                'Kits',
                '"Lo que llegó hoy"',
                'Cómo pedir / logística',
                'Recordatorio de servicio'
            ],
            'sector_rotation': None
        },
        2: {  # Wednesday
            'day_name': 'Wednesday',
            'theme': '📚 Educational / Tips',
            'content_type': 'Tips, guides, how-tos, or educational content for farmers',
            'recommended_post_types': [
                'Infografías de producto o tema',
                'Tutorial corto',
                'Pro Tip',
                'Interesting Fact',
                'Article',
                'Sabías que...'
            ],
            'sector_rotation': None
        },
        3: {  # Thursday
            'day_name': 'Thursday',
            'theme': '🛠️ Problem & Solution',
            'content_type': 'Infographic showing how one of your products solves a real problem',
            'recommended_post_types': [
                'Infografías',
                'Caso de éxito',
                'Antes / Después'
            ],
            'sector_rotation': None
        },
        4: {  # Friday
            'day_name': 'Friday',
            'theme': '📅 Seasonal Focus',
            'content_type': 'Advice or alerts based on regional crop/livestock/forestry seasons',
            'recommended_post_types': [
                'Infografías',
                'Tutorial corto',
                'Checklist operativo',
                'Recordatorio de servicio',
                'Seasonal weather tips: what to expect & how to act'
            ],
            'sector_rotation': None
        },
        5: {  # Saturday
            'day_name': 'Saturday',
            'theme': '👩‍🌾 Producer Segment Focus',
            'content_type': 'Rotate content for: forestry 🌲, plant 🌾, animal 🐄 producers',
            'recommended_post_types': [
                'Infografías',
                'FAQ / Mitos',
                'Pro Tip',
                'Interesting Fact',
                'Tutorial corto',
                'Recordatorio de servicio'
            ],
            'sector_rotation': get_saturday_sector(dt)  # Rotate weekly
        },
        6: {  # Sunday
            'day_name': 'Sunday',
            'theme': '📊 Innovation / Industry Reports',
            'content_type': 'Industry news, agri-innovation, or trending novelty in agriculture',
            'recommended_post_types': [
                'Industry novelty',
                'Trivia agrotech-style post',
                'Statistics or report highlights relevant to audience'
            ],
            'sector_rotation': None
        }
    }
    
    return themes[weekday]

def get_special_date_override(dt: datetime) -> Optional[Dict[str, Any]]:
    """
    Check if date matches a special date (holiday or agricultural day).
    Returns override theme if found, None otherwise.
    """
    month = dt.month
    day = dt.day
    
    # Mexican National Holidays & Social Dates
    special_dates = {
        (1, 1): {'name': 'Año Nuevo', 'type': 'holiday'},
        (2, 5): {'name': 'Día de la Constitución', 'type': 'holiday'},
        (3, 21): {'name': 'Natalicio de Benito Juárez', 'type': 'holiday'},
        (5, 10): {'name': 'Día de las Madres', 'type': 'social'},
        (5, 15): {'name': 'Día del Maestro', 'type': 'social'},
        (9, 16): {'name': 'Día de la Independencia', 'type': 'holiday'},
        (11, 2): {'name': 'Día de Muertos', 'type': 'holiday'},
        (12, 25): {'name': 'Navidad', 'type': 'holiday'},
        # Environment & Agriculture-Related Days
        (3, 22): {'name': 'Día Mundial del Agua', 'type': 'agricultural'},
        (4, 22): {'name': 'Día de la Tierra', 'type': 'agricultural'},
        (4, 15): {'name': 'Día del Agrónomo (Mexico)', 'type': 'agricultural'},
        (6, 5): {'name': 'Día Mundial del Medio Ambiente', 'type': 'agricultural'},
        (10, 16): {'name': 'Día Mundial de la Alimentación', 'type': 'agricultural'},
    }
    
    # Check for exact date match
    if (month, day) in special_dates:
        special = special_dates[(month, day)]
        return {
            'is_special_date': True,
            'special_date_name': special['name'],
            'special_date_type': special['type'],
            'override_weekday_theme': True,
            'recommended_post_type': 'Fechas importantes'
        }
    
    # Check for Día del Padre (3rd Sunday of June)
    if month == 6 and dt.weekday() == 6:  # Sunday
        week_of_month = (day - 1) // 7 + 1
        if week_of_month == 3:
            return {
                'is_special_date': True,
                'special_date_name': 'Día del Padre',
                'special_date_type': 'social',
                'override_weekday_theme': True,
                'recommended_post_type': 'Fechas importantes'
            }
    
    return None

class SocialPostSaveRequest(BaseModel):
    date_for: str
    caption: str
    image_prompt: Optional[str] = None
    post_type: Optional[str] = None
    content_tone: Optional[str] = None # Content tone: Motivational, Technical, Humor, Educational, Inspirational, etc.
    status: str = "planned"
    selected_product_id: Optional[str] = None
    formatted_content: Optional[Dict[str, Any]] = None
    # Channel-specific fields
    channel: Optional[str] = None  # wa-status, fb-post, tiktok, etc.
    carousel_slides: Optional[List[str]] = None  # Array of slide prompts for carousels (TikTok, FB/IG)
    needs_music: Optional[bool] = False
    user_feedback: Optional[str] = None  # 'like', 'dislike', or None
    # Topic-based deduplication fields (CRITICAL)
    topic: str  # Topic in format "Error → Daño concreto → Solución" (REQUIRED - comes from LLM or must be provided)
    problem_identified: Optional[str] = None  # Problem description from strategy phase

@router.get("/posts")
async def get_social_posts(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token) # Optional auth
):
    """
    Get all social posts (shared across all users).
    Can filter by date range and status.
    """
    try:
        query = db.query(SocialPost)
        
        # Filter by date range if provided (FIXED: Use DATE comparison, not string)
        if start_date:
            try:
                start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
                query = query.filter(SocialPost.date_for >= start_date_obj)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid start_date format: {start_date}. Expected YYYY-MM-DD")
        if end_date:
            try:
                end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
                query = query.filter(SocialPost.date_for <= end_date_obj)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid end_date format: {end_date}. Expected YYYY-MM-DD")
        
        # Filter by status if provided
        if status:
            query = query.filter(SocialPost.status == status)
        
        # Order by date_for (target date) and creation time
        posts = query.order_by(SocialPost.date_for.desc(), SocialPost.created_at.desc()).all()
        
        return {
            "status": "success",
            "count": len(posts),
            "posts": [
                {
                    "id": p.id,
                    "date_for": p.date_for,
                    "caption": p.caption,
                    "image_prompt": p.image_prompt,
                    "post_type": p.post_type,
                    "content_tone": p.content_tone,
                    "status": p.status,
                    "selected_product_id": p.selected_product_id,
                    "formatted_content": p.formatted_content,
                    "channel": p.channel,
                    "carousel_slides": p.carousel_slides,
                    "needs_music": p.needs_music,
                    "user_feedback": p.user_feedback,
                    "topic": p.topic,
                    "problem_identified": p.problem_identified,
                    "created_at": p.created_at.isoformat() if p.created_at else None
                }
                for p in posts
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/posts/by-date/{date}")
async def get_social_posts_by_date(
    date: str,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token) # Optional auth
):
    """
    Get all posts for a specific date (YYYY-MM-DD).
    """
    try:
        # FIXED: Use DATE comparison, not string
        try:
            date_obj = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid date format: {date}. Expected YYYY-MM-DD")
        posts = db.query(SocialPost).filter(
            SocialPost.date_for == date_obj
        ).order_by(SocialPost.created_at.desc()).all()
        
        return {
            "status": "success",
            "date": date,
            "count": len(posts),
            "posts": [
                {
                    "id": p.id,
                    "date_for": p.date_for,
                    "caption": p.caption,
                    "image_prompt": p.image_prompt,
                    "post_type": p.post_type,
                    "content_tone": p.content_tone,
                    "status": p.status,
                    "selected_product_id": p.selected_product_id,
                    "formatted_content": p.formatted_content,
                    "channel": p.channel,
                    "carousel_slides": p.carousel_slides,
                    "needs_music": p.needs_music,
                    "user_feedback": p.user_feedback,
                    "topic": p.topic,
                    "problem_identified": p.problem_identified,
                    "created_at": p.created_at.isoformat() if p.created_at else None
                }
                for p in posts
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/save")
async def save_social_post(
    payload: SocialPostSaveRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token) # Optional auth
):
    """Update an existing post (status, user_feedback, etc.).
    
    NOTE: New posts are automatically saved by /generate endpoint.
    This endpoint is primarily for updating existing posts.
    """
    try:
        # Validate user_feedback if provided
        if payload.user_feedback and payload.user_feedback not in ['like', 'dislike']:
            raise HTTPException(status_code=400, detail="user_feedback must be 'like', 'dislike', or None")
        
        # Extract external_id from formatted_content.id if present
        external_id = None
        if payload.formatted_content and payload.formatted_content.get('id'):
            external_id = str(payload.formatted_content.get('id'))
        
        # Check if post already exists by external_id (indexed lookup - O(1) instead of O(n))
        existing_post = None
        if external_id:
            # First, try to extract DB ID if format is "db-{id}"
            if external_id.startswith('db-'):
                try:
                    db_id_match = int(external_id.replace('db-', ''))
                    existing_post = db.query(SocialPost).filter(SocialPost.id == db_id_match).first()
                except ValueError:
                    pass
            
            # If not found by DB ID, use indexed external_id lookup
            if not existing_post:
                existing_post = db.query(SocialPost).filter(SocialPost.external_id == external_id).first()
            
            # Fallback: If external_id column doesn't exist yet (during migration), use JSONB query
            if not existing_post:
                # PostgreSQL JSONB expression query (indexed if migration ran)
                from sqlalchemy import text
                existing_post = db.query(SocialPost).filter(
                    text("formatted_content->>'id' = :target_id")
                ).params(target_id=external_id).first()
        
        # Parse date_for to DATE type (handle both string and date)
        from datetime import date as date_type
        if isinstance(payload.date_for, str):
            try:
                date_for_obj = datetime.strptime(payload.date_for, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid date format: {payload.date_for}. Expected YYYY-MM-DD")
        else:
            date_for_obj = payload.date_for
        
        # Topic is REQUIRED - it should come from the LLM or be provided explicitly
        # No extraction logic needed - the AI returns it, or it's provided by the frontend
        if not payload.topic:
            raise HTTPException(
                status_code=400,
                detail="topic is required. Posts generated via /generate are automatically saved. "
                       "If updating an existing post, provide the topic explicitly."
            )
        
        topic = payload.topic
        
        # Normalize and hash topic
        normalized_topic = social_topic.normalize_topic(topic)
        topic_hash = social_topic.compute_topic_hash(normalized_topic)
        
        if existing_post:
            # Update existing post
            existing_post.date_for = date_for_obj
            existing_post.caption = payload.caption
            existing_post.image_prompt = payload.image_prompt
            existing_post.post_type = payload.post_type
            existing_post.content_tone = payload.content_tone
            existing_post.status = payload.status
            existing_post.selected_product_id = payload.selected_product_id
            existing_post.formatted_content = payload.formatted_content
            existing_post.external_id = external_id  # Update external_id
            existing_post.channel = payload.channel
            existing_post.carousel_slides = payload.carousel_slides
            existing_post.needs_music = payload.needs_music
            existing_post.user_feedback = payload.user_feedback
            # Update topic fields
            existing_post.topic = normalized_topic
            existing_post.topic_hash = topic_hash
            existing_post.problem_identified = payload.problem_identified
            db.commit()
            db.refresh(existing_post)
            return {"status": "success", "id": existing_post.id, "updated": True}
        else:
            # Create new post
            new_post = SocialPost(
                date_for=date_for_obj,
                caption=payload.caption,
                image_prompt=payload.image_prompt,
                post_type=payload.post_type,
                content_tone=payload.content_tone,
                status=payload.status,
                selected_product_id=payload.selected_product_id,
                formatted_content=payload.formatted_content,
                external_id=external_id,  # Set external_id for efficient lookups
                channel=payload.channel,
                carousel_slides=payload.carousel_slides,
                needs_music=payload.needs_music,
                user_feedback=payload.user_feedback,
                # Topic fields (CRITICAL)
                topic=normalized_topic,
                topic_hash=topic_hash,
                problem_identified=payload.problem_identified
            )
            db.add(new_post)
            db.commit()
            db.refresh(new_post)
            return {"status": "success", "id": new_post.id, "updated": False}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

class FeedbackUpdateRequest(BaseModel):
    feedback: Optional[str] = None  # 'like', 'dislike', or None

@router.put("/posts/{post_id}/feedback")
async def update_post_feedback(
    post_id: int,
    payload: FeedbackUpdateRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token) # Optional auth
):
    """Update user feedback for an existing post."""
    try:
        # Validate feedback if provided
        if payload.feedback and payload.feedback not in ['like', 'dislike']:
            raise HTTPException(status_code=400, detail="feedback must be 'like', 'dislike', or None")
        
        post = db.query(SocialPost).filter(SocialPost.id == post_id).first()
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")
        
        post.user_feedback = payload.feedback
        db.commit()
        db.refresh(post)
        
        return {
            "status": "success",
            "id": post.id,
            "user_feedback": post.user_feedback
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# NEW MULTI-STEP PIPELINE (Feature Flag: USE_NEW_SOCIAL_PIPELINE)
# ============================================================================

def generate_with_new_pipeline(
    client: anthropic.Anthropic,
    db: Session,
    payload: 'SocialGenRequest',
    user_id: str,
    dt: datetime,
    target_date
) -> 'SocialGenResponse':
    """
    New multi-step pipeline: Topic Engine → Strategy Engine → Content Engine

    This replaces the old monolithic prompt with 3 focused LLM calls:
    1. Topic Engine: Identify problem and topic (~800 tokens)
    2. Strategy Engine: Decide post_type, tone, channel (~600 tokens)
    3. Content Engine: Generate caption and image_prompt (~1,500 tokens)

    Total: ~2,900 tokens (vs ~27,925 in old system) = 90% reduction
    """
    social_logging.safe_log_info(
        "[NEW PIPELINE] Starting multi-step generation",
        user_id=user_id,
        date=payload.date,
        use_new_pipeline=True
    )

    # Get weekday theme
    weekday_theme = social_config.WEEKDAY_THEMES.get(
        dt.strftime('%A'),
        social_config.WEEKDAY_THEMES['Monday']  # Fallback
    )

    social_logging.safe_log_info(
        "[NEW PIPELINE] Weekday theme determined",
        weekday=weekday_theme['day_name'],
        theme=weekday_theme['theme']
    )

    # ========================================================================
    # STEP 1: TOPIC ENGINE - Identify agricultural problems and topic
    # ========================================================================

    social_logging.safe_log_info("[NEW PIPELINE - STEP 1] Starting Topic Engine", user_id=user_id)

    # Get recent topics for variety
    recent_topics = social_helpers.get_recent_topics(db, lookback_days=14, limit=10)

    social_logging.safe_log_info(
        "[NEW PIPELINE - STEP 1] Recent topics loaded",
        num_recent_topics=len(recent_topics)
    )

    # Generate topic strategy (Durango seasonality context is embedded in Topic Engine for Friday posts)
    topic_strategy = social_topic_engine.generate_topic_strategy(
        client=client,
        date_str=payload.date,
        weekday_theme=weekday_theme,
        recent_topics=recent_topics,
        user_suggested_topic=payload.suggested_topic
    )

    social_logging.safe_log_info(
        "[NEW PIPELINE - STEP 1] Topic Engine complete",
        topic=topic_strategy.topic,
        angle=topic_strategy.angle,
        urgency=topic_strategy.urgency_level
    )

    # ========================================================================
    # STEP 2: STRATEGY ENGINE - Decide post_type, tone, channel
    # ========================================================================

    social_logging.safe_log_info("[NEW PIPELINE - STEP 2] Starting Strategy Engine", user_id=user_id)

    # Get recent channels for variety
    recent_channels = social_helpers.get_recent_channels(db, limit=5)

    social_logging.safe_log_info(
        "[NEW PIPELINE - STEP 2] Recent channels loaded",
        num_recent_channels=len(recent_channels)
    )

    # Generate content strategy
    content_strategy = social_strategy_engine.generate_content_strategy(
        client=client,
        topic_strategy=topic_strategy,
        weekday_theme=weekday_theme,
        recent_channels=recent_channels
    )

    social_logging.safe_log_info(
        "[NEW PIPELINE - STEP 2] Strategy Engine complete",
        post_type=content_strategy.post_type,
        tone=content_strategy.tone,
        channel=content_strategy.channel,
        search_needed=content_strategy.search_needed
    )

    # ========================================================================
    # STEP 3: PRODUCT SELECTION (if needed)
    # ========================================================================

    product_details = None
    selected_product_id = None
    selected_category = None

    if content_strategy.search_needed:
        social_logging.safe_log_info(
            "[NEW PIPELINE - STEP 3] Starting product selection",
            user_id=user_id,
            preferred_category=content_strategy.preferred_category,
            search_keywords=content_strategy.search_keywords
        )

        # Use existing product selection logic
        # Build search query from keywords or topic angle
        search_query = content_strategy.search_keywords or topic_strategy.angle or topic_strategy.topic

        selected_product_id, selected_category, product_details_dict = social_products.select_product_for_post(
            db=db,
            search_query=search_query,
            preferred_category=content_strategy.preferred_category or None
        )

        if selected_product_id and product_details_dict:
            # Use product_details_dict returned from select_product_for_post
            # Extract only the fields we need for the compact format
            product_details = {
                'name': product_details_dict.get('name', ''),
                'category': product_details_dict.get('category', ''),
                'features': product_details_dict.get('features', [])[:3]  # Max 3 features
            }

            social_logging.safe_log_info(
                "[NEW PIPELINE - STEP 3] Product selected",
                product_id=selected_product_id,
                product_name=product_details['name'],
                category=selected_category
            )
        else:
            social_logging.safe_log_info(
                "[NEW PIPELINE - STEP 3] No product found, continuing without product",
                user_id=user_id
            )
    else:
        social_logging.safe_log_info(
            "[NEW PIPELINE - STEP 3] Product selection skipped (not needed)",
            user_id=user_id
        )

    # ========================================================================
    # STEP 4: CONTENT ENGINE - Generate caption and image_prompt
    # ========================================================================

    social_logging.safe_log_info("[NEW PIPELINE - STEP 4] Starting Content Engine", user_id=user_id)

    # Generate content
    content_data = social_content_engine.generate_content(
        client=client,
        topic_strategy=topic_strategy,
        content_strategy=content_strategy,
        product_details=product_details,
        weekday_theme=weekday_theme
    )

    social_logging.safe_log_info(
        "[NEW PIPELINE - STEP 4] Content Engine complete",
        caption_length=len(content_data.get('caption', '')),
        has_image_prompt=bool(content_data.get('image_prompt')),
        channel=content_data.get('channel')
    )

    # ========================================================================
    # STEP 5: SAVE TO DATABASE
    # ========================================================================

    social_logging.safe_log_info("[NEW PIPELINE - STEP 5] Saving to database", user_id=user_id)

    # Build formatted_content for storage
    formatted_content = {
        "caption": content_data.get("caption", ""),
        "image_prompt": content_data.get("image_prompt", ""),
        "cta": content_data.get("cta", ""),
        "suggested_hashtags": content_data.get("suggested_hashtags", []),
        "posting_time": content_data.get("posting_time"),
        "notes": content_data.get("notes", ""),
        "channel": content_data.get("channel"),
        "needs_music": content_data.get("needs_music", False),
        "selected_category": selected_category,  # Store category in formatted_content
        "pipeline_version": "multi_step_v1"  # Mark as new pipeline
    }

    # Normalize and hash topic for deduplication
    normalized_topic = social_topic.normalize_topic(topic_strategy.topic)
    topic_hash = social_topic.compute_topic_hash(normalized_topic)

    # Create database record
    new_post = SocialPost(
        date_for=target_date,
        caption=content_data.get("caption", ""),
        image_prompt=content_data.get("image_prompt", ""),
        topic=normalized_topic,
        topic_hash=topic_hash,
        problem_identified=topic_strategy.problem_identified,
        post_type=content_strategy.post_type,
        content_tone=content_strategy.tone,
        channel=content_strategy.channel,
        selected_product_id=selected_product_id,
        formatted_content=formatted_content,
        created_at=datetime.now()
    )

    db.add(new_post)
    db.commit()
    db.refresh(new_post)

    saved_post_id = new_post.id
    formatted_content["id"] = str(saved_post_id)
    new_post.formatted_content = formatted_content
    db.commit()

    social_logging.safe_log_info(
        "[NEW PIPELINE - STEP 5] Post saved successfully",
        post_id=saved_post_id,
        user_id=user_id
    )

    # ========================================================================
    # STEP 5.5: GENERATE SECOND POST (FOR MONDAY ONLY)
    # ========================================================================

    second_post_response = None
    if weekday_theme.get('generate_multiple_posts') and weekday_theme.get('second_post_config'):
        social_logging.safe_log_info(
            "[NEW PIPELINE - STEP 5.5] Generating second post for Monday",
            user_id=user_id
        )

        second_post_config = weekday_theme['second_post_config']

        # Generate second topic with "La Vida en el rancho" theme
        second_topic_strategy = social_topic_engine.generate_topic_strategy(
            client=client,
            date_str=payload.date,
            weekday_theme=second_post_config,  # Use second post config
            recent_topics=recent_topics,
            user_suggested_topic=None,  # No user suggestion for second post
            is_second_post=True  # Flag to indicate this is the "La Vida en el rancho" post
        )

        social_logging.safe_log_info(
            "[NEW PIPELINE - STEP 5.5] Second topic generated",
            topic=second_topic_strategy.topic
        )

        # Generate strategy for second post
        second_content_strategy = social_strategy_engine.generate_content_strategy(
            client=client,
            topic_strategy=second_topic_strategy,
            weekday_theme=second_post_config,  # Use second post config
            recent_channels=recent_channels
        )

        social_logging.safe_log_info(
            "[NEW PIPELINE - STEP 5.5] Second strategy generated",
            post_type=second_content_strategy.post_type,
            channel=second_content_strategy.channel
        )

        # Second post doesn't need products
        second_product_details = None
        second_selected_product_id = None
        second_selected_category = None

        # Generate content for second post
        second_content_data = social_content_engine.generate_content(
            client=client,
            topic_strategy=second_topic_strategy,
            content_strategy=second_content_strategy,
            weekday_theme=second_post_config,
            product_details=None
        )

        social_logging.safe_log_info(
            "[NEW PIPELINE - STEP 5.5] Second content generated",
            has_caption=bool(second_content_data.get("caption"))
        )

        # Save second post to database
        second_formatted_content = {
            "caption": second_content_data.get("caption", ""),
            "image_prompt": second_content_data.get("image_prompt", ""),
            "cta": second_content_data.get("cta", ""),
            "suggested_hashtags": second_content_data.get("suggested_hashtags", []),
            "posting_time": second_content_data.get("posting_time"),
            "notes": second_content_data.get("notes", ""),
            "channel": second_content_data.get("channel"),
            "needs_music": second_content_data.get("needs_music", False),
            "selected_category": second_selected_category,
            "pipeline_version": "multi_step_v1",
            "is_second_post": True,
            "post_theme": "La Vida en el Rancho"
        }

        second_normalized_topic = social_topic.normalize_topic(second_topic_strategy.topic)
        second_topic_hash = social_topic.compute_topic_hash(second_normalized_topic)

        second_db_post = SocialPost(
            date_for=target_date,
            caption=second_content_data.get("caption", ""),
            image_prompt=second_content_data.get("image_prompt", ""),
            topic=second_normalized_topic,
            topic_hash=second_topic_hash,
            problem_identified=second_topic_strategy.problem_identified,
            post_type=second_content_strategy.post_type,
            content_tone=second_content_strategy.tone,
            channel=second_content_strategy.channel,
            selected_product_id=None,
            formatted_content=second_formatted_content,
            created_at=datetime.now()
        )

        db.add(second_db_post)
        db.commit()
        db.refresh(second_db_post)

        second_saved_post_id = second_db_post.id
        second_formatted_content["id"] = str(second_saved_post_id)
        second_db_post.formatted_content = second_formatted_content
        db.commit()

        social_logging.safe_log_info(
            "[NEW PIPELINE - STEP 5.5] Second post saved successfully",
            post_id=second_saved_post_id,
            user_id=user_id
        )

        # Build second post response
        second_post_response = SocialGenResponse(
            caption=second_content_data.get("caption", ""),
            image_prompt=second_content_data.get("image_prompt", ""),
            posting_time=second_content_data.get("posting_time"),
            notes=second_content_data.get("notes", ""),
            format=second_content_data.get("format"),
            cta=second_content_data.get("cta", ""),
            selected_product_id="",
            selected_category="",
            selected_product_details=None,
            post_type=second_content_strategy.post_type,
            content_tone=second_content_strategy.tone,
            channel=second_content_data.get("channel") or second_content_strategy.channel,
            carousel_slides=second_content_data.get("carousel_slides"),
            needs_music=second_content_data.get("needs_music", False),
            topic=second_topic_strategy.topic,
            problem_identified=second_topic_strategy.problem_identified,
            saved_post_id=second_saved_post_id,
            viral_angle=None,
            suggested_hashtags=second_content_data.get("suggested_hashtags", [])
        )

    # ========================================================================
    # STEP 6: BUILD RESPONSE
    # ========================================================================

    social_logging.safe_log_info("[NEW PIPELINE] Generation complete", user_id=user_id, post_id=saved_post_id)

    return SocialGenResponse(
        caption=content_data.get("caption", ""),
        image_prompt=content_data.get("image_prompt", ""),
        posting_time=content_data.get("posting_time"),
        notes=content_data.get("notes", ""),
        format=content_data.get("format"),
        cta=content_data.get("cta", ""),
        selected_product_id=selected_product_id or "",
        selected_category=selected_category or "",
        selected_product_details=product_details,
        post_type=content_strategy.post_type,
        content_tone=content_strategy.tone,
        channel=content_data.get("channel") or content_strategy.channel,
        carousel_slides=content_data.get("carousel_slides"),
        needs_music=content_data.get("needs_music", False),
        topic=topic_strategy.topic,
        problem_identified=topic_strategy.problem_identified,
        saved_post_id=saved_post_id,
        viral_angle=None,  # Not used in new pipeline
        suggested_hashtags=content_data.get("suggested_hashtags", []),
        second_post=second_post_response  # Include second post if generated
    )


@router.post("/generate", response_model=SocialGenResponse)
async def generate_social_copy(
    payload: SocialGenRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)  # Add auth
):
    """
    Agentic Generation Workflow with DB History.
    Generates ONE post per request. If multiple posts are created, check frontend for multiple calls.
    """
    # Rate limiting
    user_id = user.get("user_id", "anonymous")
    social_logging.safe_log_info(
        "[STEP 0] Starting post generation - SINGLE POST ONLY",
        user_id=user_id,
        date=payload.date,
        category=payload.category,
        has_suggested_topic=bool(payload.suggested_topic)
    )
    
    allowed, error_msg = social_rate_limit.check_rate_limit(user_id, "/generate")
    if not allowed:
        social_logging.safe_log_warning(f"[STEP 0] Rate limit exceeded", user_id=user_id)
        raise HTTPException(status_code=429, detail=error_msg)
    
    if not claude_api_key:
        social_logging.safe_log_error("[STEP 0] CLAUDE_API_KEY not configured", user_id=user_id)
        raise HTTPException(status_code=500, detail="CLAUDE_API_KEY not configured")

    client = anthropic.Client(api_key=claude_api_key)

    # --- 0. CONTEXT INIT (needed for history query) ---
    social_logging.safe_log_info("[STEP 1] Parsing date and initializing context", user_id=user_id)
    try:
        dt = datetime.strptime(payload.date, "%Y-%m-%d")
        target_date = dt.date()  # Convert to date object for proper comparison
    except ValueError:
        social_logging.safe_log_warning(f"[STEP 1] Invalid date format: {payload.date}, using today", user_id=user_id)
        dt = datetime.now()
        target_date = dt.date()

    # Use new multi-step pipeline
    return generate_with_new_pipeline(
        client=client,
        db=db,
        payload=payload,
        user_id=user_id,
        dt=dt,
        target_date=target_date
    )









