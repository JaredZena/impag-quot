"""
Claude-based classifier for WhatsApp media files and conversations.
Uses surrounding message context + filenames to classify and label media.
"""
import json
import re
from typing import List, Dict, Optional
from anthropic import Anthropic
from config import claude_api_key

client = Anthropic(api_key=claude_api_key)

# Known IMPAG filename patterns
# Format: COT-IMPAG-{number}{location_code}-{customer}-{product}.ext
# Example: COT-IMPAG-430224DGO-ANTONIO DE LA CRUZ-CHAROLA DE GERMINACION.pdf
COT_PATTERN = re.compile(
    r'COT-IMPAG-(\d+)([A-Z]{2,5})-(.+?)\.(?:pdf|docx?)',
    re.IGNORECASE
)
FACTURA_PATTERN = re.compile(
    r'(?:factura|FACTURA)\s*(.+?)\.(?:pdf|xml)',
    re.IGNORECASE
)


def classify_media_batch(
    media_refs: List[Dict],
    chat_name: str = "",
    max_batch_size: int = 50,
) -> List[Dict]:
    """
    Classify a batch of media references using Claude.
    Sends context-rich descriptions and gets back classifications.

    Returns the media_refs list with an added "classification" key on each item.
    """
    if not media_refs:
        return media_refs

    # First pass: classify what we can from filenames alone (no API call needed)
    needs_ai = []
    for ref in media_refs:
        filename = ref["attachment_info"].get("filename")
        if filename:
            local_class = _classify_from_filename(filename)
            if local_class:
                ref["classification"] = local_class
                continue
        needs_ai.append(ref)

    # Second pass: batch the rest through Claude
    for i in range(0, len(needs_ai), max_batch_size):
        batch = needs_ai[i:i + max_batch_size]
        _classify_batch_with_claude(batch, chat_name)

    return media_refs


def _classify_from_filename(filename: str) -> Optional[Dict]:
    """Classify a media file from its filename alone (no AI needed)."""
    filename_lower = filename.lower()

    # Quotation: COT-IMPAG-430224DGO-ANTONIO DE LA CRUZ-CHAROLA.pdf
    cot_match = COT_PATTERN.search(filename)
    if cot_match:
        number = cot_match.group(1)
        location = cot_match.group(2)  # e.g., DGO
        rest = cot_match.group(3).strip(' -')
        # Split customer name from product (last segment after last hyphen)
        parts = rest.rsplit('-', 1)
        if len(parts) > 1:
            customer = parts[0].strip()
            product = parts[1].strip()
        else:
            # No hyphen separator — could be "A QUIEN CORRESPONDA PLASTICO BLANCO"
            customer = "A QUIEN CORRESPONDA"
            product = rest.strip()
        return {
            "classification": "cotizacion",
            "description": f"Cotizacion #{number} ({location}) para {customer} - {product}",
            "customer_reference": customer if customer != "A QUIEN CORRESPONDA" else None,
            "product_references": [product],
            "original_filename": filename,
            "tags": ["cotizacion", product.lower().replace(' ', '-')],
        }

    # Invoice/Factura
    if 'factura' in filename_lower or filename_lower.startswith('f') and re.match(r'f\d+', filename_lower):
        return {
            "classification": "factura",
            "description": f"Factura: {filename}",
            "customer_reference": None,
            "product_references": [],
            "original_filename": filename,
            "tags": ["factura"],
        }

    # XML files (usually CFDI/SAT fiscal documents)
    if filename_lower.endswith('.xml'):
        return {
            "classification": "factura_xml",
            "description": f"CFDI XML: {filename}",
            "customer_reference": None,
            "product_references": [],
            "original_filename": filename,
            "tags": ["factura", "cfdi", "xml"],
        }

    # Catalogs
    if 'catalogo' in filename_lower or 'catalog' in filename_lower:
        return {
            "classification": "catalogo",
            "description": f"Catalogo: {filename}",
            "customer_reference": None,
            "product_references": [],
            "original_filename": filename,
            "tags": ["catalogo"],
        }

    return None


def _classify_batch_with_claude(batch: List[Dict], chat_name: str) -> None:
    """Send a batch of media references to Claude for classification."""
    if not batch:
        return

    # Build the prompt with all media items
    items_text = []
    for idx, ref in enumerate(batch):
        ts = ref["timestamp"].strftime("%Y-%m-%d %H:%M") if ref.get("timestamp") else "?"
        sender = ref.get("sender", "?")
        att = ref.get("attachment_info", {})
        media_type = att.get("type", "unknown")
        filename = att.get("filename", "sin nombre")
        matched_file = ref.get("matched_file", None)

        context_before = "\n".join(ref.get("context_before", []))
        context_after = "\n".join(ref.get("context_after", []))

        items_text.append(f"""--- Archivo #{idx + 1} ---
Fecha: {ts}
Enviado por: {sender}
Tipo: {media_type}
Nombre de archivo: {filename}
Archivo de zip: {matched_file or 'N/A'}
Mensajes antes:
{context_before or '(ninguno)'}
Mensajes despues:
{context_after or '(ninguno)'}""")

    prompt = f"""Analiza estos {len(batch)} archivos multimedia de un chat de WhatsApp de IMPAG
(empresa mexicana de insumos agricolas: geomembranas, plasticos, mallas sombra, sustratos, invernaderos).
Chat: {chat_name or 'desconocido'}

{chr(10).join(items_text)}

Para CADA archivo, responde con un JSON array. Cada elemento debe tener:
- "index": numero del archivo (empezando en 1)
- "classification": una de: cotizacion, factura, factura_xml, comprobante_pago, foto_producto, foto_obra, foto_instalacion, plano, catalogo, guia_envio, recibo, captura_pantalla, otro
- "description": descripcion breve en español (1 linea)
- "customer_reference": nombre del cliente si se puede inferir, o null
- "product_references": lista de productos mencionados, o []
- "tags": lista de etiquetas relevantes

Responde SOLO con el JSON array, sin texto adicional."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()
        # Extract JSON from possible markdown code blocks
        if text.startswith('```'):
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)

        classifications = json.loads(text)

        for item in classifications:
            idx = item.get("index", 0) - 1
            if 0 <= idx < len(batch):
                batch[idx]["classification"] = {
                    "classification": item.get("classification", "otro"),
                    "description": item.get("description", ""),
                    "customer_reference": item.get("customer_reference"),
                    "product_references": item.get("product_references", []),
                    "original_filename": batch[idx]["attachment_info"].get("filename"),
                    "tags": item.get("tags", []),
                }

    except Exception as e:
        print(f"[MediaClassifier] Claude classification failed: {e}")
        # Mark unclassified items
        for ref in batch:
            if "classification" not in ref:
                ref["classification"] = {
                    "classification": "otro",
                    "description": "No se pudo clasificar automaticamente",
                    "customer_reference": None,
                    "product_references": [],
                    "original_filename": ref["attachment_info"].get("filename"),
                    "tags": ["sin-clasificar"],
                }


def classify_conversation(messages: List[Dict], chat_name: str = "") -> Dict:
    """
    Classify an entire WhatsApp conversation using Claude.
    Returns high-level metadata about the conversation.
    """
    if not messages:
        return {"error": "No messages"}

    # Build a summary of the conversation (sample messages to stay within token limits)
    sample_size = min(len(messages), 100)
    # Take first 30, last 30, and 40 evenly spaced from the middle
    if len(messages) <= sample_size:
        sample = messages
    else:
        first = messages[:30]
        last = messages[-30:]
        middle_indices = [int(i * (len(messages) - 60) / 40) + 30 for i in range(40)]
        middle = [messages[i] for i in middle_indices if i < len(messages)]
        sample = first + middle + last

    lines = []
    for m in sample:
        ts = m["timestamp"].strftime("%Y-%m-%d %H:%M") if m.get("timestamp") else ""
        lines.append(f"[{ts}] {m['sender']}: {m['message']}")
    conversation_text = "\n".join(lines)

    # Count participants and media
    participants = list(set(m["sender"] for m in messages))
    media_count = sum(1 for m in messages if m.get("has_attachment"))

    prompt = f"""Analiza esta conversacion de WhatsApp de IMPAG (empresa de insumos agricolas).
Nombre del chat: {chat_name or 'desconocido'}
Total mensajes: {len(messages)}
Participantes: {', '.join(participants)}
Archivos multimedia: {media_count}

Muestra de la conversacion:
{conversation_text}

Responde con un JSON con estos campos:
- "customer_name": nombre del cliente (si aplica, o null para grupos internos)
- "customer_type": "cliente" | "proveedor" | "interno" | "mixto"
- "conversation_type": "venta" | "cotizacion" | "soporte" | "cobranza" | "logistica" | "operaciones" | "otro"
- "products_discussed": lista de productos mencionados
- "locations_mentioned": lista de ubicaciones mencionadas
- "status": "primer_contacto" | "negociacion" | "cotizacion_enviada" | "cerrada" | "perdida" | "activo" | "no_aplica"
- "summary": resumen de 1-2 oraciones en español
- "tags": lista de etiquetas relevantes
- "key_participants": dict con cada participante y su rol (ej: {{"IMPAG": "vendedor", "Cliente": "comprador"}})

Responde SOLO con el JSON, sin texto adicional."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()
        if text.startswith('```'):
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)

        return json.loads(text)

    except Exception as e:
        print(f"[MediaClassifier] Conversation classification failed: {e}")
        return {
            "customer_name": None,
            "customer_type": "desconocido",
            "conversation_type": "otro",
            "products_discussed": [],
            "locations_mentioned": [],
            "status": "no_aplica",
            "summary": "No se pudo clasificar automaticamente",
            "tags": ["sin-clasificar"],
            "key_participants": {},
        }
