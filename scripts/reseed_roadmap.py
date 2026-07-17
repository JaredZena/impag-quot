#!/usr/bin/env python3
"""
Re-seed the roadmap tracker with the IMPROVED, value-first plan (after the
adversarial roadmap review). Destructive: replaces ALL roadmap_item rows.
Run: python scripts/reseed_roadmap.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import SessionLocal, RoadmapItem

# (phase, title, description, need, effort, impact, status)
ITEMS = [
    # ── Phase 0: ya construido ────────────────────────────────────────────
    (0, "Búsqueda RAG híbrida + reranking", "Retrieval denso + léxico (español) + RRF + cross-encoder. Búsqueda exacta de SKU 25%→100% en top-3.", "RAG / cotizaciones", "days", "high", "done"),
    (0, "Puente RAG → Cotización rastreable", "Botón 'Generar cotización' pasa los productos recuperados a una cotización rastreable.", "Cotizaciones", "days", "high", "done"),
    (0, "Registro de feedback en /query", "Cada consulta guarda chunks, latencia, usuario; 👍/👎 para medir calidad.", "RAG / métricas", "days", "medium", "done"),
    (0, "Agente de WhatsApp (humano en el ciclo)", "Entrante → borrador con IA → cola de aprobación. Envío BLOQUEADO hasta tu autorización.", "WhatsApp / ventas", "weeks", "high", "done"),
    (0, "Endurecimiento de autenticación", "Se cerraron endpoints abiertos; verificación de firma en el webhook.", "Seguridad", "days", "high", "done"),

    # ── Phase 1: fundación (horas, no bloquea el ritmo visible) ────────────
    (1, "Adoptar Alembic + gobernanza de migraciones", "Retirar el run_migration.py de sqlite; una migración revisada por cambio; regla 'una migración strangler-fig a la vez'. Congelar tabla Product (código nuevo usa supplier_product). El riesgo #1 de corrupción silenciosa para un solo dev.", "Infraestructura", "days", "high", "planned"),
    (1, "Consolidar los 3 conceptos de cotización", "Declarar: Quote = columna rastreable de ventas; Balance/BalanceItem = motor de costeo/BOM; Quotation = render de un Quote; folios COT-/NOT- → quote_number. Medio día + adaptador delgado. Toda función nueva apunta solo a Quote.", "Arquitectura / cotizaciones", "days", "high", "planned"),

    # ── Phase 2: rebanadas de valor (semanas 1-6, cada una una pantalla) ───
    (2, "Customer 360 (cliente por teléfono)", "Semana 1. Entidad de cliente sembrada determinísticamente de WAConversation.customer_phone (~28k msgs) + Quote.customer_phone; ligar conversaciones del agente. Pantalla: nombre/tel → todos los chats, cotizaciones y docs. Sin merge de nombres todavía.", "Directorio de clientes", "weeks", "high", "planned"),
    (2, "Seguimiento automático de cotizaciones estancadas", "Semana 2. Trabajo nocturno sobre timestamps de Quote → cotización estancada N días genera borrador de WhatsApp en la cola de aprobación (un toque) + Tarea. Puro cableado sobre lo ya construido. Mayor ROI/esfuerzo del tablero: recupera ventas.", "Seguimiento de clientes", "weeks", "high", "planned"),
    (2, "¿Quién me debe? (CxC v1)", "Semana 3. Recibibles desde Quote.payment_status + vista sobre snapshots CSV emparejada por folio NOT-IMPAG (aparece en ingresos Y gastos); etiquetas humanas para el 'PP'/'50%'. Etiquetado 'vista operativa, no contabilidad'. Desacoplado de los libros.", "Balances / deudas", "weeks", "high", "planned"),
    (2, "Importar catálogo + margen por cotización", "Semana 4. Snapshot CSV → supplier_product (~1:1, columnas ya existen) a través del nuevo arnés Alembic+cuarentena (ensayo de bajo riesgo). Margen crudo por cotización = precio venta − (costo catálogo + fletes). Un número honesto temprano.", "Compras / precios", "weeks", "high", "planned"),
    (2, "Primitiva de Proyecto + BOM (con mano de obra)", "Semana 5. Extender Balance/BalanceItem: + customer_id + link folio/quote + cost_category (MATERIAL, MANO_OBRA, VIATICOS, COMBUSTIBLE, FLETE) + cuadrilla; salarios recurrentes ('Semana Hernan') como nómina asignada a proyectos. project_item = vista sobre Balance, NO 5a tabla. Mano de obra/viáticos = 40%+ del gasto.", "Proyectos / compras", "weeks", "high", "planned"),
    (2, "Columna de folios + libro de ventas (etiquetado)", "Semana 6. Parsear/validar folio NOT-IMPAG (seq, fecha, ubicación TEC/DGO, cliente) → cuarentena si está mal formado; sembrar libro de ventas del snapshot por folio ligando project_id; el parse de pago se hace en la importación (así CxC es un GROUP-BY).", "Estados financieros", "weeks", "high", "planned"),

    # ── Phase 3: finanzas operativas (NO contabilidad estatutaria) ─────────
    (3, "Libro de caja unificado + captura por WhatsApp", "Colapsar caja chica + banco en cash_transaction. Captura de campo: reenviar foto de ticket → agente redacta el gasto a la cola de aprobación. Formulario web = respaldo de escritorio (un form no se llena en la obra).", "Finanzas / caja chica", "weeks", "high", "planned"),
    (3, "Vista operativa: estado de resultados + flujo", "GROUP-BY sobre los libros, ETIQUETADO 'instantánea operativa, no libros contables', con conteo de filas en cuarentena. No presentar como autoritativo sobre remisiones sin reconciliar.", "Estados financieros", "weeks", "high", "planned"),
    (3, "Margen real vs cotizado por proyecto (KPI estrella)", "El número que importa al instalador: ¿el proyecto dejó dinero tras fletes/mano de obra vs lo cotizado? Sobre proyecto + libros. (DSO/rotación quedan opcionales.)", "Métricas financieras", "weeks", "high", "planned"),
    (3, "Exportar a paquete contable (Contpaqi/Aspel/Bind)", "Comprar, no construir: estados estatutarios, subledgers CxC/CxP y CFDI a un paquete MX maduro. NO hacer contabilidad de doble partida a mano.", "Estados financieros", "weeks", "medium", "planned"),

    # ── Phase 4: operaciones + agente sobre datos estructurados ────────────
    (4, "Agente/RAG sobre datos estructurados", "text-to-SQL sobre clientes, ventas, márgenes, deudas dentro de la cola de WhatsApp: '¿cuánto me debe Juan Arreola?', '¿qué margen dejó Zaragoza?', '¿cuánto llevo en fletes?'. El mayor consumidor de los datos que la migración produce.", "WhatsApp / IA", "weeks", "high", "planned"),
    (4, "Fusión difusa de clientes (merge-on-touch)", "Dedup incremental de nombres de texto libre (no bloqueante), con merge_eval (precisión/recall) + cola de revisión humana para pares ambiguos.", "Directorio de clientes", "weeks", "medium", "planned"),
    (4, "Inventario de herramientas/consumibles", "Tabla dedicada (herramienta vs consumible), separada del catálogo de venta; asignación a proyectos.", "Herramientas / stock", "weeks", "medium", "planned"),
    (4, "Directorio de proveedores/logística", "Contactos de campo (ferreterías, hoteles, talleres) por localidad para cuadrillas.", "Directorio", "days", "low", "planned"),

    # ── Phase 5: diferido / opcional ──────────────────────────────────────
    (5, "Social basado en proyectos reales", "Agregar project_id a SocialPost SOLO cuando algo lo consuma. Social ya es el área más sobre-construida del código.", "Social", "weeks", "low", "planned"),
    (5, "Publicación automática a FB/IG", "DIFERIDO detrás de un disparador de adopción real. Menor valor de negocio del tablero; IG requiere App Review de Meta.", "Social", "weeks", "low", "planned"),
    (5, "KPIs adicionales (DSO, rotación de inventario)", "Opcionales, después del margen por proyecto.", "Métricas financieras", "weeks", "low", "planned"),
]


def main():
    db = SessionLocal()
    try:
        deleted = db.query(RoadmapItem).delete()
        for i, (phase, title, desc, need, effort, impact, status) in enumerate(ITEMS):
            db.add(RoadmapItem(phase=phase, title=title, description=desc, need=need,
                               effort=effort, impact=impact, status=status, sort_order=i))
        db.commit()
        print(f"Reset roadmap: deleted {deleted}, inserted {len(ITEMS)} items.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
