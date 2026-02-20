#!/usr/bin/env python3
"""
Zoho CRM OAuth Helper

This script helps generate a Refresh Token for Zoho CRM API access.
Usage:
    python zoho_auth.py [client_id] [client_secret]

If arguments are not provided, it will prompt for them.
"""

import sys
import requests
import urllib.parse
import webbrowser
import time
import os
from dotenv import load_dotenv

load_dotenv()

# Zoho OAuth Configuration
# Defaults, can be overridden by args
DEFAULT_DOMAIN = "com"
REDIRECT_URI = "http://localhost:8000/callback"  # Standard redirect URI for local apps
SCOPES = "ZohoCRM.modules.ALL,ZohoCRM.users.READ"

def get_base_url(domain_suffix):
    return f"https://accounts.zoho.{domain_suffix}"

def get_authorization_code(client_id, domain_suffix):
    """Generates the authorization URL and prompts user to visit it."""
    base_url = get_base_url(domain_suffix)
    params = {
        "scope": SCOPES,
        "client_id": client_id,
        "response_type": "code",
        "access_type": "offline",
        "redirect_uri": REDIRECT_URI,
        "prompt": "consent"
    }
    auth_url = f"{base_url}/oauth/v2/auth?{urllib.parse.urlencode(params)}"
    
    print("\n" + "="*60)
    print("STEP 1: Authorization")
    print("="*60)
    print(f"Please visit the following URL to authorize the app:\n")
    print(auth_url)
    print("\n" + "-"*60)
    
    # Try to open in browser
    try:
        webbrowser.open(auth_url)
    except:
        pass
        
    print(f"After authorizing, you will be redirected to a URL that looks like:")
    print(f"{REDIRECT_URI}?code=YOUR_AUTHORIZATION_CODE&...")
    print("-"*60)
    
    auth_code = input("\nPaste the 'code' parameter from the redirected URL here: ").strip()
    return auth_code

def generate_tokens(client_id, client_secret, auth_code, domain_suffix):
    """Exchanges authorization code for access and refresh tokens."""
    base_url = get_base_url(domain_suffix)
    url = f"{base_url}/oauth/v2/token"
    params = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "code": auth_code
    }
    
    print("\n" + "="*60)
    print("STEP 2: Generating Tokens")
    print("="*60)
    
    try:
        response = requests.post(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        if "error" in data:
            print(f"Error: {data.get('error')}")
            return
            
        print("\nSUCCESS! Here are your credentials:\n")
        
        if "refresh_token" in data:
            print(f"ZOHO_REFRESH_TOKEN={data['refresh_token']}")
            print("\nCopy the above line to your .env file.")
        else:
            print("No refresh token returned. Did you already generate one? (Refresh tokens are permanent until revoked).")
            print("Access Token (valid for 1 hour):", data.get("access_token")[:20] + "...")
            
    except requests.exceptions.RequestException as e:
        print(f"Error generating tokens: {e}")
        if response:
            print(response.text)

def main():
    print("Zoho CRM OAuth Setup")
    print("--------------------")
    
    domain_suffix = "com"
    if len(sys.argv) >= 4:
        client_id = sys.argv[1]
        client_secret = sys.argv[2]
        domain_suffix = sys.argv[3]
    else:
        # Try loading from .env
        env_id = os.getenv("ZOHO_CLIENT_ID")
        env_secret = os.getenv("ZOHO_CLIENT_SECRET")
        env_domain = os.getenv("ZOHO_API_DOMAIN", "")
        
        if env_id and env_secret:
            print("Loaded Client ID and Secret from .env")
            client_id = env_id
            client_secret = env_secret
            
            # Extract domain suffix from ZOHO_API_DOMAIN (e.g., https://www.zohoapis.in -> in)
            if ".in" in env_domain: domain_suffix = "in"
            elif ".eu" in env_domain: domain_suffix = "eu"
            elif ".au" in env_domain: domain_suffix = "au"
            elif ".com" in env_domain: domain_suffix = "com"
            else:
                 # If not in env, prompt or keep default
                 pass
        else:
             client_id = input("Enter Client ID: ").strip()
             client_secret = input("Enter Client Secret: ").strip()
             
        # Allow overriding domain if it wasn't strictly found or if user wants to change it
        # But if we found it in env, maybe we don't prompt? Let's verify with user if they run with specific arg.
        # Actually, let's just prompt for domain if we didn't determine it, or verify it.
        pass
        
    # If explicitly passed via CLI, use that (handled above in if/elif). 
    # If falling through to here without args, we rely on .env or input.
    
    if not client_id or not client_secret:
        client_id = input("Enter Client ID: ").strip()
        client_secret = input("Enter Client Secret: ").strip()
        
    # Confirm domain if relying on defaults/env might be ambiguous, or just accept logic above.
    # To keep it simple for the user's request:
    if len(sys.argv) == 2 and sys.argv[1] in ['in', 'com', 'eu', 'au']:
        domain_suffix = sys.argv[1] # Allow running `python3 zoho_auth.py in`

        domain_input = input("Enter Domain (com/eu/in/au) [default: com]: ").strip()
        if domain_input:
            domain_suffix = domain_input
            
    if not client_id or not client_secret:
        print("Error: Client ID and Client Secret are required.")
        return

    auth_code = get_authorization_code(client_id, domain_suffix)
    if auth_code:
        generate_tokens(client_id, client_secret, auth_code, domain_suffix)

if __name__ == "__main__":
    main()
