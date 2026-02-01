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

SEASON_PATTERNS = {
    1: {"phase": "germinacion", "name": "Enero", "actions": ["Venta sustratos", "Charolas germinación", "Semillas inicio"]},
    2: {"phase": "trasplante", "name": "Febrero", "actions": ["Preparación suelo", "Sistemas riego", "Acolchados"]},
    3: {"phase": "crecimiento", "name": "Marzo", "actions": ["Fertilizantes inicio", "Protección plagas", "Tutoreo"]},
    4: {"phase": "crecimiento", "name": "Abril", "actions": ["Fertirriego", "Bioestimulantes", "Control preventivo"]},
    5: {"phase": "cosecha-temprana", "name": "Mayo", "actions": ["Herramientas cosecha", "Empaque", "Logística"]},
    6: {"phase": "cosecha-alta", "name": "Junio", "actions": ["Cajas plásticas", "Tarimas", "Mantenimiento post-cosecha"]},
    7: {"phase": "lluvias", "name": "Julio", "actions": ["Fungicidas", "Drenaje", "Protección humedad"]},
    8: {"phase": "pre-ciclo-oi", "name": "Agosto", "actions": ["Limpieza terreno", "Desinfección", "Planeación O-I"]},
    9: {"phase": "siembra-oi", "name": "Septiembre", "actions": ["Semilla cebolla/ajo", "Cinta riego", "Acolchado"]},
    10: {"phase": "desarrollo-oi", "name": "Octubre", "actions": ["Nutrición foliar", "Enraizadores", "Monitoreo"]},
    11: {"phase": "frio-temprano", "name": "Noviembre", "actions": ["Manta térmica", "Anti-heladas", "Invernaderos"]},
    12: {"phase": "proteccion-frio", "name": "Diciembre", "actions": ["Calefacción", "Sellado invernadero", "Mantenimiento", "Planificación ciclo 2026", "Preparación suelo", "Análisis resultados"]}
}

DEFAULT_DATES = [
    {"month": 12, "day": 12, "name": "Día de la Virgen (México)"},
    {"month": 12, "day": 24, "name": "Nochebuena"},
    {"month": 12, "day": 31, "name": "Fin de Año"},
    {"month": 1, "day": 1, "name": "Año Nuevo"},
    {"month": 2, "day": 14, "name": "San Valentín (Floricultura)"},
    {"month": 5, "day": 10, "name": "Día de las Madres (Floricultura)"},
    {"month": 5, "day": 15, "name": "Día del Agricultor"},
]

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

# --- Logic ---

# Load problems and solutions data for Tuesday/Thursday rotation
PROBLEMS_SOLUTIONS_FILE = Path(__file__).parent / "social_problems_solutions.json"
PROBLEMS_SOLUTIONS_DATA = None

def load_problems_solutions_data():
    """Load problems and solutions data from JSON file."""
    global PROBLEMS_SOLUTIONS_DATA
    if PROBLEMS_SOLUTIONS_DATA is None:
        try:
            if PROBLEMS_SOLUTIONS_FILE.exists():
                with open(PROBLEMS_SOLUTIONS_FILE, 'r', encoding='utf-8') as f:
                    PROBLEMS_SOLUTIONS_DATA = json.load(f)
                social_logging.safe_log_info("Loaded problems and solutions data", file=str(PROBLEMS_SOLUTIONS_FILE))
            else:
                social_logging.safe_log_warning("Problems and solutions file not found", file=str(PROBLEMS_SOLUTIONS_FILE))
                PROBLEMS_SOLUTIONS_DATA = {"problem_categories": [], "product_upgrades": {}}
        except Exception as e:
            social_logging.safe_log_error("Failed to load problems and solutions data", exc_info=True, error=str(e))
            PROBLEMS_SOLUTIONS_DATA = {"problem_categories": [], "product_upgrades": {}}
    return PROBLEMS_SOLUTIONS_DATA

def get_problems_solutions_for_prompt(weekday_name: str) -> str:
    """
    Get formatted problems and solutions data for Tuesday/Thursday prompts.
    Returns empty string for other days.
    """
    if weekday_name not in ['Tuesday', 'Thursday']:
        return ""
    
    data = load_problems_solutions_data()
    if not data or not data.get("problem_categories"):
        return ""
    
    prompt_section = "\n📋 PROBLEMAS Y SOLUCIONES QUE OFRECEMOS (ROTACIÓN DE TEMAS/PRODUCTOS):\n"
    prompt_section += "Estos son los problemas principales que resolvemos para nuestros clientes y las soluciones/productos que ofrecemos.\n"
    prompt_section += "USA ESTA INFORMACIÓN para rotar entre diferentes problemas y productos, evitando repetir los mismos temas.\n\n"
    
    for category in data["problem_categories"]:
        prompt_section += f"🔹 {category['name']}:\n"
        prompt_section += "   Problemas:\n"
        for problem in category['problems']:
            prompt_section += f"   - {problem}\n"
        prompt_section += "   Valor que entregamos:\n"
        for value in category['value_delivered']:
            prompt_section += f"   - {value}\n"
        prompt_section += f"   Productos relacionados: {', '.join(category['related_products'])}\n"
        prompt_section += f"   Palabras clave: {', '.join(category['related_keywords'])}\n\n"
    
    prompt_section += "💡 UPGRADES DE PRODUCTOS (para aumentar valor):\n"
    for product_type, upgrade_info in data.get("product_upgrades", {}).items():
        prompt_section += f"   - {product_type.replace('_', ' ').title()}:\n"
        prompt_section += f"     Uso actual: {upgrade_info.get('current_use', 'N/A')}\n"
        prompt_section += f"     Ideas de upgrade: {', '.join(upgrade_info.get('upgrade_ideas', []))}\n"
        prompt_section += f"     Resultado para cliente: {', '.join(upgrade_info.get('customer_outcome', []))}\n\n"
    
    prompt_section += "⚠️ INSTRUCCIONES DE ROTACIÓN:\n"
    prompt_section += "- Revisa qué problemas/productos ya has usado recientemente\n"
    prompt_section += "- Elige un problema DIFERENTE de la lista arriba\n"
    prompt_section += "- Varía entre las 6 categorías (A-F) para mantener diversidad\n"
    prompt_section += "- Combina problemas con productos relevantes de la categoría\n"
    prompt_section += "- Para martes: Enfócate en promocionar productos que resuelvan el problema elegido\n"
    prompt_section += "- Para jueves: Enfócate en mostrar cómo el producto resuelve el problema (caso de éxito, antes/después)\n\n"
    
    return prompt_section

def get_season_context(date_obj):
    month = date_obj.month
    return SEASON_PATTERNS.get(month, SEASON_PATTERNS[1])

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

def get_nearby_dates(date_obj):
    # Simple logic: return dates in the same month
    month = date_obj.month
    return [d for d in DEFAULT_DATES if d["month"] == month]

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
    
    # Get weekday theme early to determine what context is needed
    weekday_theme = get_weekday_theme(dt)
    is_friday = dt.weekday() == 4
    is_tuesday = dt.weekday() == 1
    is_broad_topic_day = weekday_theme['day_name'] in ('Wednesday', 'Saturday', 'Sunday')
    needs_sales_context = is_tuesday or is_friday  # Tuesday (Promotion) and Friday (Seasonal) need sales context
    
    # --- 1. SEASON CONTEXT (ONLY on Tuesday and Friday) ---
    # Load sales context for Tuesday (Promotion day) and Friday (Seasonal day)
    sales_context = None
    important_dates = ""
    if needs_sales_context:
        social_logging.safe_log_info(
            "[STEP 4] Loading sales context",
            user_id=user_id,
            weekday=weekday_theme['day_name'],
            month=dt.month
        )
        sales_context = get_season_context(dt)
        important_dates = str([d["name"] for d in get_nearby_dates(dt)])
    else:
        social_logging.safe_log_info(
            "[STEP 4] Skipping sales context (not Tuesday or Friday)",
            user_id=user_id,
            weekday=weekday_theme['day_name']
        )
    
    # Load Durango context ONLY for Thursday, Friday, Saturday (Problem/Solution, Seasonal Focus, Producer Segment Focus)
    durango_context = ""
    needs_durango_context = weekday_theme['day_name'] in ['Thursday', 'Friday', 'Saturday']
    
    if needs_durango_context:
        social_logging.safe_log_info("[STEP 4] Loading Durango context (required for weekday theme)", user_id=user_id, weekday=weekday_theme['day_name'])
        durango_context = social_context.load_durango_context(month=dt.month, use_summary=True)
    else:
        social_logging.safe_log_info("[STEP 4] Skipping Durango context (not needed for weekday theme)", user_id=user_id, weekday=weekday_theme['day_name'])
    
    social_logging.safe_log_info(
        "[STEP 4] Context loaded",
        user_id=user_id,
        phase=sales_context.get('phase') if sales_context else "N/A (not Tuesday/Friday)",
        important_dates_count=len(get_nearby_dates(dt)) if needs_sales_context else 0,
        durango_context_loaded=needs_durango_context,
        sales_context_loaded=needs_sales_context
    )
    
    # PHASE 0: Viral Angle Generation (BEFORE Strategy Phase)
    social_logging.safe_log_info("[STEP 5] Generating viral angle", user_id=user_id)
    viral_prompt = """ACTÚA COMO: Growth Hacker especializado en viralización de contenido agrícola.

Tu objetivo NO es educar.
Tu objetivo es HACER QUE LA GENTE SE DETENGA A VER.

FECHA: {date}
DÍA DE LA SEMANA: {weekday}
TEMA DEL DÍA: {theme}

CONTEXTO:
- Estás generando contenido para productores agrícolas en México
- El contenido debe ser relevante para agricultura
- Pero DEBE tener un gancho viral que detenga el scroll

TU TAREA:
Genera un ángulo viral que maximice:
1. SCROLL STOP: Que la gente se detenga a ver
2. RETENTION: Que sigan leyendo/viendo
3. PSYCHOLOGICAL FRAMING: Que active un trigger emocional

Devuelve:
{{
  "hook_type": "shock|curiosity|loss|authority|contrast",
  "primary_trigger": "fear|curiosity|greed|simplicity|ego",
  "hook_sentence": "Una frase de gancho que detenga el scroll (máximo 15 palabras)",
  "visual_concept": "Descripción del concepto visual que acompañará (qué imagen/video captará atención)",
  "curiosity_gap": "El gap de curiosidad - qué pregunta o misterio dejarás sin resolver para que quieran saber más"
}}

TIPOS DE HOOK:
- shock: Algo impactante, inesperado, que rompe expectativas
- curiosity: Crea una pregunta que DEBEN responder
- loss: Muestra lo que están perdiendo si no actúan
- authority: Usa credibilidad, datos, expertos
- contrast: Comparación dramática (antes/después, correcto/incorrecto)

TRIGGERS PSICOLÓGICOS:
- fear: Miedo a perder, a fallar, a quedarse atrás
- curiosity: Necesidad de saber, de resolver el misterio
- greed: Deseo de ganar más, de tener ventaja
- simplicity: Atractivo de algo fácil, rápido, sin complicaciones
- ego: Validación, reconocimiento, sentirse inteligente

EJEMPLOS DE HOOK SENTENCES:
- "Este error está matando el 40% de tus plántulas y no lo sabes"
- "Un productor duplicó su producción usando esto (y no es lo que piensas)"
- "La mayoría de agricultores desperdician 70% de su agua. Aquí está el por qué"
- "Este truco de $50 pesos puede salvar toda tu cosecha"
- Títulos en pregunta para problemas/decisiones: ¿Por qué...?, ¿Qué...?, ¿Amigo o enemigo? Alternativa: título afirmativo + beneficio: [Tema]: ¡[Beneficio]! Gap de curiosidad: ej. "Tu suelo te habla", "Elige sabiamente".

IMPORTANTE:
- El hook debe ser RELEVANTE para agricultura
- Debe ser VERDADERO (no clickbait falso)
- Debe crear URGENCIA o CURIOSIDAD genuina
- El visual_concept debe ser específico y ejecutable""".format(
        date=payload.date,
        weekday=weekday_theme['day_name'],
        theme=weekday_theme['theme']
    )
    
    viral_angle = social_llm.call_viral_angle_llm(client, viral_prompt)
    social_logging.safe_log_info(
        "[STEP 5] Viral angle generated",
        user_id=user_id,
        hook_type=viral_angle.hook_type,
        primary_trigger=viral_angle.primary_trigger
    )
    
    # Identify real problems first
    # Only pass sales context on Tuesday and Friday
    social_logging.safe_log_info("[STEP 6] Identifying agricultural problems", user_id=user_id)
    nearby_dates_list = get_nearby_dates(dt) if needs_sales_context else []
    problems_data = identify_agricultural_problems(
        dt.month if needs_sales_context else None,  # Don't pass month on non-Tuesday/Friday
        sales_context['phase'] if sales_context else None,  # Don't pass phase on non-Tuesday/Friday
        nearby_dates_list,
        durango_context if needs_durango_context else ""
    )
    social_logging.safe_log_info(
        "[STEP 6] Problems identified",
        user_id=user_id,
        urgent_count=len(problems_data.get("most_urgent", [])),
        high_priority_count=len(problems_data.get("high_priority", []))
    )
    
    # Check for special date override
    special_date = get_special_date_override(dt)
    if special_date:
        # Override recommended post type for special dates
        weekday_theme['recommended_post_types'] = [special_date['recommended_post_type']] + weekday_theme['recommended_post_types']
        social_logging.safe_log_info(
            "[PHASE 3] Special date detected",
            user_id=user_id,
            special_date=special_date['special_date_name'],
            recommended_post_type=special_date['recommended_post_type']
        )
    
    social_logging.safe_log_info(
        "[PHASE 3] Weekday theme determined",
        user_id=user_id,
        weekday=weekday_theme['day_name'],
        theme=weekday_theme['theme'],
        recommended_types=weekday_theme['recommended_post_types']
    )
    
    # Build problem-focused strategy prompt
    social_logging.safe_log_info("[STEP 7] Building strategy prompt", user_id=user_id)
    strategy_prompt = f"ACTÚA COMO: Ingeniero Agrónomo Experto con 15+ años en campo Durango.\n"
    strategy_prompt += f"Tu trabajo diario es VISITAR PARCELAS, IDENTIFICAR PROBLEMAS REALES y SOLUCIONARLOS.\n\n"
    strategy_prompt += f"FECHA: {payload.date}\n"
    
    # Include viral angle context in strategy prompt
    strategy_prompt += f"\n🎯 ÁNGULO VIRAL (GENERADO PREVIAMENTE):\n"
    strategy_prompt += f"Tipo de gancho: {viral_angle.hook_type}\n"
    strategy_prompt += f"Trigger psicológico: {viral_angle.primary_trigger}\n"
    strategy_prompt += f"Frase de gancho: \"{viral_angle.hook_sentence}\"\n"
    strategy_prompt += f"Concepto visual: {viral_angle.visual_concept}\n"
    strategy_prompt += f"Gap de curiosidad: {viral_angle.curiosity_gap}\n\n"
    strategy_prompt += f"⚠️ IMPORTANTE: El tema y contenido que generes DEBE alinearse con este ángulo viral.\n"
    strategy_prompt += f"El hook_sentence debe ser incorporado o reflejado en el tema/título del post.\n"
    strategy_prompt += f"El visual_concept debe guiar la generación del image_prompt.\n"
    strategy_prompt += f"El curiosity_gap debe estar presente en el contenido para maximizar engagement.\n\n"
    # Only include sales/phase context on Tuesday and Friday
    if needs_sales_context and sales_context:
        strategy_prompt += f"FASE AGRÍCOLA: {sales_context['phase']} ({sales_context['name']})\n"
    
    # Only include Durango context for Thursday, Friday, Saturday
    if needs_durango_context:
        strategy_prompt += f"CONTEXTO REGIONAL DURANGO: {durango_context[:500]}...\n\n"
    else:
        strategy_prompt += "⚠️ NOTA: El contexto regional de Durango NO está disponible para este día.\n"
        strategy_prompt += "Enfócate en contenido general que no requiera conocimiento regional específico.\n\n"
    
    # Add weekday theme section
    strategy_prompt += f"📅 PLAN SEMANAL DE CONTENIDO - DÍA ACTUAL: {weekday_theme['day_name']}\n"
    strategy_prompt += f"🎯 TEMA DEL DÍA: {weekday_theme['theme']}\n"
    strategy_prompt += f"📝 TIPO DE CONTENIDO: {weekday_theme['content_type']}\n\n"
    
    if weekday_theme['sector_rotation']:
        sector_emoji = {'forestry': '🌲', 'plant': '🌾', 'animal': '🐄'}.get(weekday_theme['sector_rotation'], '')
        sector_name = {'forestry': 'Forestal', 'plant': 'Plantas/Cultivos', 'animal': 'Ganadería'}.get(weekday_theme['sector_rotation'], '')
        strategy_prompt += f"👩‍🌾 SECTOR DE ESTA SEMANA (Producer Segment Focus): {sector_emoji} {sector_name}\n"
        strategy_prompt += f"Enfócate en contenido relevante para productores de {sector_name.lower()}.\n"
        strategy_prompt += f"Ejemplos de temas:\n"
        if weekday_theme['sector_rotation'] == 'forestry':
            strategy_prompt += "- Forestal: 'Cómo almacenar agua para tus viveros forestales', 'Pro Tip: Mejores prácticas para viveros'\n"
        elif weekday_theme['sector_rotation'] == 'plant':
            strategy_prompt += "- Plantas: 'Riego eficiente con accesorios que sí duran', 'FAQ: ¿Cuándo es mejor momento para fertilizar?'\n"
        else:  # animal
            strategy_prompt += "- Ganadería: 'Evita fugas con abrazaderas resistentes para sistemas de agua para ganado', 'Interesting Fact: El agua representa X% del costo'\n"
        strategy_prompt += "\n"
    
    if special_date:
        strategy_prompt += f"🎉 FECHA ESPECIAL: {special_date['special_date_name']}\n"
        strategy_prompt += f"⚠️ PRIORIZA contenido relacionado con esta fecha especial.\n"
        strategy_prompt += f"TIPO DE POST RECOMENDADO: {special_date['recommended_post_type']}\n"
        strategy_prompt += f"Puedes combinar el tema del día ({weekday_theme['theme']}) con la fecha especial.\n\n"
    
    strategy_prompt += "⚠️ IMPORTANTE - SIGUE EL TEMA DEL DÍA:\n"
    strategy_prompt += f"- PRIORIZA estos tipos de post para {weekday_theme['day_name']}: {', '.join(weekday_theme['recommended_post_types'])}\n"
    strategy_prompt += "- El tema del día es una GUÍA, no una restricción absoluta\n"
    strategy_prompt += "- Si el tema del día no encaja con problemas urgentes o fechas importantes, puedes adaptarlo\n"
    strategy_prompt += "- PERO: Siempre considera primero los tipos de post recomendados para el día\n\n"
    
    # Only include urgency hints and important dates on Tuesday and Friday (when sales context is available)
    if needs_sales_context:
        # Add urgency hints from nearby dates (e.g., heladas coming soon)
        if problems_data.get("urgency_hints"):
            strategy_prompt += "⚠️ EVENTOS PRÓXIMOS (considera en tu análisis):\n"
            for hint in problems_data["urgency_hints"]:
                if hint.get("type") == "helada_risk":
                    days = hint.get("days_until", 0)
                    strategy_prompt += f"- {hint.get('name', 'Heladas')} en {days} días - considera problemas relacionados con protección contra frío\n"
            strategy_prompt += "\n"
        
        if important_dates:
            strategy_prompt += f"EFEMÉRIDES: {important_dates}.\n"
    strategy_prompt += f"PREFERENCIA USUARIO: {payload.category or 'Ninguna - Genera contenido educativo valioso sobre cualquier tema agrícola relevante'}.\n"
    strategy_prompt += "⚠️ IMPORTANTE: Si no hay preferencia de categoría, NO estás limitado a productos.\n"
    strategy_prompt += "Puedes generar contenido educativo sobre CUALQUIER tema agrícola valioso (técnicas, gestión, planificación, etc.).\n\n"
    
    # Topic diversity: permission + examples (Wed/Sat/Sun) — §11
    if is_broad_topic_day:
        strategy_prompt += "🌱 VARIEDAD DE TEMAS (HOY PUEDES ELEGIR CUALQUIER TEMA VALUABLE):\n"
        strategy_prompt += "NO estás limitado a riego, heladas, fertilizantes, costos o sequía.\n"
        strategy_prompt += "Puedes elegir CUALQUIER tema agrícola o rural valioso: curiosidades, cultura, variedades, tecnología, prácticas, comercialización, suelo, agua, plagas, mitos, \"sabías que…\", etc.\n\n"
        strategy_prompt += "Ejemplos solo como INSPIRACIÓN (no te limites a esta lista; puedes inventar temas nuevos en el mismo estilo):\n"
        strategy_prompt += "- Fases lunares en la agricultura\n"
        strategy_prompt += "- Cómo funciona un biodigestor\n"
        strategy_prompt += "- Variedades de frijol o chile en México\n"
        strategy_prompt += "- Cultivos interesantes para el año\n"
        strategy_prompt += "- Plantas indicadoras del suelo\n"
        strategy_prompt += "- Técnicas de control de plagas: químico vs biológico\n"
        strategy_prompt += "- Manejo de humedad en el cultivo de papa\n"
        strategy_prompt += "- Cómo realizar prueba del suelo\n"
        strategy_prompt += "- Cultivo orgánico vs convencional\n"
        strategy_prompt += "- Siembra en surcos vs siembra en cama elevada\n"
        strategy_prompt += "- Riego por goteo vs riego por aspersión\n"
        strategy_prompt += "- Macronutrientes vs micronutrientes\n"
        strategy_prompt += "- Densidad de siembra: cuándo \"más plantas\" reduce el rendimiento\n"
        strategy_prompt += "- Umbral económico de plagas: cuándo sí fumigar\n"
        strategy_prompt += "- Alimentación básica para bovinos de engorda\n"
        strategy_prompt += "- Drones en agricultura: usos reales vs marketing\n"
        strategy_prompt += "- Bioinsumos de nueva generación\n"
        strategy_prompt += "- Qué sembrar en el norte de México con menos de 400 mm de lluvia\n"
        strategy_prompt += "- Transformación de leche en queso artesanal: números reales\n"
        strategy_prompt += "- Sensores de humedad de suelo: cómo funcionan y cuándo valen la pena\n"
        for t in BROAD_TOPIC_EXAMPLES_EXTRA:
            strategy_prompt += f"- {t}\n"
        strategy_prompt += "- (y cualquier otro tema agrícola o rural específico, útil y valioso)\n\n"
        strategy_prompt += "Regla de variedad (4:1): En aproximadamente 4 de cada 5 posts, genera un tema NUEVO inspirado por el estilo de los ejemplos (agrícola/rural, específico, útil), pero que no sea copia literal de la lista. En aproximadamente 1 de cada 5 posts, elige un tema directamente de la lista de ejemplos de arriba. Varía: no siempre inventes ni siempre elijas de la lista.\n\n"
    
    strategy_prompt += "TU MENTALIDAD COMO INGENIERO EXPERTO:\n\n"
    strategy_prompt += "1. VARIEDAD PRIMERO - REVISAR HISTORIAL ANTES DE DECIDIR\n"
    strategy_prompt += "   - PRIMERO: Lee el historial reciente arriba y identifica qué temas ya cubriste\n"
    strategy_prompt += "   - SEGUNDO: Elige un tema COMPLETAMENTE DIFERENTE a los temas recientes\n"
    strategy_prompt += "   - TERCERO: Identifica un problema relevante para ese tema nuevo\n"
    strategy_prompt += "   - REGLA DE ORO: Si los últimos 2-3 posts son sobre 'X', elige algo sobre 'Y' (diferente)\n"
    strategy_prompt += "   - La VARIEDAD es más importante que seguir exactamente la fase agrícola\n\n"
    strategy_prompt += "2. PROBLEMA PRIMERO, PRODUCTO DESPUÉS\n"
    strategy_prompt += "   - NO pienses '¿Qué producto promociono hoy?'\n"
    strategy_prompt += "   - SÍ piensa '¿Qué problema real está enfrentando el agricultor HOY?'\n"
    strategy_prompt += "   - Luego: '¿Qué solución técnica resuelve este problema?'\n\n"
    strategy_prompt += "3. IDENTIFICA SÍNTOMAS, NO SOLO PROBLEMAS\n"
    strategy_prompt += "   - Los agricultores ven síntomas (hojas amarillas, plantas muertas)\n"
    strategy_prompt += "   - Tú como experto identificas la causa raíz\n"
    strategy_prompt += "   - El contenido debe conectar síntoma → causa → solución\n\n"
    strategy_prompt += "4. ERRORES COMUNES SON OPORTUNIDADES DE EDUCACIÓN\n"
    strategy_prompt += "   - Si un error común causa el problema, edúcales sobre cómo evitarlo\n"
    strategy_prompt += "   - Ejemplo: 'Error común: No proteger charolas → Solución: Sistema antiheladas'\n\n"
    strategy_prompt += "5. IMPACTO MEDIBLE GENERA URGENCIA\n"
    strategy_prompt += "   - 'Pérdida 30-50% de germinación' es más urgente que 'mejora la germinación'\n"
    strategy_prompt += "   - Usa números concretos del impacto del problema\n\n"
    strategy_prompt += "6. VENTANA DE TIEMPO CREA URGENCIA\n"
    strategy_prompt += "   - 'Enero-Febrero' es más urgente que 'durante el año'\n"
    strategy_prompt += "   - Si estamos en la ventana, el problema es INMEDIATO\n\n"
    
    # Add problems and solutions data for Tuesday/Thursday (BEFORE day-specific guidance)
    problems_solutions_section = get_problems_solutions_for_prompt(weekday_theme['day_name'])
    if problems_solutions_section:
        strategy_prompt += problems_solutions_section

    # Add suggested topic if provided
    if payload.suggested_topic:
        strategy_prompt += f"💡 TEMA SUGERIDO POR EL USUARIO: {payload.suggested_topic}\n⚠️ USA ESTE TEMA COMO BASE, pero puedes adaptarlo o expandirlo según sea necesario.\n\n"
    
    strategy_prompt += "TIPOS DE POST DISPONIBLES (ELIGE UNO DE ESTA LISTA - VARÍA RESPECTO A LOS ÚLTIMOS DÍAS):\n"
    strategy_prompt += f"{POST_TYPES_DEFINITIONS}\n\n"
    
    strategy_prompt += f"🎯 TIPOS DE POST RECOMENDADOS PARA {weekday_theme['day_name']} ({weekday_theme['theme']}):\n"
    strategy_prompt += "DEBES elegir UNO de estos tipos (prioriza estos sobre otros):\n"
    for post_type in weekday_theme['recommended_post_types']:
        strategy_prompt += f"- {post_type}\n"
    strategy_prompt += "\n"
    strategy_prompt += "⚠️ PRIORIZA estos tipos recomendados. Solo elige otros tipos si ninguno de los recomendados se adapta al tema específico.\n"
    strategy_prompt += f"⚠️ El tema del día ({weekday_theme['theme']}) es una GUÍA - si hay un problema urgente o fecha especial, puedes adaptarlo, pero mantén el enfoque del día.\n\n"
    
    # Special guidance for Tuesday (Promotion / Deals day)
    if weekday_theme['day_name'] == 'Tuesday':
        strategy_prompt += "💸💸💸 MARTES - DÍA DE PROMOCIONES 💸💸💸\n"
        strategy_prompt += "Hoy es MARTES (💸 Promotion / Deals). Este día SIEMPRE requiere productos.\n\n"
        strategy_prompt += "📋 USA LA LISTA DE PROBLEMAS Y SOLUCIONES ARRIBA:\n"
        strategy_prompt += "- Elige UN problema de las 6 categorías (A-F) que NO hayas usado recientemente\n"
        strategy_prompt += "- Identifica qué producto de esa categoría resuelve el problema\n"
        strategy_prompt += "- Formula el tema como: 'Error → Daño concreto → Producto/Solución que ofrecemos'\n"
        strategy_prompt += "- Ejemplo: 'Riego desigual → Pierdes 30% de producción → Sistema riego por goteo con emisores uniformes'\n\n"
        strategy_prompt += "🚨 REGLA ABSOLUTA PARA MARTES - NO HAY EXCEPCIONES:\n"
        strategy_prompt += "1. 'search_needed' DEBE ser SIEMPRE 'true' (OBLIGATORIO)\n"
        strategy_prompt += "2. 'preferred_category' DEBE tener una categoría (ej: 'riego', 'fertilizantes', 'mallasombra', 'herramientas')\n"
        strategy_prompt += "3. 'search_keywords' DEBE tener términos de búsqueda (ej: 'sistema riego', 'fertilizante', 'malla sombra')\n"
        strategy_prompt += "4. El tema DEBE incluir un producto específico o promoción\n"
        strategy_prompt += "5. Los tipos de post para martes SIEMPRE requieren productos:\n"
        strategy_prompt += "   - Promoción puntual → requiere producto con oferta\n"
        strategy_prompt += "   - Kits → requiere combo de productos\n"
        strategy_prompt += "   - 'Lo que llegó hoy' → requiere producto nuevo\n"
        strategy_prompt += "   - Cómo pedir / logística → puede incluir producto\n"
        strategy_prompt += "   - Recordatorio de servicio → puede incluir producto relacionado\n\n"
        strategy_prompt += "❌ INVALIDO para martes (NUNCA hagas esto):\n"
        strategy_prompt += "- 'search_needed': false (SIEMPRE debe ser true)\n"
        strategy_prompt += "- Contenido educativo general sin producto\n"
        strategy_prompt += "- Tema que no mencione o incluya un producto\n"
        strategy_prompt += "- Dejar 'preferred_category' vacío\n"
        strategy_prompt += "- Dejar 'search_keywords' vacío\n\n"
        strategy_prompt += "✅ EJEMPLO CORRECTO para martes:\n"
        strategy_prompt += "- Tema: 'Fugas en sistema riego → Desperdicias 40% de agua → Kit reparación con mangueras y conectores'\n"
        strategy_prompt += "- search_needed: true\n"
        strategy_prompt += "- preferred_category: 'riego'\n"
        strategy_prompt += "- search_keywords: 'manguera riego conectores kit reparación'\n"
        strategy_prompt += "- post_type: 'Kits' o 'Promoción puntual'\n\n"
    
    # Note: Sales context is only included on Tuesday and Friday
    # On other days, the LLM won't have seasonal/sales information to work with
    if is_friday:
        # Friday - seasonal content is allowed and encouraged
        strategy_prompt += "📅 VIERNES - CONTENIDO ESTACIONAL:\n"
        strategy_prompt += "Hoy es viernes (📅 Seasonal Focus). El contexto estacional arriba (FASE AGRÍCOLA, EFEMÉRIDES) está disponible para generar contenido estacional relacionado con la época del año.\n\n"
    elif is_tuesday:
        # Tuesday - sales context available for product promotions
        strategy_prompt += "💸 MARTES - CONTEXTO DE VENTAS DISPONIBLE:\n"
        strategy_prompt += "Hoy es martes (💸 Promotion / Deals). El contexto de ventas arriba (FASE AGRÍCOLA, EFEMÉRIDES) está disponible para identificar productos relevantes para promocionar según la época del año.\n\n"
    
    # Add content tone guidance based on weekday
    strategy_prompt += "🎨 TONO DE CONTENIDO (CONTENT TONE):\n"
    strategy_prompt += "El tono del contenido debe alinearse con el tema del día, pero puedes adaptarlo según el contexto.\n\n"
    
    # Map weekday to suggested tones
    tone_guidance = {
        'Monday': {
            'primary': 'Motivational',
            'alternatives': ['Inspirational', 'Encouraging', 'Humorous']
        },
        'Tuesday': {
            'primary': 'Promotional',
            'alternatives': ['Sales-focused', 'Urgent', 'Humorous']
        },
        'Wednesday': {
            'primary': 'Educational',
            'alternatives': ['Technical', 'Informative', 'Humorous']
        },
        'Thursday': {
            'primary': 'Problem-Solving',
            'alternatives': ['Technical', 'Solution-focused', 'Educational']
        },
        'Friday': {
            'primary': 'Seasonal',
            'alternatives': ['Educational', 'Informative', 'Technical']
        },
        'Saturday': {
            'primary': 'Educational',
            'alternatives': ['Technical', 'Practical', 'Humorous']
        },
        'Sunday': {
            'primary': 'Informative',
            'alternatives': ['Technical', 'Educational', 'Humorous']
        }
    }
    
    day_name = weekday_theme['day_name']
    if day_name in tone_guidance:
        guidance = tone_guidance[day_name]
        strategy_prompt += f"TONO RECOMENDADO PARA {day_name}:\n"
        strategy_prompt += f"- Tono principal sugerido: {guidance['primary']}\n"
        strategy_prompt += f"- Tonos alternativos: {', '.join(guidance['alternatives'])}\n"
        strategy_prompt += f"- También puedes usar: Humorous (cuando sea apropiado para el tema)\n\n"
    
    strategy_prompt += "TONOS DISPONIBLES (elige uno):\n"
    strategy_prompt += "- Motivational: Inspirador, alentador, que motive a los productores\n"
    strategy_prompt += "- Promotional: Enfocado en ventas, ofertas, productos\n"
    strategy_prompt += "- Technical: Técnico, detallado, con especificaciones y datos\n"
    strategy_prompt += "- Educational: Educativo, informativo, que enseñe algo nuevo\n"
    strategy_prompt += "- Problem-Solving: Enfocado en resolver problemas específicos\n"
    strategy_prompt += "- Seasonal: Relacionado con temporadas, ciclos, fechas importantes\n"
    strategy_prompt += "- Humorous: Divertido, ligero, con humor apropiado para agricultura\n"
    strategy_prompt += "- Informative: Informativo, noticioso, con datos y estadísticas\n"
    strategy_prompt += "- Inspirational: Inspirador, que genere emociones positivas\n"
    strategy_prompt += "\n"
    strategy_prompt += "⚠️ Elige el tono que mejor se adapte al tema, tipo de post y canal seleccionado.\n"
    strategy_prompt += "⚠️ Puedes usar Humorous cuando el tema lo permita, incluso en días técnicos.\n"
    strategy_prompt += "⚠️ El tono debe ser consistente con el tipo de post (ej: Memes/tips rápidos puede ser Humorous).\n\n"

    # On Wed/Sat/Sun allow short-title format; other days require viral format
    if is_broad_topic_day:
        strategy_prompt += "FORMATO DE TEMA (HOY TIENES DOS OPCIONES):\n"
        strategy_prompt += "(A) Formato viral: 'Error → Daño concreto → Solución' (igual que otros días).\n"
        strategy_prompt += "(B) Título corto descriptivo: cuando el contenido es educativo/broad sin problema-solución concreto (ej. 'Fases lunares en la agricultura', 'Cómo funciona un biodigestor', 'Variedades de frijol en México'). Si usas (B), search_needed puede ser false y search_keywords vacío.\n\n"
    else:
        strategy_prompt += "FORMATO DE TEMA (OBLIGATORIO - FORMATO VIRAL):\n"
        strategy_prompt += "El tema DEBE seguir este formato: 'Error → Daño concreto → Solución'\n"
        strategy_prompt += "Este formato triplica el impacto emocional al mostrar el daño específico.\n\n"
    strategy_prompt += "Ejemplos CORRECTOS (formato viral):\n"
    strategy_prompt += "- 'Regar por surco → Pierdes 40% de agua → Riego por goteo presurizado'\n"
    strategy_prompt += "- 'No proteger plántulas de heladas → Mueren 50% de plántulas → Sistema antiheladas con mantas térmicas'\n"
    strategy_prompt += "- 'Sustrato seco al sembrar → Germinación desigual 30% → Técnica correcta de hidratación previa'\n"
    strategy_prompt += "- 'Exponer cultivos a calor extremo → Reduce producción 30% → Mallasombra 50% sombra'\n"
    strategy_prompt += "- 'Usar riego tradicional → Desperdicias 70% de agua → Sistema riego por goteo eficiente'\n\n"
    strategy_prompt += "Ejemplos INCORRECTOS (evitar):\n"
    strategy_prompt += "- 'Riego por surco → Riego por goteo' (falta el daño concreto - formato antiguo)\n"
    strategy_prompt += "- 'Sistemas de riego' (genérico, no identifica error ni daño)\n"
    strategy_prompt += "- 'Productos agrícolas' (no es error)\n"
    strategy_prompt += "- 'Mejora tu cultivo' (vago, no específico, sin daño concreto)\n\n"
    strategy_prompt += "⚠️ REGLAS DEL FORMATO VIRAL:\n"
    strategy_prompt += "1. ERROR: Acción o práctica incorrecta (ej: 'Regar por surco', 'No proteger de heladas')\n"
    strategy_prompt += "2. DAÑO CONCRETO: Consecuencia medible con números/porcentajes (ej: 'Pierdes 40% de agua', 'Mueren 50% de plántulas')\n"
    strategy_prompt += "3. SOLUCIÓN: Solución técnica específica (ej: 'Riego por goteo presurizado', 'Sistema antiheladas')\n"
    strategy_prompt += "El daño concreto es CRÍTICO - debe incluir números, porcentajes, o consecuencias específicas.\n"
    strategy_prompt += "Cuando el tema sea problema o decisión, considera título en pregunta; alternativa: [Tema]: ¡[Beneficio]!.\n\n"
    strategy_prompt += "REGLAS DE CONTENIDO (§8 - aplican a todo lo que generes):\n"
    strategy_prompt += "(1) Números concretos (pérdidas/ahorros/%) con contexto: 'hasta $X dependiendo de...', 'pérdidas que pueden llegar a X% en condiciones típicas'.\n"
    strategy_prompt += "(2) Beneficios comparativos, no absolutos: 'ahorro vs riego por surco', 'mejor distribución que con X'.\n"
    strategy_prompt += "(3) No exagerar especificaciones del producto; usar lenguaje preciso.\n"
    strategy_prompt += "(4) Solución = contexto + producto, no solo producto (práctica correcta + producto).\n"
    strategy_prompt += "(5) Producto como componente central de la solución, no único héroe.\n\n"
    strategy_prompt += "TU TAREA:\n"
    strategy_prompt += "1. IDENTIFICA un problema agrícola real y relevante para esta fecha y fase\n"
    if needs_durango_context:
        strategy_prompt += "   - Usa tu conocimiento de agricultura en Durango (revisa el contexto regional arriba)\n"
    else:
        strategy_prompt += "   - Enfócate en problemas generales de agricultura que no requieren conocimiento regional específico\n"
    # Only mention phase on Tuesday and Friday (when sales context is available)
    if needs_sales_context and sales_context:
        strategy_prompt += f"   - Considera la fase agrícola actual: {sales_context['phase']} ({sales_context['name']})\n"
        if is_friday:
            strategy_prompt += f"   - Considera el mes: {dt.month} y las condiciones estacionales típicas\n"
            strategy_prompt += "   - Piensa en problemas REALES que los agricultores enfrentan HOY en esta época del año\n"
        elif is_tuesday:
            strategy_prompt += f"   - Considera el mes: {dt.month} y qué productos son relevantes para esta época\n"
            strategy_prompt += "   - Piensa en productos que los agricultores necesitan HOY en esta época del año para promocionar\n"
    else:
        strategy_prompt += "   - Enfócate en problemas generales que ocurren durante todo el año (no dependientes de temporada)\n"
        strategy_prompt += "   - Piensa en problemas técnicos, de gestión, o educativos que son relevantes siempre\n"
    strategy_prompt += "   - NO uses problemas genéricos - sé específico sobre síntomas, impactos y urgencia\n"
    if needs_sales_context:
        if is_friday:
            strategy_prompt += "   - Considera problemas de: germinación, riego, protección, planificación, costos, gestión, etc.\n"
            strategy_prompt += "   - Si hay eventos próximos (ej: heladas), considera problemas relacionados\n"
        elif is_tuesday:
            strategy_prompt += "   - Considera problemas que requieren productos para resolver (riego, fertilización, protección, herramientas, etc.)\n"
            strategy_prompt += "   - Enfócate en problemas donde puedas promocionar productos relevantes para esta época\n"
    else:
        strategy_prompt += "   - Considera problemas de: riego, fertilización, control de plagas, planificación, costos, gestión, organización, etc. (problemas que ocurren todo el año)\n"
        strategy_prompt += "   - NO menciones problemas estacionales o dependientes de época del año\n"
    if is_broad_topic_day:
        strategy_prompt += "2. Formula el tema como (A) 'Error → Daño concreto → Solución' O (B) un título corto descriptivo (ej: 'Fases lunares en la agricultura', 'Cómo funciona un biodigestor'). Si eliges (B), search_needed puede ser false.\n"
    else:
        strategy_prompt += "2. Formula el tema como 'Error → Daño concreto → Solución' (formato exacto requerido)\n"
    strategy_prompt += "   - El ERROR debe ser una acción o práctica incorrecta específica (ej: 'Regar por surco', 'No proteger de heladas')\n"
    strategy_prompt += "   - El DAÑO CONCRETO debe incluir números, porcentajes o consecuencias medibles (ej: 'Pierdes 40% de agua', 'Mueren 50% de plántulas', 'Reduce producción 30%')\n"
    strategy_prompt += "   - La SOLUCIÓN debe ser técnica, específica y accionable (ej: 'Riego por goteo presurizado', 'Sistema antiheladas con mantas térmicas')\n"
    strategy_prompt += "3. Elige el tipo de post que mejor comunique la solución\n"
    strategy_prompt += "4. Selecciona categoría de producto que resuelve el problema (o vacío si no aplica)\n\n"
    # Special guidance for Thursday (Problem & Solution day)
    if weekday_theme['day_name'] == 'Thursday':
        strategy_prompt += "🛠️🛠️🛠️ JUEVES - PROBLEMA Y SOLUCIÓN 🛠️🛠️🛠️\n"
        strategy_prompt += "Hoy es JUEVES (🛠️ Problem & Solution). Este día muestra cómo nuestros productos resuelven problemas reales.\n\n"
        strategy_prompt += "📋 USA LA LISTA DE PROBLEMAS Y SOLUCIONES ARRIBA:\n"
        strategy_prompt += "- Elige UN problema de las 6 categorías (A-F) que NO hayas usado recientemente\n"
        strategy_prompt += "- Muestra cómo nuestro producto resuelve ese problema específico\n"
        strategy_prompt += "- Formula el tema como: 'Error → Daño concreto → Solución con nuestro producto'\n"
        strategy_prompt += "- Enfócate en el VALOR ENTREGADO (resultados medibles, beneficios concretos)\n"
        strategy_prompt += "- Formatos ideales: Infografías (problema vs solución), Caso de éxito, Antes/Después\n"
        strategy_prompt += "- Ejemplo: 'Riego desigual → Pierdes 30% de producción y 40% de agua → Sistema riego por goteo con emisores uniformes'\n"
        strategy_prompt += "- Si el tema permite comparación (A vs B), prioriza post_type Infografías y formato comparativo: dos columnas, beneficios de un lado y desventajas del otro, con CTA al final.\n\n"
    
    strategy_prompt += "⚠️⚠️⚠️ IMPORTANTE SOBRE TEMAS (CRÍTICO) ⚠️⚠️⚠️:\n"
    strategy_prompt += "- Los temas NO están limitados a categorías de productos que vendemos.\n"
    strategy_prompt += "- El objetivo es generar contenido VALIOSO para agricultores, no solo promocionar productos.\n"
    strategy_prompt += "- Puedes elegir CUALQUIER tema agrícola relevante que proporcione valor educativo:\n"
    strategy_prompt += "  * Técnicas agrícolas (preparación de suelo, rotación de cultivos, etc.)\n"
    strategy_prompt += "  * Gestión y planificación (inventario, costos, ROI, organización)\n"
    strategy_prompt += "  * Educación general (fertilización, riego, plagas, enfermedades)\n"
    strategy_prompt += "  * Casos de éxito y resultados\n"
    strategy_prompt += "  * Tendencias y tecnología agrícola\n"
    strategy_prompt += "  * Problemas comunes y soluciones\n"
    strategy_prompt += "  * Preparación para ciclos futuros\n"
    strategy_prompt += "- Las 'ACCIONES SUGERIDAS' son solo sugerencias, NO son obligatorias.\n"
    strategy_prompt += "- Puedes elegir temas relacionados pero DIFERENTES a las acciones sugeridas.\n"
    strategy_prompt += "- Ejemplo: Si la acción es 'Calefacción', puedes hablar de:\n"
    strategy_prompt += "  * Planificación del siguiente ciclo (maíz, frijol para primavera)\n"
    strategy_prompt += "  * Preparación de suelo para siembra\n"
    strategy_prompt += "  * Optimización de recursos y costos\n"
    strategy_prompt += "  * Educación sobre otros aspectos agrícolas (riego, fertilización, etc.)\n"
    strategy_prompt += "  * Casos de éxito o resultados del año\n"
    strategy_prompt += "  * Gestión de inventario y organización\n"
    strategy_prompt += "  * Cultivos de frío actuales (avena, trigo, alfalfa) - no solo invernaderos\n"
    strategy_prompt += "  * Tecnología y innovación agrícola\n"
    strategy_prompt += "- VARÍA los temas incluso dentro de la misma fase agrícola.\n"
    strategy_prompt += "- NO te limites solo a 'protección contra frío' - hay muchos otros temas relevantes en diciembre.\n"
    strategy_prompt += "- Considera que en diciembre también se prepara para el ciclo primavera-verano.\n"
    strategy_prompt += "- 'preferred_category' es SOLO para selección de productos (si aplica), NO limita el tema del contenido.\n"
    strategy_prompt += "- Puedes generar contenido educativo SIN producto asociado si el tema lo requiere.\n\n"
    strategy_prompt += "RESPONDE SOLO CON EL JSON:\n"
    strategy_prompt += "{\n"
    strategy_prompt += '  "problem_identified": "Descripción del problema real que el agricultor enfrenta HOY (o del tema educativo si es título corto)",\n'
    if is_broad_topic_day:
        strategy_prompt += '  "topic": "Error → Daño → Solución O un título corto descriptivo (ej. Fases lunares en la agricultura). DEBE SER DIFERENTE a temas recientes",\n'
    else:
        strategy_prompt += '  "topic": "Error → Daño concreto → Solución (formato exacto como en ejemplos) - DEBE SER DIFERENTE a temas recientes",\n'
    strategy_prompt += '  "post_type": "Escribe EXACTAMENTE el nombre del tipo (ej. Infografías, Memes/tips rápidos, Kits, etc.)",\n'
    strategy_prompt += '  "channel": "wa-status|wa-broadcast|fb-post|fb-reel|ig-post|ig-reel|tiktok (elige uno, DIFERENTE al usado ayer). Si el tema es lista o proceso de 3-5 partes (ej. los 5 mejores, 4 pasos, qué está atacando: hongo/virus/plagas), prefiere fb-post o ig-post y considera carrusel.",\n'
    strategy_prompt += '  "content_tone": "Elige UNO de los tonos disponibles (Motivational, Promotional, Technical, Educational, Problem-Solving, Seasonal, Humorous, Informative, Inspirational) que mejor se adapte al tema y tipo de post",\n'
    # Special guidance for product selection based on weekday
    if weekday_theme['day_name'] == 'Tuesday':
        strategy_prompt += '  "preferred_category": "Categoría de producto OBLIGATORIA para martes (ej. riego, mallasombra, fertilizantes, herramientas, sustratos). DEBES seleccionar una categoría - NO puede estar vacío",\n'
        strategy_prompt += '  "search_needed": true (OBLIGATORIO para martes - SIEMPRE debe ser true, NUNCA false),\n'
        strategy_prompt += '  "search_keywords": "Términos de búsqueda OBLIGATORIOS para embeddings (ej. sistema riego, fertilizante, malla sombra, kit). DEBES proporcionar keywords - NO puede estar vacío"\n'
    else:
        strategy_prompt += '  "preferred_category": "Categoría de producto preferida SOLO si el tema requiere un producto específico (ej. riego, mallasombra). Si el tema es educativo general sin producto, deja vacío",\n'
        strategy_prompt += '  "search_needed": true/false (true solo si necesitas buscar un producto para el tema, false si el contenido es educativo general sin producto),\n'
        strategy_prompt += '  "search_keywords": "términos de búsqueda para embeddings SOLO si search_needed=true (ej. arado, fertilizante inicio, protección heladas). Si no hay producto, deja vacío"\n'
    strategy_prompt += "}"
    
    # Use new LLM module with strict JSON parsing and retry
    # This will raise HTTPException if topic validation fails
    strat_response = social_llm.call_strategy_llm(client, strategy_prompt)
    
    # Ensure content_tone is never empty - use fallback if needed
    content_tone = strat_response.content_tone.strip() if strat_response.content_tone else ""
    if not content_tone:
        default_tone = get_default_tone_for_weekday(weekday_theme['day_name'])
        social_logging.safe_log_warning(
            "[STEP 10] LLM returned empty content_tone, using default",
            user_id=user_id,
            default_tone=default_tone,
            weekday=weekday_theme['day_name']
        )
        content_tone = default_tone
    
    # Note: Seasonal content validation is now handled in the prompt itself
    # The prompt explicitly prohibits seasonal topics on non-Fridays
    # No retry mechanism needed - the LLM should follow the prompt instructions
    
    # Force product selection on Tuesday (Promotion / Deals day)
    search_needed = strat_response.search_needed
    search_keywords_override = strat_response.search_keywords or ""
    preferred_category_override = strat_response.preferred_category or ""
    
    if weekday_theme['day_name'] == 'Tuesday':
        if not search_needed:
            social_logging.safe_log_warning(
                "[STEP 10] LLM set search_needed=false on Tuesday - forcing to true",
                user_id=user_id,
                original_search_needed=search_needed
            )
            search_needed = True
        
        # If no preferred_category was set, try to infer from topic
        if not preferred_category_override:
            topic_lower = strat_response.topic.lower()
            if any(word in topic_lower for word in ['riego', 'agua', 'goteo', 'aspersión', 'manguera']):
                preferred_category_override = "riego"
            elif any(word in topic_lower for word in ['fertilizante', 'nutrición', 'nutriente', 'abono']):
                preferred_category_override = "fertilizantes"
            elif any(word in topic_lower for word in ['malla', 'sombra', 'protección', 'antiheladas']):
                preferred_category_override = "mallasombra"
            elif any(word in topic_lower for word in ['herramienta', 'pala', 'azadón', 'rastrillo']):
                preferred_category_override = "herramientas"
            else:
                preferred_category_override = ""  # Will be handled in product selection
            if preferred_category_override:
                social_logging.safe_log_info(
                    "[STEP 10] Inferred preferred_category for Tuesday",
                    user_id=user_id,
                    inferred_category=preferred_category_override
                )
        
        # If no search_keywords, generate from topic
        if not search_keywords_override:
            # Extract key terms from topic for product search
            topic_words = strat_response.topic.lower().split()
            # Remove common words and keep product-related terms
            stop_words = {'problema', 'solución', '→', 'de', 'la', 'el', 'en', 'con', 'para', 'por', 'un', 'una', 'los', 'las', 'del', 'al'}
            keywords = [w for w in topic_words if w not in stop_words and len(w) > 3]
            search_keywords_override = " ".join(keywords[:5])  # Take first 5 relevant words
            if search_keywords_override:
                social_logging.safe_log_info(
                    "[STEP 10] Generated search_keywords for Tuesday",
                    user_id=user_id,
                    generated_keywords=search_keywords_override
                )
    
    strat_data = {
        "problem_identified": strat_response.problem_identified,
        "topic": strat_response.topic,
        "post_type": strat_response.post_type,
        "channel": strat_response.channel,
        "content_tone": content_tone,  # Always non-empty after fallback
        "preferred_category": preferred_category_override,
        "search_needed": search_needed,  # Forced to true on Tuesday
        "search_keywords": search_keywords_override,
        "viral_angle": {
            "hook_type": viral_angle.hook_type,
            "primary_trigger": viral_angle.primary_trigger,
            "hook_sentence": viral_angle.hook_sentence,
            "visual_concept": viral_angle.visual_concept,
            "curiosity_gap": viral_angle.curiosity_gap
        }
    }
    
    # --- 3. PRODUCT SELECTION PHASE (using embeddings) ---
    social_logging.safe_log_info(
        "[STEP 11] Starting product selection",
        user_id=user_id,
        search_needed=strat_data.get("search_needed", False),
        weekday=weekday_theme['day_name']
    )
    selected_product_id = None
    selected_category = None
    product_details = None
    
    if strat_data.get("search_needed"):
        search_query = strat_data.get("search_keywords", "") or strat_data.get("topic", "")
        preferred_category = strat_data.get("preferred_category", "")
        
        # Use new product selection module
        try:
            selected_product_id, selected_category, product_details = social_products.select_product_for_post(
                db,
                search_query,
                preferred_category=preferred_category if preferred_category else None
            )
            social_logging.safe_log_info(
                "[STEP 11] Product selected",
                user_id=user_id,
                product_id=selected_product_id,
                category=selected_category
            )
        except Exception as e:
            social_logging.safe_log_error("[STEP 11] Product selection failed", exc_info=True, user_id=user_id, error=str(e))
            # Continue without product - content can be educational without specific product
    else:
        social_logging.safe_log_info("[STEP 11] Product selection skipped (search_needed=false)", user_id=user_id)

    # --- 4. CONTENT GENERATION PHASE ---
    social_logging.safe_log_info("[STEP 12] Starting content generation phase", user_id=user_id)
    # Fetch selected product details if a product was selected
    selected_product_info = ""
    if selected_product_id:
        social_logging.safe_log_info("[STEP 12] Fetching product details", user_id=user_id, product_id=selected_product_id)
        try:
            pid = int(selected_product_id)
            sp_obj = db.query(SupplierProduct).filter(SupplierProduct.id == pid).first()
            if sp_obj:
                product_name = sp_obj.name or (sp_obj.product.name if sp_obj.product else "Unknown")
                product_desc = sp_obj.description or (sp_obj.product.description if sp_obj.product else "") or "Sin descripción disponible"
                product_specs = sp_obj.specifications or (sp_obj.product.specifications if sp_obj.product else {}) or {}
                specs_str = ", ".join([f"{k}: {v}" for k, v in product_specs.items()]) if isinstance(product_specs, dict) and len(product_specs) > 0 else str(product_specs) if product_specs else "No disponibles"
                cat_name = sp_obj.category.name if sp_obj.category else (sp_obj.product.category.name if sp_obj.product and sp_obj.product.category else "General")
                sku = sp_obj.sku or (sp_obj.product.sku if sp_obj.product else "N/A")
                
                selected_product_info = (
                    f"\n📦 PRODUCTO SELECCIONADO (USA ESTA INFORMACIÓN PARA GENERAR CONTENIDO PRECISO):\n"
                    f"- ID: {selected_product_id}\n"
                    f"- Nombre: {product_name}\n"
                    f"- Categoría: {cat_name}\n"
                    f"- SKU: {sku}\n"
                    f"- Descripción: {product_desc}\n"
                    f"- Especificaciones: {specs_str}\n"
                    f"\n⚠️ IMPORTANTE: El caption y el prompt de imagen DEBEN reflejar el uso real, propósito y características de este producto específico.\n"
                    f"Investiga mentalmente: ¿Para qué se usa este producto? ¿En qué cultivos? ¿Qué problema resuelve? ¿Cómo se instala/usa?\n"
                    f"Enfócate en el valor educativo y el interés del producto para generar contenido atractivo.\n"
                )
                social_logging.safe_log_info("[STEP 12] Product details fetched", user_id=user_id, product_name=product_name if 'product_name' in locals() else None)
        except Exception as e:
            social_logging.safe_log_error("[STEP 12] Error fetching product details", exc_info=True, user_id=user_id, error=str(e))
            selected_product_info = f"\nProducto seleccionado ID: {selected_product_id}\n"
    else:
        social_logging.safe_log_info("[STEP 12] No product selected, skipping product details", user_id=user_id)

    # durango_context already loaded earlier for problem identification

    social_logging.safe_log_info("[STEP 12] Building content generation prompt", user_id=user_id)
    
    # Get weekday theme for content generation (already computed earlier, but ensure we have it)
    if 'weekday_theme' not in locals():
        weekday_theme = get_weekday_theme(dt)
    
    # Include viral angle in content generation prompt
    viral_angle_context = ""
    if strat_data.get("viral_angle"):
        va = strat_data["viral_angle"]
        viral_angle_context = f"""
🎯 ÁNGULO VIRAL (DEBE SER INCORPORADO):
- Tipo de gancho: {va['hook_type']}
- Trigger psicológico: {va['primary_trigger']}
- Frase de gancho: "{va['hook_sentence']}"
- Concepto visual: {va['visual_concept']}
- Gap de curiosidad: {va['curiosity_gap']}

⚠️ CRÍTICO: El caption DEBE incorporar o reflejar el hook_sentence.
⚠️ CRÍTICO: El image_prompt DEBE alinearse con el visual_concept.
⚠️ CRÍTICO: El contenido DEBE crear el curiosity_gap para maximizar engagement.

"""
    
    creation_prompt = (
        f"ACTÚA COMO: Social Media Manager especializado en contenido agrícola.\n\n"
        f"{viral_angle_context}"
        f"📅 PLAN SEMANAL - DÍA ACTUAL: {weekday_theme['day_name']}\n"
        f"🎯 TEMA DEL DÍA: {weekday_theme['theme']}\n"
        f"📝 TIPO DE CONTENIDO: {weekday_theme['content_type']}\n\n"
        
        f"ESTRATEGIA DEFINIDA:\n"
        f"- TEMA: {strat_data.get('topic')}\n"
        f"- PROBLEMA IDENTIFICADO: {strat_data.get('problem_identified', '')}\n"
        f"- TIPO DE POST: {strat_data.get('post_type')}\n"
        f"- CANAL: {strat_data.get('channel')}\n"
    )
    
    # Add sector-specific guidance for Saturday
    if weekday_theme['sector_rotation']:
        sector_emoji = {'forestry': '🌲', 'plant': '🌾', 'animal': '🐄'}.get(weekday_theme['sector_rotation'], '')
        sector_name = {'forestry': 'Forestal', 'plant': 'Plantas/Cultivos', 'animal': 'Ganadería'}.get(weekday_theme['sector_rotation'], '')
        creation_prompt += f"\n👩‍🌾 SECTOR DE ESTA SEMANA (Producer Segment Focus): {sector_emoji} {sector_name}\n"
        creation_prompt += f"Asegúrate de que el contenido sea relevante para productores de {sector_name.lower()}.\n"
        creation_prompt += f"Formatos recomendados: Infografías, FAQ/Mitos, Pro Tip, Interesting Fact, Tutorial corto, Recordatorio de servicio\n"
        creation_prompt += f"Ejemplos de contenido:\n"
        if weekday_theme['sector_rotation'] == 'forestry':
            creation_prompt += "- Forestal: 'Cómo almacenar agua para tus viveros forestales', 'Pro Tip: Mejores prácticas para viveros'\n"
        elif weekday_theme['sector_rotation'] == 'plant':
            creation_prompt += "- Plantas: 'Riego eficiente con accesorios que sí duran', 'FAQ: ¿Cuándo es mejor momento para fertilizar?'\n"
        else:  # animal
            creation_prompt += "- Ganadería: 'Evita fugas con abrazaderas resistentes para sistemas de agua para ganado', 'Interesting Fact: El agua representa X% del costo'\n"
        creation_prompt += "\n"
    
    # Add day-specific content guidance
    if weekday_theme['day_name'] == 'Monday':
        creation_prompt += "✊ MOTIVATIONAL / INSPIRATIONAL MONDAY:\n"
        creation_prompt += "- Incluye un mensaje inspirador o motivacional relacionado con la agricultura/forestry\n"
        creation_prompt += "- Puede ser una frase motivacional, cita de la semana, meme educativo, o imagen/foto destacada\n"
        creation_prompt += "- Mantén el tono positivo y alentador\n"
        creation_prompt += "- Formatos: Motivational Phrase/Quote, Memes/tips rápidos, Image/Photo of the Week\n"
        creation_prompt += "- Ejemplos: 'El campo es trabajo y pasión', 'Cada siembra es una apuesta al futuro', Foto de campo con mensaje inspirador\n\n"
    elif weekday_theme['day_name'] == 'Tuesday':
        creation_prompt += "💸 PROMOTION / DEALS TUESDAY:\n"
        creation_prompt += "- Destaca el producto con precio especial, bundle, o oferta estacional\n"
        creation_prompt += "- Si hay producto seleccionado, enfatiza la promoción o el valor\n"
        creation_prompt += "- Incluye llamada a la acción clara para contacto/compra\n"
        creation_prompt += "- Formatos: Promoción puntual, Kits, 'Lo que llegó hoy', Cómo pedir/logística, Recordatorio de servicio\n"
        creation_prompt += "- Ejemplos: '¿Sabías que una Abrazadera Sin Fin puede ayudarte a optimizar tu sistema de riego?', 'Ofertas de temporada: Geotanques para captación de agua'\n\n"
    elif weekday_theme['day_name'] == 'Wednesday':
        creation_prompt += "📚 EDUCATIONAL / TIPS WEDNESDAY:\n"
        creation_prompt += "- Enfócate en educar: tips, guías, cómo hacer, datos interesantes, o artículos\n"
        creation_prompt += "- El contenido debe ser práctico y accionable\n"
        creation_prompt += "- Formatos: Infografías de producto o tema, Tutorial corto, Pro Tip, Interesting Fact, Article, Sabías que...\n"
        creation_prompt += "- Ejemplos: 'Cómo reducir la evaporación del agua en tus cultivos', 'Errores comunes al instalar un sistema de riego por goteo', 'Sabías que... los geotanques pueden almacenar hasta X litros'\n\n"
    elif weekday_theme['day_name'] == 'Thursday':
        creation_prompt += "🛠️ PROBLEM & SOLUTION THURSDAY:\n"
        creation_prompt += "- Muestra claramente el problema y cómo el producto/solución lo resuelve\n"
        creation_prompt += "- Usa formato comparativo o antes/después si es posible\n"
        creation_prompt += "- Incluye datos concretos (porcentajes, números) del impacto\n"
        creation_prompt += "- Formatos: Infografías, Caso de éxito, Antes / Después\n"
        creation_prompt += "- Ejemplos: '¿Por qué usar geotanques en vez de tambos?', 'Caso de éxito: Cómo [producto] resolvió [problema]', Comparativa visual antes/después\n\n"
    elif weekday_theme['day_name'] == 'Friday':
        creation_prompt += "📅 SEASONAL FOCUS FRIDAY:\n"
        creation_prompt += "- Enfócate en consejos o alertas basadas en temporadas regionales\n"
        creation_prompt += "- Considera el calendario de siembra, cosecha, poda, fertilización\n"
        creation_prompt += "- Incluye tips sobre clima estacional: qué esperar y cómo actuar\n"
        creation_prompt += "- Formatos: Infografías, Tutorial corto, Checklist operativo, Recordatorio de servicio, Seasonal weather tips\n"
        creation_prompt += "- Ejemplos: 'Este mes enfócate en mantenimiento del sistema de riego antes de temporada seca', 'Temporadas de cosecha: chiles, manzana', 'Alerta: Se esperan heladas esta semana'\n\n"
    elif weekday_theme['day_name'] == 'Saturday':
        creation_prompt += "👩‍🌾 PRODUCER SEGMENT FOCUS SATURDAY:\n"
        creation_prompt += f"- Enfócate en el sector: {weekday_theme['sector_rotation']}\n"
        creation_prompt += "- El contenido debe ser específico para ese tipo de productor\n"
        creation_prompt += "- Formatos: Infografías, FAQ/Mitos, Pro Tip, Interesting Fact, Tutorial corto, Recordatorio de servicio\n"
        creation_prompt += "- Ejemplos sectoriales:\n"
        if weekday_theme['sector_rotation'] == 'forestry':
            creation_prompt += "  - Forestal: 'Cómo almacenar agua para tus viveros forestales', 'Pro Tip: Mejores prácticas para viveros'\n"
        elif weekday_theme['sector_rotation'] == 'plant':
            creation_prompt += "  - Plantas: 'Riego eficiente con accesorios que sí duran', 'FAQ: ¿Cuándo es mejor momento para fertilizar?'\n"
        else:  # animal
            creation_prompt += "  - Ganadería: 'Evita fugas con abrazaderas resistentes para sistemas de agua para ganado', 'Interesting Fact: El agua representa X% del costo de producción'\n"
        creation_prompt += "\n"
    elif weekday_theme['day_name'] == 'Sunday':
        creation_prompt += "📊 INNOVATION / INDUSTRY REPORTS SUNDAY:\n"
        creation_prompt += "- Enfócate en noticias de la industria, innovación agrícola, estadísticas, o reportes\n"
        creation_prompt += "- Puede incluir novedades tecnológicas, normativas, tendencias, o trivia agrotech\n"
        creation_prompt += "- Mantén el tono informativo y actualizado\n"
        creation_prompt += "- Formatos: Industry novelty, Trivia agrotech-style post, Statistics or report highlights\n"
        creation_prompt += "- Ejemplos: 'Biofertilizantes: La nueva tendencia en agricultura', 'Trivia: ¿Sabías que México produce X% del aguacate mundial?', 'Reporte: Tendencias del campo en México 2025'\n\n"
    
    # Add universal content layer guidance
    creation_prompt += "✨ UNIVERSAL CONTENT LAYER (puedes usar en cualquier día):\n"
    creation_prompt += "- UGC (User Generated Content): Puede usarse cualquier día, en cualquier tipo de post\n"
    creation_prompt += "- Reels / Stories / TikTok: Adapta cualquier tipo de post a formato de video corto y atractivo\n"
    creation_prompt += "- Carousel Posts: Ideal para infografías, tutoriales, o formatos problema/solución\n"
    creation_prompt += "- Live / Polls / Q&A: Usa ocasionalmente para aumentar engagement\n\n"
    
    creation_prompt += f"{selected_product_info}\n"
    
    # Only include Durango context for Thursday, Friday, Saturday
    if needs_durango_context:
        creation_prompt += (
            f"CONTEXTO REGIONAL DURANGO (USA ESTA INFORMACIÓN PARA CONTENIDO RELEVANTE, PERO NO TE LIMITES SOLO A ESTO):\n"
            f"{durango_context[:800]}...\n\n"
            f"⚠️ NOTA SOBRE EL CONTEXTO: El contexto de Durango menciona actividades estacionales, pero NO debes limitarte solo a esos temas.\n"
            f"Puedes hablar de otros temas relevantes como planificación, optimización, educación, casos de éxito, etc.\n\n"
        )
    else:
        creation_prompt += (
            "⚠️ NOTA: El contexto regional de Durango NO está disponible para este día.\n"
            "Enfócate en contenido general que no requiera conocimiento regional específico.\n\n"
        )
    
    creation_prompt += (
        f"{CHANNEL_FORMATS}\n\n"
        
        "INSTRUCCIONES:\n"
        "1. Usa la información del producto si fue seleccionado, o crea contenido educativo general si no hay producto.\n"
        f"2. Canal: {strat_data.get('channel')} - Adapta el contenido a este canal específico.\n"
        "3. CAPTION POR CANAL: wa-status/stories (máx 50 chars), tiktok (máx 150), reels (máx 100), fb-post/ig-post (hasta 2000).\n"
        "   Para wa-status/stories/tiktok/reels: La imagen/video debe ser autoexplicativa, caption mínimo.\n"
        "4. REQUISITOS TÉCNICOS: Usa números exactos ('10-20 cm', '70% ahorro'), colores (Verde=bueno, Rojo=problema), tips en caja azul.\n"
        "5. Genera el contenido adaptado al canal y tipo de post.\n"
        "6. REGLAS DE CONTENIDO (§8): Números con contexto; beneficios comparativos (vs qué); no exagerar specs; solución = contexto + producto; producto como componente central.\n"
        "7. Si el tema tiene 3-5 secciones claras (ej. los 5 mejores, 4 pasos, 3 tipos de ataque), genera carousel_slides con un slide por sección (título + 1-2 frases + idea visual). Slide final opcional: CTA o resumen.\n\n"
    )
    
    # Detect structure type based on topic (before building image prompt section)
    topic_lower = strat_data.get('topic', '').lower()
    post_type_lower = strat_data.get('post_type', '').lower()
    
    if "compar" in topic_lower or " vs " in topic_lower or "tradicional" in topic_lower:
        structure_type = "COMPARATIVA"
        structure_guide = """
ESTRUCTURA: Comparativa lado a lado (Error → Daño concreto → Solución)
- Panel izquierdo (40% espacio, fondo naranja/rojo): [MÉTODO TRADICIONAL/PROBLEMA]
  * Título grande: "[MÉTODO TRADICIONAL]" (texto blanco, bold)
  * Indicador numérico grande: "[X% pérdida/problema]" (número 120px, color rojo)
  * 3-4 problemas específicos con porcentajes/datos
  * Iconos de pérdida/riesgo (rojos)
  * Flechas rojas hacia abajo
- Panel derecho (40% espacio, fondo verde): [MÉTODO MEJORADO/SOLUCIÓN]
  * Título grande: "[MÉTODO MEJORADO]" (texto blanco, bold)
  * Indicador numérico grande: "[X% ahorro/beneficio]" (número 120px, color verde)
  * 3-4 beneficios específicos con porcentajes/datos
  * Iconos de beneficio/éxito (verdes)
  * Flechas verdes hacia arriba
- Sección inferior (20% espacio, fondo blanco): Tabla comparativa
  * Columnas: Método | Consumo | Uniformidad | Costo | ROI
  * Filas: Tradicional vs Tecnificado con datos específicos
- Plantilla simple: un visual fuerte por lado + headline + 2 bullets por lado + footer.
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
    elif "sistema" in topic_lower or "instalación completa" in topic_lower or "diagrama" in topic_lower:
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
        structure_type = "MULTI-PANEL"
        structure_guide = """
ESTRUCTURA: Infografía educativa multi-panel
- Panel 1 (25% altura): Título + Concepto principal (visual grande)
- Panel 2 (20% altura): Problema/Necesidad (si aplica, fondo amarillo/naranja)
- Panel 3 (20% altura): Solución/Método (fondo verde)
- Panel 4 (20% altura): Especificaciones técnicas (tabla/lista con medidas específicas)
- Panel 5 (15% altura): Tips/Beneficios destacados (caja azul con borde verde)
"""
    
    # Continue building creation_prompt with structure detection
    creation_prompt += (
        "--- INSTRUCCIONES ESPECÍFICAS PARA image_prompt ---\n"
        f"ESTRUCTURA DETECTADA: {structure_type}\n"
        f"{structure_guide}\n\n"
        "El campo 'image_prompt' DEBE ser un prompt detallado y técnico para generación de imágenes (estilo IMPAG).\n"
        "Sigue este formato estructurado:\n\n"
        
        "⚠️⚠️⚠️ ADAPTACIÓN POR CANAL (CRÍTICO) ⚠️⚠️⚠️:\n"
        "- Para wa-status, stories, tiktok, reels: La imagen DEBE ser AUTOEXPLICATIVA con TEXTO GRANDE Y VISIBLE.\n"
        "  El usuario debe entender el mensaje SOLO viendo la imagen, sin leer el caption.\n"
        "- Para fb-post, ig-post: La imagen puede ser más técnica/detallada, el caption puede complementar.\n\n"
        
        "FORMATO REQUERIDO (adaptar dimensiones al canal):\n"
        "- wa-status/stories/tiktok/reels: Vertical 1080×1920 px\n"
        "- fb-post/ig-post: Cuadrado 1080×1080 px\n"
        "Estilo [flyer técnico/paisaje agrícola/catálogo técnico] IMPAG, con diseño limpio, moderno y profesional.\n"
        "Mantén siempre la estética corporativa IMPAG: fondo agrícola difuminado, tonos blanco–gris, acentos verde–azul, sombras suaves, tipografías gruesas para títulos y delgadas para texto técnico.\n"
    )
    if (strat_data.get('post_type') or '').lower() in ('infografías', 'infografias'):
        creation_prompt += (
            "\nPara este tipo de post usa estilo: infografía educativa ilustrada, trazo amigable, colores tierra/verde/azul, no fotorealista. Iconos claros, viñetas y texto legible. Mantener logos IMPAG.\n"
            "Para infografías: mensaje principal y 2-3 bullets por panel; especificaciones detalladas en caption si es necesario. Todo texto relevante (títulos, bullets) debe ser legible en móvil; preferir un headline y líneas cortas.\n\n"
        )
    creation_prompt += (
        
        "Instrucciones de diseño detalladas:\n"
        "1. LOGOS (OBLIGATORIO - §7 IMPAG only):\n"
        "   - Usar SOLO branding IMPAG. Logo oficial 'IMPAG Agricultura Inteligente' en esquina superior derecha, sin deformarlo.\n"
        "   - No incluir otros nombres ni logos en la imagen (no Todo para el Campo ni otros). Contacto y URL pueden ser los mismos; la identidad visual en la imagen es solo IMPAG.\n\n"
        
        "2. ELEMENTO PRINCIPAL (CON PERSONAS CUANDO APLIQUE):\n"
        "   - Si hay producto: Imagen realista del producto en alta resolución, fotorealista, iluminación de estudio suave o golden hour.\n"
        "   - ⚠️ INCLUYE PERSONAS cuando sea apropiado:\n"
        "     * Para productos agrícolas: Agricultor/productor mexicano usando el producto en campo, sosteniéndolo, o mostrándolo como recomendación.\n"
        "     * Para productos ganaderos: Ganadero usando el producto, mostrándolo en uso real.\n"
        "     * Para productos forestales: Ingeniero forestal o trabajador forestal usando el producto.\n"
        "     * Para productos de riego/instalación: Ingeniero agrónomo o técnico instalando o mostrando el producto.\n"
        "     * Las personas deben verse profesionales, auténticas, con ropa de trabajo agrícola/ganadero/forestal apropiada.\n"
        "     * Las personas deben estar interactuando con el producto de forma natural (sosteniéndolo, instalándolo, usándolo).\n"
        "   - Si es paisaje: Paisaje agrícola realista del norte de México (Durango), cultivos en hileras, iluminación natural suave.\n"
        "   - Si es kit: Componentes completamente visibles, montados o desglosados en técnica 'knolling', cables ordenados.\n"
        "   - Mantener proporción, ubicación, integración suave con fondo, estilo profesional tipo catálogo.\n"
        "   ⚠️ PARA STORIES/STATUS/TIKTOK/REELS: Agrega TEXTO GRANDE Y VISIBLE en la imagen que comunique el mensaje principal.\n"
        "   El texto debe ser legible desde lejos, con buen contraste, tamaño mínimo 60-80px.\n\n"
        
        "3. ESPECIFICACIONES TÉCNICAS (si aplica):\n"
        "   - Bloque técnico con viñetas: 📏 Especificaciones Técnicas:\n"
        "   - Lista de 4-6 datos técnicos relevantes del producto\n"
        "   - Respetar viñetas, colores, alineación, tipografía, fondo del recuadro y sombra.\n\n"
        
        "4. PIE DEL FLYER (mantener estilo IMPAG):\n"
        f"   - {CONTACT_INFO['web']}\n"
        "   - Envíos a todo México\n"
        f"   - WhatsApp: {CONTACT_INFO['whatsapp']}\n"
        f"   - 📍 {CONTACT_INFO['location']}\n\n"
        
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
        f'  "channel": "{strat_data.get("channel", "fb-post")}",\n'
        f'  "topic": "{strat_data.get("topic")}",\n'
        '  "caption": "... (RESPETA: wa-status/stories/tiktok/reels = MUY CORTO, fb-post = puede ser largo)",\n'
        '  "image_prompt": "PROMPT DETALLADO OBLIGATORIO para generación de imagen (SIEMPRE requerido). Si es carrusel, usa el prompt de la imagen de portada o primera slide. Para stories/status debe ser autoexplicativa con texto grande visible. SIEMPRE incluye logos IMPAG y dimensiones correctas (1080×1920 para vertical, 1080×1080 para cuadrado).",\n'
        '  "carousel_slides": ["Slide 1 CON TEXTO GRANDE...", "Slide 2 CON TEXTO...", ...] (SOLO si es carrusel: TikTok 2-3, FB/IG 2-10. Si es carrusel, image_prompt debe ser la portada o primera slide),\n'
        '  "needs_music": true/false,\n'
        '  "posting_time": "...",\n'
        '  "notes": "...",\n'
        '  "suggested_hashtags": ["#Riego", "#Agricultura", ...] (opcional: 5-8 hashtags en español)\n'
        "}\n\n"
        f"REGLAS FINALES: Producto ID {selected_product_id or 'ninguno'}. Incluye logos IMPAG, personas cuando aplique, sé específico sobre el producto y su uso."
    )

    # Use new LLM module with strict JSON parsing and retry
    # This will raise HTTPException on failure (no silent fallback)
    social_logging.safe_log_info("[STEP 13] Calling content LLM", user_id=user_id)
    content_response = social_llm.call_content_llm(client, creation_prompt)
    social_logging.safe_log_info(
        "[STEP 13] Content LLM response received",
        user_id=user_id,
        has_caption=bool(content_response.caption),
        has_image_prompt=bool(content_response.image_prompt),
        has_carousel=bool(content_response.carousel_slides)
    )
    
    # Verify topic matches strategy phase (content phase must echo same topic)
    content_topic = content_response.topic or strat_data.get("topic", "")
    if content_topic != strat_data.get("topic", ""):
        social_logging.safe_log_warning(
            f"Topic mismatch: strategy={strat_data.get('topic')}, content={content_topic}",
            user_id=user_id
        )
        # Use strategy topic (canonical)
        content_topic = strat_data.get("topic", "")
    
    # image_prompt is always required (for single image or carousel cover/first slide)
    is_carousel = bool(content_response.carousel_slides and len(content_response.carousel_slides) > 0)
    image_prompt_value = (content_response.image_prompt or "").strip()
    if not image_prompt_value and is_carousel and content_response.carousel_slides:
        image_prompt_value = content_response.carousel_slides[0] or ""
    if not image_prompt_value:
        social_logging.safe_log_warning(
            "[STEP 13] Missing image_prompt",
            user_id=user_id,
            channel=content_response.channel,
            post_type=strat_data.get("post_type")
        )
        raise HTTPException(
            status_code=500,
            detail="LLM failed to generate image_prompt. image_prompt is required (use cover/first slide prompt for carousel)."
        )
    
    data = {
        "selected_category": content_response.selected_category or "",
        "selected_product_id": content_response.selected_product_id or "",
        "channel": content_response.channel,
        "caption": content_response.caption,
        "image_prompt": image_prompt_value,  # Always set (never null)
        "carousel_slides": content_response.carousel_slides,
        "needs_music": content_response.needs_music,
        "posting_time": content_response.posting_time,
        "notes": content_response.notes or "",
        "topic": content_topic,  # Use canonical topic from strategy phase
        "suggested_hashtags": getattr(content_response, "suggested_hashtags", None)  # §5
    }

    # Product details already fetched in product selection phase
    # Use product_details from selection phase if available

    # Include problem_identified in notes if available
    notes_with_problem = data.get("notes", "")
    if strat_data.get("problem_identified"):
        problem_note = f"Problema identificado: {strat_data.get('problem_identified')}"
        notes_with_problem = f"{problem_note}\n\n{notes_with_problem}" if notes_with_problem else problem_note

    # Normalize posting_time to HH:MM format (time only, not full datetime)
    posting_time_raw = data.get("posting_time", "")
    posting_time_normalized = None
    if posting_time_raw:
        try:
            # Try to parse various formats and extract time
            from datetime import datetime as dt_parse
            # If it's a full datetime string (ISO format)
            if 'T' in posting_time_raw or len(posting_time_raw) > 8:
                try:
                    parsed = dt_parse.fromisoformat(posting_time_raw.replace('Z', '+00:00'))
                    posting_time_normalized = parsed.strftime("%H:%M")
                except:
                    # Try other datetime formats
                    for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"]:
                        try:
                            parsed = dt_parse.strptime(posting_time_raw, fmt)
                            posting_time_normalized = parsed.strftime("%H:%M")
                            break
                        except:
                            continue
            # If it's already in HH:MM format
            elif len(posting_time_raw) == 5 and ':' in posting_time_raw:
                # Validate it's actually HH:MM
                parts = posting_time_raw.split(':')
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    posting_time_normalized = posting_time_raw
        except Exception as e:
            social_logging.safe_log_warning(
                "[STEP 14] Failed to parse posting_time, using as-is",
                user_id=user_id,
                posting_time_raw=posting_time_raw,
                error=str(e)
            )
            posting_time_normalized = posting_time_raw[:5] if len(posting_time_raw) >= 5 else posting_time_raw
    
    # Update data with normalized posting_time
    if posting_time_normalized:
        data["posting_time"] = posting_time_normalized

    # Record successful request for rate limiting
    social_rate_limit.record_request(user_id, "/generate")
    
    # AUTOMATICALLY SAVE THE POST (backend has all the data, no need for frontend to send it back)
    # Normalize and hash topic
    canonical_topic = data.get("topic", strat_data.get("topic", ""))
    if not canonical_topic:
        canonical_topic = "sin tema → sin solución"
        social_logging.safe_log_warning(
            f"No topic in generate response, using placeholder",
            user_id=user_id
        )
    
    normalized_topic = social_topic.normalize_topic(canonical_topic)
    topic_hash = social_topic.compute_topic_hash(normalized_topic)
    
    # Build formatted_content for storage
    # Determine hookType: "seasonality" on Fridays, "general" otherwise
    hook_type = "seasonality" if is_friday else "general"
    month_phase_actual = sales_context.get("phase", "general") if sales_context else "general"
    
    formatted_content = {
        "id": None,  # Will be set after save
        "postType": strat_data.get("post_type"),
        "channels": [strat_data.get("channel") or data.get("channel")],
        "hook": "Tendencias agrícolas",  # Default hook
        "hookType": hook_type,  # Only "seasonality" on Fridays, otherwise "general"
        "products": [product_details] if product_details else [],
        "tags": [],
        "instructions": notes_with_problem,
        "postingTime": data.get("posting_time"),  # Normalized to HH:MM format
        "generationSource": "llm",
        "strategyNotes": notes_with_problem,
        "carouselSlides": data.get("carousel_slides"),
        "needsMusic": data.get("needs_music", False),
        "generatedContext": {
            "monthPhase": month_phase_actual if needs_sales_context else "general",  # Only include phase on Tuesday and Friday
            "nearbyDates": [],
            "selectedCategories": [selected_category] if selected_category else []
        }
    }
    
    # Check if post already exists by topic_hash and date (avoid duplicates)
    social_logging.safe_log_info(
        "[STEP 15] Checking for existing post by topic_hash",
        user_id=user_id,
        topic_hash=topic_hash[:16] + "...",  # Log partial hash for debugging
        target_date=str(target_date)
    )
    existing_post = db.query(SocialPost).filter(
        SocialPost.topic_hash == topic_hash,
        SocialPost.date_for == target_date
    ).first()
    
    if existing_post:
        social_logging.safe_log_warning(
            "[STEP 15] Duplicate post detected - same topic_hash and date",
            user_id=user_id,
            existing_post_id=existing_post.id,
            existing_post_topic=existing_post.topic[:50] + "...",
            new_topic=normalized_topic[:50] + "..."
        )
    
    if existing_post:
        # Update existing post with new content
        social_logging.safe_log_info(
            "[STEP 15] Existing post found, updating",
            user_id=user_id,
            existing_post_id=existing_post.id
        )
        existing_post.caption = data.get("caption", "")
        existing_post.image_prompt = data.get("image_prompt")
        existing_post.post_type = strat_data.get("post_type")
        existing_post.content_tone = strat_data.get("content_tone")
        existing_post.selected_product_id = selected_product_id or data.get("selected_product_id")
        existing_post.channel = strat_data.get("channel") or data.get("channel")
        existing_post.carousel_slides = data.get("carousel_slides")
        existing_post.needs_music = data.get("needs_music", False)
        existing_post.formatted_content = formatted_content
        existing_post.topic = normalized_topic
        existing_post.topic_hash = topic_hash
        existing_post.problem_identified = strat_data.get("problem_identified", "")
        db.commit()
        db.refresh(existing_post)
        saved_post_id = existing_post.id
        social_logging.safe_log_info(
            "[STEP 15] Post updated successfully",
            user_id=user_id,
            post_id=saved_post_id
        )
    else:
        # Create new post
        social_logging.safe_log_info("[STEP 15] Creating new post", user_id=user_id)
        new_post = SocialPost(
            date_for=target_date,
            caption=data.get("caption", ""),
            image_prompt=data.get("image_prompt"),
            post_type=strat_data.get("post_type"),
            content_tone=strat_data.get("content_tone"),
            status="planned",
            selected_product_id=selected_product_id or data.get("selected_product_id"),
            formatted_content=formatted_content,
            channel=strat_data.get("channel") or data.get("channel"),
            carousel_slides=data.get("carousel_slides"),
            needs_music=data.get("needs_music", False),
            topic=normalized_topic,
            topic_hash=topic_hash,
            problem_identified=strat_data.get("problem_identified", "")
        )
        db.add(new_post)
        db.commit()
        db.refresh(new_post)
        saved_post_id = new_post.id
        # Update formatted_content with actual ID
        formatted_content["id"] = str(saved_post_id)
        new_post.formatted_content = formatted_content
        db.commit()
        social_logging.safe_log_info(
            "[STEP 15] Post created successfully",
            user_id=user_id,
            post_id=saved_post_id
        )
    
    return SocialGenResponse(
        caption=data.get("caption", ""),
        image_prompt=data.get("image_prompt") or "",  # Never null; required from LLM or fallback to first slide
        posting_time=data.get("posting_time"),
        notes=notes_with_problem,
        format=data.get("format"),
        cta=data.get("cta"),
        selected_product_id=selected_product_id or str(data.get("selected_product_id", "")),  # Use from product selection phase
        selected_category=selected_category or data.get("selected_category"),  # Use from product selection phase
        selected_product_details=product_details,  # From product selection phase
        post_type=strat_data.get("post_type"),  # From strategy phase
        content_tone=strat_data.get("content_tone"),  # From strategy phase
        channel=strat_data.get("channel") or data.get("channel"),  # From strategy phase, fallback to content phase
        carousel_slides=data.get("carousel_slides"),
        needs_music=data.get("needs_music"),
        topic=canonical_topic,  # Canonical topic from strategy phase
        problem_identified=strat_data.get("problem_identified", ""),  # From strategy phase
        saved_post_id=saved_post_id,  # Return the saved post ID
        viral_angle=strat_data.get("viral_angle"),  # Viral angle from pre-strategy phase
        suggested_hashtags=data.get("suggested_hashtags")
    )








