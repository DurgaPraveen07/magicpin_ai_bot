#!/usr/bin/env python3
"""
Quick start script for Vera Bot
Run: python run.py
"""

import os
import subprocess
import sys

PORT = int(os.getenv("PORT", os.getenv("BOT_PORT", "8080")))
MODEL = os.getenv("BOT_MODEL", "claude-3-5-sonnet-20241022")

def check_api_key():
    """Verify ANTHROPIC_API_KEY is set"""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ERROR: ANTHROPIC_API_KEY environment variable not set")
        print("\nSet it using:")
        if sys.platform == "win32":
            print("  set ANTHROPIC_API_KEY=sk-ant-...")
        else:
            print("  export ANTHROPIC_API_KEY=sk-ant-...")
        print("\nThen run this script again.")
        sys.exit(1)
    print(f"✓ API Key configured: {api_key[:20]}...")

def check_dependencies():
    """Verify required packages installed"""
    required = ["fastapi", "uvicorn", "anthropic", "requests"]
    missing = []
    
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    
    if missing:
        print(f"❌ Missing dependencies: {', '.join(missing)}")
        print(f"\nInstall using:")
        print(f"  pip install -r requirements.txt")
        sys.exit(1)
    print("✓ All dependencies installed")

def main():
    print("\n" + "="*60)
    print("  Vera Bot — Magicpin Merchant AI Assistant")
    print("="*60 + "\n")
    
    print("Pre-flight checks:")
    check_api_key()
    check_dependencies()
    
    print("\n" + "="*60)
    print("  Starting server...")
    print("="*60 + "\n")
    print(f"Server running on: http://localhost:{PORT}")
    print(f"Health check:      curl http://localhost:{PORT}/v1/healthz")
    print(f"Metadata:          curl http://localhost:{PORT}/v1/metadata")
    print(f"Model:             {MODEL}")
    print("Test harness:      python bot_test.py (in another terminal)")
    print("\nPress Ctrl+C to stop\n")
    
    # Run the bot
    try:
        os.environ["PORT"] = str(PORT)
        os.environ.setdefault("BOT_PORT", str(PORT))
        os.environ.setdefault("BOT_MODEL", MODEL)
        subprocess.run([
            sys.executable,
            "-m",
            "uvicorn",
            "bot:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(PORT)
        ], cwd=os.path.dirname(__file__), check=False)
    except KeyboardInterrupt:
        print("\n\nServer stopped.")
        sys.exit(0)

if __name__ == "__main__":
    main()
