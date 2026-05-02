"""
Vera — magicpin Merchant AI Assistant Bot
Challenge Submission
"""

import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, asdict
from enum import Enum
import anthropic
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import logging

# ============================================================================
# CONFIGURATION & LOGGING
# ============================================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Vera Bot API", version="1.0.0")

# Deployment configuration via environment variables.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = os.getenv("BOT_MODEL", "claude-3-5-sonnet-20241022")
PORT = int(os.getenv("PORT", os.getenv("BOT_PORT", "8080")))

# Claude client for message composition
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else anthropic.Anthropic()

# ============================================================================
# DATA MODELS
# ============================================================================


class ContextBody(BaseModel):
    scope: str
    context_id: str
    version: int = 0
    payload: Dict[str, Any] = Field(default_factory=dict)


class TickBody(BaseModel):
    now: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    available_triggers: List[str] = Field(default_factory=list)


class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: str
    customer_id: Optional[str] = None
    from_role: str
    message: str
    turn_number: int = 0

class CTAType(Enum):
    BINARY_YES_NO = "binary_yes_no"
    OPEN_ENDED = "open_ended"
    NONE = "none"

class SendAsRole(Enum):
    VERA = "vera"
    MERCHANT_ON_BEHALF = "merchant_on_behalf"

class ConversationAction(Enum):
    SEND = "send"
    WAIT = "wait"
    END = "end"

@dataclass
class BotMessage:
    """Response message structure for /v1/tick and /v1/reply"""
    body: str
    cta: str
    send_as: str = "vera"
    suppression_key: Optional[str] = None
    rationale: str = ""

@dataclass
class TickAction:
    """Action returned by bot during /v1/tick"""
    conversation_id: str
    merchant_id: str
    customer_id: Optional[str]
    send_as: str
    trigger_id: str
    template_name: str
    template_params: List[str]
    body: str
    cta: str
    suppression_key: Optional[str]
    rationale: str

@dataclass
class ReplyAction:
    """Action returned by bot during /v1/reply"""
    action: str  # send, wait, end
    body: Optional[str] = None
    cta: Optional[str] = None
    wait_seconds: Optional[int] = None
    rationale: str = ""

# ============================================================================
# CONTEXT STORE (In-Memory State Management)
# ============================================================================

class ContextStore:
    def __init__(self):
        self.categories: Dict[str, Dict] = {}  # slug -> category context
        self.merchants: Dict[str, Dict] = {}   # merchant_id -> merchant context
        self.customers: Dict[str, Dict] = {}   # customer_id -> customer context
        self.triggers: Dict[str, Dict] = {}    # trigger_id -> trigger context
        self.conversations: Dict[str, Dict] = {}  # conversation_id -> conversation state
        self.sent_messages: Dict[str, set] = {}  # suppression_key -> set of conversation_ids (for dedup)
        self.startup_time = datetime.utcnow()

    def store_context(self, scope: str, context_id: str, version: int, payload: Dict) -> tuple[bool, str]:
        """Store context with versioning. Returns (success, message/ack_id)"""
        # Map scope to the correct attribute name
        scope_map = {
            "category": "categories",
            "merchant": "merchants",
            "customer": "customers",
            "trigger": "triggers"
        }
        store_name = scope_map.get(scope)
        if not store_name:
            return False, f"invalid_scope: {scope}"
        
        store = getattr(self, store_name, None)
        if store is None:
            return False, f"invalid_scope: {scope}"
        
        # Check version conflict
        existing = store.get(context_id, {})
        existing_version = existing.get("_version", -1)
        if existing_version > version:
            return False, f"stale_version: current={existing_version}"
        
        # Store with version
        payload["_version"] = version
        store[context_id] = payload
        logger.info(f"Stored {scope}:{context_id} v{version}")
        return True, f"ack_{uuid.uuid4().hex[:8]}"

    def get_merchant_context(self, merchant_id: str) -> Optional[Dict]:
        return self.merchants.get(merchant_id)
    
    def get_category_context(self, category_slug: str) -> Optional[Dict]:
        return self.categories.get(category_slug)
    
    def get_customer_context(self, customer_id: str) -> Optional[Dict]:
        return self.customers.get(customer_id)
    
    def get_trigger_context(self, trigger_id: str) -> Optional[Dict]:
        return self.triggers.get(trigger_id)

    def start_conversation(self, conversation_id: str, merchant_id: str, customer_id: Optional[str], trigger_id: str):
        """Initialize conversation state"""
        self.conversations[conversation_id] = {
            "conversation_id": conversation_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "trigger_id": trigger_id,
            "turns": [],
            "created_at": datetime.utcnow().isoformat(),
            "status": "active"
        }

    def add_turn(self, conversation_id: str, from_role: str, body: str):
        """Add a turn to conversation history"""
        if conversation_id not in self.conversations:
            self.conversations[conversation_id] = {"turns": []}
        
        self.conversations[conversation_id]["turns"].append({
            "from": from_role,
            "body": body,
            "timestamp": datetime.utcnow().isoformat()
        })

    def get_conversation(self, conversation_id: str) -> Optional[Dict]:
        return self.conversations.get(conversation_id)

    def record_suppression(self, suppression_key: str, conversation_id: str):
        """Record that a message was sent for a given suppression key"""
        if suppression_key not in self.sent_messages:
            self.sent_messages[suppression_key] = set()
        self.sent_messages[suppression_key].add(conversation_id)

    def is_suppressed(self, suppression_key: str) -> bool:
        """Check if suppression key was already used"""
        return bool(self.sent_messages.get(suppression_key, set()))

    def get_health_status(self) -> Dict:
        """Get health status for /v1/healthz"""
        uptime = (datetime.utcnow() - self.startup_time).total_seconds()
        return {
            "status": "ok",
            "uptime_seconds": int(uptime),
            "contexts_loaded": {
                "category": len(self.categories),
                "merchant": len(self.merchants),
                "customer": len(self.customers),
                "trigger": len(self.triggers)
            }
        }


# Global context store
store = ContextStore()

# ============================================================================
# MESSAGE COMPOSER (Core AI Logic)
# ============================================================================

class VeraComposer:
    """Message composition engine using Claude for context-aware merchant engagement"""

    @staticmethod
    def compose_message(
        merchant_context: Dict,
        category_context: Dict,
        trigger_context: Dict,
        customer_context: Optional[Dict] = None,
        conversation_history: Optional[List[Dict]] = None
    ) -> BotMessage:
        """
        Main composition function. Uses Claude to generate Vera messages.
        
        Returns: BotMessage with body, cta, send_as, suppression_key, rationale
        """

        # Build context for Claude
        prompt = VeraComposer._build_system_prompt(category_context, customer_context)
        user_message = VeraComposer._build_user_message(
            merchant_context, 
            trigger_context, 
            customer_context,
            conversation_history
        )

        # Call Claude
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=prompt,
            messages=[
                {"role": "user", "content": user_message}
            ]
        )

        # Parse response
        message_text = response.content[0].text
        
        # Extract JSON from Claude's response (it should return structured JSON)
        try:
            # Try to find JSON block
            if "```json" in message_text:
                json_str = message_text.split("```json")[1].split("```")[0].strip()
            elif "{" in message_text:
                json_str = message_text[message_text.find("{"):message_text.rfind("}")+1]
            else:
                json_str = message_text
            
            result = json.loads(json_str)
        except json.JSONDecodeError:
            # Fallback: Claude generated plain text, wrap it
            result = {
                "body": message_text,
                "cta": "open_ended",
                "send_as": "vera",
                "suppression_key": None,
                "rationale": "Fallback response"
            }

        return BotMessage(
            body=result.get("body", ""),
            cta=result.get("cta", "open_ended"),
            send_as=result.get("send_as", "vera"),
            suppression_key=result.get("suppression_key"),
            rationale=result.get("rationale", "")
        )

    @staticmethod
    def _build_system_prompt(category_context: Dict, customer_context: Optional[Dict]) -> str:
        """Build the system prompt with category-specific voice rules"""
        
        category_slug = category_context.get("slug", "unknown")
        voice = category_context.get("voice", {})
        offer_catalog = category_context.get("offer_catalog", [])
        
        tone = voice.get("tone", "professional peer")
        taboos = voice.get("taboos", [])
        allowed_vocab = voice.get("vocab_allowed", [])
        
        offers_str = "\n".join([f"- {o.get('title', '')}" for o in offer_catalog[:3]])
        taboos_str = ", ".join(taboos)
        
        recipient = "customer" if customer_context else "merchant"
        
        system = f"""You are Vera, magicpin's sophisticated {category_slug} AI assistant.

ROLE & CONTEXT
- You are a peer/colleague to {recipient}s, not a salesperson
- {recipient.capitalize()} category: {category_slug}
- Tone: {tone}
- Allowed technical terms: {allowed_vocab if allowed_vocab else 'standard'}
- STRICT TABOOS (never use): {taboos_str if taboos_str else 'none'}

SAMPLE OFFERS (use specific prices, not generic discounts):
{offers_str}

MANDATORY GUIDELINES FOR EVERY MESSAGE
1. Use natural Hinglish (Hindi-English code-mix common in Indian business)
2. Anchor on CONCRETE FACTS: exact numbers, dates, peer benchmarks—never hallucinate data
3. Use loss aversion: mention what they're missing ("6,700 missed searches") or opportunity
4. Use social proof: cite peer performance in same locality
5. Use effort externalization: "I've drafted X, just say go"
6. End with ONE clear binary CTA (yes/no or specific action)

CRITICAL DON'Ts
- DON'T use generic offers if specific service+price available ("Haircut ₹99" beats "10% off")
- DON'T invent citations or fake research data
- DON'T use long preambles ("I hope you are doing well")
- DON'T re-introduce yourself if chatted in last 24 hours
- DON'T offer multiple choices—keep to one simple yes/no
- DON'T hallucinate merchant data not in provided context

RESPONSE FORMAT (JSON)
Return ONLY valid JSON in this format:
{{
  "body": "The exact WhatsApp message text",
  "cta": "binary_yes_no | open_ended | none",
  "send_as": "vera | merchant_on_behalf",
  "suppression_key": "unique_key_for_dedup",
  "rationale": "Why this works (compulsion lever used, audience insight, category-specific anchor)"
}}

LANGUAGE: Use Hinglish naturally. Mix Hindi and English. Be warm but brief.
"""
        return system

    @staticmethod
    def _build_user_message(
        merchant_context: Dict,
        trigger_context: Dict,
        customer_context: Optional[Dict],
        conversation_history: Optional[List[Dict]]
    ) -> str:
        """Build the user message with all context"""
        
        merchant_name = merchant_context.get("identity", {}).get("name", "Merchant")
        owner_name = merchant_context.get("identity", {}).get("owner_first_name", "")
        city = merchant_context.get("identity", {}).get("city", "")
        locality = merchant_context.get("identity", {}).get("locality", "")
        
        performance = merchant_context.get("performance", {})
        views = performance.get("views", 0)
        calls = performance.get("calls", 0)
        ctr = performance.get("ctr", 0)
        
        trigger_kind = trigger_context.get("kind", "unknown")
        trigger_payload = trigger_context.get("payload", {})
        
        customer_name = ""
        if customer_context:
            customer_name = customer_context.get("identity", {}).get("name", "")
        
        history_str = ""
        if conversation_history:
            history_str = "CONVERSATION HISTORY:\n"
            for turn in conversation_history[-3:]:  # Last 3 turns
                role = "Vera" if turn.get("from") == "vera" else "Merchant/Customer"
                history_str += f"- {role}: {turn.get('body', '')}\n"
        
        user_msg = f"""
TRIGGER EVENT
- Kind: {trigger_kind}
- Urgency: {trigger_context.get('urgency', 0)}/5
- Payload: {json.dumps(trigger_payload, indent=2)}

MERCHANT CONTEXT
- Name: {merchant_name} ({owner_name})
- Location: {locality}, {city}
- Views (30d): {views}
- Calls (30d): {calls}
- CTR: {ctr}
- Subscription: {merchant_context.get('subscription', {}).get('status', 'unknown')}
- Recent signals: {', '.join(merchant_context.get('signals', [])[:3])}

OFFERS (use these, not generic discounts):
{json.dumps(merchant_context.get('offers', [])[:3], indent=2)}

{"CUSTOMER CONTEXT\n- Name: " + customer_name + "\n- Relationship: " + str(customer_context.get('relationship', {})) if customer_context else ""}

{history_str}

TASK
Compose ONE message from Vera to {"the merchant" if not customer_context else f"customer {customer_name} on behalf of merchant"}.
Use the specific context above. Focus on the trigger. Be specific, anchored in facts from above context.
Return ONLY the JSON response, no other text.
"""
        return user_msg.strip()

    @staticmethod
    def compose_reply(
        conversation_history: List[Dict],
        merchant_context: Dict,
        category_context: Dict,
        customer_response: str
    ) -> ReplyAction:
        """
        Compose a reply to a merchant/customer message.
        Returns: ReplyAction (send, wait, or end)
        """
        
        # Detect intent from customer message
        response_lower = customer_response.lower()
        
        # Positive signals
        if any(word in response_lower for word in ["yes", "haan", "sure", "ok", "go", "send", "yes please", "bilkul"]):
            action = "send"
            body = VeraComposer._generate_followup_message(merchant_context, category_context, "positive")
            cta = "open_ended"
            rationale = "Positive response from merchant; proceeding with next step"
        
        # Negative/delay signals
        elif any(word in response_lower for word in ["no", "later", "nahi", "time", "wait", "busy"]):
            action = "wait"
            wait_seconds = 1800  # 30 minutes
            rationale = "Merchant needs time; backing off for 30 minutes"
        
        # Auto-reply detection (WhatsApp Business auto-reply)
        elif any(word in response_lower for word in ["thank you for contacting", "thanks for reaching", "will get back", "auto-reply", "automated"]):
            action = "wait"
            wait_seconds = 300  # 5 minutes
            rationale = "Detected auto-reply; backing off briefly"
        
        # Not interested
        elif any(word in response_lower for word in ["not interested", "unsubscribe", "not needed", "stop"]):
            action = "end"
            rationale = "Merchant declined; gracefully exiting"
        
        # Clarification needed
        else:
            action = "send"
            body = "Got it! Can you share a bit more about what you're looking for? 🙏"
            cta = "open_ended"
            rationale = "Unclear response; asking for clarification"
        
        return ReplyAction(
            action=action,
            body=body,
            cta=cta,
            wait_seconds=wait_seconds if action == "wait" else None,
            rationale=rationale
        )

    @staticmethod
    def _generate_followup_message(merchant_context: Dict, category_context: Dict, sentiment: str) -> str:
        """Generate a follow-up message based on sentiment"""
        
        merchant_name = merchant_context.get("identity", {}).get("owner_first_name", "")
        
        if sentiment == "positive":
            return f"Shukriya {merchant_name}! 🙌 Sending the draft now. Check within 5 min. Koi doubt ho toh reply karna. Vera 📲"
        else:
            return f"Samajh gaya {merchant_name}. Anytime you need help, just message. We're here! 💪"

# ============================================================================
# API ROUTES
# ============================================================================

@app.get("/")
def root():
    """Root endpoint - API documentation"""
    return {
        "name": "Vera Bot API",
        "version": "1.0.0",
        "description": "Merchant engagement bot for magicpin AI Challenge",
        "endpoints": {
            "GET /v1/healthz": "Health check",
            "GET /v1/metadata": "Bot metadata and team info",
            "POST /v1/context": "Store context (category, merchant, customer, trigger)",
            "POST /v1/tick": "Initiate proactive messages",
            "POST /v1/reply": "Handle merchant/customer replies"
        },
        "documentation": "http://localhost:8080/docs",
        "redoc": "http://localhost:8080/redoc"
    }


@app.get("/v1/healthz")
def healthz():
    """Liveness probe endpoint"""
    return store.get_health_status()


@app.get("/v1/metadata")
def metadata():
    """Bot identity and submission info"""
    return {
        "team_name": "Vera AI Challenge Submission",
        "team_members": ["Vera AI"],
        "model": MODEL,
        "approach": "Claude-powered context-aware composer with category-specific voice rules, loss-aversion triggers, and social-proof anchoring",
        "contact_email": "vera@magicpin.ai",
        "version": "1.0.0",
        "submitted_at": datetime.utcnow().isoformat() + "Z"
    }


@app.post("/v1/context")
def receive_context(body: ContextBody):
    """Receive context push from judge"""
    scope = body.scope
    context_id = body.context_id
    version = body.version
    payload = body.payload
    
    # Validate
    if scope not in ["category", "merchant", "customer", "trigger"]:
        return JSONResponse(status_code=400, content={
            "accepted": False,
            "reason": "invalid_scope",
            "details": f"scope must be one of [category, merchant, customer, trigger], got: {scope}"
        })
    
    # Store
    success, message = store.store_context(scope, context_id, version, payload)
    
    if not success:
        if "stale_version" in message:
            return JSONResponse(status_code=409, content={
                "accepted": False,
                "reason": "stale_version",
                "current_version": store.merchants.get(context_id, {}).get("_version", -1)
            })
        else:
            return JSONResponse(status_code=400, content={
                "accepted": False,
                "reason": message,
                "details": message
            })
    
    return {
        "accepted": True,
        "ack_id": message,
        "stored_at": datetime.utcnow().isoformat() + "Z"
    }


@app.post("/v1/tick")
def tick(body: TickBody):
    """
    Periodic wake-up. Bot decides whether to initiate messages.
    Returns list of actions to send.
    """
    now = body.now
    available_triggers = body.available_triggers
    
    actions = []
    
    # Iterate through active triggers
    for trigger_id in available_triggers:
        trigger_context = store.get_trigger_context(trigger_id)
        if not trigger_context:
            continue
        
        # Check suppression
        suppression_key = trigger_context.get("suppression_key")
        if suppression_key and store.is_suppressed(suppression_key):
            logger.info(f"Trigger {trigger_id} suppressed (key: {suppression_key})")
            continue
        
        # Get scope and IDs
        scope = trigger_context.get("scope")  # "merchant" or "customer"
        merchant_id = trigger_context.get("merchant_id")
        customer_id = trigger_context.get("customer_id")
        
        # Fetch contexts
        merchant_context = store.get_merchant_context(merchant_id)
        if not merchant_context:
            continue
        
        category_slug = merchant_context.get("category_slug")
        category_context = store.get_category_context(category_slug)
        if not category_context:
            continue
        
        customer_context = None
        if customer_id:
            customer_context = store.get_customer_context(customer_id)
        
        # Compose message
        try:
            message = VeraComposer.compose_message(
                merchant_context,
                category_context,
                trigger_context,
                customer_context
            )
        except Exception as e:
            logger.error(f"Failed to compose message for trigger {trigger_id}: {e}")
            continue
        
        # Create conversation
        conversation_id = f"conv_{uuid.uuid4().hex[:8]}"
        store.start_conversation(conversation_id, merchant_id, customer_id, trigger_id)
        store.add_turn(conversation_id, "vera", message.body)
        
        # Record suppression
        if suppression_key:
            store.record_suppression(suppression_key, conversation_id)
        
        # Build action
        action = TickAction(
            conversation_id=conversation_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            send_as=message.send_as,
            trigger_id=trigger_id,
            template_name=f"vera_{trigger_context.get('kind', 'generic')}_v1",
            template_params=[
                merchant_context.get("identity", {}).get("owner_first_name", ""),
                trigger_context.get("kind", ""),
            ],
            body=message.body,
            cta=message.cta,
            suppression_key=suppression_key,
            rationale=message.rationale
        )
        
        actions.append(action)
    
    # Return actions
    return {
        "actions": [asdict(a) for a in actions]
    }


@app.post("/v1/reply")
def reply(body: ReplyBody):
    """
    Handle incoming reply from merchant/customer.
    Must respond within 30 seconds.
    """
    conversation_id = body.conversation_id
    merchant_id = body.merchant_id
    customer_id = body.customer_id
    from_role = body.from_role  # "merchant" or "customer"
    message_text = body.message
    turn_number = body.turn_number
    
    # Get conversation context
    conversation = store.get_conversation(conversation_id)
    if not conversation:
        return JSONResponse(status_code=200, content={
            "action": "end",
            "rationale": "Conversation not found"
        })
    
    # Add incoming message to history
    store.add_turn(conversation_id, from_role, message_text)
    
    # Get contexts for reply composition
    merchant_context = store.get_merchant_context(merchant_id)
    if not merchant_context:
        return JSONResponse(status_code=200, content={"action": "end", "rationale": "Merchant not found"})
    
    category_slug = merchant_context.get("category_slug")
    category_context = store.get_category_context(category_slug)
    if not category_context:
        return JSONResponse(status_code=200, content={"action": "end", "rationale": "Category not found"})
    
    # Compose reply
    try:
        reply_action = VeraComposer.compose_reply(
            conversation.get("turns", []),
            merchant_context,
            category_context,
            message_text
        )
    except Exception as e:
        logger.error(f"Failed to compose reply: {e}")
        return JSONResponse(status_code=200, content={
            "action": "wait",
            "wait_seconds": 300,
            "rationale": f"Error composing reply: {e}"
        })
    
    # Build response
    response = {
        "action": reply_action.action,
        "rationale": reply_action.rationale
    }
    
    if reply_action.action == "send" and reply_action.body:
        response["body"] = reply_action.body
        response["cta"] = reply_action.cta or "open_ended"
        store.add_turn(conversation_id, "vera", reply_action.body)
    elif reply_action.action == "wait":
        response["wait_seconds"] = reply_action.wait_seconds or 300
    
    return response


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    logger.info("Starting Vera Bot API Server...")
    logger.info(f"Using model: {MODEL}")
    import uvicorn
    uvicorn.run("bot:app", host="0.0.0.0", port=PORT, reload=False)
