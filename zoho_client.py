import requests
import time
import os
from typing import Optional, Dict, List, Any

class ZohoClient:
    """
    Client for Zoho CRM API interactions.
    Handles OAuth token refreshing and lead management.
    """
    def __init__(self, refresh_token: str, client_id: str, client_secret: str, api_domain: str = "https://www.zohoapis.com"):
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.api_domain = api_domain.rstrip('/')
        self.accounts_url = self._get_accounts_url(api_domain)
        
        # Token management
        self.access_token = None
        self.token_expiry = 0
        
    def _get_accounts_url(self, api_domain: str) -> str:
        """Determines the accounts URL based on the API domain."""
        if ".eu" in api_domain:
            return "https://accounts.zoho.eu"
        elif ".in" in api_domain:
            return "https://accounts.zoho.in"
        elif ".com.cn" in api_domain:
            return "https://accounts.zoho.com.cn"
        elif ".com.au" in api_domain:
            return "https://accounts.zoho.com.au"
        return "https://accounts.zoho.com"

    def _refresh_access_token(self):
        """Refreshes the OAuth access token."""
        url = f"{self.accounts_url}/oauth/v2/token"
        params = {
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token"
        }
        
        response = requests.post(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        if "error" in data:
            raise ValueError(f"Error refreshing token: {data.get('error')}")
            
        self.access_token = data["access_token"]
        # Set expiry to now + expires_in (usually 3600s) - buffer
        self.token_expiry = time.time() + data.get("expires_in", 3600) - 60

    def _get_headers(self) -> Dict[str, str]:
        """Returns headers with valid access token."""
        if not self.access_token or time.time() >= self.token_expiry:
            self._refresh_access_token()
        return {
            "Authorization": f"Zoho-oauthtoken {self.access_token}",
            "Content-Type": "application/json"
        }

    def get_leads(self, per_page=200) -> List[Dict[str, Any]]:
        """
        Fetches a list of leads from Zoho CRM.
        """
        url = f"{self.api_domain}/crm/v2/Leads"
        params = {"per_page": per_page}
        
        response = requests.get(url, headers=self._get_headers(), params=params)
        
        if response.status_code == 204:
            return []
            
        response.raise_for_status()
        return response.json().get("data", [])

    def get_leads_by_criteria(self, criteria: str) -> List[Dict[str, Any]]:
        """
        Search for leads matching specific criteria.
        Example criteria: '(Email:equals:test@example.com)'
        """
        url = f"{self.api_domain}/crm/v2/Leads/search"
        params = {"criteria": criteria}
        
        response = requests.get(url, headers=self._get_headers(), params=params)
        
        if response.status_code == 204: # No content
            return []
            
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            print(f"Zoho Search Error: {e}")
            if response:
                print(f"Response: {response.text}")
            raise e
            
        return response.json().get("data", [])

    def get_lead_details(self, lead_id: str) -> Dict[str, Any]:
        """Fetch full details for a specific lead."""
        url = f"{self.api_domain}/crm/v2/Leads/{lead_id}"
        response = requests.get(url, headers=self._get_headers())
        response.raise_for_status()
        data = response.json().get("data", [])
        return data[0] if data else {}

    def get_notes(self, parent_id: str, module: str = "Leads") -> List[Dict[str, Any]]:
        """Fetch notes associated with a record."""
        url = f"{self.api_domain}/crm/v2/{module}/{parent_id}/Notes"
        params = {"sort_by": "Created_Time", "sort_order": "desc", "per_page": 10}
        
        response = requests.get(url, headers=self._get_headers(), params=params)
        
        if response.status_code == 204:
            return []
            
        response.raise_for_status()
        return response.json().get("data", [])

    def add_note(self, parent_id: str, note_content: str, module: str = "Leads") -> bool:
        """Add a note to a record."""
        url = f"{self.api_domain}/crm/v2/{module}/{parent_id}/Notes"
        payload = {
            "data": [
                 {
                    "Note_Content": note_content,
                    "Parent_Id": parent_id,
                    "se_module": module
                }
            ]
        }
        
        response = requests.post(url, headers=self._get_headers(), json=payload)
        response.raise_for_status()
        result = response.json().get("data", [])[0]
        return result.get("code") == "SUCCESS"

    def get_emails(self, lead_id: str) -> List[Dict[str, Any]]:
        """Fetch emails associated with a lead."""
        # Using v3 as v2/v2.1 might be deprecated or behave differently for Emails data
        url = f"{self.api_domain}/crm/v3/Leads/{lead_id}/Emails"
        params = {"sort_by": "Message_Time", "sort_order": "desc", "per_page": 20}
        
        response = requests.get(url, headers=self._get_headers(), params=params)
        
        if response.status_code == 204:
            return []
            
        try:
             response.raise_for_status()
             return response.json().get("Emails", [])
        except Exception as e:
             # Print error for debugging
             print(f"DEBUG: Error fetching emails for {lead_id}: {e}")
             if response.content:
                 print(f"DEBUG: Response Content: {response.content}")
             return []

    def get_email_content(self, lead_id: str, message_id: str) -> str:
        """Fetch the full content of a specific email."""
        # Using v3
        url = f"{self.api_domain}/crm/v3/Leads/{lead_id}/Emails/{message_id}"
        
        try:
            response = requests.get(url, headers=self._get_headers())
            response.raise_for_status()
            data = response.json().get("Emails", [])
            if data and isinstance(data, list):
                return data[0].get("content", "")
            return ""
        except Exception as e:
            print(f"DEBUG: Error fetching email content for {message_id}: {e}")
            return ""

    def update_lead(self, lead_id: str, data: Dict[str, Any]) -> bool:
        """Update a lead's fields."""
        url = f"{self.api_domain}/crm/v2/Leads/{lead_id}"
        payload = {"data": [data]}
        
        response = requests.put(url, headers=self._get_headers(), json=payload)
        response.raise_for_status()
        result_data = response.json().get("data", [])
        if not result_data:
            print("DEBUG: No data returned in update_lead response.")
            return False
            
        result = result_data[0]
        if result.get("code") != "SUCCESS":
            print(f"DEBUG: Zoho Update Error Response: {result}")
            
        return result.get("code") == "SUCCESS"

