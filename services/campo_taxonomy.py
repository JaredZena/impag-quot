"""
Slugs shared by "Apoyos gubernamentales" and "Agroproyectos".

The storefront (src/lib/apoyos.ts, src/lib/agro.ts) and the admin
(src/types/campo.ts) mirror these exact slugs; change them in all three
repos together.
"""

from typing import Literal

STATES: dict[str, str] = {
    "nacional": "Nacional",
    "aguascalientes": "Aguascalientes",
    "baja-california": "Baja California",
    "baja-california-sur": "Baja California Sur",
    "campeche": "Campeche",
    "chiapas": "Chiapas",
    "chihuahua": "Chihuahua",
    "ciudad-de-mexico": "Ciudad de México",
    "coahuila": "Coahuila",
    "colima": "Colima",
    "durango": "Durango",
    "estado-de-mexico": "Estado de México",
    "guanajuato": "Guanajuato",
    "guerrero": "Guerrero",
    "hidalgo": "Hidalgo",
    "jalisco": "Jalisco",
    "michoacan": "Michoacán",
    "morelos": "Morelos",
    "nayarit": "Nayarit",
    "nuevo-leon": "Nuevo León",
    "oaxaca": "Oaxaca",
    "puebla": "Puebla",
    "queretaro": "Querétaro",
    "quintana-roo": "Quintana Roo",
    "san-luis-potosi": "San Luis Potosí",
    "sinaloa": "Sinaloa",
    "sonora": "Sonora",
    "tabasco": "Tabasco",
    "tamaulipas": "Tamaulipas",
    "tlaxcala": "Tlaxcala",
    "veracruz": "Veracruz",
    "yucatan": "Yucatán",
    "zacatecas": "Zacatecas",
}

SECTORS: dict[str, str] = {
    "agricultura": "Agricultura",
    "ganaderia": "Ganadería",
    "forestal": "Forestal",
    "pesca-acuacultura": "Pesca y acuacultura",
    "apicultura": "Apicultura",
    "agroindustria": "Agroindustria",
    "desarrollo-rural": "Desarrollo rural",
}

SUPPORT_TYPES: dict[str, str] = {
    "subsidio": "Subsidio",
    "credito": "Crédito",
    "garantia": "Garantía",
    "seguro": "Seguro",
    "capacitacion": "Capacitación",
    "asistencia-tecnica": "Asistencia técnica",
    "insumos": "Insumos",
    "transferencia-directa": "Transferencia directa",
    "infraestructura": "Infraestructura",
}

LEVELS: dict[str, str] = {
    "federal": "Federal",
    "estatal": "Estatal",
    "municipal": "Municipal",
    "financiera": "Banca de desarrollo / financiera",
    "otro": "Otro",
}

# Storefront categories (todoparaelcampo-storefront src/data/categories.json).
STORE_CATEGORIES: dict[str, str] = {
    "drones-agricolas": "Drones Agrícolas",
    "climatizacion-agricola": "Climatización Agrícola",
    "geotanques": "Geotanques y Geoestanques",
    "bombeo-solar-agricola": "Bombeo Solar Agrícola",
    "nutricion-proteccion": "Nutrición y Protección Para Cultivos",
    "sistemas-de-riego": "Sistemas de Riego",
    "energia-solar": "Energía Solar para Agricultura",
    "invernaderos-cubiertas": "Invernaderos y Cubiertas Agrícolas",
    "accesorios-equipos": "Accesorios y Equipos Agrícolas",
    "germinacion-cultivos": "Germinación de Cultivos",
    "ganaderia-y-procesamiento": "Ganadería y Procesamiento",
    "ferreteria-y-refacciones": "Ferretería y Refacciones",
}

StateSlug = Literal[
    "nacional",
    "aguascalientes",
    "baja-california",
    "baja-california-sur",
    "campeche",
    "chiapas",
    "chihuahua",
    "ciudad-de-mexico",
    "coahuila",
    "colima",
    "durango",
    "estado-de-mexico",
    "guanajuato",
    "guerrero",
    "hidalgo",
    "jalisco",
    "michoacan",
    "morelos",
    "nayarit",
    "nuevo-leon",
    "oaxaca",
    "puebla",
    "queretaro",
    "quintana-roo",
    "san-luis-potosi",
    "sinaloa",
    "sonora",
    "tabasco",
    "tamaulipas",
    "tlaxcala",
    "veracruz",
    "yucatan",
    "zacatecas",
]
SectorSlug = Literal[
    "agricultura",
    "ganaderia",
    "forestal",
    "pesca-acuacultura",
    "apicultura",
    "agroindustria",
    "desarrollo-rural",
]
SupportTypeSlug = Literal[
    "subsidio",
    "credito",
    "garantia",
    "seguro",
    "capacitacion",
    "asistencia-tecnica",
    "insumos",
    "transferencia-directa",
    "infraestructura",
]
LevelSlug = Literal["federal", "estatal", "municipal", "financiera", "otro"]
StoreCategorySlug = Literal[
    "drones-agricolas",
    "climatizacion-agricola",
    "geotanques",
    "bombeo-solar-agricola",
    "nutricion-proteccion",
    "sistemas-de-riego",
    "energia-solar",
    "invernaderos-cubiertas",
    "accesorios-equipos",
    "germinacion-cultivos",
    "ganaderia-y-procesamiento",
    "ferreteria-y-refacciones",
]


def options(labels: dict[str, str]) -> list[dict]:
    """[{value, label}] for admin selects."""
    return [{"value": k, "label": v} for k, v in labels.items()]


# ── window status of an apoyo (same rule in the storefront and the admin) ────

WINDOW_LABELS: dict[str, str] = {
    "abierta": "Abierta",
    "proxima": "Próximamente",
    "permanente": "Todo el año",
    "cerrada": "Cerrada",
    "sin-fecha": "Fecha por confirmar",
}
WINDOW_ORDER = ["abierta", "proxima", "permanente", "sin-fecha", "cerrada"]


def today_mx():
    """Today's date in Mexico (America/Mexico_City), the date windows use."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("America/Mexico_City")).date()


def window_of(opens_at, closes_at, permanent: bool, today=None) -> str:
    """cerrada > proxima > permanente > abierta > sin-fecha (dates are datetime.date or None)."""
    today = today or today_mx()
    if closes_at is not None and closes_at < today:
        return "cerrada"
    if opens_at is not None and opens_at > today:
        return "proxima"
    if permanent:
        return "permanente"
    if closes_at is not None:
        return "abierta"
    return "sin-fecha"


def days_left(closes_at, today=None) -> int | None:
    if closes_at is None:
        return None
    return (closes_at - (today or today_mx())).days
