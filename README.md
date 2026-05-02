# Vera — Magicpin Merchant AI Assistant Bot

A sophisticated WhatsApp merchant engagement bot built for the magicpin AI Challenge. Vera helps Indian merchants grow their business through context-aware, compelling Hinglish messaging powered by Claude AI.

## Overview

This submission implements the Vera bot according to the challenge specification. It:

- **Runs as an HTTP API server** with 5 endpoints required by the judge harness
- **Manages 4-layer context**: Category (vertical knowledge), Merchant (business state), Trigger (events), Customer (relationship data)
- **Composes messages using Claude** with category-specific voice rules, loss-aversion triggers, and social proof
- **Handles conversations statefully** through /v1/tick (initiate) and /v1/reply (respond)
- **Applies mandatory style guidelines**: Hinglish tone, concrete facts, single binary CTAs, no hallucinations
- **Implements suppression logic** to avoid message duplication

## Architecture

```
Judge Harness (LLM + Context Injector)
           ↓ HTTP/JSON
    [Vera Bot API Server]
           ↑ HTTP/JSON
```

The bot receives context updates via `/v1/context`, initiates messages via `/v1/tick`, and responds to merchant replies via `/v1/reply`.

## Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/context` | POST | Receive context updates (category, merchant, customer, trigger) |
| `/v1/tick` | POST | Periodic wake-up; bot initiates proactive messages |
| `/v1/reply` | POST | Handle incoming merchant/customer replies |
| `/v1/healthz` | GET | Liveness probe |
| `/v1/metadata` | GET | Bot identity and version info |

## Installation

### Prerequisites
- Python 3.9+
- `ANTHROPIC_API_KEY` environment variable set

### Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Set your API key
export ANTHROPIC_API_KEY="sk-ant-..."  # On Unix/Mac
# OR
set ANTHROPIC_API_KEY=sk-ant-...        # On Windows
```

## Running the Bot

```bash
python bot.py
```

The server starts on `http://localhost:8080` by default and is ready to accept context and messages from the judge harness.

### Health Check

```bash
curl http://localhost:8080/v1/healthz
```

Expected response:
```json
{
  "status": "ok",
  "uptime_seconds": 42,
  "contexts_loaded": {
    "category": 5,
    "merchant": 50,
    "customer": 200,
    "trigger": 25
  }
}
```

## Key Features

### 1. Context Management
- **Versioned storage**: Prevents version conflicts, always stores the highest version
- **Scoped storage**: Separate stores for categories, merchants, customers, triggers
- **Conversation state**: Maintains conversation history per conversation_id

### 2. Message Composition (`VeraComposer`)

The core engine that generates Vera messages:

```python
message = VeraComposer.compose_message(
    merchant_context,
    category_context,
    trigger_context,
    customer_context=None
)
```

**Composition Strategy**:
- Uses Claude with a carefully crafted system prompt
- Injects category-specific voice rules (tone, vocabulary, taboos)
- Anchors on concrete facts from merchant_context
- Applies "compulsion levers": loss aversion, social proof, effort externalization
- Generates JSON response with body, CTA, send_as role, suppression_key, rationale

### 3. Mandatory Style Guidelines (Enforced)

✅ **DO's**:
- Use "Loss Aversion": "6,700 missed searches"
- Use "Social Proof": Peer benchmarks
- Use "Effort Externalization": "I've drafted X, just say go"
- Honor category-specific tone
- Anchor on concrete facts (exact numbers, dates, names)

❌ **DON'Ts**:
- NO generic offers ("Flat 30% off" → use "Haircut @ ₹99")
- NO hallucinated data or fake citations
- NO long preambles
- NO re-introductions if chatted in 24h
- NO multiple CTAs (keep to yes/no)

### 4. Hinglish Support

Messages naturally mix Hindi and English:
- "Shukriya Dr. Meera! 🙌 Sending draft now."
- "Bilkul! Aapke specialty ke liye..."
- Emoticons used for warmth but not overused

### 5. Intent Detection & Reply Logic

On `/v1/reply`, the bot detects intent:
- **Positive** ("Yes", "Haan", "Sure") → Send next step
- **Negative** ("No", "Nahi", "Not interested") → End conversation
- **Delay** ("Later", "Busy") → Wait 30 minutes
- **Auto-reply** ("Thank you for contacting...") → Wait 5 minutes
- **Unclear** → Ask for clarification

## Data Flow Example

### 1. Judge sends context
```bash
POST /v1/context
{
  "scope": "category",
  "context_id": "dentists",
  "version": 1,
  "payload": { "slug": "dentists", "voice": { "tone": "peer_clinical", ... } }
}
```

### 2. Judge sends trigger
```bash
POST /v1/context
{
  "scope": "trigger",
  "context_id": "trg_001_research_digest_dentists",
  "version": 1,
  "payload": { "kind": "research_digest", "category": "dentists", ... }
}
```

### 3. Judge calls tick
```bash
POST /v1/tick
{
  "now": "2026-04-26T10:30:00Z",
  "available_triggers": ["trg_001_research_digest_dentists"]
}
```

**Bot responds**:
```json
{
  "actions": [
    {
      "conversation_id": "conv_abc123",
      "merchant_id": "m_001_drmeera",
      "body": "Dr. Meera, JIDA's Oct issue landed — 3-month fluoride recall cuts caries 38% better than 6-month. Relevant for your high-risk-adult cohort (124 patients). Abstract link: [...].\n\nShould I add this to your patient library?",
      "cta": "binary_yes_no",
      "rationale": "External research digest with clinical anchor for solo practice; merchant has high-risk cohort. Uses social proof (JIDA citation) + specificity (38%, 124 patients)."
    }
  ]
}
```

### 4. Judge simulates merchant reply
```bash
POST /v1/reply
{
  "conversation_id": "conv_abc123",
  "merchant_id": "m_001_drmeera",
  "from_role": "merchant",
  "message": "Yes, add it please",
  "turn_number": 2
}
```

**Bot responds**:
```json
{
  "action": "send",
  "body": "Shukriya Dr. Meera! 🙌 Adding to your library now. Patients will see it in their next reminder. Agar koi doubt ho, just say.",
  "cta": "open_ended",
  "rationale": "Positive response honored; executing action + low-friction follow-on."
}
```

## Suppression Logic

Messages are deduplicated using `suppression_key`:
- Each trigger has a unique key: `"research:dentists:2026-W17"`
- Once a message is sent with this key, subsequent tick calls ignore it
- Prevents spamming merchant with the same research digest

## Evaluation Criteria (Challenge)

The judge evaluates on:

1. **Message Quality**: Specificity, Hinglish tone, no hallucinations, concrete CTAs
2. **Intent Detection**: Correctly interprets merchant replies (yes/no/delay/auto-reply)
3. **Category Awareness**: Uses category voice rules, offer catalog, peer stats
4. **Engagement**: Uses loss aversion, social proof, effort externalization
5. **API Compliance**: Correct response formats, within 30-second timeout
6. **State Management**: Remembers context, handles conversations correctly

## Testing Locally

You can test endpoints manually:

```bash
# Start bot in one terminal
python bot.py

# In another terminal:

# 1. Load category context
curl -X POST http://localhost:5000/v1/context \
  -H "Content-Type: application/json" \
  -d '{
    "scope": "category",
    "context_id": "dentists",
    "version": 1,
    "payload": {
      "slug": "dentists",
      "offer_catalog": [{"title": "Dental Cleaning @ ₹299"}],
      "voice": {"tone": "peer_clinical"}
    }
  }'

# 2. Health check
curl http://localhost:5000/v1/healthz

# 3. Metadata
curl http://localhost:5000/v1/metadata
```

## Code Structure

```
bot.py
├── Configuration & Logging
├── Data Models (BotMessage, TickAction, ReplyAction, etc.)
├── ContextStore (in-memory state management)
├── VeraComposer (Claude-powered message generation)
│   ├── compose_message()
│   ├── _build_system_prompt() [category-specific rules]
│   ├── _build_user_message() [context injection]
│   └── compose_reply() [intent detection]
├── Flask Routes
│   ├── /v1/context [POST]
│   ├── /v1/tick [POST]
│   ├── /v1/reply [POST]
│   ├── /v1/healthz [GET]
│   └── /v1/metadata [GET]
└── Main
```

## Advanced Features

### Auto-Reply Detection
The bot detects WhatsApp Business auto-replies and backs off gracefully instead of processing them:
```python
if "thank you for contacting" in response.lower():
    action = "wait"  # Back off for 5 minutes
```

### Conversation Continuity
All conversation history is maintained in memory and passed to Claude for context-aware replies.

### Performance Metrics
The `/v1/healthz` endpoint tracks:
- Uptime since startup
- Number of contexts loaded per scope
- Useful for judge harness monitoring

## Troubleshooting

### "ANTHROPIC_API_KEY not set"
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
# Restart bot
```

### Bot timeout (>30 seconds on /v1/reply)
- Claude API might be slow
- Check network connectivity
- Consider reducing max_tokens in compose_message()

### Context not stored
- Check `/v1/healthz` to verify contexts_loaded count
- Ensure POST /v1/context returns `"accepted": true`

### Suppression not working
- Verify suppression_key in trigger_context
- Check that store.is_suppressed() is called before adding action

## Submission Notes

This bot is designed to excel in:
1. **Specificity**: Every number, offer, and name is from provided context
2. **Authenticity**: Hinglish naturally mixed, no corporate tone
3. **Psychology**: Loss aversion, social proof, effort externalization
4. **Category Precision**: Different voice for dentists, salons, restaurants
5. **API Reliability**: Idempotent context storage, graceful degradation, proper status codes

## License

Submitted for magicpin AI Challenge 2026.

---

**Questions?** Check the challenge briefs:
- `challenge-brief.md` — Product spec, framework, evaluation rubric
- `challenge-testing-brief.md` — Technical API contract, endpoint specs
