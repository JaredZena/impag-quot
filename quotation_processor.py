import re
import json
import os
import fitz  # PyMuPDF
import anthropic
import copy
from PIL import Image

# OCR using Tesseract
try:
    import pytesseract
    HAS_OCR = True
    print("✅ Tesseract imported successfully")
except ImportError:
    HAS_OCR = False
    print("❌ Tesseract not available")
from sqlalchemy.orm import Session
from typing import Dict, List, Optional
from models import (
    Supplier, Product, SupplierProduct, 
    ProductCategory, ProductUnit, SessionLocal
)
from utils.currency_utils import CurrencyUtils
from utils.json_repair import JSONRepair

class HybridSKUGenerator:
    def __init__(self):
        print('Initializing SKU generator')
        """SKU generator that combines AI suggestions with code consistency."""
        
        # Predefined rules for consistent variant generation
        self.variant_rules = {
            'volume_units': {'litros': 'L', 'cc': 'CC', 'ml': 'ML'},
            'weight_units': {'kg': 'KG', 'gramos': 'G'},
            'dimension_units': {'pulgadas': 'IN', 'cm': 'CM', 'm': 'M'},
            'grade_mapping': {'fino': 'F', 'medio': 'M', 'grueso': 'G'},
            'material_abbrev': {
                'aluminio': 'ALU', 'plastico': 'PLA', 'acero': 'ACE',
                'polipropileno': 'PP', 'pvc': 'PVC'
            }
        }

    def generate_base_sku_with_code(self, product_name: str) -> str:
        """Fallback code-based base SKU generation."""
        print(f'Generating base SKU with code for {product_name}')
        words = product_name.upper().split()
        meaningful_words = [w for w in words if len(w) > 2 and w not in ['DE', 'CON', 'PARA', 'DEL', 'LA', 'EL']]
        
        if len(meaningful_words) >= 2:
            return f"{meaningful_words[0][:4]}-{meaningful_words[1][:3]}"
        elif meaningful_words:
            return meaningful_words[0][:8]
        else:
            return re.sub(r"[^\w]+", "-", product_name.strip().upper())[:10]

    def generate_variant_sku_with_code(self, base_sku: str, specifications: Dict) -> str:
        """Generate variant SKU using consistent code rules."""
        print(f'Generating variant SKU with code for {base_sku} and specifications: {specifications}')
        sku_parts = [base_sku]
        
        # Volume/Capacity
        for vol_key in ['volumen_litros', 'capacidad_litros']:
            if vol_key in specifications:
                sku_parts.append(f"{int(specifications[vol_key])}L")
                break
        
        for cap_key in ['capacidad_cc', 'capacidad_ml']:
            if cap_key in specifications:
                unit = 'CC' if 'cc' in cap_key else 'ML'
                sku_parts.append(f"{int(specifications[cap_key])}{unit}")
                break
        
        # Dimensions
        if 'medida' in specifications:
            medida = specifications['medida'].replace('x', 'X').replace(' ', '')
            sku_parts.append(medida)
        elif 'ancho' in specifications and 'largo' in specifications:
            sku_parts.append(f"{specifications['ancho']}X{specifications['largo']}")
        elif 'diametro' in specifications:
            sku_parts.append(f"D{specifications['diametro']}")
        
        # Technical specs
        if 'calibre' in specifications:
            sku_parts.append(f"{specifications['calibre']}G")
        
        if 'cavidades' in specifications:
            sku_parts.append(f"{specifications['cavidades']}CAV")
        
        # Grade/Type
        if 'grado' in specifications:
            grade = specifications['grado'].lower()
            if grade in self.variant_rules['grade_mapping']:
                sku_parts.append(self.variant_rules['grade_mapping'][grade])
            else:
                sku_parts.append((grade or '').upper()[:3])
        
        # Material
        if 'material' in specifications:
            material = specifications['material'].lower()
            if material in self.variant_rules['material_abbrev']:
                sku_parts.append(self.variant_rules['material_abbrev'][material])
        
        # Color (if significant)
        if 'color' in specifications and specifications['color'].lower() not in ['natural', 'transparente']:
            sku_parts.append((specifications['color'] or '')[:3].upper())
        
        return "-".join(sku_parts)

    def get_base_sku(self, product_info: Dict) -> str:
        """Get base SKU from AI suggestion or generate with code."""
        print(f"get_base_sku called with category_id: {product_info.get('category_id')}")
        
        if 'suggested_base_sku' in product_info and product_info['suggested_base_sku']:
            # Validate and clean AI suggestion
            ai_sku = (product_info['suggested_base_sku'] or '').upper().strip()
            # Remove any invalid characters and ensure reasonable length
            ai_sku = re.sub(r'[^A-Z0-9-]', '', ai_sku)[:12]
            if len(ai_sku) >= 3:  # Minimum viable SKU length
                return ai_sku
        
        # Fallback to code generation
        return self.generate_base_sku_with_code(product_info["name"])

    def get_variant_sku(self, base_sku: str, specifications: Dict) -> str:
        """Generate variant SKU using code rules for consistency."""
        return self.generate_variant_sku_with_code(base_sku, specifications)

class QuotationProcessor:
    def __init__(self, anthropic_api_key: str, use_claude_opus: bool = False):
        """
        Initialize the quotation processor.
        
        Args:
            anthropic_api_key: Your Anthropic API key
            use_claude_opus: True for claude-3-opus (highest accuracy), False for claude-3-sonnet (faster, cheaper)
        """
        self.client = anthropic.Anthropic(api_key=anthropic_api_key)
        self.model = "claude-sonnet-4-20250514"
        self.sku_generator = HybridSKUGenerator()
        
    def get_categories(self, session: Session) -> List[Dict]:
        """Get all product categories from the database."""
        print('Getting categories')
        categories = session.query(ProductCategory).all()
        return [
            {
                "id": cat.id,
                "name": cat.name,
                "slug": cat.slug
            }
            for cat in categories
        ]
        
    def extract_text_from_pdf(self, pdf_path: str) -> str:
        """Extract text from PDF file."""
        try:
            doc = fitz.open(pdf_path)
            text = "\n".join([page.get_text() for page in doc])
            doc.close()
            return text
        except Exception as e:
            raise Exception(f"Error extracting text from PDF: {str(e)}")
    
    def extract_text_from_image(self, image_path: str) -> str:
        """Extract text from image file using Claude Vision API."""
        try:
            import base64
            
            # Read and encode the image
            with open(image_path, 'rb') as image_file:
                image_data = image_file.read()
            
            # Encode to base64
            image_base64 = base64.b64encode(image_data).decode('utf-8')
            
            # Determine media type based on file extension
            file_extension = os.path.splitext(image_path.lower())[1]
            media_type_map = {
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.gif': 'image/gif',
                '.bmp': 'image/bmp',
                '.tiff': 'image/tiff',
                '.webp': 'image/webp'
            }
            media_type = media_type_map.get(file_extension, 'image/jpeg')
            
            print(f"🔧 Claude Vision processing image: {image_path}")
            
            # Create the message for Claude
            message = self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                temperature=0,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": image_base64
                                }
                            },
                            {
                                "type": "text",
                                "text": """Please extract ALL text from this image. This appears to be a quotation or invoice document in Spanish or English. 

Extract the text exactly as it appears, maintaining the structure and formatting as much as possible. Include:
- All product names and descriptions
- All prices, quantities, and costs
- Supplier/company information
- Dates and reference numbers
- Any other text visible in the image

Return only the extracted text, without any additional commentary or analysis."""
                            }
                        ]
                    }
                ]
            )
            
            # Extract the text content from Claude's response
            extracted_text = message.content[0].text.strip()
            print(f"✅ Claude Vision completed successfully")
            
            if not extracted_text:
                raise Exception("No text could be extracted from the image")
            
            return extracted_text
            
        except Exception as e:
            print(f"❌ Error with Claude Vision: {str(e)}")
            # Fallback to pytesseract if available
            if HAS_OCR:
                print("🔄 Falling back to Tesseract OCR...")
                try:
                    from PIL import Image
                    image = Image.open(image_path)
                    custom_config = r'-l eng+spa --psm 6'
                    extracted_text = pytesseract.image_to_string(image, config=custom_config)
                    extracted_text = extracted_text.strip()
                    if extracted_text:
                        print(f"✅ Tesseract fallback completed successfully")
                        return extracted_text
                except Exception as fallback_error:
                    print(f"❌ Tesseract fallback also failed: {str(fallback_error)}")
            
            raise Exception(f"Error extracting text from image with Claude Vision: {str(e)}")
    
    def extract_text_from_txt(self, file_path: str) -> str:
        """Extract text from a .txt file (like WhatsApp conversation exports)."""
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                content = file.read().strip()
                
            if not content:
                raise Exception("The text file is empty")
                
            print(f"✅ Text file read successfully ({len(content)} characters)")
            return content
            
        except UnicodeDecodeError:
            # Try with different encoding if UTF-8 fails
            try:
                with open(file_path, 'r', encoding='latin-1') as file:
                    content = file.read().strip()
                    
                if not content:
                    raise Exception("The text file is empty")
                    
                print(f"✅ Text file read successfully with latin-1 encoding ({len(content)} characters)")
                return content
                
            except Exception as fallback_error:
                raise Exception(f"Error reading text file with fallback encoding: {str(fallback_error)}")
                
        except Exception as e:
            raise Exception(f"Error reading text file: {str(e)}")
    
    def is_image_file(self, file_path: str) -> bool:
        """Check if file is a supported image format."""
        image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp'}
        return os.path.splitext(file_path.lower())[1] in image_extensions
    
    def is_pdf_file(self, file_path: str) -> bool:
        """Check if file is a PDF."""
        return os.path.splitext(file_path.lower())[1] == '.pdf'
    
    def is_text_file(self, file_path: str) -> bool:
        """Check if file is a text file (.txt)."""
        return os.path.splitext(file_path.lower())[1] == '.txt'

    def get_suggested_category(self, product_name: str, description: str = "") -> Optional[int]:
        """
        Keyword-based category suggestion to help guide AI decisions.
        Returns suggested category ID based on product keywords.
        """
        text = f"{product_name} {description}".upper()
        
        # Category keyword mappings
        category_keywords = {
            1: ["SOMBRA", "INVERNADERO", "PLASTICO", "FILM", "COBERTURA", "VENTILACION", "CALEFACCION", "RAFIA", "ESTRUCTURA"],
            2: ["RIEGO", "ASPERSOR", "GOTERO", "VALVULA", "CONTROLADOR", "FILTRO", "TUBERIA", "MANGUERA", "CONEXION"],
            3: ["CHAROLA", "MACETA", "SUSTRATO", "GERMINACION", "PROPAGACION", "SEMILLA", "PLANTULA"],
            4: ["FERTILIZANTE", "PESTICIDA", "FUNGICIDA", "HERBICIDA", "NUTRICION", "PROTECCION", "REGULADOR", "AMENDMENT", "TRAMPA"],
            5: ["APLICADOR", "ASPERSOR", "DOSIFICADOR", "MEZCLADOR", "PULVERIZADOR", "DIFUSOR"],
            6: ["SOLAR", "PANEL", "INVERSOR", "BATERIA", "ENERGIA", "RENOVABLE", "FOTOVOLT", "ONDULA", "SENOIDAL", "MODIFICADA", "CICLO", "PROFUNDO", "AGM", "VCD", "AH", "CONTROLADOR", "SOLAR", "VCC", "POLI", "CEL", "CONECTOR", "MC4"],
            7: ["BOMBA", "BOMBEO", "SUBMERSIBLE", "CENTRIFUGA", "DIAFRAGMA", "IMPULSION"],
            8: ["MEMBRANA", "IMPERMEABILIZACION", "BARRERA", "PROTECCION"],
            9: ["MALLA", "RED", "ANTIGRANIZO", "CORTINA", "SOPORTE"],
            10: ["ACOLCHADO", "MULCH", "COBERTURA", "PROTECCION"],
            11: ["TECNOLOGIA", "AUTOMATIZACION", "PRECISION", "MONITOREO", "CONTROL"],
            12: ["HIDROPONIA", "HIDROPONICO", "NUTRIENTE", "MEDIO", "VERTICAL"],
            13: ["BOLSA", "CONTENEDOR", "EMPAQUE", "ALMACENAMIENTO", "TRANSPORTE"],
            14: ["CINTA", "MANGUERA", "TUBERIA", "PVC", "FLEXIBLE", "CONEXION", "POLIPATCH"],
            15: ["GEOTEXTIL", "TELA", "EROSION", "ESTABILIZACION"],
            16: ["SENSOR", "MONITOREO", "DATALOGGER", "ESTACION", "MEDICION"],
            17: ["ILUMINACION", "LUZ", "LED", "LUMINARIA", "CRECIMIENTO"],
            18: ["CONTROL", "TEMPORIZADOR", "AUTOMATIZACION", "SISTEMA"],
            19: ["CONSTRUCCION", "ESTRUCTURAL", "EDIFICACION", "MATERIAL"]
        }
        
        # Count keyword matches for each category
        category_scores = {}
        for cat_id, keywords in category_keywords.items():
            score = sum(1 for keyword in keywords if keyword in text)
            if score > 0:
                category_scores[cat_id] = score
        
        # Return the category with the highest score, or None if no matches
        if category_scores:
            return max(category_scores, key=category_scores.get)
        return None

    def _find_alternative_supplier(self, pdf_text: str) -> Optional[str]:
        """
        Simple fallback to find alternative supplier when IMPAG is incorrectly extracted.
        Just looks for any company name that's not IMPAG.
        """
        import re
        
        # Look for any words in ALL CAPS that might be company names
        # This is a simple heuristic - company names are often in caps in documents
        potential_companies = re.findall(r'\b[A-Z][A-Z\s\.]{3,20}\b', pdf_text.upper())
        
        for company in potential_companies:
            company_clean = company.strip()
            # Skip IMPAG and very short names
            if (company_clean and 
                'IMPAG' not in company_clean and 
                len(company_clean) > 3 and
                company_clean not in ['S.A.', 'S.A. DE C.V.', 'C.V.', 'INC', 'LLC', 'MEXICO', 'MEXICO D.F.']):
                return company_clean
        
        return None

    def preprocess_long_text(self, text: str, max_length: int = 12000) -> str:
        """
        Preprocess very long text (like WhatsApp conversations) to focus on product information.
        """
        if len(text) <= max_length:
            return text
        
        print(f"⚠️  Text is very long ({len(text)} chars), preprocessing to focus on product information...")
        
        lines = text.split('\n')
        important_lines = []
        current_length = 0
        
        # Keywords that indicate product-related messages
        product_keywords = [
            'precio', 'cotizar', 'cotización', 'disponible', 'pesos', 'usd', 'mxn', '$',
            'rollo', 'metro', 'pieza', 'kg', 'litros', 'cavidades', 'calibre',
            'charola', 'bolsa', 'malla', 'acolchado', 'calefactor', 'conector',
            'dimensiones', 'especificaciones', 'medida', 'material',
            'descripción', 'cantidad', 'unidad', 'importe', 'total', 'envio', 'costo',
            'geomembrana', 'hdpe', 'densidad'  # Common product terms
        ]
        
        # First pass: collect lines with product information
        for line in lines:
            line_lower = line.lower()
            
            # Skip very short lines or timestamps only
            if len(line.strip()) < 10:
                continue
                
            # Check if line contains product-related keywords
            has_product_info = any(keyword in line_lower for keyword in product_keywords)
            
            # Always include lines with prices
            has_price = '$' in line or 'precio' in line_lower or any(currency in line_lower for currency in ['usd', 'mxn', 'pesos'])
            
            # Include detailed product descriptions (longer lines with specifications)
            is_detailed = len(line) > 80 and ('=' in line or ':' in line or 'cm' in line_lower or 'mm' in line_lower)
            
            if has_product_info or has_price or is_detailed:
                if current_length + len(line) > max_length:
                    break
                important_lines.append(line)
                current_length += len(line)
        
        processed_text = '\n'.join(important_lines)
        print(f"✅ Reduced text from {len(text)} to {len(processed_text)} characters")
        
        return processed_text if processed_text else text[:max_length]

    def extract_structured_data(self, pdf_text: str, categories: List[Dict]) -> Dict:
        """
        Use Claude to extract structured supplier and product data from PDF text.
        Enhanced to include SKU suggestions and automatic category selection.
        """
        print("🔍 Preprocessing text...")
        # Preprocess very long text to focus on product information
        processed_text = self.preprocess_long_text(pdf_text)
        print(f"🔍 Processed text length: {len(processed_text)} characters")
        
        # Create detailed category descriptions with examples
        category_descriptions = {
            1: "Materiales para invernadero - Greenhouse materials: plastic films, shade cloth, thermal blankets, greenhouse structures, ventilation systems, heating systems, cooling systems, greenhouse accessories",
            2: "Equipos de Riego - Irrigation equipment: sprinklers, drippers, valves, controllers, pumps, filters, pipes, fittings, irrigation accessories",
            3: "Insumos para Propagacion - Propagation supplies: seed trays, pots, substrates, rooting hormones, propagation tools, seedling care products, germination supplies",
            4: "Nutricion y Proteccion Vegetal - Plant nutrition and protection: fertilizers, pesticides, fungicides, herbicides, plant growth regulators, soil amendments, foliar sprays",
            5: "Equipos de Aplicacion - Application equipment: sprayers, spreaders, applicators, dosing systems, mixing equipment, application accessories",
            6: "Sistemas de Energia Solar - Solar energy systems: solar panels, inverters, batteries, controllers, solar pumps, solar lighting, renewable energy equipment",
            7: "Equipos de Bombeo - Pumping equipment: water pumps, submersible pumps, centrifugal pumps, diaphragm pumps, pump accessories, pumping systems",
            8: "Membranas - Membranes: plastic membranes, waterproofing membranes, barrier films, protective films, membrane accessories",
            9: "Mallas - Meshes and nets: shade nets, anti-hail nets, windbreak nets, support nets, mesh accessories",
            10: "Acolchados - Mulches: plastic mulches, organic mulches, biodegradable mulches, mulch accessories",
            11: "Tecnologia para operaciones agricolas y pecuarias - Agricultural and livestock technology: monitoring systems, automation equipment, precision agriculture tools, livestock equipment",
            12: "Hidroponia - Hydroponics: hydroponic systems, nutrient solutions, growing media, hydroponic accessories, vertical farming equipment",
            13: "Bolsas y Contenedores - Bags and containers: grow bags, storage containers, transport containers, packaging materials",
            14: "Mangueras y Tuberías - Hoses and pipes: irrigation hoses, PVC pipes, flexible hoses, pipe fittings, hose accessories",
            15: "Geotextiles - Geotextiles: landscape fabrics, erosion control materials, soil stabilization products, geotextile accessories",
            16: "Equipos de Monitoreo - Monitoring equipment: sensors, data loggers, weather stations, soil monitoring, environmental monitoring",
            17: "Sistemas de Iluminación - Lighting systems: grow lights, LED systems, lighting controllers, light accessories",
            18: "Equipos de Control - Control equipment: timers, controllers, automation systems, control accessories",
            19: "Materiales de Construcción - Construction materials: structural materials, building supplies, construction accessories"
        }
        
        categories_text = "\n".join([
            f"- ID: {cat['id']}, Name: {cat['name']}, Description: {category_descriptions.get(cat['id'], 'General category')}"
            for cat in categories
        ])
        
        prompt = """You are an assistant that extracts structured information from supplier quotations and product information for a procurement system.

<document_text>
{processed_text}
</document_text>

<available_categories>
{categories_text}
</available_categories>

IMPORTANT: This document could be:
1. A traditional quotation/invoice (PDF or image)
2. A WhatsApp conversation export (.txt file) containing product discussions
3. Tabular data from Google Sheets/Excel with product information
4. Any other document format containing product and supplier information

For WhatsApp conversations:
- Look for messages that mention products, prices, quantities
- The supplier is typically the person/business sending product information
- Prices may be mentioned in various formats: "$100", "100 pesos", "cuesta 50", "USD 25", "25 dollars", etc.
- Products may be described informally: "las mallas", "el tubo de 4 pulgadas", etc.
- Pay attention to timestamps and sender names to identify the supplier
- Look for product specifications in casual language
- For VERY LONG conversations: Focus on messages that contain actual product offers with prices
- Skip general conversation, greetings, and messages without product information
- Prioritize clear product descriptions with specifications and pricing
- CURRENCY DETECTION: Look for USD indicators like "USD", "dollars", "US$", "$" (when context suggests USD), "dólares"

For Google Sheets/Excel data:
- Look for column headers like "Descripción", "Cantidad", "Precio", "Unidad", "Importe"
- Each row typically represents one product
- Extract product names from description columns
- Parse prices from price/import columns (may include $ symbols and formatting)
- Convert quantities and units appropriately
- If supplier info is not in the table, create a generic supplier entry
- Handle currency symbols and number formatting (e.g., "$ 86.92", "$3,042.10", "USD 25.50", "US$ 100")
- CURRENCY DETECTION: Look for USD indicators in headers or data like "USD", "US$", "dollars", "dólares"

SHIPPING COST EXTRACTION FOR MULTI-STAGE LOGISTICS:
- Look for shipping cost columns such as:
  * "Envio Dgo Ocurre-Manzanita Dgo U" (Stage 2 shipping cost)
  * "Envio Dgo Manzanita Dgo-Manzanita NI U" (Stage 3 shipping cost)  
  * "EnvioManzanita NI-IMPAG U" (Stage 4 shipping cost)
  * Any other shipping/freight cost columns with "U" suffix (unit cost)
- Extract unit shipping costs from columns ending with "U" 
- Ignore total shipping columns ending with "Total"
- If ANY shipping stage costs are found, set shipping_method to "OCURRE"
- If no shipping stage costs found, set shipping_method to "DIRECT"

Please return a JSON object with:

1. `products`: an array of objects, each with:
   - `name`: full product name
   - `description`: short human-readable description
   - `suggested_base_sku`: a concise, meaningful base SKU (6-12 chars, format examples: "VH-4IN", "PERL-MED", "MESA-25")
   - `unit`: one of "PIEZA", "KG", "ROLLO", "METRO" (convert from Spanish units to these exact uppercase values)
   - `iva`: true if the product includes IVA, otherwise false
   - `cost`: unit cost as a float (WITHOUT shipping costs)
   - `currency`: currency of the cost ("MXN" or "USD") - detect from context, symbols, or explicit mentions
   - `shipping_method`: "DIRECT" or "OCURRE" based on shipping cost extraction
   - `shipping_stage1_cost`: stage 1 shipping cost as float (default 0.0)
   - `shipping_stage2_cost`: stage 2 shipping cost as float (default 0.0) 
   - `shipping_stage3_cost`: stage 3 shipping cost as float (default 0.0)
   - `shipping_stage4_cost`: stage 4 shipping cost as float (default 0.0)
   - `shipping_notes`: optional notes about shipping logistics (string)
   - `specifications`: a dictionary of key attributes like size, volume, dimensions, material, etc.
   - `category_id`: the ID of the most appropriate category from the available_categories list
   - `supplier`: an object with the following fields for THIS SPECIFIC PRODUCT:
     - `name`
     - `legal_name` (if available, otherwise use name)
     - `rfc` (Mexican tax ID, format: 4 letters + 6 digits + 3 alphanumeric)
     - `address`
     - `email`
     - `phone`
     - `website_url` (if available, full URL including http/https)
     - `contact_name` (if identifiable)

IMPORTANT SUPPLIER EXTRACTION RULES:
- DO NOT extract "IMPAG", "IMPAG TECH", or any variation of "IMPAG" as the supplier name
- IMPAG is the company RECEIVING the quotation, not the supplier sending it
- Look for the actual supplier/vendor company name that is providing each product
- Each product can have a different supplier - extract the supplier for each product individually
- If the document shows multiple suppliers (like in a table with a "PROVEEDOR" column), extract the specific supplier for each product
- If you can't determine a specific supplier for a product, use the most likely supplier based on context

FOR WHATSAPP CONVERSATIONS:
- The supplier is typically the contact/person who is SENDING the product information
- Look for sender names in the conversation format (usually "Contact Name:" or similar)
- If no clear business name is provided, use the contact name as the supplier name
- Look for any business names mentioned in signatures or profiles
- If multiple people are discussing products, identify who is the actual supplier vs customer
- Example: "Juan Pérez: Tengo disponible malla sombra 50% a $120 el rollo"
  → Supplier name: "Juan Pérez" (unless a business name is mentioned)

For base SKU suggestions, create meaningful abbreviations:
- Use category abbreviation + key distinguishing features
- "Válvula Hidrante 4 pulgadas" → "VH-4IN"
- "Perlita Grado Medio 100 Litros" → "PERL-MED"
- "Mesa 25 Cavidades 380ml" → "MESA-25"
- "Codo Aluminio 4x4" → "CODO-ALU"

For category selection, carefully analyze the product and choose the most specific category:

KEYWORD GUIDANCE:
- Products containing "TRAMPA" → Category 4 (Nutricion y Proteccion Vegetal) - pest control products
- Products containing "SOMBRA" → Category 1 (Materiales para invernadero) - greenhouse shading materials
- Products containing "CINTA" → Category 14 (Mangueras y Tuberías) - tapes for irrigation/plumbing
- Products containing "RAFIA" → Category 1 (Materiales para invernadero) - twine for greenhouse support
- Products containing "POLIPATCH" → Category 14 (Mangueras y Tuberías) - repair tapes for hoses/pipes

SOLAR ENERGY PRODUCTS (Category 6):
- Products containing "SOLAR", "FOTOVOLT", "PANEL", "INVERSOR", "BATERIA", "CONTROLADOR SOLAR" → Category 6
- Products containing "ONDULA", "SENOIDAL", "MODIFICADA" → Category 6 (inverters)
- Products containing "CICLO PROFUNDO", "AGM", "VCD", "AH" → Category 6 (batteries)
- Products containing "VCC", "POLI", "CEL" → Category 6 (solar panels)
- Products containing "CONECTOR", "MC4" → Category 6 (solar connectors)

CATEGORY EXAMPLES:
- Category 1 (Materiales para invernadero): Plastic films, shade cloth, thermal blankets, greenhouse structures, ventilation systems, heating systems, cooling systems, greenhouse accessories, twine
- Category 2 (Equipos de Riego): Sprinklers, drippers, valves, controllers, pumps, filters, pipes, fittings, irrigation accessories
- Category 3 (Insumos para Propagacion): Seed trays, pots, substrates, rooting hormones, propagation tools, seedling care products, germination supplies
- Category 4 (Nutricion y Proteccion Vegetal): Fertilizers, pesticides, fungicides, herbicides, plant growth regulators, soil amendments, foliar sprays, pest traps
- Category 6 (Sistemas de Energia Solar): Solar panels, inverters, batteries, solar controllers, solar pumps, solar lighting, renewable energy equipment, MC4 connectors, solar accessories
- Category 13 (Bolsas y Contenedores): Grow bags, storage containers, transport containers, packaging materials, shipping services, freight services
- Category 14 (Mangueras y Tuberías): Irrigation hoses, PVC pipes, flexible hoses, pipe fittings, hose accessories, repair tapes

IMPORTANT: Avoid defaulting to Category 3 unless the product is specifically for propagation or seedling care.

IMPORTANT: The unit field MUST be one of these exact values: "PIEZA", "KG", "ROLLO", "METRO" (all uppercase)

CURRENCY DETECTION RULES:
- Look for explicit currency indicators: "USD", "US$", "dollars", "dólares", "USD$"
- Look for context clues: "US prices", "American prices", "precios en dólares"
- If document mentions "USD" anywhere or has "US$" symbols, assume USD for all prices
- If document mentions "pesos", "MXN", "Mexican", assume MXN for all prices
- If no clear currency indicators, default to MXN
- When in doubt, look for price patterns: USD prices are typically higher (e.g., $25 vs $500 MXN)
- Consider document language: English documents more likely to be USD, Spanish documents more likely to be MXN

CRITICAL JSON FORMAT REQUIREMENTS:
- Return ONLY a valid JSON object, no additional text
- Ensure all strings are properly quoted and escaped
- Limit product descriptions to 200 characters maximum
- If the conversation is very long, focus on the 10-15 most important products with clear pricing
- Ensure the response is under 4000 tokens to avoid truncation
- Use proper JSON escaping for special characters (quotes, newlines, etc.)
- NO trailing commas in JSON objects or arrays
- NO unescaped quotes in string values
- NO comments or explanations outside the JSON

EXAMPLE VALID JSON FORMAT:
{
  "products": [
    {
      "name": "Product Name",
      "description": "Short description",
      "cost": 25.50,
      "currency": "USD",
      "unit": "PIEZA",
      "iva": true,
      "category_id": 1,
      "supplier": {
        "name": "Supplier Name",
        "email": "supplier@example.com"
      }
    }
  ]
}

Respond only with the JSON object, no extra explanation.""".replace(
            "{processed_text}", processed_text
        ).replace(
            "{categories_text}", categories_text
        )

        try:
            print("🤖 Calling Claude AI...")
            # Try with full token limit first
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                temperature=0,
                messages=[{"role": "user", "content": prompt}]
            )
            
            print("🤖 AI response received, processing...")
            content = response.content[0].text.strip()
            # Remove potential markdown formatting
            if content.startswith("```json"):
                content = content[7:-3]
            elif content.startswith("```"):
                content = content[3:-3]
            
            # Try to parse the JSON, with error handling for malformed responses
            print("🔍 Attempting JSON parsing...")
            try:
                data = json.loads(content)
                print("✅ JSON parsed successfully")
            except json.JSONDecodeError as e:
                print(f"❌ JSON parsing error: {str(e)}")
                print(f"Content length: {len(content)} characters")
                print(f"Content preview: {content[:500]}...")
                print(f"Content starts with: {repr(content[:50])}")
                print(f"Content ends with: {repr(content[-50:])}")
                
                # Try JSON repair utility first
                print("🔧 Attempting JSON repair...")
                data = JSONRepair.repair_json(content)
                if data:
                    print("✅ Successfully repaired JSON")
                else:
                    print("❌ JSON repair failed, trying extraction...")
                    # Try extracting products array
                    print("🔧 Attempting to extract products array...")
                    data = JSONRepair.extract_products_array(content)
                    if data:
                        print("✅ Successfully extracted products array")
                    else:
                        print("❌ All repair attempts failed, retrying with reduced tokens...")
                        # If all else fails, try with reduced token limit
                        print("🔄 Retrying with reduced token limit...")
                        try:
                            retry_response = self.client.messages.create(
                                model=self.model,
                                max_tokens=2000,  # Reduced token limit
                                temperature=0,
                                messages=[{"role": "user", "content": prompt}]
                            )
                            
                            retry_content = retry_response.content[0].text.strip()
                            if retry_content.startswith("```json"):
                                retry_content = retry_content[7:-3]
                            elif retry_content.startswith("```"):
                                retry_content = retry_content[3:-3]
                            
                            data = json.loads(retry_content)
                            print("✅ Successfully parsed JSON with reduced token limit")
                            
                        except Exception as retry_error:
                            print(f"❌ All repair attempts failed: {str(retry_error)}")
                            print(f"🔍 Final category verification: Extracted data from Claude: {data}")
                            data = {
                                "products": [],
                                "error": f"Failed to parse AI response as JSON: {str(e)}"
                            }
            
            # Post-process supplier information for each product to ensure IMPAG is not extracted as supplier
            if 'products' in data:
                for product in data['products']:
                    if 'supplier' in product:
                        supplier_info = product['supplier']
                        supplier_name = (supplier_info.get('name') or '').upper()
                        
                        # Check if the extracted supplier name contains IMPAG
                        if 'IMPAG' in supplier_name:
                            print(f"\n⚠️  IMPAG detected in supplier name for product '{product.get('name')}': {supplier_info.get('name')}")
                            print("   IMPAG is the receiving company, not the supplier")
                            
                            # Try to find alternative supplier name in the text
                            alternative_supplier = self._find_alternative_supplier(pdf_text)
                            if alternative_supplier:
                                print(f"   → Using alternative: {alternative_supplier}")
                                supplier_info['name'] = alternative_supplier
                                supplier_info['legal_name'] = alternative_supplier
                            else:
                                print("   → Manual review required - no alternative found")
                                # Set a default supplier name if no alternative found
                                supplier_info['name'] = "Unknown Supplier"
                                supplier_info['legal_name'] = "Unknown Supplier"
            
            # Post-process category assignments using keyword analysis
            if 'products' in data:
                for product in data['products']:
                    product_name = product.get('name', '')
                    description = product.get('description', '')
                    current_category = product.get('category_id')
                    
                    print(f"\n🔍 Analyzing category for: '{product_name}'")
                    print(f"   Current category: {current_category}")
                    
                    # Get keyword-based suggestion
                    suggested_category = self.get_suggested_category(product_name, description)
                    print(f"   Keyword suggestion: {suggested_category}")
                    
                    # Enhanced category override logic
                    text = f"{product_name} {description}".upper()
                    print(f"   Analyzing text: {text}")
                    
                    # Strong override for solar products (Category 6)
                    solar_keywords = ["SOLAR", "FOTOVOLT", "PANEL", "INVERSOR", "BATERIA", "CONTROLADOR SOLAR", "ONDULA", "SENOIDAL", "MODIFICADA", "CICLO PROFUNDO", "AGM", "VCD", "AH", "VCC", "POLI", "CEL", "CONECTOR", "MC4"]
                    solar_matches = [keyword for keyword in solar_keywords if keyword in text]
                    if solar_matches:
                        print(f"   Solar keywords found: {solar_matches}")
                        if current_category != 6:
                            print(f"\n🔧 FORCING Category 6 for solar product: '{product_name}'")
                            print(f"  AI chose: Category {current_category}")
                            print(f"  OVERRIDING to Category 6 (Solar Energy Systems)")
                            product['category_id'] = 6
                        else:
                            print(f"   Already Category 6, no override needed")
                        continue
                    
                    # Strong override for shipping/transport (Category 13)
                    shipping_keywords = ["VIAS", "EMBARQUE", "VOLUMEN", "TERRESTRE"]
                    shipping_matches = [keyword for keyword in shipping_keywords if keyword in text]
                    if shipping_matches:
                        print(f"   Shipping keywords found: {shipping_matches}")
                        if current_category != 13:
                            print(f"\n🔧 FORCING Category 13 for shipping product: '{product_name}'")
                            print(f"  AI chose: Category {current_category}")
                            print(f"  OVERRIDING to Category 13 (Bags and Containers)")
                            product['category_id'] = 13
                        else:
                            print(f"   Already Category 13, no override needed")
                        continue
                    
                    # If AI chose category 3 but keyword analysis suggests something else, consider overriding
                    if current_category == 3 and suggested_category and suggested_category != 3:
                        print(f"\nCategory override suggestion for '{product_name}':")
                        print(f"  AI chose: Category 3 (Insumos para Propagacion)")
                        print(f"  Keywords suggest: Category {suggested_category}")
                        
                        # Only override if keyword confidence is high (multiple keyword matches)
                        category_keywords = {
                            1: ["SOMBRA", "INVERNADERO", "PLASTICO", "FILM", "COBERTURA", "VENTILACION", "CALEFACCION", "RAFIA", "ESTRUCTURA"],
                            2: ["RIEGO", "ASPERSOR", "GOTERO", "VALVULA", "CONTROLADOR", "FILTRO", "TUBERIA", "MANGUERA", "CONEXION"],
                            4: ["TRAMPA", "PESTICIDA", "FUNGICIDA", "HERBICIDA", "PROTECCION"],
                            14: ["CINTA", "MANGUERA", "TUBERIA", "PVC", "FLEXIBLE", "CONEXION", "POLIPATCH"],
                        }
                        
                        if suggested_category in category_keywords:
                            keyword_matches = sum(1 for keyword in category_keywords[suggested_category] if keyword in text)
                            if keyword_matches >= 2:  # High confidence threshold
                                print(f"  OVERRIDING to Category {suggested_category} (confidence: {keyword_matches} keywords)")
                                product['category_id'] = suggested_category
                            else:
                                print(f"  Keeping Category 3 (low keyword confidence: {keyword_matches})")
                        else:
                            print(f"  Keeping Category 3 (no specific keyword guidance)")
            
            # Final aggressive override for solar products that might have been missed
            print(f"\n🔍 Final category verification:")
            for product in data['products']:
                product_name = product.get('name', '')
                final_category = product.get('category_id')
                text = f"{product_name}".upper()
                
                # Check for obvious solar terms and force category 6
                obvious_solar_terms = ["INV.", "SAMLEX", "ONDULA", "SENOIDAL", "MODIFICADA", "BAT CICLO", "PROFUNDO", "AGM", "VCD", "AH", "CONTROLADOR SOLAR", "MOD SOL", "FOTOVOLT", "VCC", "POLI", "CEL", "CONECTOR", "MC4"]
                if any(term in text for term in obvious_solar_terms) and final_category != 6:
                    print(f"  🚨 FINAL OVERRIDE: '{product_name}' → Category 6 (was {final_category})")
                    product['category_id'] = 6
                elif final_category == 6:
                    print(f"  ✅ '{product_name}' → Category 6 (correct)")
                else:
                    print(f"  📋 '{product_name}' → Category {final_category}")
            
            # Debug: Print the extracted data
            print("\nExtracted data from Claude:")
            print(json.dumps(data, indent=2))
            print("✅ Returning structured data successfully")
            
            return data
            
        except json.JSONDecodeError as e:
            print(f"❌ JSON decode error in extract_structured_data: {str(e)}")
            raise Exception(f"Failed to parse Claude response as JSON: {str(e)}")
        except Exception as e:
            print(f"❌ General error in extract_structured_data: {str(e)}")
            raise Exception(f"Claude API error: {str(e)}")

    def get_or_create_supplier(self, session: Session, supplier_info: Dict) -> tuple[Supplier, Dict]:
        """Check if supplier exists by RFC or name, create if not. Returns supplier and detection info."""
        rfc = supplier_info.get("rfc")
        name = supplier_info.get("name") or "Unknown Supplier"
        
        # Analyze supplier detection confidence
        detection_info = {
            "confidence": "high",
            "detected_name": name,
            "has_rfc": bool(rfc),
            "has_contact_info": bool(supplier_info.get("email") or supplier_info.get("phone")),
            "warning": None
        }
        
        # Determine confidence level
        if name == "Unknown Supplier":
            detection_info["confidence"] = "none"
            detection_info["warning"] = "No supplier information could be extracted from the document"
        elif not rfc and not (supplier_info.get("email") or supplier_info.get("phone")):
            detection_info["confidence"] = "low"
            detection_info["warning"] = f"Supplier '{name}' detected but missing RFC and contact information"
        elif not rfc:
            detection_info["confidence"] = "medium"
            detection_info["warning"] = f"Supplier '{name}' detected but missing RFC"
        
        # Try to find existing supplier by RFC first, then by name
        # Only match non-archived suppliers
        existing = None
        if rfc:
            existing = session.query(Supplier).filter_by(rfc=rfc).filter(
                Supplier.archived_at.is_(None)
            ).first()
        if not existing:
            existing = session.query(Supplier).filter_by(name=name).filter(
                Supplier.archived_at.is_(None)
            ).first()
            
        if existing:
            print(f"Found existing supplier: {existing.name} (ID: {existing.id})")
            detection_info["existing_supplier"] = True
            return existing, detection_info
        
        detection_info["existing_supplier"] = False
        
        new_supplier = Supplier(
            name=supplier_info["name"],
            common_name=supplier_info["name"],
            legal_name=supplier_info.get("legal_name", supplier_info["name"]),
            rfc=rfc,  # This can be None
            address=supplier_info.get("address"),
            email=supplier_info.get("email"),
            phone=supplier_info.get("phone"),
            website_url=supplier_info.get("website_url"),
            contact_name=supplier_info.get("contact_name")
        )
        
        session.add(new_supplier)
        session.commit()
        print(f"Created new supplier: {new_supplier.name} (ID: {new_supplier.id})")
        
        if detection_info["confidence"] in ["none", "low"]:
            print(f"⚠️  Warning: {detection_info['warning']}")
        
        return new_supplier, detection_info

    def get_or_create_product(self, session: Session, product_info: Dict) -> Product:
        
        print(f'Getting or creating product: {product_info}')
        print(f'Category ID at start of get_or_create_product: {product_info.get("category_id")}')
        
        # Get base SKU first
        base_sku = self.sku_generator.get_base_sku(product_info)
        print(f"Base SKU: {base_sku}")
        
        # Get SKU using hybrid approach (AI suggestion + code fallback)
        # For flattened model, we use the main SKU directly
        sku = self.sku_generator.get_variant_sku(
            base_sku, 
            product_info.get("specifications", {})
        )
        print(f"Generated SKU: {sku}")

        existing = session.query(Product).filter_by(sku=sku).first()
        if existing:
            print(f"Found existing product: {existing.name} (ID: {existing.id}) [SKU: {existing.sku}]")
            return existing
        
        # Debug: Print the unit value we received
        print(f"\nReceived unit value: {product_info.get('unit')}")
        
        # Get the unit value and convert to enum member
        unit_value = product_info.get("unit")
        if unit_value is None:
            unit_str = "PIEZA"
        else:
            unit_str = str(unit_value).upper()

        try:
            unit = ProductUnit[unit_str]
        except KeyError:
            print(f"Invalid unit value '{unit_str}', defaulting to PIEZA")
            unit = ProductUnit.PIEZA
        
        # Debug: Print the mapped unit value
        print(f"Mapped unit value: {unit}")
        
        # Verify category exists
        category_id = product_info.get("category_id")
        if not category_id:
            raise ValueError("Category ID is required")
            
        category = session.query(ProductCategory).filter_by(id=category_id).first()
        if not category:
            raise ValueError(f"Category with ID {category_id} not found")
        
        new_product = Product(
            name=product_info["name"],
            description=product_info["description"],
            base_sku=base_sku,
            category_id=category_id,
            unit=unit,  # Use the enum member directly
            iva=product_info.get("iva", True),
            # New flattened fields
            sku=sku,
            price=product_info.get("price"),
            stock=product_info.get("stock", 0),
            specifications=product_info.get("specifications", {}),
            default_margin=0.25,  # Set 25% default margin for price calculation
            is_active=True
        )
        
        session.add(new_product)
        session.flush()  # Assign ID without committing transaction
        print(f"Created new product: {new_product.name} (ID: {new_product.id}) [SKU: {new_product.sku}]")
        return new_product

    def create_supplier_product(self, session: Session, supplier: Supplier, product: Product, 
                              product_info: Dict, supplier_sku: str = None) -> SupplierProduct:
        """Create supplier-product relationship."""
        # Check if this supplier-product relationship already exists (non-archived only)
        existing = session.query(SupplierProduct).filter_by(
            supplier_id=supplier.id,
            product_id=product.id
        ).filter(
            SupplierProduct.archived_at.is_(None)
        ).first()
        
        if existing:
            print(f"Supplier-product relationship already exists (ID: {existing.id})")
            return existing
        
        # Extract shipping costs and method from product info
        shipping_method = product_info.get("shipping_method", "DIRECT")
        shipping_cost_direct = 0.0
        
        # Handle shipping costs based on method
        if shipping_method == "DIRECT":
            # For direct shipping, use the old shipping_cost_per_unit field
            shipping_cost_direct = product_info.get("shipping_cost_per_unit", 0.0)
        
        # Store prices in their original currency
        cost = product_info.get("cost")
        currency = product_info.get("currency", "MXN")
        
        # Log currency information
        if currency == "USD":
            print(f"Storing USD price: ${cost} (original currency)")
        else:
            print(f"Storing MXN price: ${cost} (original currency)")
        
        new_supplier_product = SupplierProduct(
            supplier_id=supplier.id,
            product_id=product.id,
            supplier_sku=supplier_sku,
            cost=cost,
            currency=currency,
            shipping_method=shipping_method,
            shipping_cost_direct=shipping_cost_direct,
            shipping_stage1_cost=product_info.get("shipping_stage1_cost", 0.0),
            shipping_stage2_cost=product_info.get("shipping_stage2_cost", 0.0),
            shipping_stage3_cost=product_info.get("shipping_stage3_cost", 0.0),
            shipping_stage4_cost=product_info.get("shipping_stage4_cost", 0.0),
            shipping_notes=product_info.get("shipping_notes"),
            lead_time_days=0,  # Default, can be updated later
            is_active=True
        )
        
        session.add(new_supplier_product)
        session.flush()  # Assign ID without committing transaction
        print(f"Created supplier-product relationship (ID: {new_supplier_product.id}) [Cost: ${new_supplier_product.cost}]")
        
        # Update product price if not set (for SQLite compatibility - no triggers)
        self.update_product_price(session, product)
        
        return new_supplier_product
    
    def update_product_price(self, session: Session, product: Product):
        """Update product price based on supplier costs (for SQLite compatibility)."""
        # If product already has a price set, don't override it
        if product.price is not None:
            return
        
        # Get the default margin for this product
        if product.default_margin is None:
            # No default margin, can't calculate price
            return
        
        # Get the lowest cost from all active supplier-product relationships
        from sqlalchemy import func, case
        from models import SupplierProduct
        
        # Calculate total shipping cost based on method
        total_shipping_cost = case(
            (SupplierProduct.shipping_method == 'DIRECT', func.coalesce(SupplierProduct.shipping_cost_direct, 0)),
            else_=(
                func.coalesce(SupplierProduct.shipping_stage1_cost, 0) +
                func.coalesce(SupplierProduct.shipping_stage2_cost, 0) +
                func.coalesce(SupplierProduct.shipping_stage3_cost, 0) +
                func.coalesce(SupplierProduct.shipping_stage4_cost, 0)
            )
        )
        
        lowest_cost_result = session.query(
            func.min(SupplierProduct.cost + total_shipping_cost).label('lowest_cost')
        ).filter(
            SupplierProduct.product_id == product.id,
            SupplierProduct.is_active == True,
            SupplierProduct.cost.isnot(None),
            SupplierProduct.cost > 0
        ).first()
        
        if lowest_cost_result and lowest_cost_result.lowest_cost:
            from decimal import Decimal
            lowest_cost = Decimal(str(lowest_cost_result.lowest_cost))
            margin = Decimal(str(product.default_margin))
            
            # Calculate price with margin: price = cost / (1 - margin)
            if margin < 1:  # Margin must be less than 100%
                calculated_price = lowest_cost / (Decimal('1') - margin)
                product.calculated_price = float(calculated_price)
                product.price = float(calculated_price)
                print(f"   ✓ Calculated product price: ${calculated_price:.2f} (margin: {margin*100}%)")

    def process_quotation(self, file_path: str, category_id: Optional[int] = None) -> Dict:
        """
        Main method to process a quotation file (PDF or image) with enhanced SKU generation.
        
        Args:
            file_path: Path to the PDF or image file
            category_id: Optional product category ID. If not provided, will be determined automatically.
            
        Returns:
            Dict with processing results including SKU information
        """
        print(f"🔍 Processing quotation: {file_path}")
        print("=" * 50)
        
        try:
            # Extract text from file (PDF, image, or text)
            print("🔍 Extracting text from file...")
            if self.is_pdf_file(file_path):
                extracted_text = self.extract_text_from_pdf(file_path)
                print("✓ Text extracted from PDF")
            elif self.is_image_file(file_path):
                extracted_text = self.extract_text_from_image(file_path)
                print("✓ Text extracted from image using OCR")
            elif self.is_text_file(file_path):
                extracted_text = self.extract_text_from_txt(file_path)
                print("✓ Text extracted from TXT file (WhatsApp conversation)")
            else:
                supported_formats = "PDF, PNG, JPG, JPEG, GIF, BMP, TIFF, WEBP, TXT"
                raise ValueError(f"Unsupported file format. Supported formats: {supported_formats}")
            
            print(f"🔍 Extracted text length: {len(extracted_text)} characters")
            
            if not extracted_text.strip():
                raise ValueError("No text could be extracted from the file")
        except Exception as e:
            print(f"❌ Error extracting text: {str(e)}")
            raise
        
        print("🔍 Creating database session...")
        session = SessionLocal()
        try:
            # Get available categories
            print("🔍 Getting categories...")
            categories = self.get_categories(session)
            print(f"🔍 Found {len(categories)} categories")
            if not categories:
                raise ValueError("No product categories found in the database")
            
            # Use AI to extract structured data (including SKU suggestions and category selection)
            print("🔍 Starting AI extraction...")
            structured_data = self.extract_structured_data(extracted_text, categories)
            print("✓ Structured data extracted using Claude AI")
            
            results = {
                "suppliers": {},  # Track multiple suppliers by name
                "products_processed": 0,
                "supplier_products_created": 0,
                "skus_generated": [],
                "supplier_detection": {
                    "suppliers_detected": [],
                    "overall_confidence": "high",
                    "warnings": []
                },
                "currency_info": {
                    "currencies_detected": [],
                    "usd_products": 0,
                    "mxn_products": 0,
                    "multi_currency": False
                }
            }
            
            # Check if any products were extracted
            if not structured_data.get('products'):
                if 'error' in structured_data:
                    raise Exception(f"AI processing error: {structured_data['error']}")
                else:
                    print("⚠️  No products found in the document")
                    return {
                        "suppliers": {},
                        "products_processed": 0,
                        "supplier_products_created": 0,
                        "skus_generated": [],
                        "supplier_detection": {
                            "suppliers_detected": [],
                            "overall_confidence": "none",
                            "warnings": ["No products could be extracted from the document"]
                        }
                    }
            
            print(f"\nProcessing {len(structured_data['products'])} products...")
            print("-" * 40)
            
            # Process products, each with potentially different suppliers
            for i, product_info in enumerate(structured_data["products"], 1):
                try:
                    # Create a deep copy to prevent modifications
                    product_info_copy = copy.deepcopy(product_info)
                    
                    # Track currency detection
                    currency = product_info_copy.get("currency", "MXN")
                    if currency not in results["currency_info"]["currencies_detected"]:
                        results["currency_info"]["currencies_detected"].append(currency)
                    
                    print(f"\n[{i}/{len(structured_data['products'])}] Processing: {product_info_copy['name']}")
                    print(f"Category ID before override: {product_info_copy['category_id']}")
                    print(f"Currency detected: {currency}")

                    # Override category_id if provided
                    if category_id is not None:
                        product_info_copy["category_id"] = category_id
                        print(f"Category ID after override: {product_info_copy['category_id']}")
                    
                    print(f"Category ID before get_or_create_product: {product_info_copy['category_id']}")
                    
                    # Create/get product
                    product = self.get_or_create_product(session, product_info_copy)
                    
                    # Process supplier for this specific product
                    supplier_info = product_info_copy.get("supplier", {})
                    if not supplier_info.get("name"):
                        supplier_info["name"] = "Unknown Supplier"
                    
                    supplier, supplier_detection_info = self.get_or_create_supplier(session, supplier_info)
                    supplier_name = supplier.name or "Unknown Supplier"
                    
                    print(f"   → Supplier: {supplier_name}")
                    
                    # Track this supplier in results
                    if supplier_name not in results["suppliers"]:
                        results["suppliers"][supplier_name] = {
                            "id": supplier.id,
                            "name": supplier_name,
                            "detection_info": supplier_detection_info,
                            "products_count": 0
                        }
                        results["supplier_detection"]["suppliers_detected"].append(supplier_detection_info)
                    
                    results["suppliers"][supplier_name]["products_count"] += 1
                    
                    # Create supplier-product relationship
                    supplier_product = self.create_supplier_product(
                        session, supplier, product, product_info_copy, 
                        supplier_sku=product_info_copy.get("supplier_sku")
                    )
                    results["supplier_products_created"] += 1
                    
                    # Track currency information (no conversion, store original)
                    if currency == "USD":
                        results["currency_info"]["usd_products"] = results["currency_info"].get("usd_products", 0) + 1
                    else:
                        results["currency_info"]["mxn_products"] = results["currency_info"].get("mxn_products", 0) + 1
                    
                    # Check for multi-currency quotation
                    if (results["currency_info"]["usd_products"] > 0 and 
                        results["currency_info"]["mxn_products"] > 0):
                        results["currency_info"]["multi_currency"] = True
                    
                    # Track SKU generation
                    results["skus_generated"].append({
                        "product_name": product_info_copy["name"],
                        "supplier_name": supplier_name,
                        "base_sku": product.base_sku,
                        "variant_sku": product.sku,
                        "ai_suggested": product_info_copy.get("suggested_base_sku", "N/A"),
                        "category_id": product_info_copy["category_id"],
                        "currency": product_info_copy.get("currency", "MXN"),
                        "cost": product_info_copy.get("cost"),
                        "cost_currency": product_info_copy.get("currency", "MXN")
                    })
                    
                    results["products_processed"] += 1
                    
                except Exception as e:
                    print(f"❌ Error processing product {i} '{product_info.get('name', 'Unknown')}': {str(e)}")
                    # Continue processing other products instead of failing the entire batch
                    continue
            
            # Calculate overall confidence and warnings
            low_confidence_suppliers = [s for s in results["supplier_detection"]["suppliers_detected"] if s["confidence"] in ["low", "none"]]
            if low_confidence_suppliers:
                if any(s["confidence"] == "none" for s in low_confidence_suppliers):
                    results["supplier_detection"]["overall_confidence"] = "none"
                else:
                    results["supplier_detection"]["overall_confidence"] = "low"
                
                for supplier_info in low_confidence_suppliers:
                    if supplier_info.get("warning"):
                        results["supplier_detection"]["warnings"].append(supplier_info["warning"])
            
            session.commit()
            print(f"\n" + "=" * 50)
            print(f"✓ Successfully processed {results['products_processed']} products")
            print(f"✓ Created {results['supplier_products_created']} supplier relationships")
            print(f"✓ Detected {len(results['suppliers'])} suppliers:")
            
            for supplier_name, supplier_data in results["suppliers"].items():
                confidence = supplier_data["detection_info"]["confidence"]
                print(f"   • {supplier_name} ({supplier_data['products_count']} products, confidence: {confidence})")
            
            # Display processed product names
            if results["skus_generated"]:
                print(f"✓ Products processed:")
                for sku_info in results["skus_generated"]:
                    print(f"   • {sku_info['product_name']} ({sku_info['variant_sku']}) - {sku_info['supplier_name']}")
            
        except Exception as e:
            print(f"❌ Error in process_quotation: {str(e)}")
            print(f"❌ Error type: {type(e).__name__}")
            import traceback
            print(f"❌ Traceback: {traceback.format_exc()}")
            session.rollback()
            raise Exception(f"Database error: {str(e)}")
        finally:
            session.close()
            
        return results 