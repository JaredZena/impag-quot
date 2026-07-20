"""
RAG-grounded WhatsApp reply drafter — Phase 2 of the WhatsApp sales agent
(see PLAN_WHATSAPP_AGENT.md).

Given a customer's latest WhatsApp message and the conversation history, this
drafts a short Spanish reply grounded in IMPAG's product database via the
existing RAG retrieval. It is deliberately SIDE-EFFECT FREE: no Meta Cloud API
calls, no message sending. The draft is meant to land in the human approval
queue, where an operator approves / edits / rejects before anything is sent.

The webhook and sender (which need Meta credentials from Phase 0) call into
this; this module itself is fully testable without any Meta setup.
"""
from typing import Dict, List, Optional

import anthropic

from config import claude_api_key

# Proven available in this venv (same id used by scripts/rag_eval.py). Haiku is
# right for short, high-volume conversational drafts; the quotation path keeps
# using Sonnet.
DRAFTER_MODEL = "claude-haiku-4-5-20251001"
MAX_DRAFT_TOKENS = 400

# Persona + rules (from PLAN_WHATSAPP_AGENT.md). Kept as the system turn; the
# dynamic product/conversation context goes in the user turn.
SYSTEM_PROMPT = """Eres el asistente de ventas de IMPAG, una empresa mexicana que vende
insumos agrícolas, plásticos, geomembranas, mallas sombra, sustratos
y materiales de invernadero. Ubicados en Tecámac, Estado de México,
y Nuevo Ideal, Durango.

Tu tarea: redactar una respuesta al último mensaje del cliente.

Reglas:
- Responde en español informal pero profesional (como WhatsApp)
- Mensajes cortos y directos (máximo 2-3 oraciones por mensaje)
- Si preguntan precio: usa los datos de productos proporcionados
- Si preguntan medidas/specs: responde con lo que hay en la base de datos
- Si necesitas información del cliente (medidas, ubicación, cantidad): pregunta
- Si no tienes información suficiente: indica que consultarás y responderás
- Nunca inventes precios ni disponibilidad
- Usa emojis con moderación

Responde SOLO con el texto del mensaje a enviar (sin comillas, sin prefijos)."""


# ── Proactive follow-up (stalled-quote nudge) ────────────────────────────────
# This is an OUTBOUND-INITIATION message, not a reply. The reply drafter above
# frames its input as "the customer's last message" and would misread a nudge
# instruction as a customer utterance, so the follow-up needs its own prompt.

FOLLOWUP_SYSTEM_PROMPT = """Eres el asistente de ventas de IMPAG, una empresa mexicana
que vende insumos agrícolas, plásticos, geomembranas, mallas sombra, sustratos y
materiales de invernadero (Tecámac, Estado de México y Nuevo Ideal, Durango).

Tu tarea: redactar un mensaje BREVE de seguimiento a un cliente que recibió una
cotización hace unos días y todavía no responde. NO es una respuesta a un mensaje
del cliente: tú inicias el contacto.

Reglas:
- Español informal pero profesional, tono de WhatsApp
- Muy corto: 2-3 oraciones máximo
- Saluda por su nombre si lo tienes
- Menciona la cotización (número y monto) de forma natural
- Si ya la revisó (viewed), reconócelo con tacto; si solo se envió, confirma que le llegó
- Ofrece resolver dudas o ajustar; invita a continuar sin presionar
- Nunca inventes precios, plazos ni datos que no se te den
- Un solo emoji como máximo, opcional

Responde SOLO con el texto del mensaje a enviar (sin comillas, sin prefijos)."""


def _first_name(customer_name: Optional[str]) -> Optional[str]:
    """First token of a name, or None. Safe on whitespace-only / empty strings
    (a NOT NULL customer_name can still be '   ')."""
    parts = (customer_name or "").split()
    return parts[0] if parts else None


def _followup_template(quote_number, total, customer_name, days_since_sent,
                       was_viewed, is_expired=False):
    """Deterministic fallback nudge — used when the LLM call fails so the operator
    always has a sendable, quote-specific message to review. Must never raise."""
    fn = _first_name(customer_name)
    hi = f"Hola {fn}, " if fn else "Hola, "
    monto = f" por ${total:,.2f}" if total is not None else ""
    if is_expired:
        return (f"{hi}la cotización {quote_number}{monto} que le enviamos ya venció. "
                f"Con gusto se la actualizo con precios vigentes si todavía le interesa. "
                f"¿Le parece? 🙂")
    seen = ("vi que revisó nuestra cotización" if was_viewed
            else "le compartimos la cotización")
    return (f"{hi}{seen} {quote_number}{monto} hace {days_since_sent} días. "
            f"¿Tiene alguna duda o le gustaría que ajustemos algo para avanzar? "
            f"Quedo atento. 🙂")


def draft_quote_followup(
    quote_number: str,
    total: Optional[float] = None,
    customer_name: Optional[str] = None,
    days_since_sent: Optional[int] = None,
    was_viewed: bool = False,
    is_expired: bool = False,
    conversation_history: Optional[List[Dict]] = None,
) -> Dict:
    """Draft a short proactive follow-up for a stalled quote.

    Grounded in the quote's own facts (number, total, age, expiry) — no catalog RAG
    needed. Never raises: on any error returns the deterministic template so the
    approval queue always gets a usable draft. Returns {draft_text, model, error?}.
    """
    days = days_since_sent if days_since_sent is not None else 0
    fallback = _followup_template(quote_number, total, customer_name, days,
                                  was_viewed, is_expired)

    monto = f"${total:,.2f}" if total is not None else "(monto no disponible)"
    if is_expired:
        estado = ("La cotización YA VENCIÓ (pasó su vigencia). Ofrece actualizarla con "
                  "precios vigentes; NO afirmes que sigue válida.")
    elif was_viewed:
        estado = "El cliente YA abrió/revisó la cotización."
    else:
        estado = "La cotización se envió pero no hay confirmación de que la haya abierto."
    user_turn = (
        f"Datos de la cotización a dar seguimiento:\n"
        f"- Número: {quote_number}\n"
        f"- Monto total: {monto}\n"
        f"- Cliente: {customer_name or '(sin nombre)'}\n"
        f"- Días desde el envío: {days}\n"
        f"- Estado: {estado}\n\n"
        f"Historial de conversación previo (si hay):\n{_format_history(conversation_history)}\n\n"
        f"Redacta el mensaje de seguimiento:"
    )
    try:
        client = anthropic.Anthropic(api_key=claude_api_key)
        resp = client.messages.create(
            model=DRAFTER_MODEL,
            max_tokens=MAX_DRAFT_TOKENS,
            temperature=0.4,
            system=FOLLOWUP_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_turn}],
        )
        draft = resp.content[0].text.strip()
        return {"draft_text": draft or fallback, "model": DRAFTER_MODEL}
    except Exception as e:
        print(f"WhatsApp follow-up drafter: LLM call failed: {e}")
        return {"draft_text": fallback, "model": DRAFTER_MODEL, "error": str(e)}


def _format_history(conversation_history: Optional[List[Dict]]) -> str:
    """Render prior turns. Accepts dicts with either role ('user'/'assistant')
    or direction ('inbound'/'outbound') keys plus 'content'."""
    if not conversation_history:
        return "(sin historial previo)"
    lines = []
    for msg in conversation_history[-12:]:
        role = msg.get("role") or msg.get("direction") or ""
        who = "Cliente" if role in ("user", "inbound") else "IMPAG"
        content = (msg.get("content") or "").strip()
        if content:
            lines.append(f"{who}: {content}")
    return "\n".join(lines) or "(sin historial previo)"


def draft_whatsapp_reply(
    latest_message: str,
    conversation_history: Optional[List[Dict]] = None,
    customer_name: Optional[str] = None,
) -> Dict:
    """
    Draft a grounded Spanish reply to the customer's latest message.

    Returns {draft_text, product_context, model, error?}. Never raises for the
    normal failure modes — on error returns a safe fallback draft so the
    operator always has something to review/replace.
    """
    from rag_system_moved.embeddings import generate_embeddings
    from rag_system_moved.rag_system import get_relevant_products

    product_context = ""
    doc_context = ""
    try:
        emb = generate_embeddings([latest_message])[0]
        # (1) Live catalog + pricing from the product DB (pgvector + rerank).
        product_context = get_relevant_products(
            emb, query_text=latest_message, max_products=8
        )
        # (2) Hybrid lexical+dense document search over catalogs / past quotes /
        # invoices. The lexical arm matches product names (e.g. "cintilla") by
        # exact term, which pure pgvector on a short conversational query misses.
        try:
            from services.hybrid_search import hybrid_search
            hits = hybrid_search(latest_message, emb, top_k=6,
                                 namespaces=["catalogos", "cotizaciones", "facturas"])
            lines = []
            for h in hits:
                txt = (h.get("metadata") or {}).get("text", "")
                if txt:
                    lines.append(txt[:400])
            doc_context = "\n---\n".join(lines)
        except Exception as e:
            print(f"WhatsApp drafter: document retrieval failed: {e}")
    except Exception as e:
        print(f"WhatsApp drafter: product retrieval failed: {e}")

    history_text = _format_history(conversation_history)
    who = f"El cliente se llama {customer_name}.\n\n" if customer_name else ""
    docs_block = (
        f"Referencias de catálogos y cotizaciones previas:\n{doc_context}\n\n"
        if doc_context else ""
    )
    user_turn = (
        f"{who}Contexto de productos disponibles (de la base de datos):\n"
        f"{product_context or '(sin coincidencias de producto)'}\n\n"
        f"{docs_block}"
        f"Historial de conversación:\n{history_text}\n\n"
        f"Último mensaje del cliente:\n{latest_message}\n\n"
        f"Redacta la respuesta:"
    )

    try:
        client = anthropic.Anthropic(api_key=claude_api_key)
        resp = client.messages.create(
            model=DRAFTER_MODEL,
            max_tokens=MAX_DRAFT_TOKENS,
            temperature=0.3,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_turn}],
        )
        draft = resp.content[0].text.strip()
        full_context = product_context + (("\n\n" + docs_block) if docs_block else "")
        return {"draft_text": draft, "product_context": full_context, "model": DRAFTER_MODEL}
    except Exception as e:
        print(f"WhatsApp drafter: LLM call failed: {e}")
        return {
            "draft_text": "Gracias por su mensaje, en un momento le atendemos.",
            "product_context": product_context,
            "model": DRAFTER_MODEL,
            "error": str(e),
        }
