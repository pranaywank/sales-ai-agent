#!/usr/bin/env python3
"""
Lead Finder AI Agent

This script identifies high-engagement but stale leads in HubSpot (Marketing Contacts without deals)
and generates personalized outreach emails by enriching context from multiple sources.

Workflow:
1. Query HubSpot for Marketing Contacts (hs_marketable_status = true)
2. Exclude contacts associated with deals
3. Apply configurable filters (employee size, industry, country, job title, lifecycle stage)
4. Score contacts by engagement (email opens/clicks, website visits, form submissions, meetings)
5. Filter for stale contacts (no activity in 14+ days)
6. Rank top 10 by engagement score
7. Enrich with context from Apollo, Slack, and Fireflies
8. Generate personalized outreach emails using Claude
9. Send a digest email to the sales team

Usage:
    python lead_finder_agent.py

Environment Variables Required:
    ANTHROPIC_API_KEY - Your Anthropic API key
    HUBSPOT_ACCESS_TOKEN - Your HubSpot private app access token
    SENDGRID_API_KEY - Your SendGrid API key
    LEAD_FINDER_RECIPIENTS - Comma-separated list of email addresses for the digest
    FROM_EMAIL - Sender email address for the digest

Optional Environment Variables:
    APOLLO_API_KEY - Apollo.io API key for contact/company enrichment
    SLACK_BOT_TOKEN - Slack Bot OAuth token for searching internal discussions
    SLACK_CHANNELS - Comma-separated list of Slack channels to search
    FIREFLIES_API_KEY - Fireflies.ai API key for searching call transcripts
    
Contact Filtering (all optional):
    MIN_EMPLOYEE_SIZE - Minimum company employee size (default: 200)
    TARGET_INDUSTRIES - Comma-separated list of industries to include
    TARGET_COUNTRIES - Comma-separated list of countries to include
    TARGET_JOB_TITLES - Comma-separated job title keywords to include
    TARGET_LIFECYCLE_STAGES - Comma-separated HubSpot lifecycle stages to include
    STALE_THRESHOLD_DAYS - Days since last contact to consider stale (default: 14)
"""

import os
import json
import requests
from datetime import datetime, timedelta
from typing import Optional
import anthropic
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configuration
HUBSPOT_BASE_URL = "https://api.hubapi.com"

# Knowledge base paths
CHROMA_PERSIST_DIR = os.path.join(os.path.dirname(__file__), ".chroma")
COLLECTION_NAME = "adopt_ai_knowledge"

# Digest recipients (comma-separated in env var)
LEAD_FINDER_RECIPIENTS = [
    email.strip() 
    for email in os.getenv("LEAD_FINDER_RECIPIENTS", "").split(",") 
    if email.strip()
]
if not LEAD_FINDER_RECIPIENTS:
    import logging as _logging
    _logging.warning("LEAD_FINDER_RECIPIENTS not set — digest email will not be sent. Set this env var for full operation.")

# Sender email for digest
FROM_EMAIL = os.getenv("FROM_EMAIL", "noreply@example.com")

# Days threshold for stale contacts
STALE_THRESHOLD_DAYS = int(os.getenv("STALE_THRESHOLD_DAYS", "14"))

# Contact filtering configuration
MIN_EMPLOYEE_SIZE = int(os.getenv("MIN_EMPLOYEE_SIZE", "200"))

TARGET_INDUSTRIES = [
    ind.strip() 
    for ind in os.getenv("TARGET_INDUSTRIES", "").split(",") 
    if ind.strip()
]

TARGET_COUNTRIES = [
    country.strip() 
    for country in os.getenv("TARGET_COUNTRIES", "").split(",") 
    if country.strip()
]

TARGET_JOB_TITLES = [
    title.strip() 
    for title in os.getenv("TARGET_JOB_TITLES", "").split(",") 
    if title.strip()
]

TARGET_LIFECYCLE_STAGES = [
    stage.strip().lower() 
    for stage in os.getenv("TARGET_LIFECYCLE_STAGES", "").split(",") 
    if stage.strip()
]

# Slack channels to search for internal context
DEFAULT_SLACK_CHANNELS = "sales,marketing"
SLACK_CHANNELS = [
    channel.strip() 
    for channel in os.getenv("SLACK_CHANNELS", DEFAULT_SLACK_CHANNELS).split(",") 
    if channel.strip()
]

# Number of top leads to include in digest
TOP_LEADS_COUNT = int(os.getenv("TOP_LEADS_COUNT", "10"))


class HubSpotLeadClient:
    """Client for HubSpot API interactions focused on lead/contact operations."""
    
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
    
    def get_all_contacts(self, properties: list[str], limit: int = 100) -> list[dict]:
        """Fetch all contacts with specified properties."""
        url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts"
        
        all_contacts = []
        after = None
        
        while True:
            params = {
                "limit": limit,
                "properties": ",".join(properties)
            }
            if after:
                params["after"] = after
            
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            all_contacts.extend(data.get("results", []))
            
            paging = data.get("paging", {})
            if paging.get("next"):
                after = paging["next"]["after"]
            else:
                break
        
        return all_contacts
    
    def search_contacts(self, filters: list[dict], properties: list[str]) -> list[dict]:
        """Search for contacts with specific filters."""
        url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts/search"
        
        payload = {
            "filterGroups": [{"filters": filters}] if filters else [],
            "properties": properties,
            "limit": 100
        }
        
        all_contacts = []
        after = None
        
        while True:
            if after:
                payload["after"] = after
            
            response = requests.post(url, headers=self.headers, json=payload)
            response.raise_for_status()
            data = response.json()
            
            all_contacts.extend(data.get("results", []))
            
            paging = data.get("paging", {})
            if paging.get("next"):
                after = paging["next"]["after"]
            else:
                break
        
        return all_contacts
    
    def get_contact_deal_associations(self, contact_id: str) -> list[dict]:
        """Check if a contact has any associated deals."""
        url = f"{HUBSPOT_BASE_URL}/crm/v4/objects/contacts/{contact_id}/associations/deals"
        
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        
        return response.json().get("results", [])
    
    def get_contact_meeting_associations(self, contact_id: str) -> list[dict]:
        """Get meetings associated with a contact."""
        url = f"{HUBSPOT_BASE_URL}/crm/v4/objects/contacts/{contact_id}/associations/meetings"
        
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            return response.json().get("results", [])
        except requests.exceptions.RequestException:
            return []
    
    def get_associated_company(self, contact_id: str) -> Optional[dict]:
        """Get the company associated with a contact."""
        url = f"{HUBSPOT_BASE_URL}/crm/v4/objects/contacts/{contact_id}/associations/companies"
        
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        
        associations = response.json().get("results", [])
        if not associations:
            return None
        
        company_id = associations[0].get("toObjectId") or associations[0].get("id")
        
        # Get company details
        company_url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/companies/{company_id}"
        company_response = requests.get(
            company_url, 
            headers=self.headers,
            params={"properties": "name,industry,numberofemployees,description,website,country,city,annualrevenue"}
        )
        company_response.raise_for_status()
        
        return company_response.json()
    
    def get_contact_emails(self, contact_id: str, limit: int = 20) -> list[dict]:
        """Get emails associated with a contact."""
        all_email_ids = []
        url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts/{contact_id}/associations/emails"
        
        # Paginate through all associations
        while url:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            
            data = response.json()
            associations = data.get("results", [])
            
            for a in associations:
                email_id = a.get("toObjectId") or a.get("id")
                if email_id:
                    all_email_ids.append(email_id)
            
            # Check for next page
            paging = data.get("paging", {})
            next_link = paging.get("next", {}).get("link")
            url = next_link if next_link else None
        
        if not all_email_ids:
            return []
        
        # Fetch email details
        return self._fetch_emails_by_ids(all_email_ids[:limit])
    
    def _fetch_emails_by_ids(self, email_ids: list[str]) -> list[dict]:
        """Fetch email details by IDs."""
        if not email_ids:
            return []
        
        unique_ids = list(set(email_ids))
        all_emails = []
        emails_url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/emails/batch/read"
        
        batch_size = 100
        for i in range(0, len(unique_ids), batch_size):
            batch_ids = unique_ids[i:i + batch_size]
            
            emails_payload = {
                "inputs": [{"id": eid} for eid in batch_ids],
                "properties": ["hs_email_subject", "hs_email_status", "hs_email_direction",
                              "hs_timestamp", "hs_email_text", "hs_createdate"]
            }
            
            emails_response = requests.post(emails_url, headers=self.headers, json=emails_payload)
            emails_response.raise_for_status()
            
            all_emails.extend(emails_response.json().get("results", []))
        
        return all_emails
    
    def get_contact_notes(self, contact_id: str, limit: int = 5) -> list[dict]:
        """Get notes associated with a contact."""
        url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts/{contact_id}/associations/notes"
        
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        
        associations = response.json().get("results", [])
        if not associations:
            return []
        
        note_ids = [a.get("toObjectId") or a.get("id") for a in associations[:limit]]
        
        # Batch read notes
        notes_url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/notes/batch/read"
        notes_payload = {
            "inputs": [{"id": nid} for nid in note_ids],
            "properties": ["hs_note_body", "hs_timestamp", "hs_createdate"]
        }
        
        notes_response = requests.post(notes_url, headers=self.headers, json=notes_payload)
        notes_response.raise_for_status()
        
        return notes_response.json().get("results", [])


class ApolloClient:
    """Client for Apollo.io API interactions for contact/company enrichment."""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.apollo.io/api/v1"
        self.headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": api_key
        }
    
    def enrich_contact(self, email: str) -> dict:
        """Enrich a contact by email address.
        
        Returns company info, contact details, intent signals, and similar companies.
        """
        url = f"{self.base_url}/people/match"
        
        # Apollo API takes parameters as query params
        params = {
            "email": email,
            "reveal_personal_emails": "false"
        }
        
        try:
            response = requests.post(url, headers=self.headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            person = data.get("person", {})
            organization = person.get("organization", {})
            
            return {
                "found": bool(person),
                "contact": {
                    "name": person.get("name", ""),
                    "title": person.get("title", ""),
                    "seniority": person.get("seniority", ""),
                    "department": person.get("departments", []),
                    "linkedin_url": person.get("linkedin_url", ""),
                },
                "company": {
                    "name": organization.get("name", ""),
                    "website": organization.get("website_url", ""),
                    "industry": organization.get("industry", ""),
                    "employee_count": organization.get("estimated_num_employees", 0),
                    "funding_stage": organization.get("funding_stage", ""),
                    "total_funding": organization.get("total_funding", 0),
                    "latest_funding_round": organization.get("latest_funding_round_type", ""),
                    "tech_stack": organization.get("technologies", []),
                    "keywords": organization.get("keywords", []),
                    "city": organization.get("city", ""),
                    "country": organization.get("country", ""),
                    "linkedin_url": organization.get("linkedin_url", ""),
                    "phone": organization.get("phone", ""),
                    "annual_revenue": organization.get("annual_revenue", ""),
                },
                "intent_signals": {
                    "hiring_signal": bool(organization.get("current_job_openings", [])),
                    "job_openings": organization.get("current_job_openings", [])[:5],
                    "recent_news": person.get("employment_history", [])[:2],
                }
            }
            
        except requests.exceptions.RequestException as e:
            print(f"   ⚠️ Apollo API error: {e}")
            return {"found": False, "error": str(e)}
    
    def format_apollo_context(self, enrichment: dict) -> str:
        """Format Apollo enrichment data into a readable context string."""
        if not enrichment.get("found"):
            return "No Apollo enrichment data available."
        
        company = enrichment.get("company", {})
        contact = enrichment.get("contact", {})
        intent = enrichment.get("intent_signals", {})
        
        lines = []
        
        # Company info
        if company.get("name"):
            lines.append(f"**Company**: {company['name']}")
            if company.get("industry"):
                lines.append(f"  - Industry: {company['industry']}")
            if company.get("employee_count"):
                lines.append(f"  - Employees: {company['employee_count']:,}")
            if company.get("funding_stage"):
                lines.append(f"  - Funding Stage: {company['funding_stage']}")
            if company.get("total_funding"):
                lines.append(f"  - Total Funding: ${company['total_funding']:,}")
            if company.get("annual_revenue"):
                lines.append(f"  - Annual Revenue: {company['annual_revenue']}")
            if company.get("tech_stack"):
                tech_list = company['tech_stack'][:10]
                lines.append(f"  - Tech Stack: {', '.join(tech_list)}")
        
        # Contact info
        if contact.get("seniority"):
            lines.append(f"**Contact Seniority**: {contact['seniority']}")
        if contact.get("department"):
            lines.append(f"**Department**: {', '.join(contact['department'])}")
        
        # Intent signals
        if intent.get("hiring_signal"):
            lines.append(f"**Hiring Signal**: Currently hiring")
            if intent.get("job_openings"):
                openings = [j.get("title", "") for j in intent["job_openings"] if j.get("title")]
                if openings:
                    lines.append(f"  - Open roles: {', '.join(openings[:3])}")
        
        return "\n".join(lines) if lines else "Limited Apollo data available."


class SlackClient:
    """Client for Slack API interactions."""
    
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.headers = {
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json"
        }
        self.base_url = "https://slack.com/api"
    
    def search_messages(self, query: str, channels: list[str], limit: int = 10) -> list[dict]:
        """Search for messages in specified channels."""
        channel_filter = " ".join([f"in:#{ch}" for ch in channels])
        full_query = f"{query} {channel_filter}"
        
        url = f"{self.base_url}/search.messages"
        params = {
            "query": full_query,
            "count": limit,
            "sort": "timestamp",
            "sort_dir": "desc"
        }
        
        response = requests.get(url, headers=self.headers, params=params)
        response.raise_for_status()
        
        data = response.json()
        
        if not data.get("ok"):
            error = data.get("error", "Unknown error")
            print(f"   ⚠️ Slack search error: {error}")
            return []
        
        messages = data.get("messages", {}).get("matches", [])
        
        formatted = []
        for msg in messages[:limit]:
            formatted.append({
                "text": msg.get("text", "")[:500],
                "user": msg.get("username", msg.get("user", "Unknown")),
                "channel": msg.get("channel", {}).get("name", "unknown"),
                "timestamp": msg.get("ts", ""),
                "permalink": msg.get("permalink", "")
            })
        
        return formatted
    
    def format_slack_context(self, messages: list[dict]) -> str:
        """Format Slack messages into a readable context string."""
        if not messages:
            return "No relevant Slack discussions found."
        
        formatted_messages = []
        for msg in messages:
            try:
                ts = float(msg["timestamp"])
                date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                date_str = "Unknown date"
            
            formatted_messages.append(
                f"- [{date_str}] #{msg['channel']} - @{msg['user']}: {msg['text']}"
            )
        
        return "\n".join(formatted_messages)


class FirefliesClient:
    """Client for Fireflies.ai API interactions."""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.fireflies.ai/graphql"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    
    def search_transcripts_by_title(self, search_term: str, limit: int = 5) -> list[dict]:
        """Search for meeting transcripts by title."""
        query = """
        query TranscriptsByTitle($title: String!, $limit: Int) {
            transcripts(title: $title, limit: $limit) {
                id
                title
                date
                duration
                summary {
                    overview
                    action_items
                    keywords
                }
            }
        }
        """
        
        variables = {
            "title": search_term,
            "limit": limit
        }
        
        try:
            response = requests.post(
                self.base_url,
                headers=self.headers,
                json={"query": query, "variables": variables}
            )
            response.raise_for_status()
            
            data = response.json()
            
            if "errors" in data:
                print(f"   ⚠️ Fireflies API error: {data['errors']}")
                return []
            
            return data.get("data", {}).get("transcripts", []) or []
            
        except requests.exceptions.RequestException as e:
            print(f"   ⚠️ Fireflies request error: {e}")
            return []
    
    def format_fireflies_context(self, transcripts: list[dict]) -> str:
        """Format Fireflies transcripts into a readable context string."""
        if not transcripts:
            return "No call transcripts found for this contact."
        
        formatted_transcripts = []
        
        for transcript in transcripts:
            date_val = transcript.get("date", "Unknown date")
            date_str = "Unknown date"
            if date_val:
                try:
                    if isinstance(date_val, (int, float)):
                        ts = date_val / 1000 if date_val > 1e12 else date_val
                        dt = datetime.fromtimestamp(ts)
                        date_str = dt.strftime("%Y-%m-%d")
                    elif isinstance(date_val, str):
                        dt = datetime.fromisoformat(date_val.replace("Z", "+00:00"))
                        date_str = dt.strftime("%Y-%m-%d")
                except (ValueError, TypeError, OSError):
                    date_str = str(date_val) if date_val else "Unknown date"
            
            title = transcript.get("title", "Untitled Meeting")
            duration = transcript.get("duration", 0)
            duration_mins = round(duration / 60) if duration else 0
            
            summary = transcript.get("summary", {}) or {}
            overview = summary.get("overview", "No summary available")
            action_items = summary.get("action_items", []) or []
            keywords = summary.get("keywords", []) or []
            
            transcript_text = f"📞 **{title}** ({date_str}, {duration_mins} mins)\n"
            transcript_text += f"   Summary: {overview[:500]}...\n" if len(overview) > 500 else f"   Summary: {overview}\n"
            
            if action_items:
                transcript_text += f"   Action Items: {', '.join(action_items[:5])}\n"
            
            if keywords:
                transcript_text += f"   Keywords: {', '.join(keywords[:10])}\n"
            
            formatted_transcripts.append(transcript_text)
        
        return "\n".join(formatted_transcripts)


class KnowledgeBaseClient:
    """Client for querying the Adopt AI knowledge base via ChromaDB."""
    
    def __init__(self):
        self.client = None
        self.collection = None
        self._initialized = False
        
    def initialize(self) -> bool:
        """Initialize ChromaDB connection. Returns True if successful."""
        if self._initialized:
            return True
            
        try:
            import chromadb
            
            if not os.path.exists(CHROMA_PERSIST_DIR):
                print("   ⚠️ Knowledge base not found. Run 'python index_knowledge_base.py' first.")
                return False
            
            self.client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
            self.collection = self.client.get_collection(COLLECTION_NAME)
            self._initialized = True
            return True
            
        except ImportError:
            print("   ⚠️ ChromaDB not installed. Run 'pip install chromadb'")
            return False
        except ValueError as e:
            print(f"   ⚠️ Knowledge base collection not found: {e}")
            print("   Run 'python index_knowledge_base.py' to create it.")
            return False
        except Exception as e:
            print(f"   ⚠️ Error initializing knowledge base: {e}")
            return False
    
    def search(self, query: str, n_results: int = 5) -> list[dict]:
        """Search the knowledge base for relevant content.
        
        Args:
            query: The search query (e.g., persona, industry, use case)
            n_results: Number of results to return
            
        Returns:
            List of dicts with 'content', 'source', and 'category' keys
        """
        if not self._initialized and not self.initialize():
            return []
        
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results
            )
            
            if not results["documents"] or not results["documents"][0]:
                return []
            
            formatted_results = []
            for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
                formatted_results.append({
                    "content": doc,
                    "source": meta.get("source", "unknown"),
                    "category": meta.get("category", "general"),
                })
            
            return formatted_results
            
        except Exception as e:
            print(f"   ⚠️ Knowledge base search error: {e}")
            return []
    
    def get_context_for_lead(self, lead_context: dict) -> str:
        """Build a comprehensive knowledge base context for a specific lead.
        
        Searches for:
        1. Industry-specific use cases and capabilities
        2. Persona-relevant messaging (based on job title)
        3. Company size-appropriate value propositions
        """
        if not self._initialized and not self.initialize():
            return "Knowledge base not available."
        
        all_results = []
        
        # Search by industry
        industry = lead_context.get("company_industry", "")
        if industry and industry != "Unknown":
            results = self.search(f"{industry} industry use case capabilities", n_results=3)
            all_results.extend(results)
        
        # Search by job title/persona
        job_title = lead_context.get("contact_title", "")
        if job_title and job_title != "Unknown":
            results = self.search(f"{job_title} persona messaging value proposition", n_results=3)
            all_results.extend(results)
        
        # Search by company context (tech stack, etc.)
        apollo_data = lead_context.get("apollo_enrichment", {})
        if apollo_data.get("found"):
            company_data = apollo_data.get("company", {})
            tech_stack = company_data.get("tech_stack", [])
            if tech_stack:
                tech_query = " ".join(tech_stack[:5])
                results = self.search(f"integration {tech_query} API", n_results=2)
                all_results.extend(results)
        
        # General capabilities search
        results = self.search("Adopt AI capabilities features platform", n_results=2)
        all_results.extend(results)
        
        # Deduplicate and format
        seen_content = set()
        unique_results = []
        for r in all_results:
            content_hash = hash(r["content"][:100])
            if content_hash not in seen_content:
                seen_content.add(content_hash)
                unique_results.append(r)
        
        return self.format_kb_context(unique_results[:8])
    
    def format_kb_context(self, results: list[dict]) -> str:
        """Format knowledge base results into a context string for the prompt."""
        if not results:
            return "No relevant knowledge base content found."
        
        formatted = []
        for r in results:
            source = r.get("source", "unknown")
            category = r.get("category", "general")
            content = r.get("content", "")[:800]  # Limit content length
            
            formatted.append(f"[{category}/{source}]\n{content}")
        
        return "\n\n---\n\n".join(formatted)


def calculate_engagement_score(contact: dict, meeting_count: int = 0) -> int:
    """Calculate engagement score for a contact based on HubSpot activity properties.
    
    Scoring:
    - Email opens: 2 points per open (max 20)
    - Email clicks: 5 points per click (max 25)
    - Email replies: 15 points if recent reply exists
    - Website visits: 1 point per page view (max 15)
    - Form submissions: 10 points per conversion (max 30)
    - Meetings: 20 points per meeting (max 40)
    
    Total possible: ~145 points
    """
    props = contact.get("properties", {})
    score = 0
    
    # Email opens (2 pts each, max 20)
    email_opens = int(props.get("hs_email_open_count", 0) or 0)
    score += min(email_opens * 2, 20)
    
    # Email clicks (5 pts each, max 25)
    email_clicks = int(props.get("hs_email_click_count", 0) or 0)
    score += min(email_clicks * 5, 25)
    
    # Email replies (15 pts if recent)
    last_reply = props.get("hs_sales_email_last_replied")
    if last_reply:
        score += 15
    
    # Website visits (1 pt each, max 15)
    page_views = int(props.get("hs_analytics_num_page_views", 0) or 0)
    score += min(page_views, 15)
    
    # Form submissions (10 pts each, max 30)
    conversions = int(props.get("num_conversion_events", 0) or 0)
    score += min(conversions * 10, 30)
    
    # Meetings (20 pts each, max 40)
    score += min(meeting_count * 20, 40)
    
    return score


def is_contact_stale(contact: dict, threshold_days: int = STALE_THRESHOLD_DAYS) -> bool:
    """Check if a contact is stale based on last activity date."""
    props = contact.get("properties", {})
    
    # Check multiple last-contacted properties
    last_contacted = props.get("notes_last_contacted")
    last_email_sent = props.get("hs_sales_email_last_sent")
    last_activity = props.get("hs_last_sales_activity_timestamp")
    
    # Find the most recent activity date
    most_recent = None
    
    for date_val in [last_contacted, last_email_sent, last_activity]:
        if not date_val:
            continue
        try:
            if isinstance(date_val, str):
                dt = datetime.fromisoformat(date_val.replace("Z", "+00:00"))
            else:
                dt = datetime.fromtimestamp(date_val / 1000)
            
            if most_recent is None or dt > most_recent:
                most_recent = dt
        except (ValueError, TypeError):
            continue
    
    # If no activity found, consider it stale
    if most_recent is None:
        return True
    
    # Check if activity is older than threshold
    now = datetime.now(most_recent.tzinfo) if most_recent.tzinfo else datetime.now()
    cutoff = now - timedelta(days=threshold_days)
    
    return most_recent < cutoff


def passes_filters(contact: dict, company: Optional[dict]) -> tuple[bool, str]:
    """Check if a contact passes all configured filters.
    
    Returns (passes, reason) tuple.
    """
    props = contact.get("properties", {})
    company_props = company.get("properties", {}) if company else {}
    
    # Filter: Employee size
    if MIN_EMPLOYEE_SIZE > 0:
        employee_count = company_props.get("numberofemployees")
        if employee_count:
            try:
                emp_count = int(employee_count)
                if emp_count < MIN_EMPLOYEE_SIZE:
                    return False, f"Company size ({emp_count}) below minimum ({MIN_EMPLOYEE_SIZE})"
            except (ValueError, TypeError):
                pass
        else:
            # No employee count data - skip this contact
            return False, "No company employee count data"
    
    # Filter: Industry
    if TARGET_INDUSTRIES:
        company_industry = company_props.get("industry", "")
        if not company_industry or company_industry not in TARGET_INDUSTRIES:
            return False, f"Industry '{company_industry}' not in target list"
    
    # Filter: Country
    if TARGET_COUNTRIES:
        contact_country = props.get("country", "")
        company_country = company_props.get("country", "")
        country = contact_country or company_country
        if not country or country not in TARGET_COUNTRIES:
            return False, f"Country '{country}' not in target list"
    
    # Filter: Job title keywords
    if TARGET_JOB_TITLES:
        job_title = props.get("jobtitle", "")
        if not job_title:
            return False, "No job title"
        
        title_lower = job_title.lower()
        if not any(keyword.lower() in title_lower for keyword in TARGET_JOB_TITLES):
            return False, f"Job title '{job_title}' doesn't match target keywords"
    
    # Filter: Lifecycle stage
    if TARGET_LIFECYCLE_STAGES:
        lifecycle_stage = props.get("lifecyclestage", "").lower()
        if not lifecycle_stage or lifecycle_stage not in TARGET_LIFECYCLE_STAGES:
            return False, f"Lifecycle stage '{lifecycle_stage}' not in target list"
    
    return True, "Passed all filters"


def generate_outreach_email(client: anthropic.Anthropic, lead_context: dict) -> dict:
    """Use Claude to generate a personalized outreach email for a lead."""
    
    prompt = f"""## Role & Purpose

You are an AI sales assistant for Adopt AI, specializing in generating personalized outreach emails for high-engagement leads who haven't been contacted recently. Your goal is to re-engage prospects by connecting their demonstrated interest (engagement signals) to relevant value propositions.

## Lead Context

**Contact:**
- Name: {lead_context['contact_name']}
- Title: {lead_context['contact_title']}
- Email: {lead_context['contact_email']}

**Company:**
- Name: {lead_context['company_name']}
- Industry: {lead_context['company_industry']}
- Size: {lead_context['company_size']} employees

**Engagement Signals:**
- Engagement Score: {lead_context['engagement_score']}/100
- Email Opens: {lead_context.get('email_opens', 0)}
- Email Clicks: {lead_context.get('email_clicks', 0)}
- Website Page Views: {lead_context.get('page_views', 0)}
- Form Submissions: {lead_context.get('form_submissions', 0)}
- Days Since Last Activity: {lead_context['days_since_activity']}

**Apollo Enrichment Data:**
{lead_context.get('apollo_context', 'No Apollo data available.')}

**Internal Slack Discussions:**
{lead_context.get('slack_context', 'No Slack context available.')}

**Call Recording Transcripts (from Fireflies):**
{lead_context.get('fireflies_context', 'No call transcripts available.')}

**Recent Notes:**
{lead_context.get('notes', 'No notes available.')}

## Adopt AI Knowledge Base (Relevant Context)

The following content was retrieved from our knowledge base based on this lead's industry, persona, and company profile. Use this to craft a more personalized and relevant email:

{lead_context.get('knowledge_base_context', 'No knowledge base content available.')}

## Your Task

### Step 1: Analyze the Context

From the engagement signals, Apollo data, Slack discussions, and call transcripts, identify:
1. **Interest Indicators**: What has this lead engaged with? What are they interested in?
2. **Company Context**: What does Apollo tell us about their company, tech stack, or hiring signals?
3. **Internal Intelligence**: What do we know from Slack or previous calls?
4. **Relevant Angle**: What capability or use case would resonate most?

### Step 2: Generate the Email

**Email Structure (150-200 words MAX):**
1. **Subject Line**: Reference their engagement or a specific interest
2. **Opening**: Acknowledge their engagement naturally ("I noticed you've been exploring...")
3. **Value Hook**: Connect their apparent interest to a specific capability or outcome
4. **Social Proof (optional)**: Brief mention of similar company or use case
5. **Simple CTA**: One clear, low-friction ask

**Tone Guidelines:**
- Professional but warm
- Show you've noticed their specific engagement
- Focus on THEIR potential use case, not our features
- Concise and respectful of their time
- No aggressive sales language

## Response Format

Respond with JSON in this exact format:
{{
    "analysis": {{
        "engagement_summary": "Brief summary of their engagement patterns",
        "company_insights": "Key insights from Apollo enrichment",
        "recommended_angle": "The approach you're taking and why"
    }},
    "subject": "Email subject line",
    "body": "The email body (150-200 words max)",
    "talking_points": ["Point if they respond", "How to handle likely questions"],
    "flags": ["Any missing info or recommendations"]
}}
"""

    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    response_text = response.content[0].text
    
    try:
        if "```json" in response_text:
            json_str = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            json_str = response_text.split("```")[1].split("```")[0].strip()
        else:
            json_str = response_text.strip()
        
        return json.loads(json_str)
    except json.JSONDecodeError:
        return {
            "analysis": {
                "engagement_summary": "Unable to parse - see raw response",
                "company_insights": "Unknown",
                "recommended_angle": "Unknown"
            },
            "subject": "Let's connect",
            "body": response_text,
            "talking_points": [],
            "flags": ["Failed to parse structured response"]
        }


def format_lead_digest_html(leads: list[dict]) -> str:
    """Format all leads into an HTML digest email."""
    
    date_str = datetime.now().strftime("%B %d, %Y at %I:%M %p")
    count = len(leads)
    
    html = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 800px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%); color: white; padding: 30px; border-radius: 10px; margin-bottom: 30px; }}
        .header h1 {{ margin: 0; font-size: 24px; }}
        .header p {{ margin: 10px 0 0 0; opacity: 0.9; }}
        .lead-card {{ background: #f8f9fa; border-radius: 10px; padding: 25px; margin-bottom: 25px; border-left: 4px solid #11998e; }}
        .lead-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; flex-wrap: wrap; gap: 10px; }}
        .lead-name {{ font-size: 18px; font-weight: 600; color: #333; }}
        .engagement-score {{ background: #11998e; color: white; padding: 4px 12px; border-radius: 20px; font-size: 12px; }}
        .lead-meta {{ color: #666; font-size: 14px; margin-bottom: 15px; }}
        .engagement-signals {{ background: #e8f5e9; border-radius: 8px; padding: 15px; margin: 15px 0; }}
        .engagement-signals h4 {{ margin: 0 0 10px 0; color: #2e7d32; font-size: 13px; text-transform: uppercase; }}
        .signal-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; }}
        .signal {{ text-align: center; padding: 8px; background: white; border-radius: 6px; }}
        .signal-value {{ font-size: 20px; font-weight: bold; color: #11998e; }}
        .signal-label {{ font-size: 11px; color: #666; }}
        .apollo-insights {{ background: #e3f2fd; border-radius: 8px; padding: 15px; margin: 15px 0; border-left: 3px solid #1976d2; }}
        .apollo-insights h4 {{ margin: 0 0 10px 0; color: #1976d2; font-size: 13px; text-transform: uppercase; }}
        .apollo-item {{ font-size: 13px; color: #555; margin-bottom: 5px; }}
        .email-preview {{ background: white; border-radius: 8px; padding: 20px; margin-top: 15px; }}
        .email-subject {{ font-weight: 600; color: #333; margin-bottom: 10px; border-bottom: 1px solid #eee; padding-bottom: 10px; }}
        .email-body {{ white-space: pre-wrap; color: #444; }}
        .talking-points {{ margin-top: 15px; padding-top: 15px; border-top: 1px dashed #ddd; }}
        .talking-points h4 {{ margin: 0 0 10px 0; color: #666; font-size: 13px; text-transform: uppercase; }}
        .talking-points ul {{ margin: 0; padding-left: 20px; }}
        .talking-points li {{ color: #555; margin-bottom: 5px; }}
        .analysis {{ background: #fff3e0; border-radius: 8px; padding: 15px; margin: 15px 0; border-left: 3px solid #ff9800; }}
        .analysis h4 {{ margin: 0 0 10px 0; color: #e65100; font-size: 13px; text-transform: uppercase; }}
        .analysis-item {{ font-size: 13px; color: #555; margin-bottom: 8px; }}
        .flags {{ background: #fff3cd; border-radius: 8px; padding: 15px; margin: 15px 0; border-left: 3px solid #ffc107; }}
        .flags h4 {{ margin: 0 0 10px 0; color: #856404; font-size: 13px; text-transform: uppercase; }}
        .flags ul {{ margin: 0; padding-left: 20px; }}
        .flags li {{ color: #856404; margin-bottom: 5px; font-size: 13px; }}
        .footer {{ text-align: center; color: #999; font-size: 12px; margin-top: 40px; padding-top: 20px; border-top: 1px solid #eee; }}
        .no-leads {{ text-align: center; padding: 40px; color: #666; }}
        .stats {{ display: flex; gap: 20px; margin-top: 15px; }}
        .stat {{ background: rgba(255,255,255,0.2); padding: 10px 15px; border-radius: 8px; }}
        .stat-value {{ font-size: 24px; font-weight: bold; }}
        .stat-label {{ font-size: 12px; opacity: 0.9; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🎯 Lead Finder Digest</h1>
        <p>High-Engagement Stale Leads - Generated on {date_str}</p>
        <div class="stats">
            <div class="stat">
                <div class="stat-value">{count}</div>
                <div class="stat-label">Top Leads Found</div>
            </div>
        </div>
    </div>
"""
    
    if not leads:
        html += """
    <div class="no-leads">
        <h2>No leads found</h2>
        <p>No high-engagement stale leads matching your criteria were found today.</p>
    </div>
"""
    else:
        for i, lead in enumerate(leads, 1):
            # Build engagement signals HTML
            signals_html = f"""
            <div class="engagement-signals">
                <h4>📊 Engagement Signals</h4>
                <div class="signal-grid">
                    <div class="signal">
                        <div class="signal-value">{lead.get('email_opens', 0)}</div>
                        <div class="signal-label">Email Opens</div>
                    </div>
                    <div class="signal">
                        <div class="signal-value">{lead.get('email_clicks', 0)}</div>
                        <div class="signal-label">Email Clicks</div>
                    </div>
                    <div class="signal">
                        <div class="signal-value">{lead.get('page_views', 0)}</div>
                        <div class="signal-label">Page Views</div>
                    </div>
                    <div class="signal">
                        <div class="signal-value">{lead.get('form_submissions', 0)}</div>
                        <div class="signal-label">Form Fills</div>
                    </div>
                </div>
            </div>
"""
            
            # Build Apollo insights HTML
            apollo_html = ""
            apollo_data = lead.get("apollo_enrichment", {})
            if apollo_data.get("found"):
                company_data = apollo_data.get("company", {})
                contact_data = apollo_data.get("contact", {})
                intent_data = apollo_data.get("intent_signals", {})
                
                apollo_items = []
                if company_data.get("funding_stage"):
                    apollo_items.append(f"<div class='apollo-item'><strong>Funding:</strong> {company_data['funding_stage']}</div>")
                if company_data.get("tech_stack"):
                    tech_preview = ", ".join(company_data["tech_stack"][:5])
                    apollo_items.append(f"<div class='apollo-item'><strong>Tech Stack:</strong> {tech_preview}</div>")
                if contact_data.get("seniority"):
                    apollo_items.append(f"<div class='apollo-item'><strong>Seniority:</strong> {contact_data['seniority']}</div>")
                if intent_data.get("hiring_signal"):
                    apollo_items.append(f"<div class='apollo-item'><strong>🔥 Hiring Signal:</strong> Currently hiring</div>")
                
                if apollo_items:
                    apollo_html = f"""
                <div class="apollo-insights">
                    <h4>🔍 Apollo Insights</h4>
                    {"".join(apollo_items)}
                </div>
"""
            
            # Build analysis HTML
            analysis_html = ""
            analysis = lead.get("analysis", {})
            if analysis:
                analysis_html = f"""
                <div class="analysis">
                    <h4>🧠 AI Analysis</h4>
                    <div class="analysis-item"><strong>Engagement:</strong> {analysis.get('engagement_summary', 'N/A')}</div>
                    <div class="analysis-item"><strong>Company Insights:</strong> {analysis.get('company_insights', 'N/A')}</div>
                    <div class="analysis-item"><strong>Recommended Angle:</strong> {analysis.get('recommended_angle', 'N/A')}</div>
                </div>
"""
            
            # Build flags HTML
            flags_html = ""
            flags = lead.get("flags", [])
            if flags:
                flag_items = "".join(f"<li>{f}</li>" for f in flags)
                flags_html = f"""
                <div class="flags">
                    <h4>⚠️ Flags</h4>
                    <ul>{flag_items}</ul>
                </div>
"""
            
            # Build talking points HTML
            talking_points_html = ""
            if lead.get("talking_points"):
                points = "".join(f"<li>{p}</li>" for p in lead["talking_points"])
                talking_points_html = f"""
                <div class="talking-points">
                    <h4>💡 Talking Points</h4>
                    <ul>{points}</ul>
                </div>
"""
            
            html += f"""
    <div class="lead-card">
        <div class="lead-header">
            <span class="lead-name">#{i} {lead['contact_name']}</span>
            <span class="engagement-score">Score: {lead['engagement_score']}</span>
        </div>
        <div class="lead-meta">
            <strong>{lead['contact_title']}</strong> at <strong>{lead['company_name']}</strong><br>
            {lead['contact_email']} • {lead['company_industry']} • {lead['company_size']} employees<br>
            Last activity: {lead['days_since_activity']} days ago{f" • <a href='{lead['contact_linkedin_url']}' style='color: #0077b5;'>LinkedIn</a>" if lead.get('contact_linkedin_url') else ""}
        </div>
        {signals_html}
        {apollo_html}
        {analysis_html}
        {flags_html}
        <div class="email-preview">
            <div class="email-subject">📝 Subject: {lead['email_subject']}</div>
            <div class="email-body">{lead['email_body']}</div>
            {talking_points_html}
        </div>
    </div>
"""
    
    html += """
    <div class="footer">
        <p>This digest was automatically generated by the Lead Finder AI Agent.<br>
        Review each email before sending and personalize as needed.</p>
    </div>
</body>
</html>
"""
    
    return html


def send_digest_email_sendgrid(to_emails: list[str], html_content: str, api_key: str):
    """Send the digest email using SendGrid."""
    url = "https://api.sendgrid.com/v3/mail/send"
    
    payload = {
        "personalizations": [{"to": [{"email": email} for email in to_emails]}],
        "from": {"email": FROM_EMAIL, "name": "Lead Finder Agent"},
        "subject": f"🎯 Lead Finder Digest - {datetime.now().strftime('%B %d, %Y')}",
        "content": [{"type": "text/html", "value": html_content}]
    }
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    
    print(f"✅ Digest email sent to {', '.join(to_emails)}")


def main():
    """Main execution flow."""
    print("🎯 Starting Lead Finder Agent...")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 50)
    
    # Initialize clients
    hubspot_token = os.getenv("HUBSPOT_ACCESS_TOKEN")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    sendgrid_key = os.getenv("SENDGRID_API_KEY")
    
    if not hubspot_token:
        raise ValueError("HUBSPOT_ACCESS_TOKEN environment variable is required")
    if not anthropic_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable is required")
    
    hubspot = HubSpotLeadClient(hubspot_token)
    claude = anthropic.Anthropic(api_key=anthropic_key)
    
    # Initialize optional clients
    apollo_key = os.getenv("APOLLO_API_KEY")
    apollo = ApolloClient(apollo_key) if apollo_key else None
    if apollo:
        print("🔗 Apollo integration enabled")
    else:
        print("ℹ️ Apollo integration disabled (APOLLO_API_KEY not set)")
    
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    slack = SlackClient(slack_token) if slack_token else None
    if slack:
        print(f"💬 Slack integration enabled (searching: #{', #'.join(SLACK_CHANNELS)})")
    else:
        print("ℹ️ Slack integration disabled (SLACK_BOT_TOKEN not set)")
    
    fireflies_key = os.getenv("FIREFLIES_API_KEY")
    fireflies = FirefliesClient(fireflies_key) if fireflies_key else None
    if fireflies:
        print("📞 Fireflies integration enabled")
    else:
        print("ℹ️ Fireflies integration disabled (FIREFLIES_API_KEY not set)")
    
    # Initialize knowledge base client
    knowledge_base = KnowledgeBaseClient()
    if knowledge_base.initialize():
        print(f"📚 Knowledge base enabled ({knowledge_base.collection.count()} chunks indexed)")
    else:
        print("ℹ️ Knowledge base disabled (run 'python index_knowledge_base.py' to enable)")
        knowledge_base = None
    
    # Calculate the stale date cutoff
    stale_cutoff = datetime.now() - timedelta(days=STALE_THRESHOLD_DAYS)
    stale_cutoff_ms = int(stale_cutoff.timestamp() * 1000)  # HubSpot uses milliseconds
    
    # Print filter configuration
    print(f"\n📋 Filter Configuration:")
    print(f"   - Marketing Contacts only: Yes")
    print(f"   - Min employee size: {MIN_EMPLOYEE_SIZE}")
    print(f"   - Stale threshold: {STALE_THRESHOLD_DAYS} days (before {stale_cutoff.strftime('%Y-%m-%d')})")
    print(f"   - Target industries: {TARGET_INDUSTRIES if TARGET_INDUSTRIES else 'All'}")
    print(f"   - Target countries: {TARGET_COUNTRIES if TARGET_COUNTRIES else 'All'}")
    print(f"   - Target job titles: {TARGET_JOB_TITLES if TARGET_JOB_TITLES else 'All'}")
    print(f"   - Target lifecycle stages: {TARGET_LIFECYCLE_STAGES if TARGET_LIFECYCLE_STAGES else 'All'}")
    
    # Step 1: Search for contacts using HubSpot filters
    # Filters: Marketing Contact = Yes, Employee Size >= 200, Last Activity > 14 days ago
    print(f"\n📊 Searching HubSpot for stale Marketing Contacts (employee size >= {MIN_EMPLOYEE_SIZE})...")
    
    contact_properties = [
        "email", "firstname", "lastname", "jobtitle", "company", "country",
        "lifecyclestage", "notes_last_contacted", "hs_sales_email_last_sent",
        "notes_last_updated", "hs_email_open_count", 
        "hs_email_click_count", "hs_sales_email_last_replied",
        "hs_analytics_num_page_views", "num_conversion_events",
        "associatedcompanyid", "hs_marketable_status", "employee_size",
        "hs_linkedin_url"
    ]
    
    # Build HubSpot search filters - all in one filter group (AND logic)
    hubspot_filters = [
        # Marketing Contact = Yes
        {
            "propertyName": "hs_marketable_status",
            "operator": "EQ",
            "value": "true"
        },
        # Employee Size >= MIN_EMPLOYEE_SIZE (on contact object)
        {
            "propertyName": "employee_size",
            "operator": "GTE",
            "value": str(MIN_EMPLOYEE_SIZE)
        },
        # Last activity date is before the stale cutoff (i.e., older than 14 days)
        {
            "propertyName": "notes_last_updated",
            "operator": "LT",
            "value": str(stale_cutoff_ms)
        }
    ]
    
    contacts = hubspot.search_contacts(filters=hubspot_filters, properties=contact_properties)
    print(f"   Found {len(contacts)} contacts matching HubSpot filters")
    
    # Step 2: Take first 10 contacts for processing
    # (HubSpot already filtered by Marketing Contact, Employee Size, and Stale Date)
    contacts_to_process = contacts[:TOP_LEADS_COUNT]
    print(f"   Processing first {len(contacts_to_process)} contacts...")
    
    # Step 3: Enrich the selected contacts with company data and calculate engagement scores
    print(f"\n🔍 Enriching contacts and calculating engagement scores...")
    
    qualified_leads = []
    
    for contact in contacts_to_process:
        contact_id = contact["id"]
        props = contact.get("properties", {})
        contact_name = f"{props.get('firstname', '')} {props.get('lastname', '')}".strip() or "Unknown"
        
        print(f"   Processing: {contact_name}...")
        
        # Get associated company for additional context
        company = hubspot.get_associated_company(contact_id)
        company_props = company.get("properties", {}) if company else {}
        
        # Apply additional filters (industry, country, job title, lifecycle stage)
        passes, reason = passes_filters(contact, company)
        if not passes:
            print(f"      ⚠️ Skipped: {reason}")
            continue
        
        # Get meeting count for engagement scoring
        meetings = hubspot.get_contact_meeting_associations(contact_id)
        meeting_count = len(meetings)
        
        # Calculate engagement score
        engagement_score = calculate_engagement_score(contact, meeting_count)
        
        # Store qualified lead with all data
        qualified_leads.append({
            "contact_id": contact_id,
            "contact": contact,
            "company": company,
            "engagement_score": engagement_score,
            "meeting_count": meeting_count,
            "contact_name": contact_name,
            "contact_email": props.get("email", "No email"),
            "contact_title": props.get("jobtitle", "Unknown"),
            "contact_linkedin_url": props.get("hs_linkedin_url", ""),
            "company_name": company_props.get("name", props.get("company", "Unknown Company")),
            "company_industry": company_props.get("industry", "Unknown"),
            "company_size": props.get("employee_size", company_props.get("numberofemployees", "Unknown")),
            "email_opens": int(props.get("hs_email_open_count", 0) or 0),
            "email_clicks": int(props.get("hs_email_click_count", 0) or 0),
            "page_views": int(props.get("hs_analytics_num_page_views", 0) or 0),
            "form_submissions": int(props.get("num_conversion_events", 0) or 0),
        })
        print(f"      ✓ Engagement score: {engagement_score}")
    
    # Sort by engagement score (highest first)
    qualified_leads.sort(key=lambda x: x["engagement_score"], reverse=True)
    top_leads = qualified_leads[:TOP_LEADS_COUNT]
    
    print(f"\n🏆 {len(top_leads)} leads ready for outreach (sorted by engagement):")
    for i, lead in enumerate(top_leads, 1):
        print(f"   {i}. {lead['contact_name']} ({lead['company_name']}) - Score: {lead['engagement_score']}")
    
    # Step 4: Enrich leads with context and generate emails
    print(f"\n✍️ Enriching leads and generating emails...")
    
    final_leads = []
    
    for lead in top_leads:
        contact_name = lead["contact_name"]
        company_name = lead["company_name"]
        contact_email = lead["contact_email"]
        contact_id = lead["contact_id"]
        
        print(f"\n   Processing: {contact_name} ({company_name})")
        
        # Calculate days since activity
        props = lead["contact"].get("properties", {})
        last_activity = props.get("hs_last_sales_activity_timestamp") or props.get("notes_last_contacted")
        days_since = "30+"
        if last_activity:
            try:
                if isinstance(last_activity, str):
                    dt = datetime.fromisoformat(last_activity.replace("Z", "+00:00"))
                else:
                    dt = datetime.fromtimestamp(last_activity / 1000)
                now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
                days_since = (now - dt).days
            except (ValueError, TypeError):
                pass
        
        lead["days_since_activity"] = days_since
        
        # Apollo enrichment
        apollo_context = "Apollo integration not enabled."
        apollo_enrichment = {}
        if apollo and contact_email and contact_email != "No email":
            print(f"      🔍 Enriching via Apollo...")
            apollo_enrichment = apollo.enrich_contact(contact_email)
            apollo_context = apollo.format_apollo_context(apollo_enrichment)
            if apollo_enrichment.get("found"):
                print(f"      ✓ Apollo data found")
            else:
                print(f"      ℹ️ No Apollo data found")
        
        lead["apollo_context"] = apollo_context
        lead["apollo_enrichment"] = apollo_enrichment
        
        # Slack context
        slack_context = "Slack integration not enabled."
        if slack:
            search_terms = [t for t in [company_name, contact_name] if t and t != "Unknown"]
            all_slack_messages = []
            for term in search_terms[:2]:
                try:
                    messages = slack.search_messages(term, SLACK_CHANNELS, limit=5)
                    all_slack_messages.extend(messages)
                except Exception as e:
                    print(f"      ⚠️ Slack search error: {e}")
            
            seen_permalinks = set()
            unique_messages = []
            for msg in all_slack_messages:
                if msg.get("permalink") not in seen_permalinks:
                    seen_permalinks.add(msg.get("permalink"))
                    unique_messages.append(msg)
            
            slack_context = slack.format_slack_context(unique_messages[:10])
            if unique_messages:
                print(f"      💬 Found {len(unique_messages)} Slack messages")
        
        lead["slack_context"] = slack_context
        
        # Fireflies context
        fireflies_context = "Fireflies integration not enabled."
        if fireflies and company_name and company_name != "Unknown Company":
            try:
                transcripts = fireflies.search_transcripts_by_title(company_name, limit=5)
                if transcripts:
                    fireflies_context = fireflies.format_fireflies_context(transcripts)
                    print(f"      📞 Found {len(transcripts)} call transcripts")
                else:
                    fireflies_context = "No call transcripts found."
            except Exception as e:
                print(f"      ⚠️ Fireflies error: {e}")
        
        lead["fireflies_context"] = fireflies_context
        
        # Get notes
        notes = hubspot.get_contact_notes(contact_id)
        notes_text = "\n".join([
            n.get("properties", {}).get("hs_note_body", "")[:500] 
            for n in notes[:3]
        ]) or "No notes available"
        lead["notes"] = notes_text
        
        # Knowledge base context (RAG)
        kb_context = "Knowledge base not available."
        if knowledge_base:
            print(f"      📚 Searching knowledge base...")
            kb_context = knowledge_base.get_context_for_lead(lead)
            if kb_context and "No relevant" not in kb_context and "not available" not in kb_context:
                print(f"      ✓ Found relevant knowledge base content")
            else:
                print(f"      ℹ️ No specific knowledge base matches")
        
        lead["knowledge_base_context"] = kb_context
        
        # Generate email
        print(f"      ✍️ Generating outreach email...")
        try:
            email_content = generate_outreach_email(claude, lead)
            
            lead["email_subject"] = email_content.get("subject", "Let's connect")
            lead["email_body"] = email_content.get("body", "")
            lead["talking_points"] = email_content.get("talking_points", [])
            lead["analysis"] = email_content.get("analysis", {})
            lead["flags"] = email_content.get("flags", [])
            
            print(f"      ✓ Generated: {lead['email_subject'][:50]}...")
            
        except Exception as e:
            print(f"      ❌ Error generating email: {e}")
            lead["email_subject"] = "Let's connect"
            lead["email_body"] = "Error generating email content."
            lead["talking_points"] = []
            lead["analysis"] = {}
            lead["flags"] = [f"Error: {str(e)}"]
        
        final_leads.append(lead)
    
    # Step 5: Save all data to JSON
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # Prepare JSON-serializable output with all enrichment data
    json_output = {
        "generated_at": datetime.now().isoformat(),
        "filter_config": {
            "marketing_contacts_only": True,
            "min_employee_size": MIN_EMPLOYEE_SIZE,
            "stale_threshold_days": STALE_THRESHOLD_DAYS,
            "target_industries": TARGET_INDUSTRIES,
            "target_countries": TARGET_COUNTRIES,
            "target_job_titles": TARGET_JOB_TITLES,
            "target_lifecycle_stages": TARGET_LIFECYCLE_STAGES,
        },
        "summary": {
            "contacts_matching_hubspot_filters": len(contacts),
            "leads_processed": len(qualified_leads),
            "emails_generated": len(final_leads),
        },
        "leads": []
    }
    
    for lead in final_leads:
        # Build a clean JSON structure for each lead
        lead_data = {
            "contact": {
                "id": lead.get("contact_id"),
                "name": lead.get("contact_name"),
                "email": lead.get("contact_email"),
                "title": lead.get("contact_title"),
                "linkedin_url": lead.get("contact_linkedin_url"),
            },
            "company": {
                "name": lead.get("company_name"),
                "industry": lead.get("company_industry"),
                "size": lead.get("company_size"),
            },
            "engagement": {
                "score": lead.get("engagement_score"),
                "email_opens": lead.get("email_opens"),
                "email_clicks": lead.get("email_clicks"),
                "page_views": lead.get("page_views"),
                "form_submissions": lead.get("form_submissions"),
                "meeting_count": lead.get("meeting_count"),
                "days_since_activity": lead.get("days_since_activity"),
            },
            "apollo_enrichment": lead.get("apollo_enrichment", {}),
            "slack_context": lead.get("slack_context"),
            "fireflies_context": lead.get("fireflies_context"),
            "knowledge_base_context": lead.get("knowledge_base_context"),
            "notes": lead.get("notes"),
            "generated_email": {
                "subject": lead.get("email_subject"),
                "body": lead.get("email_body"),
                "talking_points": lead.get("talking_points", []),
                "analysis": lead.get("analysis", {}),
                "flags": lead.get("flags", []),
            }
        }
        json_output["leads"].append(lead_data)
    
    # Print JSON to console for testing/debugging
    print(f"\n" + "=" * 60)
    print("📊 LEAD DATA (JSON)")
    print("=" * 60)
    print(json.dumps(json_output, indent=2, default=str))
    print("=" * 60)
    
    # Save JSON output to file
    json_path = f"/Users/sunil/adopt-followup/docs/lead_finder_data_{timestamp}.json"
    with open(json_path, "w") as f:
        json.dump(json_output, f, indent=2, default=str)
    print(f"\n📄 Saved JSON data: {json_path}")
    
    # Step 6: Create and send digest
    print(f"\n📧 Creating digest email...")
    
    html_digest = format_lead_digest_html(final_leads)
    
    # Save HTML copy
    digest_path = f"/Users/sunil/adopt-followup/docs/lead_finder_digest_{timestamp}.html"
    with open(digest_path, "w") as f:
        f.write(html_digest)
    print(f"   📄 Saved HTML digest: {digest_path}")
    
    # Send the email
    if sendgrid_key:
        send_digest_email_sendgrid(LEAD_FINDER_RECIPIENTS, html_digest, sendgrid_key)
    else:
        print(f"\n⚠️ No SendGrid API key configured. Digest saved to: {digest_path}")
        print(f"   Set SENDGRID_API_KEY environment variable to enable email delivery.")
    
    print(f"\n✅ Lead Finder Agent completed successfully!")
    print(f"   Contacts matching HubSpot filters: {len(contacts)}")
    print(f"   Leads processed: {len(qualified_leads)}")
    print(f"   Emails generated: {len(final_leads)}")
    print(f"   JSON output: {json_path}")
    
    return final_leads


if __name__ == "__main__":
    main()
