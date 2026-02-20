# Sales AI Agents

A suite of AI-powered sales automation agents that manage follow-ups and identify new opportunities by researching prospects across multiple data sources.

> ⚠️ **Note**: These agents require customization before use. The AI prompts are currently configured for a specific product. See the [Customize AI Prompts](#customize-ai-prompts) section to adapt them for your product/service.

## Agents

### 1. Zoho Sales Agent (`zoho_agent.py`)
Interactive Slack bot that manages **sales follow-ups** via Zoho CRM. Fetches leads due for action today, generates AI-powered email drafts, and posts them to Slack for approval before sending.

### 2. Lead Finder Agent (`lead_finder_agent.py`)
Finds **high-engagement Marketing Contacts** in HubSpot without deals and generates outreach emails to re-engage them.

### 3. Slack Handler (`slack_handler.py`)
Lambda-compatible HTTP handler that exposes `/check-leads` and `/find-leads` as Slack slash commands.

## Features

### Zoho Sales Agent
- **Zoho CRM Integration**: Queries leads with action due today
- **Smart Drip Sequences**: Automated cold outreach (Day 0 → Day 40 breakup)
- **Active Response Detection**: Detects lead replies and generates direct responses
- **Re-engagement**: Identifies leads who went silent after replying
- **Interactive Slack Workflow**: Approve ✅, Edit ✏️, or Skip ⏭️ each draft
- **Gmail Integration**: Sends emails with thread context via Gmail API
- **AI-Powered Emails**: Uses Claude to generate personalized, context-rich emails

### Lead Finder Agent
- **Smart Filtering**: Finds Marketing Contacts with high engagement but no recent activity
- **Configurable Filters**: Filter by employee size, industry, country, job title, lifecycle stage
- **Multi-Source Enrichment**:
  - 🔍 Apollo.io for company funding, tech stack, and hiring signals
  - 💬 Slack internal discussions
  - 📞 Fireflies call transcripts
  - 📚 Knowledge base (RAG) for relevant product context
- **Engagement Scoring**: Ranks leads by email opens, clicks, page views, form submissions
- **AI-Powered Outreach**: Generates personalized re-engagement emails using Claude

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 3. Run the Agents

```bash
# Run the Zoho Sales Agent (interactive Slack bot)
python zoho_agent.py

# Run the Lead Finder Agent (HubSpot digest)
python lead_finder_agent.py

# Run the Slack Handler locally (for slash commands)
python slack_handler.py
```

### 4. Set Up Knowledge Base (Optional)

The Lead Finder Agent can use a knowledge base (RAG) to include relevant product context in emails:

```bash
# Add your documents to the docs/ folder (.md, .txt, .pdf)
python index_knowledge_base.py

# Full re-index (rebuild from scratch)
python index_knowledge_base.py --full
```

## Configuration

### Zoho Agent Environment Variables

```bash
# Zoho CRM (Required)
ZOHO_CLIENT_ID=your_client_id
ZOHO_CLIENT_SECRET=your_client_secret
ZOHO_REFRESH_TOKEN=your_refresh_token
ZOHO_API_DOMAIN=https://www.zohoapis.com

# AI (Required)
ANTHROPIC_API_KEY=sk-ant-xxxxx

# Gmail (Required for sending)
GOOGLE_REFRESH_TOKEN=your_token
GOOGLE_CLIENT_ID=your_client_id
GOOGLE_CLIENT_SECRET=your_client_secret

# Slack Bot (Required)
SLACK_BOT_TOKEN=xoxb-xxxxx
SLACK_APP_TOKEN=xapp-xxxxx
SLACK_CHANNEL=C0123456789
SLACK_SIGNING_SECRET=your_signing_secret
```

### Lead Finder Environment Variables

```bash
# HubSpot (Required)
HUBSPOT_ACCESS_TOKEN=pat-xxxxx

# Digest Settings
LEAD_FINDER_RECIPIENTS=user1@example.com,user2@example.com
SENDGRID_API_KEY=SG.xxxxx
FROM_EMAIL=noreply@example.com

# Contact Filters (Optional)
MIN_EMPLOYEE_SIZE=200
TARGET_INDUSTRIES=Technology,Healthcare
TARGET_COUNTRIES=United States,Canada
TARGET_JOB_TITLES=VP,Director,Head of
TARGET_LIFECYCLE_STAGES=lead,marketingqualifiedlead
TOP_LEADS_COUNT=10

# Optional Integrations
APOLLO_API_KEY=xxxxx
SLACK_BOT_TOKEN=xoxb-xxxxx
SLACK_CHANNELS=sales,marketing
FIREFLIES_API_KEY=xxxxx
```

## Integration Setup

### Zoho CRM
1. Go to [Zoho API Console](https://api-console.zoho.com/)
2. Create a Self Client
3. Generate a refresh token with scopes: `ZohoCRM.modules.ALL`, `ZohoCRM.settings.ALL`
4. Run `python zoho_auth.py` to verify authentication

### Gmail
1. Create OAuth credentials in [Google Cloud Console](https://console.cloud.google.com/)
2. Run `python gmail_auth.py` to get a refresh token
3. Add credentials to `.env`

### Slack App
1. Create a Slack App at [api.slack.com/apps](https://api.slack.com/apps)
2. Add OAuth scopes: `chat:write`, `commands`, `search:read`
3. Enable Socket Mode (for `zoho_agent.py`) and/or set Request URLs (for `slack_handler.py`)
4. Create slash commands: `/check-leads` and `/find-leads`
5. Install to your workspace

### Slack Slash Commands (Lambda)
After deploying `slack_handler.py` to AWS Lambda:
1. Set Request URL for both commands to: `https://<api-gateway-url>/slack/events`
2. Add `SLACK_SIGNING_SECRET` to Lambda env vars

## Customize AI Prompts

⚠️ The default prompts are configured for a specific product. You **must** customize for your own product/service.

### Zoho Agent
Edit `system_prompt.md` to update the AI's role, product capabilities, and email templates.

### Lead Finder Agent
Edit the `generate_outreach_email()` function in `lead_finder_agent.py` to update the product context and email generation prompt.

## Scheduling

### Cron (Linux/Mac)
```bash
# Run Zoho Agent at 9 AM weekdays
0 9 * * 1-5 cd /path/to/project && python3 zoho_agent.py --auto >> agent.log 2>&1

# Run Lead Finder at 9 AM daily
0 9 * * * cd /path/to/project && python3 lead_finder_agent.py >> lead_finder.log 2>&1
```

### AWS Lambda
Deploy `slack_handler.py` with API Gateway for slash command support. Use EventBridge for scheduled runs.

## Project Structure

```
sales-ai-agents/
├── zoho_agent.py            # Zoho Sales Agent (interactive Slack bot)
├── lead_finder_agent.py     # Lead Finder Agent (HubSpot digest)
├── slack_handler.py         # Slack slash command handler (Lambda)
├── zoho_client.py           # Zoho CRM API client
├── zoho_auth.py             # Zoho OAuth helper
├── gmail_client.py          # Gmail API client
├── gmail_auth.py            # Gmail OAuth helper
├── index_knowledge_base.py  # Knowledge base indexer
├── company_context.md       # Company context for AI prompts
├── system_prompt.md         # System prompt for email generation
├── requirements.txt         # Python dependencies
├── setup_cron.py            # Cron setup helper
├── .env.example             # Environment template
└── .env                     # Your configuration (gitignored)
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| No leads found | Verify Zoho leads have today's `Next_Action_Date` |
| Slack `dispatch_failed` | Deploy `slack_handler.py` and set Request URL |
| Emails not sending | Check Gmail/SendGrid credentials |
| Knowledge base not working | Run `python index_knowledge_base.py` |
| Apollo enrichment failing | Verify API key and check rate limits |

## License

MIT License - feel free to use and modify for your own sales workflow.