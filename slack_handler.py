#!/usr/bin/env python3
"""
Slack Slash Command Handler (Lambda-Compatible)

Handles /check-leads and /find-leads slash commands via HTTP.
Designed to run on AWS Lambda with API Gateway (via Mangum adapter).

Includes interactive handlers (Buttons, Modals) for Zoho Agent drafts.
"""

import os
import json
import logging
import threading
import re
import requests
from dotenv import load_dotenv
from datetime import datetime, timedelta

from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request

# Import Zoho Agent components
# safe to import as zoho_agent.py guards main() with if __name__ == "__main__"
from zoho_agent import agent, draft_manager, get_draft_blocks

# Setup
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Slack App (HTTP mode)
app = App(
    token=os.getenv("SLACK_BOT_TOKEN"),
    signing_secret=os.getenv("SLACK_SIGNING_SECRET"),
    process_before_response=True,
)

# ==========================================
# /check-leads — Zoho Agent (Threaded Drafts)
# ==========================================

def run_check_leads(response_url: str, user_id: str, channel_id: str, client):
    """Background task: fetch leads, generate drafts, post to thread."""
    try:
        # 1. Post the summary message (Parent of thread)
        # We use client.chat_postMessage instead of response_url to get a persistent message ID (ts)
        start_msg = client.chat_postMessage(
            channel=channel_id,
            text="🕵️ Checking for leads due today..."
        )
        thread_ts = start_msg["ts"]

        # 2. Fetch Leads
        leads = agent.fetch_pending_leads()
        
        if not leads:
            client.chat_update(
                channel=channel_id,
                ts=thread_ts,
                text="✅ No leads need action today."
            )
            return

        # 3. Update Summary
        client.chat_update(
            channel=channel_id,
            ts=thread_ts,
            text=f"🚀 *Found {len(leads)} leads due today.* Generating drafts...",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"🚀 *Found {len(leads)} leads due today.*\nGenerating drafts in this thread 🧵..."}
                }
            ]
        )

        # 4. Generate Drafts & Post to Thread
        for lead in leads:
            try:
                # Plan & Generate
                plan = agent.determine_next_step(lead)
                
                if plan['type'] == 'email':
                    content = agent.generate_email_content(lead, plan)
                    
                    # Fallback if generation fails
                    mock_content = content or {"subject": "Generation Error", "body": "Failed"}
                    draft_id = draft_manager.save_draft(lead, plan, mock_content)
                    
                    if content:
                        client.chat_postMessage(
                            channel=channel_id,
                            thread_ts=thread_ts,
                            text=f"Draft for {lead.get('Last_Name')}",
                            blocks=get_draft_blocks(lead, content, draft_id)
                        )
                    else:
                        # Report Failure
                         client.chat_postMessage(
                            channel=channel_id,
                            thread_ts=thread_ts,
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
                    # Non-email action (e.g. Review)
                    client.chat_postMessage(
                        channel=channel_id,
                        thread_ts=thread_ts,
                        text=f"ℹ️ *{lead.get('First_Name')} {lead.get('Last_Name')}*: {plan['reason']}"
                    )
                    
            except Exception as e:
                logger.error(f"Error processing lead {lead.get('id')}: {e}")
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=f"❌ Error processing lead: {e}"
                )

        # Final Update to Parent (Optional - maybe just a reaction?)
        # client.reactions_add(name="white_check_mark", channel=channel_id, timestamp=thread_ts)


    except Exception as e:
        logger.error(f"Error in run_check_leads: {e}", exc_info=True)
        requests.post(response_url, json={
            "response_type": "ephemeral",
            "text": f"❌ Error: {str(e)}"
        })


def check_leads_ack(ack):
    ack() # Ack immediately

def check_leads_lazy(body, logger, client):
    """Lazy listener for /check-leads"""
    response_url = body.get("response_url")
    user_id = body.get("user_id")
    channel_id = body.get("channel_id")
    
    # Run in thread (local) or direct (Lambda)
    # process_before_response=True in App config handles Lambda timing
    thread = threading.Thread(target=run_check_leads, args=(response_url, user_id, channel_id, client))
    thread.start()

app.command("/check-leads")(ack=check_leads_ack, lazy=[check_leads_lazy])


# ==========================================
# Interactive Handlers (Buttons & Modals)
# ==========================================

@app.action("approve_draft")
def handle_approval(ack, body, client):
    ack()
    draft_id = body['actions'][0]['value']
    draft_data = draft_manager.get_draft(draft_id)
    
    if not draft_data:
        client.chat_postMessage(channel=body['channel']['id'], text="⚠️ Draft expired or not found.")
        return

    # Execute Send
    status = agent.execute_send(draft_data['lead'], draft_data['plan'], draft_data['content'])
    
    ts = body['message']['ts']
    channel = body['channel']['id']

    if status == "SENT":
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
        draft_manager.delete_draft(draft_id)
        
    elif status == "EMAIL_FAILED":
        client.chat_postMessage(
            channel=channel,
            thread_ts=ts, # Reply in thread if possible
            text="❌ Email Failed to Send."
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
        logger.error(f"Skip failed: {e}")
    
    client.chat_update(
        channel=channel,
        ts=ts,
        text="Skipped ⏭️",
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": f"⏭️ *Skipped - will retry tomorrow*."}}]
    )
    draft_manager.delete_draft(draft_id)

@app.action("edit_draft")
def handle_edit(ack, body, client):
    ack()
    draft_id = body['actions'][0]['value']
    draft_data = draft_manager.get_draft(draft_id)
    
    if not draft_data:
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

@app.view("submit_refinement")
def handle_submit_refinement(ack, body, client):
    ack()
    metadata = json.loads(body['view']['private_metadata'])
    draft_id = metadata['draft_id']
    feedback = body['view']['state']['values']['feedback_block']['feedback_input']['value']
    new_subject = body['view']['state']['values']['subject_block']['subject_input']['value']
    new_body = body['view']['state']['values']['body_block']['body_input']['value']
    
    draft_data = draft_manager.get_draft(draft_id)
    if not draft_data: return

    # Notify
    loading_msg = "🔄 Regenerating via AI..." if feedback else "💾 Saving Edits..."
    client.chat_update(
        channel=metadata['channel'],
        ts=metadata['ts'],
        text=loading_msg,
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": f"*{loading_msg}*"}}]
    )

    if feedback:
        new_content = agent.generate_email_content(draft_data['lead'], draft_data['plan'], feedback)
    else:
        new_content = {"subject": new_subject, "body": new_body}
    
    draft_manager.drafts[draft_id]['content'] = new_content
    draft_manager._save_drafts()
    
    client.chat_update(
        channel=metadata['channel'],
        ts=metadata['ts'],
        text="Updated Draft",
        blocks=get_draft_blocks(draft_data['lead'], new_content, draft_id)
    )

@app.action("retry_generation")
def handle_retry(ack, body, client):
    ack()
    draft_id = body['actions'][0]['value']
    draft_data = draft_manager.get_draft(draft_id)
    if not draft_data: return
    
    ts = body['message']['ts']
    channel = body['channel']['id']
    
    client.chat_update(channel=channel, ts=ts, text="🔄 Retrying...", blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "*Retrying...*"}}])
    
    content = agent.generate_email_content(draft_data['lead'], draft_data['plan'])
    if content:
        draft_manager.drafts[draft_id]['content'] = content
        draft_manager._save_drafts()
        client.chat_update(channel=channel, ts=ts, text="Draft", blocks=get_draft_blocks(draft_data['lead'], content, draft_id))
    else:
        client.chat_update(channel=channel, ts=ts, text="Failed", blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "Failed again."}}])

# ==========================================
# /find-leads — Lead Finder Agent
# ==========================================
# (Kept same as before, but ensure imports are present if needed)

def run_find_leads(response_url: str, user_id: str):
    """Background task: run HubSpot lead finder and post results to Slack."""
    try:
        from lead_finder_agent import (
            HubSpotLeadClient, calculate_engagement_score, passes_filters,
            TOP_LEADS_COUNT, MIN_EMPLOYEE_SIZE, STALE_THRESHOLD_DAYS
        )
        import anthropic
        
        hubspot_token = os.getenv("HUBSPOT_ACCESS_TOKEN")
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")

        if not hubspot_token or not anthropic_key:
            requests.post(response_url, json={"text": "❌ Missing API keys."})
            return

        hubspot = HubSpotLeadClient(hubspot_token)
        
        # Search for stale marketing contacts
        stale_cutoff = datetime.now() - timedelta(days=STALE_THRESHOLD_DAYS)
        stale_cutoff_ms = int(stale_cutoff.timestamp() * 1000)

        contact_properties = [
            "email", "firstname", "lastname", "jobtitle", "company", "country",
            "lifecyclestage", "hs_email_open_count", "hs_email_click_count",
            "hs_analytics_num_page_views", "num_conversion_events",
            "associatedcompanyid", "hs_marketable_status", "employee_size",
            "notes_last_updated"
        ]

        hubspot_filters = [
            {"propertyName": "hs_marketable_status", "operator": "EQ", "value": "true"},
            {"propertyName": "employee_size", "operator": "GTE", "value": str(MIN_EMPLOYEE_SIZE)},
            {"propertyName": "notes_last_updated", "operator": "LT", "value": str(stale_cutoff_ms)},
        ]

        contacts = hubspot.search_contacts(filters=hubspot_filters, properties=contact_properties)

        if not contacts:
            requests.post(response_url, json={"response_type": "in_channel", "text": "✅ No high-engagement stale leads found."})
            return

        # Process and score
        qualified_leads = []
        for contact in contacts[:TOP_LEADS_COUNT]:
            contact_id = contact["id"]
            props = contact.get("properties", {})
            name = f"{props.get('firstname', '')} {props.get('lastname', '')}".strip() or "Unknown"
            
            company = hubspot.get_associated_company(contact_id)
            passes, reason = passes_filters(contact, company)
            if not passes: continue

            meetings = hubspot.get_contact_meeting_associations(contact_id)
            score = calculate_engagement_score(contact, len(meetings))
            company_props = company.get("properties", {}) if company else {}

            qualified_leads.append({
                "contact_name": name,
                "contact_email": props.get("email", "No email"),
                "contact_title": props.get("jobtitle", "Unknown"),
                "company_name": company_props.get("name", props.get("company", "Unknown")),
                "company_industry": company_props.get("industry", "Unknown"),
                "company_size": props.get("employee_size", "Unknown"),
                "engagement_score": score,
                "email_opens": int(props.get("hs_email_open_count", 0) or 0),
                "email_clicks": int(props.get("hs_email_click_count", 0) or 0),
                "page_views": int(props.get("hs_analytics_num_page_views", 0) or 0),
            })

        qualified_leads.sort(key=lambda x: x["engagement_score"], reverse=True)
        top_leads = qualified_leads[:10]

        if not top_leads:
            requests.post(response_url, json={"response_type": "in_channel", "text": "✅ No leads passed all filters."})
            return

        # Build summary
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": f"🎯 Top {len(top_leads)} High-Engagement Leads"}},
            {"type": "divider"}
        ]

        for i, lead in enumerate(top_leads, 1):
             blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{i}. {lead['contact_name']}* — {lead['contact_title']}\n"
                        f"🏢 {lead['company_name']} ({lead['company_industry']}, {lead['company_size']} emp)\n"
                        f"📊 Score: *{lead['engagement_score']}* (Opens: {lead['email_opens']}, Clicks: {lead['email_clicks']})"
                    )
                }
            })
             
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "💡 Run `python lead_finder_agent.py` for full details"}]
        })

        requests.post(response_url, json={
            "response_type": "in_channel",
            "blocks": blocks,
            "text": f"Found {len(top_leads)} leads"
        })

    except Exception as e:
        logger.error(f"Error in /find-leads: {e}", exc_info=True)
        requests.post(response_url, json={"response_type": "ephemeral", "text": f"❌ Error: {str(e)}"})

def find_leads_ack(ack): ack()

def find_leads_lazy(body):
    response_url = body.get("response_url")
    user_id = body.get("user_id")
    thread = threading.Thread(target=run_find_leads, args=(response_url, user_id))
    thread.start()


def run_update_context(response_url: str):
    """Background task to sync context for active leads."""
    try:
        # 1. Fetch active leads (limit to 10 for now to avoid timeout)
        requests.post(response_url, json={"text": "🔄 *Scanning* active leads for context updates..."})
        leads = agent.fetch_active_leads(limit=10)
        
        if not leads:
            requests.post(response_url, json={"text": "✅ No active leads found to update."})
            return

        updated_count = 0
        details = []

        # 2. Update each lead
        for lead in leads:
            name = f"{lead.get('First_Name')} {lead.get('Last_Name')}"
            success, result = agent.update_lead_context(lead)
            
            if success and isinstance(result, dict) and result:
                updated_count += 1
                details.append(f"• *{name}*: Updated {', '.join(result.keys())}")
            elif success:
                # No updates needed case
                pass
            else:
                details.append(f"• *{name}*: ❌ Error ({result})")

        # 3. Report results
        if updated_count > 0:
            msg = f"✅ *Context Sync Complete*\nUpdated {updated_count} leads:\n" + "\n".join(details)
        else:
            msg = f"✅ *Context Sync Complete*\nScanned {len(leads)} leads. No updates needed based on recent context."
            
        requests.post(response_url, json={"text": msg})

    except Exception as e:
        logger.error(f"Error in run_update_context: {e}")
        requests.post(response_url, json={"text": f"❌ Error updating context: {str(e)}"})

@app.command("/update")
def handle_update_command(ack, body, respond):
    """Updates context (Last Conversation, Next Action) for active leads."""
    ack()
    threading.Thread(target=run_update_context, args=(body["response_url"],)).start()

app.command("/find-leads")(ack=find_leads_ack, lazy=[find_leads_lazy])


# ==========================================
# Flask App + Lambda Handler
# ==========================================

flask_app = Flask(__name__)
handler = SlackRequestHandler(app)

@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

@flask_app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}

# Lambda entry point
try:
    from mangum import Mangum
    lambda_handler = Mangum(flask_app, lifespan="off")
except ImportError:
    lambda_handler = None

if __name__ == "__main__":
    print("⚡ Slack Handler running on http://localhost:3000")
    flask_app.run(port=3000, debug=True)
