#!/usr/bin/env python3
"""
JSON repair utilities for handling malformed JSON from AI responses
"""

import json
import re
from typing import Optional, Dict, Any

class JSONRepair:
    """Utility class for repairing malformed JSON"""
    
    @staticmethod
    def repair_json(content: str) -> Optional[Dict[Any, Any]]:
        """
        Attempt to repair malformed JSON content
        
        Args:
            content: Raw JSON string that may be malformed
            
        Returns:
            Parsed JSON dict or None if repair fails
        """
        if not content.strip():
            return None
        
        # Try direct parsing first
        try:
            result = json.loads(content)
            # If the result is a string, it's likely malformed JSON that needs repair
            if isinstance(result, str):
                # Don't return string results, continue to repair strategies
                pass
            else:
                return result
        except json.JSONDecodeError:
            pass
        
        # Try multiple repair strategies
        repair_strategies = [
            JSONRepair._repair_missing_braces,
            JSONRepair._repair_unterminated_strings,
            JSONRepair._repair_quotes_and_commas,
            JSONRepair._repair_with_manual_parsing,
            JSONRepair._repair_by_extraction
        ]
        
        for strategy in repair_strategies:
            try:
                result = strategy(content)
                if result:
                    return result
            except Exception:
                continue
        
        return None
    
    @staticmethod
    def _repair_missing_braces(content: str) -> Optional[Dict[Any, Any]]:
        """Repair JSON that's missing opening/closing braces"""
        try:
            original_content = content
            content = content.strip()
            
            # Handle case where content is just "products" (incomplete JSON)
            if content == '"products"' or content == 'products':
                return {"products": []}
            
            # Handle case where content starts with "products" without opening brace
            if content.startswith('"products"'):
                content = '{' + content
            
            # Handle case where content has leading whitespace/newlines and missing opening brace
            elif '"products"' in content and not content.startswith('{'):
                # Remove leading whitespace and add opening brace
                content = content.lstrip()
                if not content.startswith('{'):
                    content = '{' + content
            
            # Ensure proper closing brace
            if not content.endswith('}') and not content.endswith(']'):
                content = content.rstrip() + '}'
            
            # Double-check that we have proper JSON structure
            if content.startswith('{') and not content.endswith('}'):
                content = content + '}'
            
            return json.loads(content)
        except Exception:
            return None
    
    @staticmethod
    def _repair_unterminated_strings(content: str) -> Optional[Dict[Any, Any]]:
        """Repair JSON with unterminated strings"""
        try:
            # Strategy: Walk backwards through the content looking for valid JSON
            # This handles cases where the JSON was cut off mid-string or mid-object
            
            # First, try to find the last complete product object by looking for },
            # This is more reliable than looking for }] which might not exist
            for i in range(len(content) - 1, max(0, len(content) - 3000), -1):
                if i > 0 and content[i-1:i+1] == '},':
                    # Found potential end of a product object
                    # Try to close the array and object
                    test_content = content[:i] + '}]}'
                    try:
                        result = json.loads(test_content)
                        if isinstance(result, dict) and 'products' in result and len(result['products']) > 0:
                            print(f"✅ Repaired unterminated JSON (found {len(result['products'])} products)")
                            return result
                    except:
                        continue
            
            # Try to find the last complete product by looking for }
            # within the products array
            for i in range(len(content) - 1, max(0, len(content) - 3000), -1):
                if content[i] == '}':
                    # Try closing the array and object
                    test_content = content[:i+1] + ']}'
                    try:
                        result = json.loads(test_content)
                        if isinstance(result, dict) and 'products' in result and len(result['products']) > 0:
                            print(f"✅ Repaired unterminated JSON (found {len(result['products'])} products)")
                            return result
                    except:
                        continue
            
            # Try to find the last valid closing brace for the products array
            # Look for patterns like: }] at the end
            for i in range(len(content) - 1, max(0, len(content) - 1000), -1):
                if i < len(content) - 1 and content[i:i+2] == '}]':
                    # Found potential end of products array, try adding closing brace
                    test_content = content[:i+2] + '}'
                    try:
                        result = json.loads(test_content)
                        if isinstance(result, dict) and 'products' in result:
                            print(f"✅ Repaired unterminated JSON (found {len(result['products'])} products)")
                            return result
                    except:
                        continue
            
            return None
        except Exception:
            return None
    
    @staticmethod
    def _repair_common_issues(json_content: str) -> str:
        """
        Repair common JSON issues
        
        Args:
            json_content: JSON string to repair
            
        Returns:
            Repaired JSON string
        """
        # Remove trailing commas
        json_content = re.sub(r',(\s*[}\]])', r'\1', json_content)
        
        # Fix unescaped quotes in string values - more aggressive approach
        # Look for patterns like: "text with "quotes" inside"
        def fix_quotes(match):
            full_match = match.group(0)
            # Escape internal quotes
            fixed = full_match.replace('"', '\\"')
            return fixed
        
        # Find string values and fix quotes within them
        json_content = re.sub(r'"[^"]*"[^"]*"[^"]*"', fix_quotes, json_content)
        
        # Fix unescaped newlines in strings
        json_content = json_content.replace('\n', '\\n').replace('\r', '\\r')
        
        # Fix unescaped tabs
        json_content = json_content.replace('\t', '\\t')
        
        # Ensure proper closing
        if not json_content.strip().endswith('}'):
            json_content = json_content.rstrip() + '}'
        
        return json_content
    
    @staticmethod
    def _repair_quotes_and_commas(content: str) -> Optional[Dict[Any, Any]]:
        """Repair quotes and comma issues"""
        try:
            # Fix content that starts with newline and missing opening brace
            content = content.strip()
            if content.startswith('"products"') or content.startswith('"products"'):
                content = '{' + content
            if not content.startswith('{') and not content.startswith('['):
                # Try to find the first { or [ and add it if missing
                if '"products"' in content:
                    content = '{' + content
                elif '"error"' in content:
                    content = '{' + content
            
            # Remove trailing commas
            content = re.sub(r',(\s*[}\]])', r'\1', content)
            
            # Fix smart quotes and other quote issues
            content = content.replace('"', '"').replace('"', '"')  # Smart quotes
            content = content.replace(''', "'").replace(''', "'")  # Smart apostrophes
            content = content.replace('"', '"').replace('"', '"')  # Other quote variants
            
            # Fix unescaped quotes in string values
            lines = content.split('\n')
            fixed_lines = []
            
            for line in lines:
                # Look for lines with string values that might have unescaped quotes
                if '": "' in line and line.count('"') > 2:
                    # This line has a string value with potential quote issues
                    # Find the value part and escape quotes within it
                    colon_pos = line.find('": "')
                    if colon_pos != -1:
                        key_part = line[:colon_pos + 4]  # Include the ": " part
                        value_part = line[colon_pos + 4:]
                        
                        # Find the end of the value (last quote before comma or end)
                        end_quote_pos = value_part.rfind('"')
                        if end_quote_pos != -1:
                            value_content = value_part[:end_quote_pos]
                            rest = value_part[end_quote_pos:]
                            
                            # Escape quotes within the value content
                            value_content = value_content.replace('"', '\\"')
                            
                            # Reconstruct the line
                            line = key_part + value_content + rest
                
                fixed_lines.append(line)
            
            content = '\n'.join(fixed_lines)
            
            return json.loads(content)
        except:
            return None
    
    @staticmethod
    def _repair_with_manual_parsing(content: str) -> Optional[Dict[Any, Any]]:
        """Try to manually parse and reconstruct JSON"""
        try:
            # Find the main JSON object
            start_idx = content.find('{')
            if start_idx == -1:
                return None
            
            # Count braces to find the end
            brace_count = 0
            end_idx = start_idx
            
            for i, char in enumerate(content[start_idx:], start_idx):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_idx = i + 1
                        break
            
            json_content = content[start_idx:end_idx]
            
            # Try to fix the content
            json_content = json_content.replace('\n', '\\n').replace('\t', '\\t')
            
            return json.loads(json_content)
        except:
            return None
    
    @staticmethod
    def _repair_by_extraction(content: str) -> Optional[Dict[Any, Any]]:
        """Extract products array and reconstruct"""
        try:
            # Look for products array
            products_start = content.find('"products"')
            if products_start == -1:
                return None
            
            # Find the array
            array_start = content.find('[', products_start)
            if array_start == -1:
                return None
            
            # Find the end of the array
            bracket_count = 0
            end_idx = array_start
            
            for i, char in enumerate(content[array_start:], array_start):
                if char == '[':
                    bracket_count += 1
                elif char == ']':
                    bracket_count -= 1
                    if bracket_count == 0:
                        end_idx = i + 1
                        break
            
            products_json = content[array_start:end_idx]
            
            # Try to parse the array
            products_array = json.loads(products_json)
            
            return {"products": products_array}
        except:
            return None
    
    @staticmethod
    def extract_products_array(content: str) -> Optional[Dict[Any, Any]]:
        """
        Extract just the products array from malformed JSON
        
        Args:
            content: Raw content that may contain malformed JSON
            
        Returns:
            Dict with products array or None if extraction fails
        """
        try:
            # Look for products array
            products_start = content.find('"products"')
            if products_start == -1:
                return None
            
            # Find the start of the array
            array_start = content.find('[', products_start)
            if array_start == -1:
                return None
            
            # Count brackets to find the end
            bracket_count = 0
            end_idx = array_start
            
            for i, char in enumerate(content[array_start:], array_start):
                if char == '[':
                    bracket_count += 1
                elif char == ']':
                    bracket_count -= 1
                    if bracket_count == 0:
                        end_idx = i + 1
                        break
            
            # Extract the products array
            products_json = content[array_start:end_idx]
            
            # Try to parse the array
            products_array = json.loads(products_json)
            
            # Return as a proper structure
            return {"products": products_array}
            
        except Exception:
            return None
