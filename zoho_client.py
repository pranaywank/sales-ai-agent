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

    def _extract_email_rows(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Zoho email APIs can return different keys by endpoint/region/version.
        Normalize both to a list of email rows.
        """
        rows = payload.get("Emails")
        if isinstance(rows, list):
            return rows
        rows = payload.get("email_related_list")
        if isinstance(rows, list):
            return rows
        rows = payload.get("data")
        if isinstance(rows, list):
            return rows
        return []

    def get_emails(self, lead_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Fetch emails associated with a lead.
        Paginates using Zoho's index cursor when available.
        """
        url = f"{self.api_domain}/crm/v3/Leads/{lead_id}/Emails"
        next_index: Optional[str] = None
        collected: List[Dict[str, Any]] = []

        while len(collected) < max(limit, 1):
            params: Dict[str, Any] = {
                "sort_by": "Message_Time",
                "sort_order": "desc",
                "per_page": min(200, max(limit, 10)),
            }
            if next_index:
                params["index"] = next_index

            response = requests.get(url, headers=self._get_headers(), params=params)
            if response.status_code == 204:
                break

            try:
                response.raise_for_status()
                payload = response.json()
                rows = self._extract_email_rows(payload)
                if not rows:
                    break
                collected.extend(rows)

                info = payload.get("info", {}) if isinstance(payload, dict) else {}
                more_records = bool(info.get("more_records"))
                next_index = info.get("next_index")
                if not more_records or not next_index:
                    break
            except Exception as e:
                print(f"DEBUG: Error fetching emails for {lead_id}: {e}")
                if response.content:
                    print(f"DEBUG: Response Content: {response.content}")
                break

        return collected[:limit]

    def get_email_content(self, lead_id: str, message_id: str, owner_id: Optional[str] = None) -> str:
        """
        Fetch full content for a specific email.
        For shared mailboxes, Zoho may require user_id (owner id) on this endpoint.
        """
        url = f"{self.api_domain}/crm/v3/Leads/{lead_id}/Emails/{message_id}"
        user_ids_to_try: List[Optional[str]] = [None]
        if owner_id:
            user_ids_to_try.append(owner_id)

        last_error = None
        for user_id in user_ids_to_try:
            params = {"user_id": user_id} if user_id else None
            try:
                response = requests.get(url, headers=self._get_headers(), params=params)
                response.raise_for_status()
                rows = self._extract_email_rows(response.json())
                if rows and isinstance(rows[0], dict):
                    return rows[0].get("content", "") or ""
                return ""
            except Exception as e:
                last_error = e
                if hasattr(e, "response") and e.response is not None and e.response.content:
                    print(f"DEBUG: View email error ({message_id}, user_id={user_id}): {e.response.content}")

        print(f"DEBUG: Error fetching email content for {message_id}: {last_error}")
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
