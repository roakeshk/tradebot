#!/usr/bin/env python3
# ============================================================
#  tradebot / generate_token_angel.py
#  Angel One session generator.
#
#  ADVANTAGE OVER ZERODHA/FYERS:
#  Angel One uses TOTP (Google Authenticator-style) 2FA.
#  No browser redirect needed — fully automatable.
#  You can run this from a script / cron job each morning.
#
#  Usage:
#    python generate_token_angel.py
#
#  Or automate (cron at 9:00 AM):
#    0 9 * * 1-5 cd /path/to/tradebot && python generate_token_angel.py
#
#  Setup (one-time):
#    1. Open Angel One account at angelone.in
#    2. Go to smartapi.angelone.in → Create App → get API key
#    3. In Angel One mobile app → My Profile → Enable TOTP
#    4. Scan the QR code with any TOTP app to get the base32 secret
#    5. Put the base32 secret in ANGEL_ONE["totp_secret"] in settings.py
# ============================================================

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))


def main():
    try:
        from SmartApi import SmartConnect
        import pyotp
    except ImportError:
        print("Install: pip install smartapi-python pyotp")
        sys.exit(1)

    from config.settings import ANGEL_ONE

    print("\n" + "=" * 60)
    print("ANGEL ONE SESSION GENERATOR")
    print("=" * 60)

    api    = SmartConnect(api_key=ANGEL_ONE["api_key"])
    totp   = pyotp.TOTP(ANGEL_ONE["totp_secret"]).now()
    data   = api.generateSession(ANGEL_ONE["client_id"], ANGEL_ONE["password"], totp)

    if not data["status"]:
        print(f"ERROR: Login failed: {data['message']}")
        sys.exit(1)

    auth_token    = data["data"]["jwtToken"]
    refresh_token = data["data"]["refreshToken"]
    feed_token    = api.getfeedToken()

    # Save tokens
    base = Path(__file__).parent
    (base / ".angel_auth_token").write_text(auth_token)
    (base / ".angel_feed_token").write_text(feed_token)
    (base / ".angel_refresh_token").write_text(refresh_token)

    print(f"\nLogged in as: {ANGEL_ONE['client_id']}")
    print(f"Auth token saved. Valid for today.")

    # Verify with profile
    profile = api.getProfile(refresh_token)
    if profile["status"]:
        name = profile["data"].get("name", "")
        print(f"Profile: {name}")
    print("\nAngel One ready. No browser step needed.")


if __name__ == "__main__":
    main()
