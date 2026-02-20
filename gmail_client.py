import requests
import time
import base64
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

class GmailClient:
    """
    Client for Gmail API.
    Uses OAuth refresh token to obtain access tokens and send emails.
    """
    TOKEN_URI = "https://oauth2.googleapis.com/token"
    API_URL = "https://gmail.googleapis.com/gmail/v1/users/me"

    def __init__(self, refresh_token: str, client_id: str, client_secret: str):
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = None
        self.token_expiry = 0

    def _refresh_access_token(self):
        """Refreshes the OAuth access token."""
        params = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token
        }
        
        try:
            response = requests.post(self.TOKEN_URI, data=params)
            response.raise_for_status()
            data = response.json()
            
            self.access_token = data["access_token"]
            self.token_expiry = time.time() + data.get("expires_in", 3500)
        except requests.exceptions.RequestException as e:
             print(f"Token Refresh Error: {e}")
             if hasattr(e, 'response') and e.response:
                 print(f"Response: {e.response.text}")
             raise e

    def _get_headers(self):
        """Returns headers with valid access token."""
        if not self.access_token or time.time() >= self.token_expiry:
            self._refresh_access_token()
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

    def find_last_thread_id(self, to_email: str):
        """
        Finds the last thread ID with a specific email address.
        Returns a dict with threadId, messageId (for In-Reply-To) and references.
        """
        try:
            url = f"{self.API_URL}/messages"
            params = {"q": f"to:{to_email} OR from:{to_email}", "maxResults": 1}
            response = requests.get(url, headers=self._get_headers(), params=params)
            response.raise_for_status()
            messages = response.json().get("messages", [])
            
            if not messages:
                return None
            
            last_msg_id = messages[0]["id"]
            thread_id = messages[0]["threadId"]
            
            # Fetch details to get headers
            details = self.get_message_details(last_msg_id)
            if not details:
                return {"threadId": thread_id} # Minimal fallback
                
            headers = details.get("payload", {}).get("headers", [])
            msg_id_header = next((h["value"] for h in headers if h["name"].lower() == "message-id"), None)
            references_header = next((h["value"] for h in headers if h["name"].lower() == "references"), "")
            
            # If References exist, append current msg_id to it. If not, start new.
            new_references = f"{references_header} {msg_id_header}".strip() if msg_id_header else references_header

            return {
                "threadId": thread_id,
                "in_reply_to": msg_id_header,
                "references": new_references
            }
        except Exception as e:
            print(f"Error finding thread: {e}")
            return None

    def get_message_details(self, message_id: str):
         """Get details of a specific message to extract Message-ID."""
         try:
            url = f"{self.API_URL}/messages/{message_id}"
            response = requests.get(url, headers=self._get_headers())
            return response.json()
         except:
             return None

    def send_email(self, to_email: str, subject: str, body_html: str, thread_context: dict = None) -> bool:
        """Sends an email via Gmail API, optionally replying to a thread."""
        try:
            message = MIMEMultipart()
            message['to'] = to_email
            
            # Threading Logic
            thread_id = None
            if thread_context:
                thread_id = thread_context.get("threadId")
                
                # If replying, subject usually starts with Re: (but Gmail handles this smarter too)
                if not subject.lower().startswith("re:"):
                    subject = f"Re: {subject}"
                
                if thread_context.get("in_reply_to"):
                    message['In-Reply-To'] = thread_context["in_reply_to"]
                
                if thread_context.get("references"):
                    message['References'] = thread_context["references"]
            
            message['subject'] = subject

            msg = MIMEText(body_html, 'html')
            message.attach(msg)
            
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
            
            payload = {'raw': raw_message}
            
            if thread_id:
                payload['threadId'] = thread_id
            
            url = f"{self.API_URL}/messages/send"
            response = requests.post(url, headers=self._get_headers(), json=payload)
            response.raise_for_status()
            
            print(f"Gmail: Sent email to {to_email} (Id: {response.json().get('id')})")
            return True
            
        except Exception as e:
            print(f"Error sending email via Gmail: {e}")
            if hasattr(e, 'response') and e.response:
                print(e.response.text)
            return False
