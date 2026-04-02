"""
Social content generator configuration.
All static rules, formats, and constraints extracted from prompts.
"""

# ===================================================================
# IMPAG BRAND CONTEXT
# Injected into topic and caption prompts so the LLM understands the
# full scope of what IMPAG offers — not just field inputs.
# ===================================================================

IMPAG_BRAND_CONTEXT = """IMPAG AGRICULTURA INTELIGENTE — CONTEXTO DE MARCA:
Empresa agropecuaria con sede en Nuevo Ideal, Durango. Atendemos a productores mexicanos en los tres niveles de su operación, desde el pequeño agricultor temporal hasta el agroindustrial.

NIVEL 1 — PRODUCCIÓN PRIMARIA (insumos y equipos de campo):
Mallasombra, invernaderos, sistemas de riego (cintilla, goteo, aspersión, valvulería), acolchado, charolas, sustratos, plásticos agrícolas, antiheladas, bombas, aspersoras, fertilizantes, agroquímicos, semillas, herramientas.

NIVEL 2 — VALOR AGREGADO Y PROCESAMIENTO (transformar la cosecha en producto):
Molinos y equipo de procesamiento agroindustrial, secadores solares, deshidratadores, equipos de tostado y pelado, soluciones de almacenamiento (silos, bodegas), materiales de empaque y envase para producto terminado.

NIVEL 3 — COMERCIALIZACIÓN Y POSICIONAMIENTO (llevar el producto al mercado):
Marketing agrícola, tecnología para posicionar productos y marcas, diseño de empaque retail, estrategia comercial para que el productor venda a mejor precio y con mayor control.

QUIÉN ES NUESTRO CLIENTE: Cualquier productor mexicano — el que siembra temporal, el que tiene invernadero, el que procesa su cosecha, el que quiere vender con marca propia. Estamos en todas las etapas.
"""

# ===================================================================
# CONTACT INFORMATION
# ===================================================================

CONTACT_INFO = {
    "web": "todoparaelcampo.com.mx",
    "whatsapp": "677-119-7737",
    "location": "Nuevo Ideal, Durango",
    "social": "@impag.tech",
    "email": "ventas@impag.tech"
}

# ===================================================================
# CHANNEL FORMAT SPECIFICATIONS
# ===================================================================

CHANNEL_FORMATS = {
    'wa-status': {
        'aspect_ratio': '9:16',
        'caption_max_chars': 50,
        'needs_music': True,
        'music_style': 'corridos mexicanos, regional',
        'priority': 'visual',  # Visual is priority, not caption
        'ephemeral': True,  # Disappears in 24h
        'duration': '15-30 segundos',
        'notes': 'El contenido visual/imagen debe comunicar TODO. Caption MÍNIMO O VACÍO.'
    },
    'wa-broadcast': {
        'aspect_ratio': '1:1',
        'caption_max_chars': 200,
        'needs_music': False,
        'priority': 'balanced',
        'notes': 'Caption corto pero informativo'
    },
    'wa-message': {
        'aspect_ratio': '1:1',
        'caption_max_chars': 500,
        'needs_music': False,
        'priority': 'text',
        'notes': 'Texto conversacional, personal'
    },
    'fb-post': {
        'aspect_ratio': '1:1',
        'caption_max_chars': 2000,
        'carousel_max_slides': 10,
        'needs_music': False,
        'priority': 'balanced',
        'auto_replicate': 'ig-post',  # Se replica automáticamente a Instagram
        'notes': 'Caption LARGO permitido. Ideal para infografías, carruseles educativos'
    },
    'ig-post': {
        'aspect_ratio': '1:1',
        'caption_max_chars': 2000,
        'carousel_max_slides': 10,
        'needs_music': False,
        'priority': 'balanced',
        'notes': 'Mismo formato que fb-post'
    },
    'fb-reel': {
        'aspect_ratio': '9:16',
        'video_duration': '15-90 segundos',
        'caption_max_chars': 100,
        'needs_music': True,
        'music_style': 'trending o mexicana',
        'priority': 'visual',
        'auto_replicate': 'ig-reel',
        'notes': 'Caption MUY CORTO. El texto principal va EN EL VIDEO con subtítulos. Hook en primeros 3 segundos'
    },
    'ig-reel': {
        'aspect_ratio': '9:16',
        'video_duration': '15-90 segundos',
        'caption_max_chars': 100,
        'needs_music': True,
        'music_style': 'trending o mexicana',
        'priority': 'visual',
        'notes': 'Mismo formato que fb-reel'
    },
    'fb-story': {
        'aspect_ratio': '9:16',
        'caption_max_chars': 50,
        'needs_music': False,
        'priority': 'visual',
        'ephemeral': True,
        'notes': 'Caption MÍNIMO O VACÍO. El contenido visual debe comunicar TODO'
    },
    'ig-story': {
        'aspect_ratio': '9:16',
        'caption_max_chars': 50,
        'needs_music': False,
        'priority': 'visual',
        'ephemeral': True,
        'notes': 'Caption MÍNIMO O VACÍO. El contenido visual debe comunicar TODO'
    },
    'tiktok': {
        'aspect_ratio': '9:16',
        'format_type': 'carousel',  # CARRUSEL DE 2-3 IMÁGENES (NO video)
        'carousel_slides': [2, 3],  # 2-3 images
        'caption_max_chars': 150,
        'needs_music': True,
        'music_style': 'corridos mexicanos, regional popular',
        'priority': 'visual',  # TODO EL TEXTO PRINCIPAL VA EN LAS IMÁGENES, NO en caption
        'notes': 'Caption MUY CORTO (SOLO hashtags o texto mínimo). Las imágenes con texto grande son lo importante.',
        'structure': {
            'slide_1': 'HOOK/Problema (primera imagen engancha con texto grande visible)',
            'slide_2': 'CONTENIDO/Solución (texto en imagen)',
            'slide_3': 'CTA/Contacto (texto en imagen)'
        }
    }
}

# ===================================================================
# WEEKDAY THEMES & RECOMMENDED POST TYPES
# ===================================================================

WEEKDAY_THEMES = {
    'Monday': {
        'day_name': 'Monday',
        'theme': '✊ Motivational / Inspirational',
        'content_type': 'Inspiring quote or message for agriculture/forestry producers',
        'primary_tone': 'Motivational',
        'alternative_tones': ['Inspirational', 'Encouraging', 'Humorous'],
        'recommended_post_types': [
            'Motivational Phrase or Quote of the Week',
            'Memes/tips rápidos',
            'Image / Photo of the Week'
        ],
        'product_strategy': 'educational_only',  # No products
        'sector_rotation': None,
        'generate_multiple_posts': True,  # Monday generates 2 posts
        'second_post_config': {
            'day_name': 'Monday',  # Required by topic engine
            'theme': '🌾 La Vida en el Rancho',
            'content_type': 'Personal stories and anecdotes from ranch life',
            'primary_tone': 'Nostalgic',
            'alternative_tones': ['Reflective', 'Personal', 'Storytelling'],
            'recommended_post_types': [
                'Personal Story / Anecdote',
                'Ranch Life Reflection',
                'Photo of the Week with Story'
            ],
            'product_strategy': 'educational_only',  # No products for second post
            'sector_rotation': None,  # No sector rotation for Monday
            'style_notes': 'Focus on authentic ranch experiences, daily life moments, traditions, and personal connections to the land. Tone should be warm, genuine, and reflective.'
        }
    },
    'Tuesday': {
        'day_name': 'Tuesday',
        'theme': '💸 Promotion / Deals',
        'content_type': 'Highlight a product with a special price, bundle, or seasonal offer',
        'primary_tone': 'Promotional',
        'alternative_tones': ['Sales-focused', 'Urgent', 'Humorous'],
        'recommended_post_types': [
            'Promoción puntual',
            'Kits',
            '"Lo que llegó hoy"',
            'Cómo pedir / logística',
            'Recordatorio de servicio'
        ],
        'product_strategy': 'required',  # Always requires product
        'sector_rotation': None
    },
    'Wednesday': {
        'day_name': 'Wednesday',
        'theme': '📚 Educational / Tips',
        'content_type': 'Tips, guides, how-tos, or educational content for farmers',
        'primary_tone': 'Educational',
        'alternative_tones': ['Technical', 'Informative', 'Humorous'],
        'recommended_post_types': [
            'Infografías de producto o tema',
            'Tutorial corto',
            'Pro Tip',
            'Interesting Fact',
            'Article',
            'Sabías que...'
        ],
        'product_strategy': 'educational_only',  # No products
        'sector_rotation': None
    },
    'Thursday': {
        'day_name': 'Thursday',
        'theme': '🛠️ Problem & Solution',
        'content_type': 'Infographic showing how one of your products solves a real problem',
        'primary_tone': 'Problem-Solving',
        'alternative_tones': ['Technical', 'Solution-focused', 'Educational'],
        'recommended_post_types': [
            'Infografías',
            'Caso de éxito',
            'Antes / Después'
        ],
        'product_strategy': 'flexible',  # Can include products
        'sector_rotation': None
    },
    'Friday': {
        'day_name': 'Friday',
        'theme': '📅 Seasonal Focus',
        'content_type': 'Advice or alerts based on regional crop/livestock/forestry seasons',
        'primary_tone': 'Seasonal',
        'alternative_tones': ['Educational', 'Informative', 'Technical'],
        'recommended_post_types': [
            'Infografías',
            'Tutorial corto',
            'Checklist operativo',
            'Recordatorio de servicio',
            'Seasonal weather tips: what to expect & how to act'
        ],
        'product_strategy': 'flexible',  # Can include products
        'sector_rotation': None
    },
    'Saturday': {
        'day_name': 'Saturday',
        'theme': '👩‍🌾 Producer Segment Focus',
        'content_type': 'Generate content for ALL 3 sectors: forestry 🌲, plant 🌾, animal 🐄',
        'primary_tone': 'Educational',
        'alternative_tones': ['Technical', 'Practical', 'Humorous'],
        'recommended_post_types': [
            'Infografías',
            'FAQ / Mitos',
            'Pro Tip',
            'Interesting Fact',
            'Tutorial corto',
            'Recordatorio de servicio'
        ],
        'product_strategy': 'educational_only',  # No products
        'sector_rotation': None,  # No rotation - generate all 3 sectors
        'generate_multiple_posts': True,  # Saturday generates 3 posts (one per sector)
        'sector_posts': [
            {
                'day_name': 'Saturday',
                'sector': 'forestry',
                'theme': '🌲 Forestal - Bosques y Viveros',
                'content_type': 'Sector-specific problems, solutions, and technical guidance for forestry producers',
                'primary_tone': 'Technical',
                'alternative_tones': ['Practical', 'Educational', 'Problem-Solving'],
                'recommended_post_types': [
                    'Pro Tip Forestal',
                    'FAQ Forestry',
                    'Tutorial forestal',
                    'Problema y Solución Forestal'
                ],
                'product_strategy': 'educational_only',
                'emotional_angle': 'Long-term vision and patience - forestry as generational investment',
                'problem_focus': [
                    'Fire prevention and management (Jan-Jun critical)',
                    'Reforestation survival rates',
                    'Nursery water management',
                    'Pest control in pine/oak systems',
                    'Low diversification and value-added products',
                    'Seasonal supply disruption to sawmills'
                ],
                'technical_depth': 'High - include specific species (pine, oak), technical practices, and regional data',
                'durango_context': 'Durango: 4.0M hectares forest use, ~4.17M m³ pine + 0.80M m³ oak annually, leader in sawmilling ($1,512M MX)'
            },
            {
                'day_name': 'Saturday',
                'sector': 'plant',
                'theme': '🌾 Plantas y Cultivos - Agricultura Vegetal',
                'content_type': 'Sector-specific problems, solutions, and technical guidance for crop producers',
                'primary_tone': 'Practical',
                'alternative_tones': ['Technical', 'Educational', 'Problem-Solving'],
                'recommended_post_types': [
                    'Pro Tip Agrícola',
                    'FAQ Cultivos',
                    'Tutorial cultivos',
                    'Problema y Solución Agrícola'
                ],
                'product_strategy': 'educational_only',
                'emotional_angle': 'Seasonal anxiety and timing precision - rainfed dependence as existential risk',
                'problem_focus': [
                    'Rainfed dependence (79% temporal) and drought risk',
                    'Low bean yields despite large hectares',
                    'Protected agriculture scale-up challenges',
                    'Soil fertility loss (34% of producers)',
                    'High input costs (94.9% dominant problem)',
                    'Irrigation efficiency for forage crops'
                ],
                'technical_depth': 'High - include crop calendars, yield data, regional challenges (frijol 301k ha, maíz forrajero 2.3M t)',
                'durango_context': 'Durango: 79% rainfed (746k ha), forage-dominant (91% tonnage), beans low yield, Primavera-Verano cycle critical'
            },
            {
                'day_name': 'Saturday',
                'sector': 'animal',
                'theme': '🐄 Ganadería - Producción Animal',
                'content_type': 'Sector-specific problems, solutions, and technical guidance for livestock/dairy producers',
                'primary_tone': 'Practical',
                'alternative_tones': ['Technical', 'Educational', 'Problem-Solving'],
                'recommended_post_types': [
                    'Pro Tip Ganadero',
                    'FAQ Ganadería',
                    'Tutorial ganadero',
                    'Problema y Solución Ganadera'
                ],
                'product_strategy': 'educational_only',
                'emotional_angle': 'Daily grind and economics - dairy/livestock as operating system, not just animals',
                'problem_focus': [
                    'Feed cost management (forage efficiency)',
                    'Heat stress and summer dairy seasonality',
                    'Water systems for livestock',
                    'Dairy processing vertical integration opportunities',
                    'Manure management and waste-to-value',
                    'Animal health preventive care'
                ],
                'technical_depth': 'High - include herd economics, milk production data, feed ratios, Comarca Lagunera context',
                'durango_context': 'Durango: 1.58M cattle, 5.6M liters milk/day, 3rd nationally (11.4%), Comarca Lagunera anchor, forage-livestock system'
            }
        ]
    },
    'Sunday': {
        'day_name': 'Sunday',
        'theme': '📊 Innovation / Industry Reports',
        'content_type': 'Industry news, agri-innovation, or trending novelty in agriculture',
        'primary_tone': 'Informative',
        'alternative_tones': ['Technical', 'Educational', 'Humorous'],
        'recommended_post_types': [
            'Industry novelty',
            'Trivia agrotech-style post',
            'Statistics or report highlights relevant to audience'
        ],
        'product_strategy': 'educational_only',  # No products
        'sector_rotation': None
    }
}

# ===================================================================
# POST TYPES DEFINITIONS
# ===================================================================

POST_TYPES = [
    'Infografías',
    'Fechas importantes',
    'Memes/tips rápidos',
    'Promoción puntual',
    'Kits',
    'Caso de éxito / UGC',
    'Antes / Después',
    'Checklist operativo',
    'Tutorial corto / "Cómo se hace"',
    '"Lo que llegó hoy"',
    'FAQ / Mitos',
    'Seguridad y prevención',
    'ROI / números rápidos',
    'Convocatoria a UGC',
    'Recordatorio de servicio',
    'Cómo pedir / logística',
    'Motivational Phrase or Quote of the Week',
    'Image / Photo of the Week',
    'Pro Tip',
    'Interesting Fact',
    'Article',
    'Sabías que...',
    'Seasonal weather tips: what to expect & how to act',
    'Industry novelty',
    'Trivia agrotech-style post',
    'Statistics or report highlights relevant to audience',
    'Personal Story / Anecdote',
    'Ranch Life Reflection',
    'Photo of the Week with Story'
]

POST_TYPES_DESCRIPTIONS = {
    'Infografías': 'Explicar rápido (riego, acolchado). Versión resumida para Reels.',
    'Fechas importantes': 'Anclar promos o recordatorios (Día del Agricultor, heladas).',
    'Memes/tips rápidos': 'Humor educativo (errores comunes).',
    'Promoción puntual': 'Liquidar overstock o empujar alta rotación.',
    'Kits': 'Combo de productos (solución completa, ej. kit riego).',
    'Caso de éxito / UGC': 'Prueba social (instalaciones, resultados).',
    'Antes / Después': 'Demostrar impacto visual.',
    'Checklist operativo': 'Guía de acciones por temporada (previo a helada, arranque riego).',
    'Tutorial corto / "Cómo se hace"': 'Educar en 30–45s.',
    '"Lo que llegó hoy"': 'Novedades y entradas de inventario.',
    'FAQ / Mitos': 'Remover objeciones (costos, duración).',
    'Seguridad y prevención': 'Cuidado de personal/equipo.',
    'ROI / números rápidos': 'Justificar inversión con datos.',
    'Convocatoria a UGC': 'Pedir fotos/video de clientes.',
    'Recordatorio de servicio': 'Mantenimiento (lavado filtros, revisión bomba).',
    'Cómo pedir / logística': 'Simplificar proceso de compra.',
    'Personal Story / Anecdote': 'Historias personales de la vida en el rancho, experiencias auténticas.',
    'Ranch Life Reflection': 'Reflexiones sobre la vida rural, tradiciones y valores del rancho.',
    'Photo of the Week with Story': 'Foto destacada con historia personal del rancho.'
}

# ===================================================================
# CONTENT RULES (§8)
# ===================================================================

CONTENT_RULES = [
    "NUNCA inventes porcentajes, cifras o estadísticas que no estén en el contexto proporcionado. Si no tienes el dato real, describe el efecto de forma cualitativa: 'puede dañar la cosecha', 'reduce el rendimiento', 'genera pérdidas evitables'.",
    "Usa SOLO los datos numéricos del contexto entregado (estadísticas de Durango, datos de productos). No agregues números propios como si fueran hechos establecidos.",
    "Beneficios comparativos sin inventar cifras: 'consume menos agua que riego por surco', 'distribución más uniforme que con aspersor tradicional'.",
    "No exagerar especificaciones del producto; usar lenguaje preciso.",
    "Solución = contexto + producto, no solo producto (práctica correcta + producto).",
    "Producto como componente central de la solución, no único héroe."
]

# ===================================================================
# FEW-SHOT EXAMPLES FOR USER-SUGGESTED TOPICS
# These calibrate caption quality when the user provides a specific topic.
# Keyed by weekday — inject the matching example to show tone, depth, and format.
# ===================================================================

FEW_SHOT_USER_TOPIC_EXAMPLES = {
    'Thursday': {
        'topic_hint': 'chile pasado',
        'caption': """El chile pasado mal secado no se ve diferente al bueno hasta que ya es tarde.

El proceso artesanal del El Sereno — sol durante el día, adentro en la noche — funciona cuando el clima coopera. Pero en Durango, una lluvia nocturna fuera de temporada o una semana de cielos nublados en pleno proceso puede arruinar un lote completo sin que lo notes hasta que ya tiene humedad atrapada y empieza a engrasar.

El problema no es el método. Es la falta de control sobre las condiciones de secado.

Lo que marca la diferencia en el secado de chile pasado:

1. ESTRUCTURA DE SECADO — Un techo translúcido con plástico UV permite el sol sin exponer el chile a lluvia o rocío nocturno
2. FLUJO DE AIRE — Ventilación lateral con mallasombra evita condensación y acelera el secado uniforme
3. VOLTEO REGULAR — Cada 24 horas para que el secado sea parejo en toda la superficie
4. MEDICIÓN — El chile pasado listo tiene entre 12-15% de humedad; sin medidor, estás adivinando

Un secador simple con materiales accesibles puede darte control sobre el proceso que el método artesanal no tiene.

¿Produces chile pasado o conoces a alguien que lo hace? Platícanos cómo lo manejas.

📲 677-119-7737
🌐 todoparaelcampo.com.mx

#ChilePasado #Durango #ValorAgregado #IMPAG #ProcesamientoAgrícola"""
    },
    'Tuesday': {
        'topic_hint': 'miel de abeja',
        'caption': """En verano, tus colmenas están trabajando de más — y no precisamente produciendo miel.

Cuando la temperatura dentro de la colmena sube demasiado, las abejas dejan de pecorear y se dedican a ventilar: forman cadenas en la piquera y baten las alas para enfriar. Es trabajo que no produce nada y agota a la colonia.

Una colmena bajo sol directo en julio en Durango puede pasar horas en modo enfriamiento en lugar de modo producción.

La solución es más simple de lo que parece:

Instalar mallasombra al 50% sobre el área del apiario — orientada para bloquear el sol de las 11am a 4pm — reduce la carga térmica sobre las colmenas sin bloquear el vuelo ni la ventilación natural.

No necesitas estructura elaborada: postes, alambre tensor y mallasombra es suficiente para proteger 10-20 colmenas.

Tenemos mallasombra por metro o en rollos completos.

📲 677-119-7737
📍 Nuevo Ideal, Durango
🌐 todoparaelcampo.com.mx

#Apicultura #MielDurango #Apiario #Mallasombra #IMPAG"""
    },
    'Wednesday': {
        'topic_hint': 'vaca lechera',
        'caption': """Los 21 días antes y 21 días después del parto definen el éxito de toda la siguiente lactancia — y son los que más productores manejan con menos atención.

Lo que pasa en el período de transición:

La vaca reduce su consumo de materia seca justo cuando más energía necesita. El cuerpo empieza a movilizar grasa como combustible. Si esto dura demasiado o es muy intenso, el hígado se satura — y viene cetosis, hígado graso, metritis, desplazamiento de abomaso.

No son enfermedades de mala suerte. Son el resultado predecible de un período de transición mal manejado.

Lo que sí funciona:

1. DIETA PRECEBO — 3 semanas antes del parto, acostumbrar el rumen a la dieta de lactancia. No cambiar de golpe.
2. ESPACIO Y ESTRÉS — Vacas en transición necesitan comedero y bebedero sin competencia. El estrés social baja el consumo.
3. CONDICIÓN CORPORAL — Al parto: idealmente 3.0-3.5. Más grasa = más riesgo de hígado graso.
4. CALCIO DISPONIBLE — Hipocalcemia subclínica es más común de lo que se diagnostica y frena el arranque de lactancia.

Una vaca que arranca bien la lactancia produce más en todo el ciclo. Una que arranca mal nunca alcanza su potencial aunque el resto del manejo sea bueno.

¿Cómo manejas el secado y la transición en tu hato?

📲 677-119-7737
🌐 todoparaelcampo.com.mx

#VacaLechera #GanaderíaDurango #ComarcaLagunera #IMPAG #ProducciónLáctea"""
    },
    'Friday': {
        'topic_hint': 'chile pasado',
        'caption': """Durango produce chile. Mucho chile.
Pero la mayoría sale fresco al mercado, donde el precio lo pone el comprador y la competencia es de todos contra todos.

El chile pasado cambia esa ecuación.

Es el mismo chile — poblano o chilaca — pero con un proceso artesanal de tostado y secado que lo transforma en un ingrediente de identidad regional. El caldillo durangueño no existe sin él. Los restaurantes que lo sirven lo buscan con calidad consistente y están dispuestos a pagar un precio diferente al del chile fresco de temporada.

Abril y mayo son el momento de empezar a planear:

— ¿Cuánto chile vas a destinar a proceso este ciclo?
— ¿Tienes la infraestructura para secar con consistencia?
— ¿Ya tienes comprador o vendes al mercado spot?

El productor que llega a la cosecha con un comprador de chile pasado ya amarrado y una estructura de secado lista opera en condiciones completamente distintas al que vende fresco sin contrato.

El valor agregado no empieza en la cosecha. Empieza en la planeación.

¿Produces chile en Durango? Cuéntanos cómo lo comercializas.

📲 677-119-7737
🌐 todoparaelcampo.com.mx

#ChilePasado #DurangoAgricola #ValorAgregado #IMPAG #AgriculturaInteligente"""
    },
    'Monday': {
        'topic_hint': 'general',
        'caption': """La troca sigue estacionada en el mismo lugar.
La mesa tiene los mismos lugares de siempre.
Pero hay sillas que ya nadie jala.

No es que se hayan ido peleados.
Es que la ciudad prometió más de lo que el rancho podía ofrecer.
Y uno los dejó ir, porque así es querer.

Lo difícil no es el trabajo.
Lo difícil es trabajar para algo que no sabes si alguien va a querer continuar.

Pero uno sigue levantándose.
Porque el rancho no espera,
y porque en el fondo uno todavía cree
que algún día van a entender
lo que vale lo que aquí se construyó.

Solo quien vive del campo entiende ese peso.

🌾 IMPAG — Agricultura Inteligente
Nuevo Ideal, Durango"""
    },
    'Saturday_plant': {
        'topic_hint': 'frijol temporal',
        'caption': """Durango tiene 301,375 hectáreas de frijol.
Y sin embargo, los rendimientos siguen siendo de los más bajos del país.

No es un problema de tierra. Es un problema de timing y de decisiones en los primeros 30 días del ciclo.

Con 79% de superficie temporal — completamente dependiente de lluvia — el margen de error es mínimo. La ventana de siembra correcta en Durango es estrecha: demasiado temprano y el suelo no tiene humedad suficiente para una germinación uniforme; demasiado tarde y las heladas de octubre cortan el ciclo antes de madurez.

Lo que define el rendimiento antes de que caiga la primera gota:

1. VARIEDAD CORRECTA — No toda semilla certificada es igual. La variedad debe estar adaptada al ciclo de lluvias de tu zona específica dentro de Durango. Pinto Saltillo, Flor de Junio, Flor de Mayo — cada una tiene su ventana.

2. DENSIDAD DE SIEMBRA — En temporal, sembrar de más es tan dañino como sembrar de menos. Las plantas compiten por la misma agua. Calcula la densidad según el ciclo esperado de lluvia, no según el año anterior.

3. FERTILIZACIÓN BASAL — El fósforo y potasio van al fondo del surco antes de sembrar. Si esperas a que llueva para fertilizar, ya perdiste las primeras semanas críticas de desarrollo radicular.

4. CONTROL DE MALEZA TEMPRANO — Los primeros 21 días, la maleza compite directamente con la plántula por humedad. Un preemergente aplicado correctamente puede definir el stand del ciclo completo.

El temporal no perdona errores de preparación. Lo que se hace en abril define lo que se cosecha en octubre.

¿Qué variedad siembras en tu zona?

📲 677-119-7737
🌐 todoparaelcampo.com.mx

#FrijolTemporal #AgriculturaDurango #Siembra2026 #IMPAG #AgriculturaInteligente"""
    },
    'Saturday_animal': {
        'topic_hint': 'ganadería lechera',
        'caption': """En la Comarca Lagunera se producen alrededor de 5.6 millones de litros de leche al día.
Eso representa el 21.7% de la producción láctea nacional — desde Durango y Coahuila.

Con esos volúmenes, un punto porcentual de eficiencia en conversión de forraje representa millones de litros al año.

El problema que más dinero cuesta y menos se mide: la calidad del agua para el hato.

Una vaca de alta producción consume entre 100 y 150 litros de agua al día. En verano, bajo estrés térmico, ese número sube. Y cuando el agua está turbia, tibia o contaminada, la vaca bebe menos — y lo primero que cae es la producción láctea, no la salud visible del animal.

Lo que muchos ganaderos no conectan: la vaca que "produce menos en verano" no siempre es estrés térmico. A veces es simplemente que no está bebiendo suficiente.

Puntos críticos en el sistema de agua para ganado lechero:

1. BEBEDEROS — Limpios, a la sombra, con flujo suficiente para que varias vacas beban simultáneamente sin competencia
2. CALIDAD — Agua con algas, sedimento o contaminación bacteriana reduce el consumo voluntario
3. TEMPERATURA — Agua fresca aumenta el consumo. En sistemas expuestos al sol, la temperatura del agua puede subir considerablemente al mediodía
4. ACCESO — En corrales densamente poblados, las vacas de menor jerarquía no llegan al bebedero. Más puntos de acceso = consumo más uniforme del hato

La producción láctea es directamente proporcional al consumo de agua. No hay suplemento que compense un sistema de agua deficiente.

¿Cómo tienes tu sistema de agua en el corral?

📲 677-119-7737
🌐 todoparaelcampo.com.mx

#GanaderíaLechera #ComarcaLagunera #ProducciónLáctea #IMPAG #Durango"""
    },
    'Saturday_forestry': {
        'topic_hint': 'forestal',
        'caption': """Durango es el estado con mayor producción forestal del país.
4 millones de hectáreas con uso forestal. Líder nacional en aserrado de pino y encino.

Y cada año, entre abril y junio, parte de eso arde.

La temporada de incendios forestales en Durango no es una sorpresa — es un calendario. El mismo periodo, los mismos factores: vegetación seca de invierno, vientos de primavera, humedad baja antes de que lleguen las lluvias de junio. Lo que cambia es qué tan preparado está cada predio cuando llega.

Los incendios que destruyen superficies grandes casi siempre tienen algo en común: nadie los detectó a tiempo o no había forma de contenerlos en las primeras horas.

Lo que sí está en manos del productor forestal:

1. BRECHAS CORTAFUEGO — Mantenidas y ampliadas antes de abril. Una brecha descuidada no detiene nada.

2. HERRAMIENTA EN CAMPO — Palas, azadones, bombas de mochila con agua. El primer equipo en llegar al foco define si se controla en media hectárea o en cincuenta.

3. VIGILANCIA EN HORAS CRÍTICAS — Los incendios inician más entre las 12 y las 17 horas, con viento. Ese es el horario de mayor riesgo.

4. COORDINACIÓN CON VECINOS — Un incendio que entra de predio vecino no respeta linderos. La comunicación anticipada puede ser la diferencia entre una llamada a tiempo y una pérdida total.

5. REGISTRO Y REPORTE — Conocer los puntos de mayor riesgo dentro del predio y tener el contacto de CONAFOR actualizado.

La madera que no se quema es la que llega al aserradero. La prevención es la inversión forestal más barata que existe.

¿Tienes tus brechas listas para este ciclo?

📲 677-119-7737
🌐 todoparaelcampo.com.mx

#ForestalDurango #IncendiosForestales #PrevenciónForestal #IMPAG #ManejoForestal"""
    },
    'Sunday': {
        'topic_hint': 'bombeo solar',
        'caption': """Hace cinco años, instalar un sistema de bombeo solar en una parcela era una conversación para grandes productores o proyectos con subsidio.

Hoy es una decisión que cualquier productor con más de 5 hectáreas debería estar evaluando.

¿Qué cambió? El costo de los paneles cayó. La tecnología de inversores y bombas se estandarizó. Y los productores que instalaron hace tres años ya están viendo los números reales — no las proyecciones del vendedor.

Lo que un sistema de bombeo solar realmente resuelve en una operación agrícola en Durango:

EL PROBLEMA QUE SÍ RESUELVE:
Riego en parcelas sin acceso a electricidad de la CFE, o donde el costo de la tarifa agrícola hace el riego prohibitivo en los meses de mayor demanda (mayo-agosto).

EL PROBLEMA QUE NO RESUELVE:
No es para riego continuo de alta demanda sin almacenamiento. Un sistema sin tanque de reserva riega mientras hay sol — y para cuando más se necesita el agua (madrugada, tarde noche), no bombea.

LO QUE DETERMINA SI EL ROI TIENE SENTIDO:
— ¿Cuántas horas de bombeo necesitas al día?
— ¿Tienes dónde almacenar el agua bombeada durante el día?
— ¿Tu cultivo tolera el horario de bombeo solar o necesita riego nocturno?

Un sistema bien dimensionado para las condiciones reales del predio tiene retorno en 2-4 ciclos. Uno mal dimensionado es un gasto que no rinde.

Antes de comprar, dimensiona. Antes de dimensionar, mide tu consumo real.

¿Estás evaluando bombeo solar para este ciclo?

📲 677-119-7737
🌐 todoparaelcampo.com.mx

#BombeoSolar #EnergíaSolar #AgriculturaDurango #IMPAG #TecnologíaAgrícola"""
    },
}

# ===================================================================
# SPECIAL DATES (Mexican National Holidays & Agricultural Days)
# ===================================================================

SPECIAL_DATES = {
    # Mexican National Holidays & Social Dates
    (1, 1): {'name': 'Año Nuevo', 'type': 'holiday'},
    (2, 5): {'name': 'Día de la Constitución', 'type': 'holiday'},
    (3, 8): {'name': 'Día Internacional de la Mujer', 'type': 'social'},
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

# ===================================================================
# AVAILABLE TONES
# ===================================================================

AVAILABLE_TONES = [
    'Motivational',      # Inspirador, alentador, que motive a los productores
    'Promotional',       # Enfocado en ventas, ofertas, productos
    'Technical',         # Técnico, detallado, con especificaciones y datos
    'Educational',       # Educativo, informativo, que enseñe algo nuevo
    'Problem-Solving',   # Enfocado en resolver problemas específicos
    'Seasonal',          # Relacionado con temporadas, ciclos, fechas importantes
    'Humorous',          # Divertido, ligero, con humor apropiado para agricultura
    'Informative',       # Informativo, noticioso, con datos y estadísticas
    'Inspirational',     # Inspirador, que genere emociones positivas
    'Nostalgic',         # Nostálgico, que evoque recuerdos y tradiciones
    'Reflective',        # Reflexivo, pensativo, introspectivo
    'Personal',          # Personal, íntimo, auténtico
    'Storytelling'       # Narrativo, que cuenta una historia
]

# ===================================================================
# SATURDAY SECTOR ROTATION
# ===================================================================

SATURDAY_SECTORS = ['forestry', 'plant', 'animal']

# ===================================================================
# DURANGO SEASONALITY CONTEXT (FOR FRIDAY POSTS)
# ===================================================================

DURANGO_SEASONALITY_CONTEXT = """Durango — Contexto Agrícola Regional (referencia para posts de viernes)

CLIMA Y CICLOS:
- Lluvia: ~500 mm/año, concentrada en verano (jun–sep). Sistema temporal domina (79% del área agrícola).
- Ciclo principal: Primavera–Verano. Riego reservado a forrajes y horticultura de alto valor.
- Temperatura media ~17°C. Sequía primaveral frecuente (feb–may).

CALENDARIOS DE SIEMBRA CLAVE:
- Maíz forrajero (riego): siembra mar 20–abr 15 (primavera) o jun 20–jul 30 (verano)
- Sorgo forrajero (riego): siembra mar 20–abr 30 (primavera) o jun 1–jul 15 (verano)
- Maíz temporal: siembra con lluvias, límite ~25 jul (después se destina a forraje)
- Frijol temporal: siembra jun–jul, cosecha otoño (301k ha, rendimiento bajo por sequía)
- Alfalfa: producción perenne, pilar del sistema lechero
- Manzana (zona Canatlán/Nuevo Ideal): cosecha jul–ago

GANADERÍA Y LÁCTEOS (eje económico del estado):
- ~1.58 millones de cabezas bovinas; ~5.6 millones litros de leche/día
- 3er productor nacional de leche (11.4% nacional, 2023); Comarca Lagunera = 21.7% nacional
- La cadena láctea depende directamente de forrajes: alfalfa, maíz, sorgo, avena forrajera
- Producción de leche sube en verano (más forraje disponible)

AGRICULTURA PROTEGIDA (pequeña pero concentrada):
- Solo 389 ha: 39.7% mallasombra, 36.4% invernadero
- Tomate: 84.2% de su producción total viene de ag. protegida
- Oportunidad real en zonas con agua segura: tomate, pimientos, berries

SECTOR FORESTAL:
- 4 millones de ha con aprovechamiento forestal (mayor que el área agrícola)
- Pino (~4.17M m³) y encino (~0.80M m³) dominan
- Temporada de incendios: ene–jun, crítico abr–jun (estiaje)
- Durango lidera México en producción bruta de aserraderos

PRINCIPALES PRESIONES DEL PRODUCTOR:
- Costos altos de insumos y servicios (94.9% lo reportan como problema principal)
- Solo 8.5% accede a crédito formal
- Base productora envejecida (73.8% mayores de 45 años)

OPORTUNIDADES DE VALOR AGREGADO:
- Lácteos: integración vertical (quesos, procesados, biogás de estiércol)
- Manzana: empaque, cadena de frío, industrialización de fruta de rechazo
- Hortalizas protegidas donde hay agua segura
- Productos con identidad regional: chile pasado, frijol pinto, manzana serrana
"""

SECTOR_EMOJIS = {
    'forestry': '🌲',
    'plant': '🌾',
    'animal': '🐄'
}

SECTOR_NAMES = {
    'forestry': 'Forestal',
    'plant': 'Plantas/Cultivos',
    'animal': 'Ganadería'
}

SECTOR_EXAMPLES = {
    'forestry': [
        'Cómo almacenar agua para tus viveros forestales',
        'Pro Tip: Mejores prácticas para viveros'
    ],
    'plant': [
        'Riego eficiente con accesorios que sí duran',
        'FAQ: ¿Cuándo es mejor momento para fertilizar?'
    ],
    'animal': [
        'Evita fugas con abrazaderas resistentes para sistemas de agua para ganado',
        'Interesting Fact: El agua representa X% del costo de producción'
    ]
}

# ===================================================================
# HELPER FUNCTIONS
# ===================================================================

def get_saturday_sector(week_number: int) -> str:
    """
    Get the sector for Saturday posts based on week number.

    Args:
        week_number: ISO week number

    Returns:
        Sector string: 'forestry', 'plant', or 'animal'
    """
    return SATURDAY_SECTORS[week_number % 3]

def get_channel_format_brief(channel: str) -> str:
    """
    Get brief format instructions for a channel.

    Args:
        channel: Channel name (e.g., 'fb-post', 'tiktok')

    Returns:
        Brief format string for prompt
    """
    fmt = CHANNEL_FORMATS.get(channel, {})
    if not fmt:
        return ""

    brief = f"Format: {fmt['aspect_ratio']}, caption max {fmt['caption_max_chars']} chars"
    if fmt.get('needs_music'):
        brief += f", música {fmt['music_style']}"
    if fmt.get('priority'):
        brief += f", prioridad: {fmt['priority']}"

    return brief

def get_weekday_theme_by_date(dt) -> dict:
    """
    Get weekday theme for a given date.

    Args:
        dt: datetime object

    Returns:
        Weekday theme dict from WEEKDAY_THEMES
    """
    day_name = dt.strftime('%A')
    theme = WEEKDAY_THEMES.get(day_name)

    if theme and theme.get('sector_rotation') == 'rotate':
        # Calculate Saturday sector rotation
        week_num = dt.isocalendar()[1]
        theme = theme.copy()  # Don't modify original
        theme['sector_rotation'] = get_saturday_sector(week_num)

    return theme

def get_special_date(month: int, day: int, weekday: int = None) -> dict:
    """
    Check if date is a special date.

    Args:
        month: Month (1-12)
        day: Day (1-31)
        weekday: Weekday (0=Monday, 6=Sunday) for Día del Padre check

    Returns:
        Special date dict or None
    """
    # Check for exact date match
    if (month, day) in SPECIAL_DATES:
        special = SPECIAL_DATES[(month, day)]
        return {
            'is_special_date': True,
            'special_date_name': special['name'],
            'special_date_type': special['type'],
            'recommended_post_type': 'Fechas importantes'
        }

    # Check for Día del Padre (3rd Sunday of June)
    if month == 6 and weekday == 6:  # Sunday
        week_of_month = (day - 1) // 7 + 1
        if week_of_month == 3:
            return {
                'is_special_date': True,
                'special_date_name': 'Día del Padre',
                'special_date_type': 'social',
                'recommended_post_type': 'Fechas importantes'
            }

    return None
