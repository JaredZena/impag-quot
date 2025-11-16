#!/usr/bin/env python3
"""
Test runner for IMPAG Admin Tool APIs
Run with: python run_tests.py
"""

import subprocess
import sys
import os

def run_command(command, description):
    """Run a command and print results"""
    print(f"\n{'='*60}")
    print(f"🧪 {description}")
    print(f"{'='*60}")
    
    try:
        result = subprocess.run(
            command, 
            shell=True, 
            capture_output=True, 
            text=True,
            check=True
        )
        
        if result.stdout:
            print("✅ STDOUT:")
            print(result.stdout)
        
        if result.stderr:
            print("⚠️ STDERR:")
            print(result.stderr)
            
        print(f"✅ {description} completed successfully!")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"❌ {description} failed!")
        print(f"Exit code: {e.returncode}")
        if e.stdout:
            print("STDOUT:")
            print(e.stdout)
        if e.stderr:
            print("STDERR:")
            print(e.stderr)
        return False

def main():
    """Run all tests"""
    print("🚀 Starting IMPAG Admin Tool API Tests")
    
    # Check if pytest is installed
    try:
        import pytest
    except ImportError:
        print("❌ pytest is not installed. Install it with: pip install pytest")
        return False
    
    # Check if required modules are available
    try:
        import fastapi
        import sqlalchemy
        import pydantic
    except ImportError as e:
        print(f"❌ Required dependency missing: {e}")
        return False
    
    all_passed = True
    
    # Test 1: Run Kits API tests
    if not run_command("python -m pytest test_kits.py -v", "Testing Kits API"):
        all_passed = False
    
    # Test 2: Run Balance API tests  
    if not run_command("python -m pytest test_balance.py -v", "Testing Balance API"):
        all_passed = False
    
    # Test 3: Run both tests together for integration
    if not run_command("python -m pytest test_kits.py test_balance.py -v", "Running Integration Tests"):
        all_passed = False
    
    # Test 4: Test API endpoints directly (if server is running)
    print(f"\n{'='*60}")
    print("🌐 Testing Live API Endpoints (optional)")
    print(f"{'='*60}")
    
    try:
        import requests
        
        # Test if server is running
        try:
            response = requests.get("http://localhost:8000/health", timeout=2)
            if response.status_code == 200:
                print("✅ Server is running!")
                
                # Test kits endpoint
                kits_response = requests.get("http://localhost:8000/kits", timeout=2)
                if kits_response.status_code == 200:
                    print("✅ Kits endpoint is working!")
                    print(f"   Response: {kits_response.json()}")
                else:
                    print(f"❌ Kits endpoint failed: {kits_response.status_code}")
                    all_passed = False
                
                # Test balance endpoint
                balance_response = requests.get("http://localhost:8000/balance", timeout=2)
                if balance_response.status_code == 200:
                    print("✅ Balance endpoint is working!")
                    print(f"   Response: {balance_response.json()}")
                else:
                    print(f"❌ Balance endpoint failed: {balance_response.status_code}")
                    all_passed = False
                    
            else:
                print(f"⚠️ Server responded with status: {response.status_code}")
                
        except requests.exceptions.RequestException:
            print("⚠️ Server is not running (this is optional)")
            print("   Start server with: uvicorn main:app --reload")
            
    except ImportError:
        print("⚠️ requests not installed, skipping live API tests")
    
    # Final results
    print(f"\n{'='*60}")
    if all_passed:
        print("🎉 ALL TESTS PASSED!")
        print("✅ Your Kits and Balance APIs are working correctly!")
    else:
        print("❌ SOME TESTS FAILED!")
        print("🔧 Check the error messages above and fix any issues.")
    print(f"{'='*60}")
    
    return all_passed

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)