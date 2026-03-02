"""
Claude-based extraction of structured logistics/packaging data from document text.
"""
import json
import anthropic
from typing import Dict
from config import claude_api_key


def extract_logistics_data(extracted_text: str) -> Dict:
    """
    Use Claude to extract structured logistics information from document text.
    Returns a dict with logistics fields.
    """
    client = anthropic.Anthropic(api_key=claude_api_key)

    prompt = f"""Analyze this document text and extract packaging/logistics information.
Return a JSON object with these fields (use null for unknown values):

{{
    "product_name": "Product being shipped/packaged",
    "quantity": 100,
    "package_size": "25kg bags",
    "package_type": "pallet | box | sack | roll | container | other",
    "weight_kg": 500.0,
    "dimensions": "120x80x100 cm",
    "origin": "City/warehouse of origin",
    "destination": "City/warehouse of destination",
    "carrier": "Shipping carrier name",
    "tracking_number": "Tracking number if available",
    "estimated_delivery": "2024-03-15",
    "cost": 15000.00,
    "currency": "MXN or USD",
    "confidence": "high | medium | low"
}}

IMPORTANT:
- Extract ALL available fields from the document
- For costs, extract the shipping/logistics cost, not the product cost
- If the document is a shipping label, extract tracking and carrier info
- If the document is a packing list, extract quantities and package details
- If the document is a logistics quotation, extract costs and routes
- Return ONLY valid JSON, no additional text

<document_text>
{extracted_text[:8000]}
</document_text>"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        temperature=0,
        messages=[{"role": "user", "content": prompt}]
    )

    response_text = message.content[0].text.strip()

    try:
        # Handle potential markdown code blocks
        if response_text.startswith('```'):
            response_text = response_text.split('```')[1]
            if response_text.startswith('json'):
                response_text = response_text[4:]
        data = json.loads(response_text)
        return data
    except json.JSONDecodeError:
        return {"error": "Failed to parse extraction response", "raw": response_text}
