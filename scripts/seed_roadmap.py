#!/usr/bin/env python3
"""
Seed the in-app roadmap tracker. Idempotent: only inserts when the table is empty
(edit statuses/notes from the app afterward, not here). Phase 0 = already shipped.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import SessionLocal, RoadmapItem

# (phase, title, description, need, effort, impact, status)
ITEMS = [
    # ── Phase 0: already shipped ──────────────────────────────────────────
    (0, "Búsqueda RAG híbrida + reranking", "Retrieval denso + léxico (tsvector español) + fusión RRF + cross-encoder. Búsquedas exactas de SKU pasaron de 25% a 100% en top-3.", "RAG / cotizaciones", "days", "high", "done"),
    (0, "Puente RAG → Cotización rastreable", "Botón 'Generar cotización' que pasa los productos que el RAG recuperó a una cotización rastreable, sin re-teclear.", "Cotizaciones", "days", "high", "done"),
    (0, "Registro de feedback en /query", "Cada consulta guarda chunks recuperados, latencia, usuario; pulgar arriba/abajo para medir calidad.", "RAG / métricas", "days", "medium", "done"),
    (0, "Agente de WhatsApp (humano en el ciclo)", "Mensaje entrante → borrador con IA fundamentado en catálogo → cola de aprobación. Envío BLOQUEADO hasta tu autorización.", "WhatsApp / ventas", "weeks", "high", "done"),
    (0, "Endurecimiento de autenticación", "Se cerró /query y otros endpoints que estaban abiertos; verificación de firma en el webhook.", "Seguridad", "days", "high", "done"),

    # ── Phase 1: fundaciones de datos ─────────────────────────────────────
    (1, "Maestro de clientes/leads + importador", "La pieza clave: una tabla de clientes deduplicada (fuzzy-merge + revisión humana) desde las 6 hojas donde hoy son texto libre. Desbloquea directorio, deudas y seguimiento.", "Directorio de clientes", "weeks", "high", "planned"),
    (1, "Conector de Google Sheets", "Que la app lea tus hojas vivas (con la cuenta de Google de IMPAG) para importar caja chica, inventario, balances y precios.", "Infraestructura", "weeks", "high", "planned"),
    (1, "Importar catálogo de precios", "Concentrado + Lista de Precios → tabla supplier_product que YA existe (costo multi-proveedor, fletes, márgenes). Casi 1:1.", "Compras / precios", "weeks", "high", "planned"),

    # ── Phase 2: la columna vertebral ─────────────────────────────────────
    (2, "Libro de ventas/notas", "Migrar Control de Ventas (~$1.48M MXN al año) con código de nota + bandera factura/remisión. La hoja más rica.", "Estados financieros", "weeks", "high", "planned"),
    (2, "Libro de caja unificado", "Colapsar caja chica + banco en una tabla cash_transaction (entra/sale, cuenta, categoría). Base de flujo de caja y P&L.", "Finanzas / caja chica", "weeks", "high", "planned"),
    (2, "Primitiva de Proyecto + BOM", "El átomo del negocio: cada obra con su lista de materiales + estado de compra por ítem + margen. Reusa balance/balance_item existentes.", "Proyectos / compras", "weeks", "high", "planned"),

    # ── Phase 3: valor derivado ───────────────────────────────────────────
    (3, "Estado de resultados + flujo de caja", "Vistas automáticas (ingresos vs egresos por mes, saldo corrido, proyección 13 semanas). No es contabilidad de doble partida.", "Estados financieros", "weeks", "high", "planned"),
    (3, "Cuentas por cobrar/pagar (deudas)", "Capa derivada: reconciliar pagos parciales (PP) contra ventas por código de nota; aging 0-30/31-60/61-90/90+.", "Balances / deudas", "weeks", "high", "planned"),
    (3, "Automatización de seguimiento", "Trabajo diario que detecta cotizaciones estancadas → crea tarea y/o borrador de WhatsApp en la cola de aprobación (nunca auto-envía).", "Seguimiento de clientes", "weeks", "high", "planned"),

    # ── Phase 4: operaciones + contenido ──────────────────────────────────
    (4, "Formularios de caja chica", "Captura móvil de gastos con foto de ticket, saldo corrido, totales por categoría.", "Caja chica", "weeks", "medium", "planned"),
    (4, "Inventario de herramientas/consumibles", "Tabla dedicada (herramienta vs consumible), separada del catálogo de venta; asignación a proyectos.", "Herramientas / stock", "weeks", "medium", "planned"),
    (4, "Social basado en proyectos reales", "Posts que muestran obras reales instaladas y material realmente disponible; agregar project_id al SocialPost existente.", "Social", "days", "medium", "planned"),
    (4, "Publicación automática a FB/IG", "Generar un post diario y publicarlo a Facebook/Instagram (con buffer de aprobación). Requiere App Review de Meta para IG.", "Social", "weeks", "medium", "planned"),
    (4, "Directorio de proveedores/logística", "Contactos de campo (ferreterías, hoteles, talleres) por localidad para apoyo a cuadrillas.", "Directorio", "days", "low", "planned"),

    # ── Phase 5: métricas ─────────────────────────────────────────────────
    (5, "Tablero de KPIs del distribuidor", "Margen, DSO, rotación de inventario, ciclo de conversión de efectivo — vistas sobre los libros de ventas y caja.", "Métricas financieras", "weeks", "medium", "planned"),
]


def main():
    db = SessionLocal()
    try:
        if db.query(RoadmapItem).count() > 0:
            print("roadmap_item already seeded — skipping.")
            return
        for i, (phase, title, desc, need, effort, impact, status) in enumerate(ITEMS):
            db.add(RoadmapItem(phase=phase, title=title, description=desc, need=need,
                               effort=effort, impact=impact, status=status, sort_order=i))
        db.commit()
        print(f"Seeded {len(ITEMS)} roadmap items.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
