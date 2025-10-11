#!/usr/bin/env python3
"""
Currency utilities for handling USD and MXN currencies
"""

import re
from decimal import Decimal
from typing import Optional, Tuple
from services.exchange_rate_service import exchange_rate_service

class CurrencyUtils:
    """Utility class for currency detection and conversion"""
    
    # USD indicators
    USD_INDICATORS = [
        'USD', 'US$', 'dollars', 'dólares', 'USD$', 
        'US dollars', 'american dollars', 'dólares americanos'
    ]
    
    # MXN indicators  
    MXN_INDICATORS = [
        'MXN', 'pesos', 'pesos mexicanos', 'peso mexicano',
        'mexican pesos', 'pesos MXN'
    ]
    
    
    @classmethod
    def detect_currency(cls, text: str) -> str:
        """
        Detect currency from text content
        
        Args:
            text: Text content to analyze
            
        Returns:
            'USD' or 'MXN' based on detected currency
        """
        text_lower = text.lower()
        
        # Check for USD indicators
        usd_score = sum(1 for indicator in cls.USD_INDICATORS if indicator.lower() in text_lower)
        
        # Check for MXN indicators
        mxn_score = sum(1 for indicator in cls.MXN_INDICATORS if indicator.lower() in text_lower)
        
        # If USD indicators found, return USD
        if usd_score > 0:
            return 'USD'
        
        # If MXN indicators found, return MXN
        if mxn_score > 0:
            return 'MXN'
        
        # Default to MXN if no clear indicators
        return 'MXN'
    
    @classmethod
    def parse_currency_value(cls, value_str: str, currency: str = 'MXN') -> Optional[Decimal]:
        """
        Parse currency value from string, handling both USD and MXN formats
        
        Args:
            value_str: String containing currency value
            currency: Expected currency ('USD' or 'MXN')
            
        Returns:
            Decimal value or None if parsing fails
        """
        if not value_str or value_str.strip() == '':
            return None
        
        # Remove currency codes first (before removing $)
        cleaned = re.sub(r'US\$', '', value_str.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r'\b(USD|MXN)\b', '', cleaned, flags=re.IGNORECASE)
        
        # Remove currency words
        cleaned = re.sub(r'\b(dollars?|pesos?|dólares?)\b', '', cleaned, flags=re.IGNORECASE)
        
        # Remove common currency symbols and formatting
        cleaned = re.sub(r'[\$,]', '', cleaned)
        
        # Clean up any extra spaces
        cleaned = cleaned.strip()
        
        try:
            return Decimal(cleaned)
        except:
            return None
    
    @classmethod
    def get_exchange_rate(cls, from_currency: str, to_currency: str) -> Optional[float]:
        """
        Get exchange rate between currencies
        
        Args:
            from_currency: Source currency ('USD' or 'MXN')
            to_currency: Target currency ('USD' or 'MXN')
            
        Returns:
            Exchange rate as float or None if unavailable
        """
        return exchange_rate_service.get_exchange_rate(from_currency, to_currency)
    
    @classmethod
    def convert_currency(cls, amount: Decimal, from_currency: str, to_currency: str) -> Optional[Decimal]:
        """
        Convert amount from one currency to another
        
        Args:
            amount: Amount to convert
            from_currency: Source currency
            to_currency: Target currency
            
        Returns:
            Converted amount or None if conversion fails
        """
        return exchange_rate_service.convert_amount(amount, from_currency, to_currency)
    
    @classmethod
    def normalize_currency_value(cls, value_str: str, detected_currency: str, target_currency: str = 'MXN') -> Tuple[Optional[Decimal], str]:
        """
        Parse and convert currency value to target currency
        
        Args:
            value_str: String containing currency value
            detected_currency: Currency detected in the text
            target_currency: Currency to convert to (default: MXN)
            
        Returns:
            Tuple of (converted_amount, final_currency)
        """
        # Parse the value
        amount = cls.parse_currency_value(value_str, detected_currency)
        if amount is None:
            return None, detected_currency
        
        # Convert if needed
        if detected_currency != target_currency:
            converted_amount = cls.convert_currency(amount, detected_currency, target_currency)
            if converted_amount is not None:
                return converted_amount, target_currency
        
        return amount, detected_currency
