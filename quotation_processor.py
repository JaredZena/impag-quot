import re
import json
import fitz  # PyMuPDF
import anthropic
import copy
from sqlalchemy.orm import Session
from typing import Dict, List, Optional
from models import (
    Supplier, Product, ProductVariant, SupplierProduct, 
    ProductCategory, ProductUnit, SessionLocal
)

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
                sku_parts.append(grade.upper()[:3])
        
        # Material
        if 'material' in specifications:
            material = specifications['material'].lower()
            if material in self.variant_rules['material_abbrev']:
                sku_parts.append(self.variant_rules['material_abbrev'][material])
        
        # Color (if significant)
        if 'color' in specifications and specifications['color'].lower() not in ['natural', 'transparente']:
            sku_parts.append(specifications['color'][:3].upper())
        
        return "-".join(sku_parts)

    def get_base_sku(self, product_info: Dict) -> str:
        """Get base SKU from AI suggestion or generate with code."""
        print(f"get_base_sku called with category_id: {product_info.get('category_id')}")
        
        if 'suggested_base_sku' in product_info and product_info['suggested_base_sku']:
            # Validate and clean AI suggestion
            ai_sku = product_info['suggested_base_sku'].upper().strip()
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

    def extract_structured_data(self, pdf_text: str, categories: List[Dict]) -> Dict:
        """
        Use Claude to extract structured supplier and product data from PDF text.
        Enhanced to include SKU suggestions and automatic category selection.
        """
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
        
        prompt = f"""You are an assistant that extracts structured information from supplier quotations for a procurement system.

<quotation_text>
{pdf_text}
</quotation_text>

<available_categories>
{categories_text}
</available_categories>

Please return a JSON object with:

1. `supplier`: an object with the following fields:
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
- Look for the actual supplier/vendor company name that is providing the products
- The supplier is typically found in the header, footer, or contact information of the quotation
- If you see "IMPAG" in the document, ignore it and look for the actual supplier name

2. `products`: an array of objects, each with:
   - `name`: full product name
   - `description`: short human-readable description
   - `suggested_base_sku`: a concise, meaningful base SKU (6-12 chars, format examples: "VH-4IN", "PERL-MED", "MESA-25")
   - `unit`: one of "PIEZA", "KG", "ROLLO", "METRO" (convert from Spanish units to these exact uppercase values)
   - `iva`: true if the product includes IVA, otherwise false
   - `cost`: unit cost as a float
   - `specifications`: a dictionary of key attributes like size, volume, dimensions, material, etc.
   - `category_id`: the ID of the most appropriate category from the available_categories list

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

Respond only with the JSON object, no extra explanation."""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                temperature=0,
                messages=[{"role": "user", "content": prompt}]
            )
            
            content = response.content[0].text.strip()
            # Remove potential markdown formatting
            if content.startswith("```json"):
                content = content[7:-3]
            elif content.startswith("```"):
                content = content[3:-3]
            
            data = json.loads(content)
            
            # Post-process supplier information to ensure IMPAG is not extracted as supplier
            if 'supplier' in data:
                supplier_info = data['supplier']
                supplier_name = supplier_info.get('name', '').upper()
                
                # Check if the extracted supplier name contains IMPAG
                if 'IMPAG' in supplier_name:
                    print(f"\n⚠️  IMPAG detected in supplier name: {supplier_info.get('name')}")
                    print("   IMPAG is the receiving company, not the supplier")
                    
                    # Try to find alternative supplier name in the text
                    alternative_supplier = self._find_alternative_supplier(pdf_text)
                    if alternative_supplier:
                        print(f"   → Using alternative: {alternative_supplier}")
                        supplier_info['name'] = alternative_supplier
                        supplier_info['legal_name'] = alternative_supplier
                    else:
                        print("   → Manual review required - no alternative found")
            
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
            
            return data
            
        except json.JSONDecodeError as e:
            raise Exception(f"Failed to parse Claude response as JSON: {str(e)}")
        except Exception as e:
            raise Exception(f"Claude API error: {str(e)}")

    def get_or_create_supplier(self, session: Session, supplier_info: Dict) -> Supplier:
        """Check if supplier exists by RFC or name, create if not."""
        rfc = supplier_info.get("rfc")
        name = supplier_info["name"]
        
        # Try to find existing supplier by RFC first, then by name
        existing = None
        if rfc:
            existing = session.query(Supplier).filter_by(rfc=rfc).first()
        if not existing:
            existing = session.query(Supplier).filter_by(name=name).first()
            
        if existing:
            print(f"Found existing supplier: {existing.name} (ID: {existing.id})")
            return existing
        
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
        return new_supplier

    def get_or_create_product(self, session: Session, product_info: Dict) -> Product:
        """Check if product exists by base_sku, create if not. Uses hybrid SKU generation."""
        print(f'Getting or creating product: {product_info}')
        print(f'Category ID at start of get_or_create_product: {product_info.get("category_id")}')
        
        # Get base SKU using hybrid approach (AI suggestion + code fallback)
        base_sku = self.sku_generator.get_base_sku(product_info)
        print(f"Base SKU: {base_sku}")

        existing = session.query(Product).filter_by(base_sku=base_sku).first()
        if existing:
            print(f"Found existing product: {existing.name} (ID: {existing.id}) [SKU: {existing.base_sku}]")
            return existing
        
        
        # Debug: Print the unit value we received
        print(f"\nReceived unit value: {product_info.get('unit')}")
        
        # Get the unit value and convert to enum member
        unit_str = product_info.get("unit", "PIEZA").upper()
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
            iva=product_info.get("iva", True)
        )
        
        session.add(new_product)
        session.commit()
        print(f"Created new product: {new_product.name} (ID: {new_product.id}) [SKU: {new_product.base_sku}]")
        return new_product

    def create_product_variant(self, session: Session, product: Product, product_info: Dict) -> ProductVariant:
        """Create a new product variant with hybrid SKU generation."""
        # Generate variant SKU using code rules for consistency
        variant_sku = self.sku_generator.get_variant_sku(
            product.base_sku, 
            product_info.get("specifications", {})
        )
        
        # Check if variant with this SKU already exists
        existing = session.query(ProductVariant).filter_by(sku=variant_sku).first()
        if existing:
            print(f"Found existing variant: {existing.sku} (ID: {existing.id})")
            return existing
        
        new_variant = ProductVariant(
            product_id=product.id,
            sku=variant_sku,
            specifications=product_info.get("specifications", {}),
            is_active=True
        )
        
        session.add(new_variant)
        session.commit()
        print(f"Created new variant: {new_variant.sku} (ID: {new_variant.id})")
        return new_variant

    def create_supplier_product(self, session: Session, supplier: Supplier, variant: ProductVariant, 
                              product_info: Dict, supplier_sku: str = None) -> SupplierProduct:
        """Create supplier-product relationship."""
        # Check if this supplier-variant relationship already exists
        existing = session.query(SupplierProduct).filter_by(
            supplier_id=supplier.id,
            variant_id=variant.id
        ).first()
        
        if existing:
            print(f"Supplier-product relationship already exists (ID: {existing.id})")
            return existing
        
        new_supplier_product = SupplierProduct(
            supplier_id=supplier.id,
            variant_id=variant.id,
            supplier_sku=supplier_sku,
            cost=product_info.get("cost"),
            lead_time_days=0,  # Default, can be updated later
            is_active=True
        )
        
        session.add(new_supplier_product)
        session.commit()
        print(f"Created supplier-product relationship (ID: {new_supplier_product.id}) [Cost: ${new_supplier_product.cost}]")
        return new_supplier_product

    def process_quotation(self, pdf_path: str, category_id: Optional[int] = None) -> Dict:
        """
        Main method to process a quotation PDF with enhanced SKU generation.
        
        Args:
            pdf_path: Path to the PDF file
            category_id: Optional product category ID. If not provided, will be determined automatically.
            
        Returns:
            Dict with processing results including SKU information
        """
        print(f"Processing quotation: {pdf_path}")
        print("=" * 50)
        
        # Extract text from PDF
        pdf_text = self.extract_text_from_pdf(pdf_path)
        print("✓ Text extracted from PDF")
        
        session = SessionLocal()
        try:
            # Get available categories
            categories = self.get_categories(session)
            if not categories:
                raise ValueError("No product categories found in the database")
            
            # Use AI to extract structured data (including SKU suggestions and category selection)
            structured_data = self.extract_structured_data(pdf_text, categories)
            print("✓ Structured data extracted using Claude AI")
            
            results = {
                "supplier": None,
                "products_processed": 0,
                "variants_created": 0,
                "supplier_products_created": 0,
                "skus_generated": []
            }
            
            # Process supplier
            supplier = self.get_or_create_supplier(session, structured_data["supplier"])
            results["supplier"] = supplier.name
            
            print(f"\nProcessing {len(structured_data['products'])} products...")
            print("-" * 40)
            
            # Process products
            for i, product_info in enumerate(structured_data["products"], 1):
                # Create a deep copy to prevent modifications
                product_info_copy = copy.deepcopy(product_info)
                
                print(f"\n[{i}/{len(structured_data['products'])}] Processing: {product_info_copy['name']}")
                print(f"Category ID before override: {product_info_copy['category_id']}")

                # Override category_id if provided
                if category_id is not None:
                    product_info_copy["category_id"] = category_id
                    print(f"Category ID after override: {product_info_copy['category_id']}")
                
                print(f"Category ID before get_or_create_product: {product_info_copy['category_id']}")
                
                # Create/get product
                product = self.get_or_create_product(session, product_info_copy)
                
                # Create variant (no price/stock set initially)
                variant = self.create_product_variant(session, product, product_info_copy)
                results["variants_created"] += 1
                
                # Create supplier-product relationship
                self.create_supplier_product(
                    session, supplier, variant, product_info_copy, 
                    supplier_sku=product_info_copy.get("supplier_sku")
                )
                results["supplier_products_created"] += 1
                
                # Track SKU generation
                results["skus_generated"].append({
                    "product_name": product_info_copy["name"],
                    "base_sku": product.base_sku,
                    "variant_sku": variant.sku,
                    "ai_suggested": product_info_copy.get("suggested_base_sku", "N/A"),
                    "category_id": product_info_copy["category_id"]
                })
                
                results["products_processed"] += 1
            
            session.commit()
            print(f"\n" + "=" * 50)
            print(f"✓ Successfully processed {results['products_processed']} products")
            print(f"✓ Created {results['variants_created']} variants")
            print(f"✓ Created {results['supplier_products_created']} supplier relationships")
            
        except Exception as e:
            session.rollback()
            raise Exception(f"Database error: {str(e)}")
        finally:
            session.close()
            
        return results 