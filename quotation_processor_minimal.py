import re
import json
import os
import anthropic
import copy
from sqlalchemy.orm import Session
from typing import Dict, List, Optional

# Import with fallbacks for optional dependencies
try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False
    
try:
    import easyocr
    HAS_EASYOCR = True
except ImportError:
    HAS_EASYOCR = False

try:
    import pypdf
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

from models import (
    Supplier, Product, SupplierProduct, 
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
        """Generate variant SKU using predefined rules."""
        print(f'Generating variant SKU with code for base: {base_sku}')
        
        variant_parts = []
        
        # Process specifications with rules
        for key, value in specifications.items():
            if not value:
                continue
                
            key_lower = key.lower()
            value_str = str(value).lower()
            
            # Apply transformation rules
            if any(unit in key_lower for unit in ['volumen', 'capacidad', 'litros']):
                for unit_key, unit_abbrev in self.variant_rules['volume_units'].items():
                    if unit_key in value_str:
                        variant_parts.append(f"{unit_abbrev}{re.sub(r'[^0-9.]', '', value_str)}")
                        break
            elif any(unit in key_lower for unit in ['peso', 'weight']):
                for unit_key, unit_abbrev in self.variant_rules['weight_units'].items():
                    if unit_key in value_str:
                        variant_parts.append(f"{unit_abbrev}{re.sub(r'[^0-9.]', '', value_str)}")
                        break
            elif any(dim in key_lower for dim in ['dimension', 'tamaño', 'size']):
                for unit_key, unit_abbrev in self.variant_rules['dimension_units'].items():
                    if unit_key in value_str:
                        variant_parts.append(f"{unit_abbrev}{re.sub(r'[^0-9.]', '', value_str)}")
                        break
            elif 'material' in key_lower:
                for mat_key, mat_abbrev in self.variant_rules['material_abbrev'].items():
                    if mat_key in value_str:
                        variant_parts.append(mat_abbrev)
                        break
            elif 'grade' in key_lower or 'grado' in key_lower:
                for grade_key, grade_abbrev in self.variant_rules['grade_mapping'].items():
                    if grade_key in value_str:
                        variant_parts.append(grade_abbrev)
                        break
            else:
                # Generic handling: take first 3 chars of alphanumeric
                clean_value = re.sub(r'[^a-zA-Z0-9]', '', value_str)
                if clean_value:
                    variant_parts.append(clean_value[:3].upper())
        
        # Construct final variant SKU
        if variant_parts:
            return f"{base_sku}-{''.join(variant_parts[:3])}"
        else:
            return f"{base_sku}-VAR"
    
    def get_variant_sku(self, base_sku: str, specifications: Dict) -> str:
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
        """Extract text from PDF file using available libraries."""
        if HAS_PYMUPDF:
            try:
                doc = fitz.open(pdf_path)
                text = "\n".join([page.get_text() for page in doc])
                doc.close()
                return text
            except Exception as e:
                print(f"PyMuPDF failed: {e}, trying pypdf...")
        
        if HAS_PYPDF:
            try:
                import pypdf
                with open(pdf_path, 'rb') as file:
                    pdf_reader = pypdf.PdfReader(file)
                    text = ""
                    for page in pdf_reader.pages:
                        text += page.extract_text() + "\n"
                    return text
            except Exception as e:
                print(f"pypdf failed: {e}")
        
        raise Exception("No PDF processing library available. Install PyMuPDF or pypdf.")
    
    def extract_text_from_image(self, image_path: str) -> str:
        """Extract text from image file using OCR."""
        if not HAS_EASYOCR:
            raise Exception("EasyOCR not available. Image processing is disabled to reduce deployment size. Install easyocr if needed.")
        
        try:
            # Initialize EasyOCR reader for English and Spanish
            reader = easyocr.Reader(['en', 'es'])
            
            # Read text from image
            results = reader.readtext(image_path)
            
            # Extract text from results (results contain bounding box, text, confidence)
            text_lines = []
            for (bbox, text, confidence) in results:
                # Only include text with reasonable confidence (> 0.5)
                if confidence > 0.5:
                    text_lines.append(text)
            
            # Join all text lines
            extracted_text = "\n".join(text_lines)
            return extracted_text
            
        except Exception as e:
            raise Exception(f"Error extracting text from image: {str(e)}")
    
    def is_image_file(self, file_path: str) -> bool:
        """Check if file is a supported image format."""
        image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp'}
        return os.path.splitext(file_path.lower())[1] in image_extensions
    
    def is_pdf_file(self, file_path: str) -> bool:
        """Check if file is a PDF."""
        return os.path.splitext(file_path.lower())[1] == '.pdf'

    # ... (rest of the methods would be the same)
    # For brevity, I'm only showing the critical parts that handle missing dependencies