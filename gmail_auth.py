#!/usr/bin/env python3
"""
Gmail OAuth Helper

This script helps generate a Refresh Token for Gmail API access.
Usage:
    python gmail_auth.py [client_id] [client_secret]

If arguments are not provided, it will prompt for them.
"""

import sys
import urllib.parse
import webbrowser
import requests
import json
import os

# Google OAuth Configuration
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REDIRECT_URI = "http://localhost:8080" # Google usually requires a specific port or loopback
SCOPES = "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly"

def get_authorization_code(client_id):
    """Generates the authorization URL and prompts user to visit it."""
    params = {
        "scope": SCOPES,
        "client_id": client_id,
        "response_type": "code",
        "access_type": "offline", # Important for refresh token
        "redirect_uri": REDIRECT_URI,
        "prompt": "consent" # Force consent to ensure refresh token is returned
    }
    auth_url = f"{AUTH_URI}?{urllib.parse.urlencode(params)}"
    
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
    print(f"{REDIRECT_URI}/?code=YOUR_AUTHORIZATION_CODE&...")
    print("-"*60)
    
    auth_code = input("\nPaste the 'code' parameter from the redirected URL here: ").strip()
    # Handle case where user pastes full URL
    if "code=" in auth_code:
        try:
            auth_code = auth_code.split("code=")[1].split("&")[0]
        except:
            pass
            
    return urllib.parse.unquote(auth_code)

def generate_tokens(client_id, client_secret, auth_code):
    """Exchanges authorization code for access and refresh tokens."""
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
        response = requests.post(TOKEN_URI, data=params)
        response.raise_for_status()
        data = response.json()
        
        if "error" in data:
            print(f"Error: {data.get('error')}")
            if "error_description" in data:
                print(f"Description: {data.get('error_description')}")
            return
            
        print("\nSUCCESS! Here are your credentials:\n")
        
        if "refresh_token" in data:
            print(f"GOOGLE_REFRESH_TOKEN={data['refresh_token']}")
            print("\nCopy the above line to your .env file.")
        else:
            print("No refresh token returned. Did you already generate one? (Refresh tokens are permanent until revoked).")
            print("Try revoking access in your Google Account permissions and running this script again.")
            
    except requests.exceptions.RequestException as e:
        print(f"Error generating tokens: {e}")
        if 'response' in locals() and response:
            print(response.text)

def main():
    print("Gmail OAuth Setup")
    print("-----------------")
    
    if len(sys.argv) >= 3:
        client_id = sys.argv[1]
        client_secret = sys.argv[2]
    else:
        # Try to load from .env if available, just for convenience
        from dotenv import load_dotenv
        load_dotenv()
        env_id = os.getenv("GOOGLE_CLIENT_ID")
        env_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        
        if env_id and env_secret:
            client_id = env_id
            client_secret = env_secret
            print("Loaded credentials from .env")
        else:
            client_id = input("Enter Client ID: ").strip()
            client_secret = input("Enter Client Secret: ").strip()
        
    if not client_id or not client_secret:
        print("Error: Client ID and Client Secret are required.")
        return

    auth_code = get_authorization_code(client_id)
    if auth_code:
        generate_tokens(client_id, client_secret, auth_code)

if __name__ == "__main__":
    main()
