"""
Social content generator configuration.
All static rules, formats, and constraints extracted from prompts.
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
        'content_type': 'Rotate content for: forestry 🌲, plant 🌾, animal 🐄 producers',
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
        'sector_rotation': 'rotate'  # Will be determined by week number
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
    "Números concretos con contexto: 'hasta $X dependiendo de...', 'pérdidas que pueden llegar a X% en condiciones típicas'",
    "Beneficios comparativos, no absolutos: 'ahorro vs riego por surco', 'mejor distribución que con X'",
    "No exagerar especificaciones del producto; usar lenguaje preciso",
    "Solución = contexto + producto, no solo producto (práctica correcta + producto)",
    "Producto como componente central de la solución, no único héroe"
]

# ===================================================================
# SPECIAL DATES (Mexican National Holidays & Agricultural Days)
# ===================================================================

SPECIAL_DATES = {
    # Mexican National Holidays & Social Dates
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

DURANGO_SEASONALITY_CONTEXT = """Durango's Primary Sector and Agroindustry: Current Conditions, Seasonality, and Growth Potential

Geographic and climate context shaping production
Durango sits in the transition zone that defines much of northern Mexico's primary-sector reality: strong climatic gradients, a short "reliable" rainy window, and a structural dependence on groundwater where irrigation exists. In fact, 40% of the state's territory is classified as dry to semi‑dry, another 14% as very dry, while about 34% is temperate sub‑humid (with smaller shares warm sub‑humid and temperate humid).

That mix drives a simple operational truth: most field agriculture is scheduled around summer rainfall. INEGI reports an average annual temperature around 17°C and average annual precipitation about 500 mm, with rains occurring "mainly during summer."  This is why the state's annual cropping is organized around the Primavera–Verano cycle (rainy season), with irrigation mainly used to stabilize forage and high-value crops where water access exists.

Water governance and infrastructure matter because irrigation and agroindustry both depend on stable supply. An older but still structurally relevant INEGI profile notes that, for public water supply, most collection points are wells (about 90% in 2010), i.e., groundwater is not a marginal input—it is the backbone wherever "reliable" water exists.

Current primary sector structure and economic weight
The "primary sector" matters in Durango not as a small rural side-story, but as a pillar of the state economy. In 2024, Durango's nominal GDP was 451,314 million pesos, and primary activities contributed 9.0% of total state GDP—an unusually high share compared with many Mexican states.

Recent growth also signals what matters inside that primary sector. INEGI's state GDP-by-activity report shows that in 2024 the gross value added (VAB) of primary activities grew 2.4%, and it attributes the increase mainly to animal raising/exploitation (+4.1%), i.e., livestock-led momentum.  This is a consistent pattern with Durango's production structure (forage crops → animal systems → processing hubs).

Structurally, the official 2022 agricultural census results for Durango describe a state with large rural land but a limited share under crop use: out of 12.3 million hectares total, INEGI reports 4.0 million hectares with forest-use/forest management and 2.3 million hectares with agricultural vocation (with the rest outside agro/forest use categories).  This is crucial: Durango's competitive edge in the primary sector is not "row crops everywhere"—it is a combined forest + rangeland + forage + livestock system.

Agriculture today: forage dominance, rainfed exposure, and small but real protected ag

The core pattern: agriculture is built to feed animals
INEGI's 2022 census results show that the largest annual crops by output are overwhelmingly forages: maize forage (2,328,168 t), oats forage (1,372,973 t), and sorghum forage (1,073,186 t).  When you compare these to other top annual crops—white maize grain (275,918 t), beans (127,706 t), and melon (84,049 t)—the signal is blunt: the "mass" of agricultural tonnage is forage.

A useful way to say this without hand-waving: among the top six annual crops by production volume listed in the census for Durango, forages account for ~91% of total tonnage (calculated from the INEGI crop tonnage figures).  This is why any serious discussion of agroindustry in Durango must start with feed → milk/meat → processing, not with the assumption that the state is primarily a vegetable/fruit export platform.

Perennial crops reinforce the same logic. The main perennial crop is alfalfa: 2,485,473 t over 31,670 ha.  After alfalfa, perennial volumes drop sharply into niche commodities like pecan (9,837 t) and apple (7,818 t).

Rainfed dependence is still the system-level constraint
Durango's agricultural surface is mostly temporal (rainfed). INEGI's census results show 943,024 ha of agricultural area in active production units, of which about 21% is irrigated (196,380 ha) and 79% rainfed (746,644 ha).

The census also records meaningful "lost" area due to operational constraints: of the 943k ha, 70,235 ha were in rest, and 54,962 ha were "not sown" for reasons including bad weather, lack of credit, disease, lack of money/support, labor availability, etc.  This is not a footnote—this is what vulnerability looks like in a climate where the rainy season timing controls the entire Primavera–Verano calendar.

Protected agriculture exists, but it is small and concentrated
Durango has protected agriculture, but the scale is still modest relative to leading protected-ag states. INEGI reports 128 production units using protected agriculture across 389 ha. The largest structure types by area are shade mesh (39.7%) and greenhouses (36.4%), with nurseries (18.5%) and shade houses (10.7%) following.

The leading protected-ag crop is tomato: of 27,012 t total tomato production recorded in the census period, 22,742 t (84.2%) came from protected agriculture.  In other words, where Durango invests in controlled environment production, yields and output concentrate fast—but the footprint is still too small to redefine the state's agricultural profile today.

The "business reality" constraints: costs, finance access, and aging labor
The census makes it hard to romanticize the sector. Producers report that the dominant problem is high input and service costs (94.9%), followed by loss of soil fertility (34.0%), and price/income shocks (e.g., COVID-19–related demand/price declines: 29.2%). Additional frictions include intermediaries affecting commercialization (16.7%), insecurity (13.1%), transport difficulties (12.4%) and labor scarcity (12.5%).

Financial access is structurally thin: only 8.5% of production units reported obtaining credit and 1.0% reported having insurance.  Mechanization is improving (tractors owned more than doubled from 2007 to 2022), but that does not solve water risk, market power, or input-price exposure on its own.

The labor pipeline is also a quiet crisis: 361,294 people participated as agricultural labor, with a strong male skew (89.2%), and the producer population is older (73.8% older than 45).  If you want vertical agroindustrial expansion, you need skills, workforce stability, and management depth—not just raw output.

Livestock and dairy: the anchor for agroindustry
Durango's primary sector becomes coherent once you accept that livestock—especially dairy—anchors the system. This is not an opinion; it shows up in both GDP decomposition and physical production structure. INEGI attributes Durango's 2024 primary-sector growth mainly to animal raising.

Herd structure and scale
At the 15 September 2022 census reference date, Durango recorded approximately 1.58 million head of cattle, plus very large poultry inventories (~31.0 million birds) and smaller but meaningful sheep/goat/beekeeping numbers.

Milk production and the Comarca Lagunera effect
INEGI's dairy infographic (based on census results) reports Durango among the highest milk-producing states, with ~5.6 million liters per day.  A simple annualized translation is about 2.0 billion liters/year (5.6M × 365)—useful as an order-of-magnitude indicator, not as a precise SIAP annual total.

For the market-facing annual framing, industry and SIAP-based compilations put Durango consistently among the top producers. CANILEC's 2013–2023 compendium lists Durango as third nationally by share in 2023 (11.4%, behind Jalisco and Coahuila).  The same compendium also documents that national milk production shows seasonality, with higher volumes in summer months.

A key reason Durango's dairy system is not "just Durango" is Comarca Lagunera—a bi-state dairy basin integrated across suppliers, feed systems, and processors. Academic work using SIAP data estimates the Comarca's 2023 production around 2.896 billion liters, about 21.7% of national production.  That scale explains why forage production (alfalfa, maize forage, oats forage, sorghum forage) is not optional in Durango—it is the dairy system's operating substrate.

Where the value is captured: processing and institutions
Durango's dairy agroindustry is not theoretical; it exists as institutional and corporate infrastructure. Grupo Lala has its corporate offices in Gómez Palacio (address published in official company documents and on the BMV issuer profile).  This is proof of a mature processing-and-distribution anchor inside the state, not merely a production zone shipping raw milk out.

Public-sector demand and distribution also matter. LICONSA continues to expand local "lechería" points of sale/distribution in Gómez Palacio as part of the national milk support system.  This reinforces Durango's dairy linkage: you have both commercial processors and state-supported distribution channels interacting with producer economics.

Forestry and forest-based industry: Durango's other primary-sector pillar
Durango is not just a livestock state; it is also a forest state. The 2022 census state results report 4.0 million hectares with forest exploitation/management use—larger than the area described as having agricultural vocation.

Wood production scale and composition
INEGI reports very large wood volumes in the census period (Oct 2021–Sep 2022): pine ~4.17 million m³, oak ~0.80 million m³, with smaller volumes for juniper and other species.  This lines up with Durango's position in the Sierra belt, where conifer–oak systems dominate commercially relevant forests.

The same census summary shows that forest production units are not passive: large majorities report firebreaks/access, prevention activities, and compliance practices related to fire use and forest protection.  This matters because fire is not a random shock; it is a seasonal operational constraint across northern/central Mexico.

Forest industry: Durango captures value in sawmilling, but the ceiling is higher
On the industrial side, Durango stands out nationally in sawmilling economics. The federal DataMéxico portal (built on economic census data) reports that in the 2019 Economic Census, Durango had the highest gross production in "Sawmills and Wood Preservation" (~$1,512M MX) and also the highest income (~$1,569M MX), ahead of Chihuahua.  This is one of the clearest signals that Durango already has meaningful industrial capture beyond raw log extraction.

At the same time, national policy diagnostics repeatedly flag a known structural weakness: Mexico's forest production is often low-diversification and low value-added relative to potential. A federal CONAFOR institutional publication explicitly points to low diversification/low value added as a recurring sector limitation (national framing, but directly relevant to Durango given its scale).  This is exactly where vertical growth opportunities live—if the governance and investment conditions allow it.

Seasonality in Durango and how it constrains—or enables—growth
Durango's seasonality is not just "weather trivia"; it decides cash timing, labor demand, feed availability, processing utilization, and working-capital stress. The best way to understand it is as overlapping calendars: rainfed grains/legumes, irrigated forage, perennial fruit/nuts, dairy output, and forest operations.

Climate seasonality you can actually plan around
INEGI's baseline climate profile is straightforward: precipitation is modest (~500 mm/year) and falls mainly in summer.  Operationally, that means:
- Planting windows compress into the onset of rains for temporal systems.
- Spring is a risk window for forests (dry fuels) and for feed systems dependent on irrigated forage.

Crop calendars (what typically happens when)
Forage maize (irrigated, dairy-linked): INIFAP technical guidance for the Lagunera production system indicates the temperature-suitable period for maize runs roughly late March to late October, with recommended sowing windows Mar 20–Apr 15 (spring) and Jun 20–Jul 30 (summer).  This matches Durango's massive forage maize volumes in the census.

Forage sorghum (irrigated, heat-tolerant feed): INIFAP recommends sowing Mar 20–Apr 30 for spring and Jun 1–Jul 15 for summer, aligned to heat requirements and the production calendar.

Rainfed maize (temporal): INIFAP's Durango agenda notes that sowing depends on the regular start of rains, with a practical sowing limit around July 25 (after which plantings may be directed to forage rather than grain).

Beans (frijol): Durango's beans are largely a Primavera–Verano rainfed crop in practice (and therefore drought-sensitive). The 2022 census shows huge bean area (301,375 ha) but modest output (127,706 t), implying low average yields consistent with rainfed exposure. For irrigated beans, an INIFAP technical note states sowing typically falls from the last week of June to about July 10, assuming pre-irrigation and adequate soil moisture.  This aligns with the general farm logic: beans begin with rains and finish into the autumn harvest window.

Apples (Canatlán/Nuevo Ideal zone): Apples are a smaller-tonnage crop than forages, but they are strategically important because they can support packing, cold storage, and processing. INEGI's census records 7,818 t over 4,435 ha in the reference year. Government agricultural outreach material (SADER portal) notes that apple harvest ("pizca") begins in July and August in the Durango fruit region.

Dairy seasonality (why summer rains still matter even if you irrigate)
Dairy production has its own seasonality curve. CANILEC's SIAP-based statistical compendium explicitly states that national milk production shows seasonal patterns, with higher volumes in summer.  Practically, in Durango that seasonal uplift is tightly linked to forage availability and heat stress management: summer rains support pasture/rainfed forage where it exists, but they also raise animal heat loads—so the net effect depends on infrastructure, water access, and feed strategy.

Forestry seasonality (fire risk is the binding constraint)
Forests in Durango face a predictable dry-season risk cycle. SEMARNAT describes two national fire seasons, with the north/center/northeast season running January–June. Durango's own civil protection "estiaje/drought/fire" program describes the critical dry period as January–June, with the most critical months April–June due to heat and wind conditions.  This reality affects:
- timing and cost of forest operations and transport,
- the need to finance prevention brigades and infrastructure,
- and the reliability of log supply into sawmills (i.e., agroindustry utilization risk).

Growth potential: 30% future-focused, but grounded in what the system is today
The right way to think about "potential" is not generic diversification—it is horizontal expansion where water/climate allow it and vertical integration where Durango already has scale (dairy, forestry, selected crops).

Horizontal growth: add products only where constraints won't choke them
Expand controlled environment horticulture in targeted corridors. Protected agriculture is only 389 ha today, yet when used it drives high output concentration (e.g., tomato).  If Durango wants horizontal growth into higher-value produce, it should not aim for "more acres of everything." It should pick water-secured zones (irrigation districts / groundwater-stable areas) and build greenhouse + cold-chain + packout systems around a few crops with proven market pull (tomato, peppers, berries are already present in the census protected-ag list).

Improve bean productivity before expanding bean area. The bean footprint is huge but yields implied by census totals are low (large hectares, modest tons).  More hectares of low-yield beans is not a growth strategy; it is a working-capital sink. Horizontal growth here means seed quality + agronomic timing + drought-risk management, not acreage expansion. Federal agriculture programs have recently emphasized distributing improved/certified bean seed for the Primavera–Verano cycle, which matters directly for Durango's rainfed systems.

Vertical growth: capture value where Durango already has scale
Dairy vertical integration is the cleanest path. Durango already sits inside a mega-cluster (Comarca Lagunera) with national significance, and it already hosts major corporate infrastructure (Grupo Lala corporate offices in Gómez Palacio).  The vertical playbook is clear:
- stabilize and lower feed costs via forage efficiency (water-efficient alfalfa/maize systems),
- invest in higher-margin processing (cheeses, specialized powders, ingredients),
- and industrialize waste-to-value (manure → biogas/energy; whey utilization).

The "hard truth" constraint is that this requires financing depth, stable water, and professional operations—not just more milk. Low credit penetration (8.5%) is a direct friction against this transition.

Forestry vertical integration is Durango's second major opportunity—if fire risk and governance are handled. Durango already leads Mexico in sawmilling gross production value (economic census) and produces massive pine/oak volumes.  Moving up the value chain into engineered wood, furniture components, certified products, and biomass/pellets is the classic vertical move—especially given federal acknowledgment that the sector tends to be low in diversification and value added.  But the binding risk is seasonal fire exposure (Jan–Jun, critical Apr–Jun), which can disrupt supply and elevate costs. Any serious vertical strategy must embed prevention, monitoring, and logistics resilience as non-negotiables.

Apple value chain: fix the orchard base first, then industrialize. In census terms, apples are smaller than forages, but they can drive cold storage + sorting + branded packing + processing.  The state also publicly pushes technology improvements (e.g., drone-assisted application programs in Canatlán).  The weak link is structural: local reporting highlights aging orchards and the investment gap to renovate into higher-density, higher-yield systems.  Vertical growth here should be staged: (1) orchard renewal + water efficiency + pest management, (2) packing/cold chain utilization, (3) processing for grade-outs ("industrial fruit") into concentrates or derivatives only once consistent volume is secured.

The non-negotiable constraints you must price into any "potential" story
If you ignore these, you'll overestimate Durango's upside:
- Water & climate risk: summer-dependent rainfall and a groundwater-heavy supply structure mean water stability is the gating factor for both more crops and more processing.
- Input-cost pressure: producers report input/service costs as the dominant constraint (94.9%). Any expansion model must show how margins survive cost volatility.
- Finance depth: low credit and essentially negligible insurance constrain modernization speed, especially for capital-intensive agroindustry.
- Labor and management pipeline: an aging producer base and limited youth participation increase execution risk for complex vertical projects.
- Fire seasonality in forests: the Jan–Jun dry season (especially Apr–Jun in Durango) is a recurring operational hazard.

The fastest route to "more agroindustria" is not dispersing into dozens of new crops—it is deepening value capture in the two systems the state already runs at scale (dairy and forestry), while selectively scaling protected horticulture where water and logistics are defensible.
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
