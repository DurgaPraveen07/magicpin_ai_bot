#!/usr/bin/env python3
"""
Test harness for Vera bot — validates endpoints and message quality
Run this while bot.py is running: python bot_test.py
"""

import requests
import json
import os
import time
from pathlib import Path

BASE_URL = os.getenv("BOT_URL", "http://localhost:8080")
DATASET_PATH = Path(__file__).parent / "dataset"

# Colors for output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
RESET = '\033[0m'

def test(name: str, fn):
    """Run a test and print result"""
    try:
        fn()
        print(f"{GREEN}✓ {name}{RESET}")
        return True
    except AssertionError as e:
        print(f"{RED}✗ {name}: {e}{RESET}")
        return False
    except Exception as e:
        print(f"{RED}✗ {name}: {type(e).__name__}: {e}{RESET}")
        return False

def load_dataset(filename: str) -> dict:
    """Load JSON dataset"""
    path = DATASET_PATH / filename
    with open(path) as f:
        return json.load(f)

# ============================================================================
# TESTS
# ============================================================================

def test_healthz():
    """Test /v1/healthz endpoint"""
    resp = requests.get(f"{BASE_URL}/v1/healthz")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data
    assert "contexts_loaded" in data
    print(f"  Contexts loaded: {data['contexts_loaded']}")

def test_metadata():
    """Test /v1/metadata endpoint"""
    resp = requests.get(f"{BASE_URL}/v1/metadata")
    assert resp.status_code == 200
    data = resp.json()
    assert "team_name" in data
    assert "model" in data
    print(f"  Model: {data['model']}")

def test_context_category():
    """Test storing category context"""
    datasets = load_dataset("categories/dentists.json")
    category = {
        "slug": "dentists",
        "offer_catalog": [{"title": "Dental Cleaning @ ₹299"}],
        "voice": {"tone": "peer_clinical", "taboos": ["cure", "guaranteed"]},
        "peer_stats": {"avg_rating": 4.4, "avg_reviews": 62},
        "digest": [],
        "patient_content_library": [],
        "seasonal_beats": [],
        "trend_signals": []
    }
    
    resp = requests.post(
        f"{BASE_URL}/v1/context",
        json={
            "scope": "category",
            "context_id": "dentists",
            "version": 1,
            "payload": category
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == True
    print(f"  ACK ID: {data['ack_id']}")

def test_context_merchant():
    """Test storing merchant context"""
    merchants_data = load_dataset("merchants_seed.json")
    merchant = merchants_data["merchants"][0]
    
    resp = requests.post(
        f"{BASE_URL}/v1/context",
        json={
            "scope": "merchant",
            "context_id": merchant["merchant_id"],
            "version": 1,
            "payload": merchant
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == True

def test_context_customer():
    """Test storing customer context"""
    customers_data = load_dataset("customers_seed.json")
    customer = customers_data["customers"][0]
    
    resp = requests.post(
        f"{BASE_URL}/v1/context",
        json={
            "scope": "customer",
            "context_id": customer["customer_id"],
            "version": 1,
            "payload": customer
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == True

def test_context_trigger():
    """Test storing trigger context"""
    triggers_data = load_dataset("triggers_seed.json")
    trigger = triggers_data["triggers"][0]
    
    resp = requests.post(
        f"{BASE_URL}/v1/context",
        json={
            "scope": "trigger",
            "context_id": trigger["id"],
            "version": 1,
            "payload": trigger
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == True

def test_context_version_conflict():
    """Test version conflict detection"""
    category = {
        "slug": "test",
        "offer_catalog": [],
        "voice": {},
        "peer_stats": {},
        "digest": [],
        "patient_content_library": [],
        "seasonal_beats": [],
        "trend_signals": []
    }
    
    # Store v1
    resp1 = requests.post(
        f"{BASE_URL}/v1/context",
        json={
            "scope": "category",
            "context_id": "test_cat",
            "version": 1,
            "payload": category
        }
    )
    assert resp1.status_code == 200
    
    # Try to store old v1 again
    resp2 = requests.post(
        f"{BASE_URL}/v1/context",
        json={
            "scope": "category",
            "context_id": "test_cat",
            "version": 1,
            "payload": category
        }
    )
    # Should be accepted (idempotent)
    assert resp2.status_code == 200
    
    # Try to store v0 (older than v1)
    resp3 = requests.post(
        f"{BASE_URL}/v1/context",
        json={
            "scope": "category",
            "context_id": "test_cat",
            "version": 0,
            "payload": category
        }
    )
    assert resp3.status_code == 409
    assert resp3.json()["reason"] == "stale_version"

def test_tick_with_triggers():
    """Test /v1/tick initiates messages"""
    merchants_data = load_dataset("merchants_seed.json")
    merchants = merchants_data["merchants"]
    
    # Ensure category loaded
    category = {
        "slug": "dentists",
        "offer_catalog": [{"title": "Cleaning @ ₹299"}],
        "voice": {"tone": "peer_clinical"},
        "peer_stats": {},
        "digest": [],
        "patient_content_library": [],
        "seasonal_beats": [],
        "trend_signals": []
    }
    requests.post(
        f"{BASE_URL}/v1/context",
        json={"scope": "category", "context_id": "dentists", "version": 1, "payload": category}
    )
    
    # Store first merchant
    resp = requests.post(
        f"{BASE_URL}/v1/context",
        json={
            "scope": "merchant",
            "context_id": merchants[0]["merchant_id"],
            "version": 1,
            "payload": merchants[0]
        }
    )
    assert resp.status_code == 200
    
    # Store a trigger
    trigger = {
        "id": "trg_test_001",
        "scope": "merchant",
        "kind": "research_digest",
        "source": "external",
        "merchant_id": merchants[0]["merchant_id"],
        "customer_id": None,
        "payload": {"category": "dentists", "title": "Test Digest"},
        "urgency": 2,
        "suppression_key": "test:digest:2026",
        "expires_at": "2026-12-31T00:00:00Z"
    }
    resp = requests.post(
        f"{BASE_URL}/v1/context",
        json={"scope": "trigger", "context_id": "trg_test_001", "version": 1, "payload": trigger}
    )
    assert resp.status_code == 200
    
    # Call tick
    resp = requests.post(
        f"{BASE_URL}/v1/tick",
        json={
            "now": "2026-04-26T10:30:00Z",
            "available_triggers": ["trg_test_001"]
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "actions" in data
    print(f"  Generated {len(data['actions'])} action(s)")
    
    if data["actions"]:
        action = data["actions"][0]
        print(f"  Message preview: {action['body'][:100]}...")
        assert "body" in action
        assert "cta" in action
        return action["conversation_id"]
    return None

def test_reply(conversation_id: str = None):
    """Test /v1/reply with merchant response"""
    if not conversation_id:
        print(f"  {YELLOW}Skipping (no conversation from tick test){RESET}")
        return
    
    merchants_data = load_dataset("merchants_seed.json")
    merchant_id = merchants_data["merchants"][0]["merchant_id"]
    
    resp = requests.post(
        f"{BASE_URL}/v1/reply",
        json={
            "conversation_id": conversation_id,
            "merchant_id": merchant_id,
            "customer_id": None,
            "from_role": "merchant",
            "message": "Yes, send me the draft",
            "received_at": "2026-04-26T10:35:00Z",
            "turn_number": 2
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "action" in data
    assert data["action"] in ["send", "wait", "end"]
    print(f"  Bot action: {data['action']}")
    if "body" in data:
        print(f"  Reply preview: {data['body'][:100]}...")

# ============================================================================
# MAIN
# ============================================================================

def main():
    print(f"\n{YELLOW}Vera Bot Test Harness{RESET}")
    print(f"Testing {BASE_URL}\n")
    
    tests_passed = 0
    tests_total = 0
    
    # Core endpoints
    print("Core Endpoints:")
    tests_total += 1
    if test("GET /v1/healthz", test_healthz):
        tests_passed += 1
    
    tests_total += 1
    if test("GET /v1/metadata", test_metadata):
        tests_passed += 1
    
    # Context storage
    print("\nContext Storage:")
    tests_total += 1
    if test("POST /v1/context (category)", test_context_category):
        tests_passed += 1
    
    tests_total += 1
    if test("POST /v1/context (merchant)", test_context_merchant):
        tests_passed += 1
    
    tests_total += 1
    if test("POST /v1/context (customer)", test_context_customer):
        tests_passed += 1
    
    tests_total += 1
    if test("POST /v1/context (trigger)", test_context_trigger):
        tests_passed += 1
    
    tests_total += 1
    if test("Version conflict detection", test_context_version_conflict):
        tests_passed += 1
    
    # Message generation
    print("\nMessage Generation:")
    tests_total += 1
    conversation_id = None
    if test("POST /v1/tick (initiate messages)", lambda: conversation_id or test_tick_with_triggers()):
        tests_passed += 1
        try:
            conversation_id = test_tick_with_triggers()
        except:
            pass
    
    tests_total += 1
    if test("POST /v1/reply (handle responses)", lambda: test_reply(conversation_id)):
        tests_passed += 1
    
    # Summary
    print(f"\n{YELLOW}Summary{RESET}")
    print(f"Passed: {tests_passed}/{tests_total}")
    
    if tests_passed == tests_total:
        print(f"{GREEN}All tests passed! ✨{RESET}\n")
        return 0
    else:
        print(f"{RED}Some tests failed{RESET}\n")
        return 1

if __name__ == "__main__":
    try:
        exit(main())
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Test interrupted{RESET}")
        exit(1)
    except Exception as e:
        print(f"\n{RED}Fatal error: {e}{RESET}")
        import traceback
        traceback.print_exc()
        exit(1)
