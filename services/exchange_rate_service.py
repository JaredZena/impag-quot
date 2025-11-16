#!/usr/bin/env python3
"""
Exchange rate service for USD to MXN conversion
"""

import requests
import json
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Optional
import os

class ExchangeRateService:
    """Service for fetching and caching exchange rates"""
    
    def __init__(self):
        self.cache_duration_hours = 1
        self._cache = {}
        self._cache_expiry = None
        
        # Fallback rates if API fails
        self.fallback_rates = {
            'USD_MXN': 20.0,  # Approximate rate
            'MXN_USD': 0.05   # Approximate rate
        }
    
    def get_exchange_rate(self, from_currency: str, to_currency: str) -> Optional[float]:
        """
        Get exchange rate between currencies
        
        Args:
            from_currency: Source currency ('USD' or 'MXN')
            to_currency: Target currency ('USD' or 'MXN')
            
        Returns:
            Exchange rate as float or None if unavailable
        """
        if from_currency == to_currency:
            return 1.0
        
        cache_key = f"{from_currency}_{to_currency}"
        
        # Check cache first
        if (cache_key in self._cache and 
            self._cache_expiry and 
            datetime.now() < self._cache_expiry):
            return self._cache[cache_key]
        
        # Try to get rate from API
        rate = self._fetch_rate_from_api(from_currency, to_currency)
        
        # Fallback to cached rate or default
        if rate is None:
            rate = self.fallback_rates.get(cache_key)
        
        if rate is not None:
            # Cache the rate
            self._cache[cache_key] = rate
            self._cache_expiry = datetime.now() + timedelta(hours=self.cache_duration_hours)
        
        return rate
    
    def _fetch_rate_from_api(self, from_currency: str, to_currency: str) -> Optional[float]:
        """
        Fetch exchange rate from external API
        
        Args:
            from_currency: Source currency
            to_currency: Target currency
            
        Returns:
            Exchange rate or None if API fails
        """
        try:
            # Try multiple APIs for reliability
            rate = self._try_fixer_api(from_currency, to_currency)
            if rate is not None:
                return rate
            
            rate = self._try_exchangerate_api(from_currency, to_currency)
            if rate is not None:
                return rate
                
        except Exception as e:
            print(f"Error fetching exchange rate: {e}")
        
        return None
    
    def _try_fixer_api(self, from_currency: str, to_currency: str) -> Optional[float]:
        """Try Fixer.io API (requires API key)"""
        api_key = os.getenv('FIXER_API_KEY')
        if not api_key:
            return None
        
        try:
            url = f"http://data.fixer.io/api/latest?access_key={api_key}&symbols={to_currency}&base={from_currency}"
            response = requests.get(url, timeout=10)
            data = response.json()
            
            if data.get('success') and to_currency in data.get('rates', {}):
                return float(data['rates'][to_currency])
        except Exception as e:
            print(f"Fixer API error: {e}")
        
        return None
    
    def _try_exchangerate_api(self, from_currency: str, to_currency: str) -> Optional[float]:
        """Try ExchangeRate-API (free tier available)"""
        try:
            url = f"https://api.exchangerate-api.com/v4/latest/{from_currency}"
            response = requests.get(url, timeout=10)
            data = response.json()
            
            if 'rates' in data and to_currency in data['rates']:
                return float(data['rates'][to_currency])
        except Exception as e:
            print(f"ExchangeRate API error: {e}")
        
        return None
    
    def convert_amount(self, amount: Decimal, from_currency: str, to_currency: str) -> Optional[Decimal]:
        """
        Convert amount from one currency to another
        
        Args:
            amount: Amount to convert
            from_currency: Source currency
            to_currency: Target currency
            
        Returns:
            Converted amount or None if conversion fails
        """
        if from_currency == to_currency:
            return amount
        
        rate = self.get_exchange_rate(from_currency, to_currency)
        if rate is None:
            return None
        
        return amount * Decimal(str(rate))
    
    def get_rate_info(self, from_currency: str, to_currency: str) -> dict:
        """
        Get detailed rate information
        
        Returns:
            Dict with rate, source, and timestamp
        """
        rate = self.get_exchange_rate(from_currency, to_currency)
        
        return {
            'rate': rate,
            'from_currency': from_currency,
            'to_currency': to_currency,
            'source': 'api' if rate != self.fallback_rates.get(f"{from_currency}_{to_currency}") else 'fallback',
            'timestamp': datetime.now().isoformat(),
            'cache_expiry': self._cache_expiry.isoformat() if self._cache_expiry else None
        }

# Global instance
exchange_rate_service = ExchangeRateService()
