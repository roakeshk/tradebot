#!/usr/bin/env python3
# ============================================================
#  tradebot / generate_token_fyers.py
#  Run once each morning before market opens.
#  Saves access token to .fyers_token file.
#
#  Usage:
#    python generate_token_fyers.py
# ============================================================

import sys, webbrowser
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from config.settings import FYERS


def main():
    try:
        from fyers_apiv3 import fyersModel
    except ImportError:
        print("Install: pip install fyers-apiv3")
        sys.exit(1)

    session = fyersModel.SessionModel(
        client_id=FYERS["client_id"],
        secret_key=FYERS["secret_key"],
        redirect_uri=FYERS["redirect_uri"],
        response_type="code",
        grant_type="authorization_code",
    )

    auth_url = session.generate_authcode()
    print("\n" + "=" * 60)
    print("FYERS TOKEN GENERATOR")
    print("=" * 60)
    print("\n1. Opening Fyers login in your browser...")
    webbrowser.open(auth_url)
    print(f"   URL: {auth_url}")
    print("\n2. Login with your Fyers credentials")
    print("\n3. After login you'll be redirected. Copy the FULL redirect URL:")
    print("   https://127.0.0.1/?state=fyers&code=XXXXXXXXXXXXXXXX")

    redirect = input("\nPaste redirect URL: ").strip()
    import urllib.parse
    auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(redirect).query).get("code", [None])[0]
    if not auth_code:
        print("ERROR: Could not extract auth code.")
        sys.exit(1)

    session.set_token(auth_code)
    result = session.generate_token()
    access_token = result.get("access_token")
    if not access_token:
        print(f"ERROR: Token generation failed: {result}")
        sys.exit(1)

    token_file = Path(__file__).parent / ".fyers_token"
    token_file.write_text(access_token)
    print(f"\nToken saved to: {token_file}")
    print("Valid for today. Run again tomorrow morning.")


if __name__ == "__main__":
    main()
