#!/usr/bin/env python3
# ============================================================
#  tradebot / generate_token.py
#  Run this once per trading day to generate Zerodha access token.
#  The token expires daily at midnight.
#
#  Usage:
#    python generate_token.py
#
#  What it does:
#    1. Opens the Zerodha login URL in your browser
#    2. After login, you're redirected to a URL with ?request_token=...
#    3. Paste that URL here — the script extracts the token
#    4. Saves access_token to .zerodha_token file
# ============================================================

import sys
import webbrowser
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import ZERODHA


def main():
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        print("Install kiteconnect: pip install kiteconnect")
        sys.exit(1)

    kite = KiteConnect(api_key=ZERODHA["api_key"])
    login_url = kite.login_url()

    print("\n" + "=" * 60)
    print("ZERODHA TOKEN GENERATOR")
    print("=" * 60)
    print(f"\n1. Opening login URL in your browser...")
    webbrowser.open(login_url)
    print(f"   URL: {login_url}")
    print("\n2. Login with your Zerodha credentials + 2FA")
    print("\n3. After login, you'll be redirected to a URL like:")
    print("   http://127.0.0.1/?request_token=XXXXXXXXXX&action=login&status=success")
    print("\n4. Paste the FULL redirect URL below:")

    redirect_url = input("\nRedirect URL: ").strip()

    # Extract request_token from URL
    import urllib.parse
    params = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_url).query)
    request_token = params.get("request_token", [None])[0]

    if not request_token:
        print("ERROR: Could not extract request_token from URL.")
        sys.exit(1)

    # Generate session
    session = kite.generate_session(request_token, api_secret=ZERODHA["api_secret"])
    access_token = session["access_token"]

    # Save token
    token_file = Path(__file__).parent / ".zerodha_token"
    token_file.write_text(access_token)
    print(f"\nAccess token saved to: {token_file}")

    # Verify
    kite.set_access_token(access_token)
    profile = kite.profile()
    print(f"Logged in as: {profile['user_name']} ({profile['user_id']})")
    print("\nToken is valid for today. Run this script again tomorrow morning.")


if __name__ == "__main__":
    main()
