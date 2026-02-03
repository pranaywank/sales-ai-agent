# Sales AI Agents

A suite of AI-powered sales automation agents that identify opportunities and generate personalized outreach emails by researching prospects across multiple data sources.

> ⚠️ **Note**: These agents require customization before use. The AI prompts are currently configured for a specific product. See the [Customize AI Prompts](#customize-ai-prompts-required) section to adapt them for your product/service.

## Agents

### 1. Follow-up Agent (`followup_agent.py`)
Identifies **stale deals** in your HubSpot pipeline and generates follow-up emails.

### 2. Lead Finder Agent (`lead_finder_agent.py`)
Finds **high-engagement Marketing Contacts** without deals and generates outreach emails to re-engage them.

## Features

### Follow-up Agent
- **CRM Integration**: Queries HubSpot for deals in specific pipeline stages
- **Multi-Source Research**: Gathers context from:
  - 📧 HubSpot emails and notes from Contact, Deal & Account objects
  - 💬 Slack internal discussions
  - 📞 Fireflies call transcripts
  - 🌐 Web search for company news & AI initiatives
  - 📚 Knowledge base (RAG) for relevant product context
- **AI-Powered Emails**: Uses Claude to generate personalized, context-rich follow-up emails
- **Daily Digest**: Sends a beautifully formatted HTML digest with all draft emails ready to review

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

## How It Works

### Follow-up Agent Flow

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   HubSpot CRM   │────▶│  Gather Context  │────▶│  Claude AI      │
│   (Stale Deals) │     │  from all sources│     │  (Generate      │
└─────────────────┘     └──────────────────┘     │   Emails)       │
                               ▲                 └────────┬────────┘
                               │                          │
                        ┌──────┴───────┐                  │
                        │ Knowledge    │                  │
                        │ Base (RAG)   │                  │
                        └──────────────┘                  │
                       ┌──────────────────┐               │
                       │  Email Digest    │◀──────────────┘
                       │  (SendGrid/SMTP) │
                       └──────────────────┘
```

1. **Query HubSpot** for deals in target stages (First Meeting, Demo, Potential Fit, Cold)
2. **Filter stale deals** where the last email was sent >14 days ago
3. **Research each deal** using Slack, Fireflies, web search, and knowledge base
4. **Generate personalized follow-ups** using Claude with full context
5. **Send a digest email** with all draft emails ready for review

### Lead Finder Agent Flow

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  HubSpot CRM    │────▶│  Apply Filters   │────▶│  Score & Rank   │
│  (Marketing     │     │  (size, industry │     │  by Engagement  │
│   Contacts)     │     │   title, etc.)   │     │                 │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
┌─────────────────┐     ┌──────────────────┐              │
│  Knowledge Base │────▶│  Enrich Context  │◀─────────────┘
│  (ChromaDB RAG) │     │  Apollo, Slack,  │
└─────────────────┘     │  Fireflies       │
                        └────────┬─────────┘
                                 │
                        ┌────────▼─────────┐
                        │  Claude AI       │
                        │  (Generate       │
                        │   Outreach)      │
                        └────────┬─────────┘
                                 │
                        ┌────────▼─────────┐
                        │  Email Digest    │
                        │  (SendGrid)      │
                        └──────────────────┘
```

1. **Query HubSpot** for Marketing Contacts (employee size ≥200, stale >14 days)
2. **Filter contacts** without deals, matching your criteria
3. **Score engagement** (email opens, clicks, page views, form submissions)
4. **Select top 10** leads by engagement score
5. **Enrich context** from Apollo, Slack, Fireflies, and Knowledge Base
6. **Generate outreach emails** using Claude with full context
7. **Send a digest email** with all draft emails ready for review

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/sneurgaonkar/sales-followup-agent.git
cd sales-followup-agent
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 4. Run the Agents

```bash
# Run the Follow-up Agent (for stale deals)
python followup_agent.py

# Run the Lead Finder Agent (for high-engagement contacts)
python lead_finder_agent.py
```

### 5. Test with a Single Deal (Optional)

```bash
python test_single_deal.py "Deal Name"
```

### 6. Set Up Knowledge Base (Optional)

Both the Follow-up Agent and Lead Finder Agent can use a knowledge base (RAG) to include relevant product context in emails:

```bash
# Add your documents to the docs/ folder
# Supported formats: .md, .txt, .pdf

# Index the knowledge base (first time)
python index_knowledge_base.py

# Re-index after adding new documents (incremental)
python index_knowledge_base.py

# Full re-index (rebuild from scratch)
python index_knowledge_base.py --full
```

## Requirements

### Python Dependencies

```
anthropic>=0.39.0
requests>=2.31.0
python-dotenv>=1.0.0
chromadb>=0.4.0           # For knowledge base (Lead Finder)
sentence-transformers>=2.2.0  # For embeddings
pypdf>=3.0.0              # For PDF document loading
```

### API Keys & Tokens

| Service | Follow-up Agent | Lead Finder Agent | Purpose |
|---------|-----------------|-------------------|---------|
| [Anthropic](https://console.anthropic.com/) | ✅ Required | ✅ Required | Claude AI for email generation |
| [HubSpot](https://developers.hubspot.com/) | ✅ Required | ✅ Required | CRM data (deals, contacts, companies) |
| [SendGrid](https://sendgrid.com/) | ✅ Required | ✅ Required | Email delivery |
| [Slack](https://api.slack.com/) | ❌ Optional | ❌ Optional | Internal discussion search |
| [Fireflies.ai](https://fireflies.ai/) | ❌ Optional | ❌ Optional | Call transcript search |
| [Apollo.io](https://www.apollo.io/) | ❌ Not used | ❌ Optional | Contact/company enrichment |

## Configuration

### Required Environment Variables

```bash
# API Keys
ANTHROPIC_API_KEY=sk-ant-xxxxx
HUBSPOT_ACCESS_TOKEN=pat-xxxxx

# Digest Settings
DIGEST_RECIPIENTS=user1@example.com,user2@example.com
FROM_EMAIL=noreply@example.com

# HubSpot Deal Stages to Monitor (REQUIRED)
# Find stage IDs: HubSpot Settings → Objects → Deals → Pipelines → click stage
TARGET_STAGES=appointmentscheduled,qualifiedtobuy,12345678
```

### Email Delivery (Choose One)

**Option A: SendGrid (Recommended)**
```bash
SENDGRID_API_KEY=SG.xxxxx
```

**Option B: SMTP**
```bash
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
```

### Optional Integrations

```bash
# Slack - for searching internal discussions
SLACK_BOT_TOKEN=xoxb-xxxxx
SLACK_CHANNELS=sales,marketing,support

# Fireflies - for searching call transcripts
FIREFLIES_API_KEY=xxxxx
```

### Optional Customization

```bash
# Days since last email to consider a deal stale
STALE_THRESHOLD_DAYS=14

# Default deal name for test script
TEST_DEAL_NAME=Test Deal
```

### Lead Finder Agent Configuration

```bash
# Digest recipients for lead finder
LEAD_FINDER_RECIPIENTS=user1@example.com,user2@example.com

# Apollo.io API key (optional, for enrichment)
APOLLO_API_KEY=xxxxx

# Contact filters (all optional)
MIN_EMPLOYEE_SIZE=200
TARGET_INDUSTRIES=Technology,Financial Services,Healthcare
TARGET_COUNTRIES=United States,Canada
TARGET_JOB_TITLES=VP,Director,Head of,Chief,Manager
TARGET_LIFECYCLE_STAGES=lead,marketingqualifiedlead

# Number of top leads to process
TOP_LEADS_COUNT=10
```

## Integration Setup

### HubSpot Private App

1. Go to **Settings → Integrations → Private Apps**
2. Create a new app with these scopes:
   - `crm.objects.deals.read`
   - `crm.objects.contacts.read`
   - `crm.objects.companies.read`
   - `sales-email-read`
3. Copy the access token to your `.env`

### Slack Bot (Optional)

1. Create a Slack App at [api.slack.com/apps](https://api.slack.com/apps)
2. Add OAuth scope: `search:read`
3. Install to your workspace
4. Add the bot to channels you want to search (e.g., `#sales`, `#marketing`)
5. Copy the Bot User OAuth Token to your `.env`

### Fireflies (Optional)

1. Go to [Fireflies Integrations](https://app.fireflies.ai/integrations)
2. Generate an API key
3. Copy to your `.env`

### Anthropic Web Search (Optional)

To enable web search for company news:
1. Go to [Anthropic Console](https://console.anthropic.com/settings/organization/features)
2. Enable the "Web Search" feature for your organization

### Apollo.io (Optional - Lead Finder Only)

1. Sign up at [Apollo.io](https://www.apollo.io/)
2. Go to Settings → API Keys
3. Generate an API key
4. Add to your `.env` as `APOLLO_API_KEY`

## Knowledge Base Setup

Both the Follow-up Agent and Lead Finder Agent use a vector database (ChromaDB) to retrieve relevant product information when generating emails. This helps create more personalized outreach based on the deal/lead's industry, persona, and context.

### 1. Add Documents

Place your documents in the `docs/` folder:

```
docs/
├── capabilities/
│   ├── feature-1.md
│   └── feature-2.md
├── use-cases/
│   ├── industry-1.md
│   └── industry-2.md
├── personas/
│   ├── cto-messaging.md
│   └── vp-engineering.md
└── case-studies/
    ├── customer-1.pdf
    └── customer-2.pdf
```

**Supported formats:** `.md`, `.txt`, `.pdf`

### 2. Index the Knowledge Base

```bash
# First time or after adding new documents
python index_knowledge_base.py

# Full re-index (if needed)
python index_knowledge_base.py --full
```

The indexer:
- Loads all documents from `docs/`
- Splits them into chunks (~1000 characters)
- Generates embeddings using `all-MiniLM-L6-v2` model
- Stores in ChromaDB (persisted in `.chroma/` folder)
- **Incremental**: Only processes new/modified files on subsequent runs

### 3. How RAG Works

When generating emails, both agents search the knowledge base for:
- **Industry-specific** use cases and capabilities
- **Persona-relevant** messaging (based on job title)
- **Context from notes** (problems, blockers, or specific needs mentioned)
- **Tech stack** integrations (from Apollo data, Lead Finder only)
- **General** product capabilities

This context is included in the Claude prompt to generate more relevant, personalized outreach.

| Agent | What RAG Searches For |
|-------|----------------------|
| Follow-up Agent | Industry, job title/persona, deal notes, general capabilities |
| Lead Finder Agent | Industry, job title/persona, tech stack (Apollo), general capabilities |

## Scheduling

### Option 1: Cron (Linux/Mac)

```bash
# Edit crontab
crontab -e

# Run at 9 AM daily
0 9 * * * cd /path/to/sales-followup-agent && python3 followup_agent.py >> /var/log/followup-agent.log 2>&1
```

### Option 2: GitHub Actions

Create `.github/workflows/followup.yml`:

```yaml
name: Daily Follow-up Agent

on:
  schedule:
    - cron: '0 16 * * *'  # 9 AM PST = 4 PM UTC
  workflow_dispatch:  # Manual trigger

jobs:
  run-agent:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: python followup_agent.py
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          HUBSPOT_ACCESS_TOKEN: ${{ secrets.HUBSPOT_ACCESS_TOKEN }}
          SENDGRID_API_KEY: ${{ secrets.SENDGRID_API_KEY }}
          DIGEST_RECIPIENTS: ${{ secrets.DIGEST_RECIPIENTS }}
          FROM_EMAIL: ${{ secrets.FROM_EMAIL }}
          SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}
          SLACK_CHANNELS: ${{ secrets.SLACK_CHANNELS }}
          FIREFLIES_API_KEY: ${{ secrets.FIREFLIES_API_KEY }}
```

### Option 3: AWS Lambda + EventBridge

```bash
# Package for Lambda
pip install -r requirements.txt -t package/
cp followup_agent.py package/
cd package && zip -r ../deployment.zip .
```

Create Lambda function and EventBridge rule: `cron(0 9 * * ? *)`

## Customization

All customization is done through environment variables in your `.env` file:

### Finding HubSpot Deal Stage IDs (Required)

The `TARGET_STAGES` environment variable is **required**. To find your deal stage IDs:

1. Go to **HubSpot Settings → Objects → Deals → Pipelines**
2. Click on a pipeline to see its stages
3. Click on a stage name - the URL will show the stage ID (e.g., `...dealstage/12345678`)
4. Alternatively, some stages use text IDs like `appointmentscheduled` or `qualifiedtobuy`

```bash
# Example with numeric and text stage IDs
TARGET_STAGES=1930059496,appointmentscheduled,qualifiedtobuy,1647352507
```

> **Tip**: You can also find stage IDs by inspecting the network requests in your browser's developer tools when viewing deals in HubSpot.

### Change Stale Threshold

```bash
STALE_THRESHOLD_DAYS=7  # For weekly follow-ups
```

### Change Slack Channels

```bash
SLACK_CHANNELS=sales,marketing,support,deals
```

### Add/Remove Digest Recipients

```bash
DIGEST_RECIPIENTS=user1@example.com,user2@example.com,team@example.com
```

### Customize AI Prompts (Required)

⚠️ **Important**: The default prompts are configured for a specific product (Adopt AI). You **must** customize these for your own product/service.

#### 1. Email Generation Prompt

Edit the `generate_followup_email()` function in `followup_agent.py` (~line 600). Update:

- **Role & Purpose**: Change the AI's role description to match your sales context
- **Product Capabilities**: Replace the "Current Capabilities" section with your product's features
- **Email Scenarios**: Adjust the email templates for your typical sales situations
- **Tone Guidelines**: Modify to match your brand voice

```python
# Look for this section in generate_followup_email():
prompt = f"""You are a senior sales development representative...

# Update the "Current Capabilities" section:
## Current [Your Product] Capabilities
- Feature 1: Description
- Feature 2: Description
...
```

#### 2. Web Search Prompt

Edit the `search_company_news()` function in `followup_agent.py` (~line 550). Update the search query to focus on signals relevant to your product:

```python
# Current prompt searches for AI-related news
# Change to match your product's value proposition:
messages=[{
    "role": "user", 
    "content": f"Search for recent news about {company_name} related to [your relevant topics]. "
               f"Focus on: [signals that indicate buying intent for your product]..."
}]
```

#### 3. What to Customize

| Section | What to Change |
|---------|----------------|
| Product name | Replace "Adopt AI" with your product |
| Capabilities | List your product's features and benefits |
| Use cases | Describe how customers use your product |
| Search topics | What news signals buying intent for you? |
| Email tone | Match your brand voice and sales style |
| Talking points | Customize for your typical objections |

## Sample Output

The daily digest email includes:

- 📊 Total deals needing follow-up
- For each deal:
  - Deal name and pipeline stage
  - Contact name and email
  - Company name and days since last contact
  - 🔍 **Research Summary**:
    - Situation overview
    - Problems/blockers identified
    - Call insights (from Fireflies)
    - Internal insights (from Slack)
    - Web intelligence (company news)
    - Applicable product capabilities
  - 📝 **Generated Email** (subject + body)
  - 💡 **Talking Points** for responses
  - ⚠️ **Flags** and recommendations

## Troubleshooting

### No deals found?
- Verify deals exist in the target stages in HubSpot
- Check that the HubSpot token has correct scopes
- Ensure deals have associated contacts

### Emails not sending?
- Verify SendGrid API key or SMTP credentials
- Check spam folder
- Review console output for errors

### Slack/Fireflies not working?
- Verify API tokens are correct
- Check that the bot has access to the channels
- These integrations are optional - the agent works without them

### Web search failing?
- Enable web search in [Anthropic Console](https://console.anthropic.com/settings/organization/features)
- Web search is optional - emails will still generate without it

### Rate limits?
- HubSpot: 100 requests/10 seconds
- Anthropic: Check your plan limits
- Add delays if processing many deals

### Knowledge base not working?
- Run `python index_knowledge_base.py` to create the index
- Ensure documents exist in the `docs/` folder
- Check that ChromaDB is installed: `pip install chromadb sentence-transformers`

### Apollo enrichment failing?
- Verify your API key is correct
- Check Apollo API limits
- Apollo is optional - the agent works without it

## Project Structure

```
sales-followup-agent/
├── followup_agent.py        # Follow-up Agent (stale deals) - uses RAG
├── lead_finder_agent.py     # Lead Finder Agent (high-engagement contacts) - uses RAG
├── test_single_deal.py      # Test script for single deal - uses RAG
├── index_knowledge_base.py  # Knowledge base indexer (shared by all agents)
├── requirements.txt         # Python dependencies
├── .env.example             # Environment template
├── .env                     # Your configuration (gitignored)
├── docs/                    # Knowledge base documents (gitignored)
│   ├── capabilities/        # Product features and capabilities
│   ├── use-cases/           # Industry-specific use cases
│   ├── personas/            # Messaging for different job titles
│   └── case-studies/        # Customer success stories
├── .chroma/                 # ChromaDB vector database (gitignored)
└── README.md
```

## License

MIT License - feel free to use and modify for your own sales workflow.

## Contributing

Contributions welcome! Please open an issue or PR.