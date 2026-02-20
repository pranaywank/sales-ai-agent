#!/usr/bin/env python3
"""
Zoho Sales Agent - Slack Bot Edition
Interactively manages sales follow-ups via Slack.
"""

import os
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Third-party imports
import time
import uuid
import re
import requests
import anthropic
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# Local imports
from zoho_client import ZohoClient
from gmail_client import GmailClient

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load Config
load_dotenv()

ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID")
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET")
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN")
ZOHO_API_DOMAIN = os.getenv("ZOHO_API_DOMAIN", "https://www.zohoapis.com")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL")

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL")

# Initialize Slack App
app = App(token=SLACK_BOT_TOKEN)

# Global State to store drafts temporarily
class DraftManager:
    def __init__(self, filepath="pending_drafts.json"):
        self.filepath = filepath
        self._load_drafts()

    def _load_drafts(self):
        try:
            if os.path.exists(self.filepath):
                with open(self.filepath, 'r') as f:
                    self.drafts = json.load(f)
            else:
                self.drafts = {}
        except Exception as e:
            logging.error(f"Failed to load drafts: {e}")
            self.drafts = {}

    def _save_drafts(self):
        try:
            with open(self.filepath, 'w') as f:
                json.dump(self.drafts, f, indent=2)
        except Exception as e:
            logging.error(f"Failed to save drafts: {e}")

    def save_draft(self, lead, plan, content):
        draft_id = str(uuid.uuid4())
        self.drafts[draft_id] = {
            "lead": lead,
            "plan": plan,
            "content": content,
            "created_at": time.time(),
            "lead_id": lead.get("id")
        }
        self._save_drafts()
        return draft_id

    def get_draft(self, draft_id):
        # Refresh from disk if not in memory (handles multi-process updates better)
        if draft_id not in self.drafts:
             self._load_drafts()
        return self.drafts.get(draft_id)

    def delete_draft(self, draft_id):
        if draft_id in self.drafts:
            del self.drafts[draft_id]
            self._save_drafts()

    def cleanup_old_drafts(self, max_age_seconds=86400): # 24 hours default
        current_time = time.time()
        to_delete = []
        for did, data in self.drafts.items():
            if current_time - data.get("created_at", 0) > max_age_seconds:
                to_delete.append(did)
        
        for did in to_delete:
            del self.drafts[did]
        
        if to_delete:
            self._save_drafts()

draft_manager = DraftManager()

# ==========================================
# Core Agent Logic (Refactored from previous version)
# ==========================================

class ZohoAgentCore:
    def __init__(self):
        self.zoho = ZohoClient(ZOHO_REFRESH_TOKEN, ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_API_DOMAIN)
        
        # Configure AI
        if ANTHROPIC_API_KEY:
            self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        else:
            self.client = None
            logging.warning("No Anthropic API Key found.")

        # Configure Email
        if GOOGLE_REFRESH_TOKEN and GOOGLE_CLIENT_ID:
            self.email_client = GmailClient(GOOGLE_REFRESH_TOKEN, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
        elif SENDGRID_API_KEY:
            from zoho_agent import EmailClient # Fallback to defined class if needed, or re-implement
            self.email_client = EmailClient(SENDGRID_API_KEY, FROM_EMAIL)
        else:
            self.email_client = None

    def fetch_active_leads(self, limit=10):
        """Fetches up to 'limit' active leads (not Closed/Junk)."""
        # Fetch latest 200 leads (default sort is usually Created_Time or Modified_Time?)
        # Zoho Agent implementation suggests get_leads() returns recent ones.
        leads = self.zoho.get_leads()
        
        active_leads = []
        for lead in leads:
             status = lead.get("Reachout_Plan_Status", "")
             if status not in ["Closed", "Junk_Lead", "Dead", "Analysis_Completed"]:
                 active_leads.append(lead)
                 if len(active_leads) >= limit:
                     break
        return active_leads

    def fetch_pending_leads(self):
        """Fetch leads with action due today."""
        today = datetime.now().strftime("%Y-%m-%d")
        print("Fetching leads from Zoho...")
        all_leads = self.zoho.get_leads()
        pending = []
        for lead in all_leads:
            next_date = lead.get("Next_Action_Date")
            if next_date and next_date[:10] == today:
                 pending.append(lead)
        return pending

    def _clean_email_body(self, content):
        """Removes HTML tags and quoted reply text."""
        if not content: return ""
        import re as _re
        # Strip HTML
        text = _re.sub(r'<[^>]+>', ' ', str(content))
        text = _re.sub(r'\s+', ' ', text).strip()
        
        # Strip Quoted Text (Simple heuristics)
        # 1. "On ... wrote:"
        # 2. "-----Original"
        # 3. "From: ... Sent: ... To:"
        
        lower_text = text.lower()
        
        # List of markers and whether they use regex
        markers = [
            ("-----original", False),
            ("from:", False), # Risky if message starts with "From:", but usually safe for replies
            ("on ", True) # logical check below
        ]
        
        # Regex for "On ... wrote:"
        match = _re.search(r'on\s+.*?\s+wrote:', text, _re.IGNORECASE | _re.DOTALL)
        if match and match.start() > 5:
            text = text[:match.start()]
            
        # Divider style
        idx = text.find("-----Original")
        if idx > 5: text = text[:idx]
            
        # Outlook style "From: ... Sent:"
        match = _re.search(r'from:\s+.*?\s+sent:\s+', text, _re.IGNORECASE | _re.DOTALL)
        if match and match.start() > 5:
             text = text[:match.start()]
             
        return text.strip()

    def get_enriched_emails(self, lead_id, limit=5):
        """
        Fetches emails using V3 API, sorts by time, and fetches full content for top emails.
        Returns sorted list of email dicts with 'full_content', '_clean_content', and '_direction' populated.
        """
        emails = self.zoho.get_emails(lead_id)
        if not emails:
            return []

        # Sort by time (V3 'time', V2 'Message_Time', or 'sent_time')
        def get_time(e):
             return e.get('time') or e.get('Message_Time') or e.get('sent_time') or ''
        
        try:
            emails.sort(key=get_time, reverse=True)
        except: pass

        enriched = []
        for e in emails[:limit]:
            # Populate content if missing
            content = e.get('content') or e.get('summary') or ""
            msg_id = e.get('message_id') or e.get('id')
            
            # Decide if we need to fetch full content
            # If content is empty or looks like a snippet
            if (not content or len(str(content)) < 50) and msg_id:
                # Fetch full content
                # print(f"DEBUG: Fetching full content for email {msg_id}...")
                full = self.zoho.get_email_content(lead_id, msg_id)
                if full:
                    e['full_content'] = full
                    e['content'] = full # Update main field too
                else:
                    e['full_content'] = content
            else:
                 e['full_content'] = content
            
            e['_sort_time'] = get_time(e)
            
            # Determine Direction
            is_sent = e.get('sent', True) 
            status_list = e.get('status', [])
            status_type = status_list[0].get('type', 'Unknown') if status_list else 'Unknown'
            
            if is_sent is False or status_type.lower() in ['received', 'incoming']:
                e['_direction'] = 'RECEIVED'
            else:
                e['_direction'] = 'SENT'
                
            e['_clean_content'] = self._clean_email_body(e.get('full_content'))
            
            enriched.append(e)
            
        return enriched

    def determine_next_step(self, lead):
        """Same logic as before."""
        print("figuring out the plan, AAAA")
        status = lead.get("Reachout_Plan_Status", "Not Active")
        created_time = lead.get("Created_Time", "")
        lead_id = lead.get("id")
        
        try:
            c_time = lead.get("Created_Time") or ""
            created_dt = datetime.strptime(c_time[:10], "%Y-%m-%d")
            days_since_creation = (datetime.now() - created_dt).days
        except:
            days_since_creation = 0

        plan = {"type": "none", "reason": "No action", "lead_id": lead_id}

        # ----------------------------------------------------
        # NEW: Smart Lead Status Detection via Email History (Refactored for V3)
        # ----------------------------------------------------
        try:
            emails = self.get_enriched_emails(lead_id, limit=5)
            latest_incoming = None
            days_since_reply = 999
            
            if emails:
                print(f"\nDEBUG: === EMAIL ANALYSIS FOR LEAD {lead_id} ===")
                print(f"DEBUG: Total emails found: {len(emails)}")
                
                for i, e in enumerate(emails[:5]):
                    print(f"DEBUG: Email {i}: [{e.get('_direction')}] | Time: {e.get('_sort_time')[:19]} | Subj: {e.get('subject', 'No Subject')}")
                
                # Find the most recent RECEIVED email
                latest_incoming = next((e for e in emails if e.get('_direction') == 'RECEIVED'), None)
                
                if latest_incoming:
                    msg_time = latest_incoming.get('_sort_time', '')[:10]
                    try:
                        msg_dt = datetime.strptime(msg_time, "%Y-%m-%d")
                        days_since_reply = (datetime.now() - msg_dt).days
                    except Exception as date_err:
                        print(f"DEBUG: Error parsing date '{msg_time}': {date_err}")
                        days_since_reply = 0
                    
                    print(f"DEBUG: Lead last replied {days_since_reply} days ago")
                else:
                    print("DEBUG: No incoming emails found from lead in recent history.")
            
            # Logic Branches
            if latest_incoming and days_since_reply <= 7:
                # ACTIVE: Lead replied recently
                print(f"\nDEBUG: *** EMAIL TYPE: ACTIVE RESPONSE ***")
                
                # Use cleaned content from helper
                reply_content = latest_incoming.get('_clean_content') or latest_incoming.get('summary') or "No content found."
                
                print(f"DEBUG: Lead's message (cleaned): {reply_content[:300]}")
                
                plan["type"] = "email"
                plan["template"] = "active_response"
                plan["reason"] = f"Lead is ACTIVE - Replied {days_since_reply} days ago"
                plan["context"] = {
                    "last_reply_date": latest_incoming.get("_sort_time", "")[:10],
                    "last_reply_body": reply_content,
                    "last_reply_subject": latest_incoming.get("subject", "")
                }
                return plan
                
            elif latest_incoming and days_since_reply > 14:
                # RE-ENGAGEMENT: Lead replied but went silent (> 14 days)
                print(f"\nDEBUG: *** EMAIL TYPE: RE-ENGAGEMENT *** (Lead last replied {days_since_reply} days ago)")
                plan["type"] = "email"
                plan["template"] = "re_engagement"
                plan["reason"] = f"Re-engagement - Silent for {days_since_reply} days"
                return plan
            else:
                print(f"\nDEBUG: *** EMAIL TYPE: COLD/DRIP ***")
                
        except Exception as e:
            print(f"DEBUG: Error in determine_next_step email check: {e}")
            import traceback; traceback.print_exc()

        # Fallback to existing Drip Logic (COLD)
        print(f"DEBUG: Lead {lead_id} status check -> COLD (No recent reply detected)")
        
        if status == "Active":
            plan["type"] = "review"
            plan["reason"] = "Zoho Status is Active (Manual)"
        elif status == "Nurture":
            plan["type"] = "email"
            plan["template"] = "nurture_monthly_update"
            plan["reason"] = "Monthly nurture"
            plan["next_date"] = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        elif status == "Not Active" or not status:
            # Simple schedule
            day = days_since_creation
            template = None
            next_interval = 0
            
            if day <= 1: template, next_interval = "day_0_intro", 2
            elif 2 <= day <= 4: template, next_interval = "day_2_followup", 5
            elif 7 <= day <= 10: template, next_interval = "day_7_followup", 7
            elif 14 <= day <= 20: template, next_interval = "day_14_value_add", 14
            elif 28 <= day <= 32: template, next_interval = "day_28_check_in", 7
            elif 35 <= day <= 38: template, next_interval = "day_35_bump", 5
            elif 39 <= day <= 45: 
                template, next_interval = "day_40_breakup", 0
                plan["move_to_newsletter"] = True
            
            if template:
                plan["type"] = "email"
                plan["template"] = template
                plan["reason"] = f"Cold Drip Sequence Day {day}"
                if next_interval > 0:
                    plan["next_date"] = (datetime.now() + timedelta(days=next_interval)).strftime("%Y-%m-%d")
        
        return plan

    def generate_email_content(self, lead, plan, feedback=None):
        """Generates email content using Claude (Anthropic)."""
        if not self.client or not plan.get("template"): return None
        
        # Context Gathering
        name = f"{lead.get('First_Name', '')} {lead.get('Last_Name', '')}".strip()
        company = lead.get("Company", "")
        project = lead.get("Project_Name", "your project")
        # Fetch Description (standard) and Project_Description (custom)
        desc_standard = lead.get("Description") or ""
        desc_custom = lead.get("Project_Description") or ""
        full_description = f"{desc_standard}\n{desc_custom}".strip() or "No description available."

        lead_id = lead.get("id")
        last_convo = lead.get("Last_Conversation") or "No recorded conversation."

        # Next Action Context
        next_action = lead.get("Next_Action") or "None"
        next_action_date = lead.get("Next_Action_Date", "")[:10]

        
        lat = lead.get("Last_Activity_Time") # Might be None
        last_convo_date = (lat[:10] if lat else "Unknown")
        
        # Calculate Day in Sequence
        created_time = lead.get("Created_Time") or ""
        try:
            created_dt = datetime.strptime(created_time[:10], "%Y-%m-%d")
            day_in_sequence = (datetime.now() - created_dt).days
        except:
            day_in_sequence = 0

        print(f"   Fetching context for {name}...")
        notes = self.zoho.get_notes(lead_id)
        emails = self.zoho.get_emails(lead_id)
        
        # Get FULL content
        notes_text = ""
        if notes:
            notes_text = "\n".join([f"- [{n.get('Created_Time')[:10]}] Note: {n.get('Note_Content')}" for n in notes[:5]])
        
        emails_text = ""
        latest_email_context = "None"
        
        if emails:
            # Sort by sent_time descending (correct lowercase field name from Zoho API)
            try:
                emails.sort(key=lambda x: x.get('sent_time', ''), reverse=True)
            except:
                pass
                
            # Take up to 5 recent emails for history context
            recent_emails = emails[:5]
            emails_text_list: List[str] = []
            
            # Fetch content for the LATEST email (Index 0)
            if emails:
                latest_email = emails[0]
                l_id = latest_email.get("message_id")  # Correct field name from Zoho list API
                l_is_sent = latest_email.get("sent", True)
                l_sender = "AGENT" if l_is_sent else "LEAD"
                print(f"DEBUG: Fetching content for LATEST email: {latest_email.get('subject')} ({l_id}) | From: {l_sender}")
                
                l_content = ""
                if l_id:
                    l_content = self.zoho.get_email_content(lead_id, l_id)
                if not l_content:
                    l_content = latest_email.get("summary") or "No content found"
                
                latest_email_context = f"""
                [{latest_email.get('sent_time', '')[:10]}] {l_sender}: {latest_email.get('subject')}
                BODY:
                {l_content[:1500]}
                """
            
            # History Loop - use correct lowercase field names
            for e in recent_emails:
                e_id = e.get("message_id")  # Correct field name
                e_subj = e.get("subject", "No Subject")
                e_time = e.get("sent_time", "")[:10]
                e_is_sent = e.get("sent", True)
                e_dir = "AGENT" if e_is_sent else "LEAD"
                
                # Reuse content if this is the same as the latest email
                if emails and e.get("message_id") == emails[0].get("message_id"):
                    emails_text_list.append(f"- [{e_time}] {e_dir}: {e_subj} - {str(l_content)[:200]}...")
                    continue

                # Fetch content for others
                e_content = "Content not available"
                try:
                    if e_id:
                        e_content = self.zoho.get_email_content(lead.get("id"), e_id) or "No content"
                except: pass
                
                emails_text_list.append(f"- [{e_time}] {e_dir}: {e_subj} - {str(e_content)[:200]}...")
            
            emails_text = "\n".join(emails_text_list)

        
        # DEBUG LOGGING FOR CONTEXT
        print("\n" + "="*40)
        print("DEBUG: GENERATE EMAIL CONTEXT ANALYSIS")
        print("-" * 20)
        print(f"Fetched {len(emails)} emails total for {name}.")
        for i, e in enumerate(emails[:5]):
             e_dir = 'AGENT' if e.get('sent', True) else 'LEAD'
             print(f"DEBUG Email {i}: [{e_dir}] | Time={e.get('sent_time', '')[:19]} | Subj={e.get('subject', 'N/A')}")
        print("-" * 20)
        print("="*40 + "\n")
        
        history_context = f"RECENT NOTES:\n{notes_text}\n\nRECENT EMAILS:\n{emails_text}"

        try:
            with open("company_context.md", "r") as f:
                company_ctx = f.read()
        except:
            company_ctx = "Digital Agents Interactive - Immersive Tech Company."

        # Prepare explicit active context if available from plan
        active_context = ""
        email_type_label = "COLD_DRIP"
        
        if plan.get("template") == "active_response" and plan.get("context"):
            ctx = plan["context"]
            email_type_label = "ACTIVE_RESPONSE"
            active_context = f"""
            *** EMAIL TYPE: ACTIVE RESPONSE ***
            The lead replied on {ctx.get('last_reply_date')}.
            
            LEAD'S MESSAGE (their exact words, questions to answer):
            "{ctx.get('last_reply_body')}"
            
            TASK: Write a DIRECT RESPONSE to this specific message.
            - Answer their questions directly
            - Do NOT send a generic drip or follow-up
            - Reference what they said
            """
        elif plan.get("template") in ["day_2_followup", "day_7_followup", "day_14_value_add", "day_28_check_in", "day_35_bump"]:
            email_type_label = "FOLLOW_UP_NO_RESPONSE"
        
        try:
            with open("system_prompt.md", "r") as f:
                prompt_template = f.read()
        except:
             # Fallback
             prompt_template = """
              Act as a BDR for: {company_context}
              Lead: {name} from {company}.
              Description & Notes: {full_description}
              
              CONTEXT AND STATUS:
              - Last Interaction: {last_conversation}
              - Next Action: {next_action} (Due: {next_action_date})
              
              LATEST EMAIL CONTEXT: {latest_email_context}
              History: {history_context}
              
              Task: Write a {template} email (Day {day_in_sequence}).
              {active_context}
              
              IMPORTANT: Output the email body in standard text format with normal paragraph breaks (use \\n). 
              DO NOT escape newline characters as literal "\\n" strings. Just use actual newlines in the JSON string value.
              Output JSON keys: subject, body.
              """

        prompt = prompt_template.format(
            company_context=company_ctx,
            name=name,
            company=company,
            project=project,
            project_description=full_description, # Mapping full_description to this key for backward compat in MD file
            full_description=full_description,    # also passing as new key
            last_conversation=last_convo,
            last_conversation_date=last_convo_date,
            next_action=next_action,              # NEW
            next_action_date=next_action_date,    # NEW
            latest_email_context=latest_email_context,
            day_in_sequence=day_in_sequence,
            history_context=history_context,
            template=plan['template'],
            active_context=active_context,
            email_type=email_type_label
        )
        
        if feedback:
            prompt += f"\nREFINE BASED ON FEEDBACK: {feedback}"

        # Configuration
        model_name = "claude-3-5-haiku-20241022" # Using Claude 3.5 Haiku as requested (release 2024-10-22)
        
        # DEBUG LOG
        print("\n" + "="*40)
        print(f"DEBUG: AI GENERATION - Model: {model_name}")
        print("="*40)
        print("DEBUG: FULL PROMPT SENT TO CLAUDE:")
        print("-" * 20)
        print(prompt)
        print("-" * 20)
        print("="*40 + "\n")

        try:
            message = self.client.messages.create(
                model=model_name,
                max_tokens=1024,
                temperature=0.7,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            raw_response = message.content[0].text
            clean_response = raw_response.strip()
            if "```" in clean_response:
                clean_response = re.sub(r'```json\s*', '', clean_response)
                clean_response = re.sub(r'```\s*', '', clean_response)
            
            start_idx = clean_response.find('{')
            end_idx = clean_response.rfind('}')
            
            if start_idx != -1 and end_idx != -1:
                clean_response = clean_response[start_idx:end_idx+1]

            result_json = json.loads(clean_response)
            
            # Post-Process: Fix literal '\n' escaping issues if present
            if result_json.get('body'):
                # First, ensure legitimate newlines are preserved
                # If Claude returned literal "\\n", json.loads turns it into "\n".
                # But if Claude returned "\\\\n", json.loads turns it into "\\n" (literal backslash n).
                # The user says "old \n \nl types continuous".
                # Let's try to unescape any residual literal "\n" sequences just in case.
                
                body = result_json['body']
                # Replace literal "\n" string with actual newline character, if somehow escaped.
                body = body.replace('\\n', '\n') 
                
                # Also, sometimes it outputs "\r\n" which is fine, but let's normalize to \n
                body = body.replace('\r\n', '\n')
                
                result_json['body'] = body

            return result_json
        except Exception as e:
            print(f"AI Generation Error: {e}")
            return None # Indicate failure for retry button


        
    # Helper to return SENT/FAILED statuses
    # Re-writing execute_send signature
    def execute_send(self, lead, plan, content):
        """Sends the email and updates Zoho with smart scheduling logic."""
        lead_id = lead.get("id")
        email = lead.get("Email")
        
        # Threading
        thread_context = None
        if hasattr(self.email_client, 'find_last_thread_id'):
             try:
                 thread_context = self.email_client.find_last_thread_id(email)
             except Exception as e:
                 print(f"ERROR: Failed to find thread context: {e}")
        
        formatted_body = content["body"].replace("\n", "<br>")
        
        try:
            sent = self.email_client.send_email(email, content["subject"], formatted_body, thread_context=thread_context)
            if not sent: return "EMAIL_FAILED"
        except Exception as e:
            print(f"ERROR: Gmail Send Exception: {e}")
            return "EMAIL_FAILED"
        
        # Email Sent - Proceed with Zoho Updates (Best Effort)
        try:
            self.zoho.add_note(lead_id, f"Agent Sent ({plan.get('template', 'Email')}):\n{content['subject']}\n\n{content['body']}")
            
            # --- Logic for Next Steps (Simplified copy of existing logic) ---
            # Fetch full history to determine reply status
            emails = self.zoho.get_emails(lead_id)
            print(f"DEBUG: execute_send fetched {len(emails)} emails for {lead_id}")
            
            has_replied = False
            last_reply_date = None
            if emails:
                for i, e in enumerate(emails):
                    direction = str(e.get("Direction") or e.get("direction")).lower()
                    msg_time = e.get("Message_Time", "")[:10]
                    subject = e.get("Subject", "No Subject")
                    
                    print(f"DEBUG: Email {i} - Dir: {direction} | Time: {msg_time} | Subj: {subject}")
                    
                    if direction in ["incoming", "from_contact", "received", "1"]: # Added '1' just in case
                        has_replied = True
                        if not last_reply_date or msg_time > last_reply_date:
                            last_reply_date = msg_time
                            # Capture snippet for Last_Conversation update
                            content_body = e.get('Content') or e.get('Body') or ''
                            last_reply_snippet = f"Customer Replied ({msg_time}): {subject} - {content_body[:100]}..."
                            updates["Last_Conversation"] = last_reply_snippet
                            print(f"DEBUG: Detect Reply! Set Last_Conversation to: {last_reply_snippet}")
            
            today_date = datetime.now()
            updates = {}
            updates["Last_Conversation_Date"] = today_date.strftime("%Y-%m-%dT%H:%M:%S+05:30")
            
            next_action_date = None
            next_action_desc = ""
            
            if not has_replied:
                # PATH A: Cold Lead
                created_time = lead.get("Created_Time", "")
                try:
                    created_dt = datetime.strptime(created_time[:10], "%Y-%m-%d")
                    day_in_seq = (today_date - created_dt).days
                except:
                    day_in_seq = 0
                
                next_interval = 2
                if day_in_seq < 2: next_interval = 2
                elif day_in_seq < 7: next_interval = 5
                elif day_in_seq < 14: next_interval = 7
                elif day_in_seq < 28: next_interval = 14
                elif day_in_seq < 35: next_interval = 7
                elif day_in_seq < 40: next_interval = 5
                else: next_interval = 0
                
                if next_interval > 0:
                    next_date_obj = today_date + timedelta(days=next_interval)
                    next_action_date = next_date_obj.strftime("%Y-%m-%dT09:00:00+05:30")
                    next_action_desc = f"Drip Follow-up (Day {day_in_seq + next_interval})"
                else:
                    next_action_desc = "Drip Complete - Move to Newsletter"
                    updates["Reachout_Plan_Status"] = "Newsletter"
            else:
                # Has Replied
                days_since_reply = 999
                if last_reply_date:
                    try:
                        dt = datetime.strptime(last_reply_date, "%Y-%m-%d")
                        days_since_reply = (today_date - dt).days
                    except: pass
                
                if days_since_reply > 14:
                     # PATH B: Inactive
                     next_date_obj = today_date + timedelta(days=5)
                     next_action_date = next_date_obj.strftime("%Y-%m-%dT09:00:00+05:30")
                     next_action_desc = "Re-engagement Drip (Day 7)"
                else:
                    # PATH C: Active
                    next_action_date = None 
                    next_action_desc = "Lead Active - Follow up manually based on reply"

            if next_action_date:
                updates["Next_Action_Date"] = next_action_date
            if next_action_desc:
                updates["Next_Action"] = next_action_desc

            print(f"DEBUG: Updating Zoho with: {updates}")
            self.zoho.update_lead(lead_id, updates)
            
        except Exception as e:
            print(f"ERROR: Zoho Update Failed (Non-critical): {e}")
            
        return "SENT"

    def update_lead_context(self, lead):
        """
        Analyzes full lead context (Desc, Notes, Emails) using LLM to update:
        - Last_Conversation (Summary)
        - Last_Conversation_Date
        - Next_Action
        - Next_Action_Date
        """
        print(f"🔄 Analyzing context for {lead.get('Last_Name')}...")
        lead_id = lead.get("id")
        
        # 1. Fetch ALL context
        notes = self.zoho.get_notes(lead_id)
        # Use helper for robust email fetching (v3 + content)
        emails = self.get_enriched_emails(lead_id, limit=5)
        
        print(f"DEBUG: Fetched {len(notes)} notes and {len(emails)} emails for {lead.get('Last_Name')}")
        
        # Format Notes
        notes_text = "No notes."
        if notes:
            notes_text = "\n".join([f"- [{n.get('Created_Time')[:10]}] {n.get('Note_Content')}" for n in notes[:5]])

        # Format Emails & Identify Latest Reply
        emails_text = "No emails."
        latest_interaction_date = None
        latest_received = None
        
        if emails:
            # Find the most recent RECEIVED email
            latest_received = next((e for e in emails if e.get('_direction') == 'RECEIVED'), None)
            
            emails_text_list = []
            for e in emails[:5]:
                # Use enriched fields
                time_str = e.get('_sort_time')[:16]
                direction = e.get('_direction', 'UNKNOWN')
                subject = e.get('subject', 'No Subject')
                content = e.get('_clean_content', '')[:600]
                
                emails_text_list.append(f"- [{time_str}] {direction}: {subject}\n  Content: {content}\n")
                
                if time_str and (not latest_interaction_date or time_str > latest_interaction_date):
                    latest_interaction_date = time_str
            
            emails_text = "\n".join(emails_text_list)

        # Build "Last Key Reply" section for prompt
        last_reply_section = ""
        if latest_received:
            last_reply_section = f"""
        IMPORTANT - LATEST REPLY FROM LEAD:
        Date: {latest_received.get('_sort_time')[:16]}
        Subject: {latest_received.get('subject')}
        Body: {latest_received.get('_clean_content')[:1000]}
        """

        # Current Description
        desc_standard = lead.get("Description") or ""
        desc_custom = lead.get("Project_Description") or ""
        full_description = f"{desc_standard}\n{desc_custom}".strip() or "No description."

        # 2. LLM Analysis
        prompt = f"""
        Analyze this sales lead context and update status fields.
        
        LEAD: {lead.get('First_Name')} {lead.get('Last_Name')} ({lead.get('Company')})
        DESCRIPTION: {full_description}
        
        RECENT NOTES:
        {notes_text}
        
        RECENT EMAILS (Newest First):
        {emails_text}
        {last_reply_section}
        
        TASK:
        Based on the recent notes and emails (especially the Lead's last reply if present):
        1. Summarize the LAST interaction (email or note) into a short sentence. If the lead replied recently, focus on that over our automated follow-ups.
        2. Determine the date of that last interaction (YYYY-MM-DD).
        3. Propose the NEXT logical action (e.g., "Answer question about pricing", "Follow up on proposal") based on the lead's needs.
        4. Propose a date for that next action (YYYY-MM-DD).
        
        OUTPUT JSON ONLY:
        {{
            "last_conversation_summary": "...",
            "last_conversation_date": "YYYY-MM-DD",
            "next_action": "...",
            "next_action_date": "YYYY-MM-DD"
        }}
        """
        
        try:
            model_name = "claude-3-5-haiku-20241022"
            message = self.client.messages.create(
                model=model_name,
                max_tokens=200,
                temperature=0.7,
                messages=[{"role": "user", "content": prompt}]
            )
            
            # Parse JSON
            content = message.content[0].text
            import re
            json_match = re.search(r'{.*}', content, re.DOTALL)
            if json_match:
                updates = json.loads(json_match.group(0))
                
                # Validation & Formatting
                zoho_update = {}
                
                if updates.get("last_conversation_summary"):
                    zoho_update["Last_Conversation"] = updates["last_conversation_summary"]
                
                if updates.get("last_conversation_date"):
                    # Basic validation of date format
                    d = updates["last_conversation_date"]
                    if len(d) == 10:
                        zoho_update["Last_Conversation_Date"] = f"{d}T09:00:00+05:30"
                
                if updates.get("next_action"):
                    zoho_update["Next_Action"] = updates["next_action"]
                
                if updates.get("next_action_date"):
                    d = updates["next_action_date"]
                    if len(d) == 10:
                        zoho_update["Next_Action_Date"] = f"{d}T09:00:00+05:30"

                print(f"✅ Calculated Updates for {lead.get('Last_Name')}: {zoho_update}")
                
                # 3. Push to Zoho
                if zoho_update:
                    self.zoho.update_lead(lead_id, zoho_update)
                    return True, zoho_update
                else:
                    return True, "No updates needed" # Soft success
                    
            return False, "Failed to parse AI response"
            
        except Exception as e:
            print(f"Error in update_lead_context: {e}")
            import traceback
            traceback.print_exc()
            return False, str(e)

# Initialize Core Agent
agent = ZohoAgentCore()

# ==========================================
# Slack Handlers
# ==========================================

def get_draft_blocks(lead, content, draft_id):
    """Constructs the Slack UI blocks for a draft."""
    # Clean body for Slack (strip basic HTML if present)
    body_text = re.sub('<[^<]+?>', '', content['body']).strip()
    
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Draft for {lead.get('First_Name')} {lead.get('Last_Name')}* ({lead.get('Company')})\n*Subject:* {content['subject']}"
            }
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{body_text}"
            }
        },
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Approve & Send"},
                    "style": "primary",
                    "action_id": "approve_draft",
                    "value": draft_id
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✏️ Edit / Regenerate"},
                    "action_id": "edit_draft",
                    "value": draft_id
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "⏭️ Skip"},
                    "action_id": "skip_draft",
                    "value": draft_id
                }
            ]
        }
    ]

@app.action("approve_draft")
def handle_approval(ack, body, client):
    ack()
    draft_id = body['actions'][0]['value']
    
    # DEBUG: Print ID and Keys
    print(f"DEBUG: Processing Approval for Draft ID: {draft_id}")
    print(f"DEBUG: Available Draft IDs: {list(draft_manager.drafts.keys())}")
    
    draft_data = draft_manager.get_draft(draft_id)
    
    if not draft_data:
        print(f"DEBUG: Draft {draft_id} NOT FOUND.")
        client.chat_postMessage(channel=body['channel']['id'], text="⚠️ Draft expired or not found. Please regenerate.")
        return

    # Execute Send
    status = agent.execute_send(draft_data['lead'], draft_data['plan'], draft_data['content'])
    
    if status == "SENT":
        # Update message to remove buttons
        ts = body['message']['ts']
        channel = body['channel']['id']
        try:
            client.chat_update(
                channel=channel,
                ts=ts,
                text="Email Sent ✅",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"✅ *Sent to {draft_data['lead'].get('First_Name')}*:\n{draft_data['content']['subject']}"
                        }
                    }
                ]
            )
        except Exception as e:
            print(f"ERROR: Failed to update Slack message: {e}")
            
        # Cleanup
        draft_manager.delete_draft(draft_id)
        
    elif status == "EMAIL_FAILED":
        client.chat_postMessage(
            channel=body['channel']['id'], 
            text="❌ Email Failed to Send.",
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": "❌ *Gmail Send Failed*."}},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "🔄 Retry Send"},
                            "action_id": "approve_draft", # Re-use approval to retry
                            "value": draft_id
                        }
                    ]
                }
            ]
        )

@app.action("skip_draft")
def handle_skip(ack, body, client):
    ack()
    draft_id = body['actions'][0]['value']
    ts = body['message']['ts']
    channel = body['channel']['id']

    draft_data = draft_manager.get_draft(draft_id)
    if not draft_data:
        client.chat_update(channel=channel, ts=ts, text="⚠️ Draft expired.")
        return

    # Update Zoho
    lead_id = draft_data['lead']['id']
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT09:00:00+05:30")
    
    try:
        agent.zoho.add_note(lead_id, "Agent: Draft Skipped via Slack. Rescheduled for tomorrow.")
        agent.zoho.update_lead(lead_id, {
            "Next_Action_Date": tomorrow,
            "Next_Action": "Skipped - Retry Tomorrow"
        })
    except Exception as e:
        print(f"ERROR: Skip Action Failed: {e}")
    
    client.chat_update(
        channel=channel,
        ts=ts,
        text="Skipped ⏭️",
        blocks=[
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"⏭️ *Skipped - will retry tomorrow*."}
            }
        ]
    )
    draft_manager.delete_draft(draft_id)

@app.action("edit_draft")
def handle_edit(ack, body, client):
    ack()
    draft_id = body['actions'][0]['value']
    
    draft_data = draft_manager.get_draft(draft_id)
    if not draft_data:
        client.chat_postMessage(channel=body['channel']['id'], text="⚠️ Draft expired.")
        return

    # Open Modal
    client.views_open(
        trigger_id=body['trigger_id'],
        view={
            "type": "modal",
            "callback_id": "submit_refinement",
            "private_metadata": json.dumps({"draft_id": draft_id, "channel": body['channel']['id'], "ts": body['message']['ts']}),
            "title": {"type": "plain_text", "text": "Edit Email Draft"},
            "submit": {"type": "plain_text", "text": "Save / Regenerate"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "subject_block",
                    "label": {"type": "plain_text", "text": "Subject"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "subject_input",
                        "initial_value": draft_data['content'].get('subject', '')
                    }
                },
                {
                    "type": "input",
                    "block_id": "body_block",
                    "label": {"type": "plain_text", "text": "Body"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "body_input",
                        "initial_value": draft_data['content'].get('body', ''),
                        "multiline": True
                    }
                },
                {
                    "type": "input",
                    "block_id": "feedback_block",
                    "label": {"type": "plain_text", "text": "AI Feedback (Optional)"},
                    "optional": True,
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "feedback_input",
                        "placeholder": {"type": "plain_text", "text": "Leave empty to save manual edits above. Or type instructions to regenerate entirely."}
                    }
                }
            ]
        }
    )

@app.message("check leads")
def handle_check_leads(message, say):
    """Trigger the lead check manually from Slack."""
    say("🕵️‍♂️ Checking for pending leads...")
    
    leads = agent.fetch_pending_leads()
    if not leads:
        say("✅ No leads need action today.")
        return

    say(f"🚀 Found {len(leads)} leads. Generating drafts...")
    
    for lead in leads:
        plan = agent.determine_next_step(lead)
        if plan['type'] == 'email':
            # Try generation
            content = agent.generate_email_content(lead, plan)
            
            # Store lead/plan even if content failed (mock content for id)
            mock_content = content or {"subject": "Generation Error", "body": "Failed"}
            draft_id = draft_manager.save_draft(lead, plan, mock_content)
            
            if content:
                app.client.chat_postMessage(
                    channel=message['channel'],
                    text="Draft Email",
                    blocks=get_draft_blocks(lead, content, draft_id)
                )
            else:
                 app.client.chat_postMessage(
                    channel=message['channel'],
                    text="⚠️ Generation Failed",
                    blocks=[
                        {"type": "section", "text": {"type": "mrkdwn", "text": f"⚠️ *Failed to generate email for {lead.get('First_Name')}*."}},
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "🔄 Try Again"},
                                    "action_id": "retry_generation",
                                    "value": draft_id
                                }
                            ]
                        }
                    ]
                )

@app.action("retry_generation")
def handle_retry_gen(ack, body, client):
    ack()
    draft_id = body['actions'][0]['value']
    draft_data = draft_manager.get_draft(draft_id)
    if not draft_data:
        return

    ts = body['message']['ts']
    channel = body['channel']['id']
    
    client.chat_update(
        channel=channel, 
        ts=ts, 
        text="🔄 Retrying...",
        blocks=[
             {"type": "section", "text": {"type": "mrkdwn", "text": "🔄 *Retrying Generation...*"}}
        ]
    )
    
    content = agent.generate_email_content(draft_data['lead'], draft_data['plan'])
    
    if content:
        # Update draft
        draft_manager.drafts[draft_id]['content'] = content
        draft_manager._save_drafts()
        
        client.chat_update(
            channel=channel,
            ts=ts,
            text="Draft Generated",
            blocks=get_draft_blocks(draft_data['lead'], content, draft_id)
        )
    else:
        # Failed again
        client.chat_update(
            channel=channel,
            ts=ts,
            text="⚠️ Failed Again",
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": "⚠️ *Failed again. Check logs.*"}},
                 {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "🔄 Try Again"},
                            "action_id": "retry_generation",
                            "value": draft_id
                        }
                    ]
                }
            ]
        )

@app.view("submit_refinement")
def handle_refinement(ack, body, client):
    ack()
    metadata = json.loads(body['view']['private_metadata'])
    draft_id = metadata['draft_id']
    feedback = body['view']['state']['values']['feedback_block']['feedback_input']['value']
    new_subject = body['view']['state']['values']['subject_block']['subject_input']['value']
    new_body = body['view']['state']['values']['body_block']['body_input']['value']
    
    draft_data = draft_manager.get_draft(draft_id)
    if not draft_data:
        return

    # Notify processing
    loading_msg = "🔄 Regenerating via AI..." if feedback else "💾 Saving Edits..."
    client.chat_update(
        channel=metadata['channel'],
        ts=metadata['ts'],
        text=loading_msg,
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": f"*{loading_msg}*"}}]
    )

    if feedback:
        # Regenerate entirely based on feedback
        new_content = agent.generate_email_content(draft_data['lead'], draft_data['plan'], feedback)
    else:
        # Use manual edits
        new_content = {
            "subject": new_subject,
            "body": new_body
        }
    
    # Update Draft Store (update existing ID)
    draft_manager.drafts[draft_id]['content'] = new_content
    draft_manager._save_drafts()
    
    # Repost new draft
    client.chat_update(
        channel=metadata['channel'],
        ts=metadata['ts'],
        text="Updated Draft",
        blocks=get_draft_blocks(draft_data['lead'], new_content, draft_id)
    )

def main():
    # On startup, check leads once (manual trigger mode)
    print("🤖 Bot Started. Checking for pending leads...")
    leads = agent.fetch_pending_leads()
    print(f"Found {len(leads)} leads.")
    
    if leads:
        app.client.chat_postMessage(channel=SLACK_CHANNEL, text=f"🚀 *Daily Sales Plan*: Found {len(leads)} leads.")
        
        for lead in leads:
            plan = agent.determine_next_step(lead)
            if plan['type'] == 'email':
                print(f"Generating draft for {lead.get('Last_Name')}...")
                content = agent.generate_email_content(lead, plan)
                
                mock_content = content or {"subject": "Generation Error", "body": "Failed"}
                draft_id = draft_manager.save_draft(lead, plan, mock_content)

                if content:
                    # Post to Slack
                    app.client.chat_postMessage(
                        channel=SLACK_CHANNEL,
                        text="Draft Email",
                        blocks=get_draft_blocks(lead, content, draft_id)
                    )
                else:
                    app.client.chat_postMessage(
                        channel=SLACK_CHANNEL,
                        text="⚠️ Generation Failed",
                        blocks=[
                            {"type": "section", "text": {"type": "mrkdwn", "text": f"⚠️ *Failed to generate email for {lead.get('First_Name')}*."}},
                            {
                                "type": "actions",
                                "elements": [
                                    {
                                        "type": "button",
                                        "text": {"type": "plain_text", "text": "🔄 Try Again"},
                                        "action_id": "retry_generation",
                                        "value": draft_id
                                    }
                                ]
                            }
                        ]
                    )
    else:
        print("No leads needed action.")

    print("⚡️ Listening for Slack interactions...")
    SocketModeHandler(app, SLACK_APP_TOKEN).start()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Exiting...")
