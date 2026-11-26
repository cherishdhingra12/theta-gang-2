import os
import time
import base64
import hmac
import hashlib
import struct
import json
from urllib.parse import urlparse, parse_qs, urljoin
import requests
from playwright.sync_api import sync_playwright
from kiteconnect import KiteConnect

SCRATCH_DIR = os.path.join(os.path.dirname(__file__), "scratch")
STATUS_PATH = os.path.join(SCRATCH_DIR, "kite_status.json")
TOTP_CODE_PATH = os.path.join(SCRATCH_DIR, "totp_code.txt")

def generate_totp(secret):
    """
    Generates a standard RFC 6238 TOTP 6-digit token using base32 secret.
    """
    try:
        # Normalize padding
        secret = secret.replace(" ", "")
        missing_padding = len(secret) % 8
        if missing_padding:
            secret += "=" * (8 - missing_padding)
            
        key = base64.b32decode(secret, casefold=True)
        t = int(time.time() / 30)
        msg = struct.pack(">Q", t)
        hmac_hash = hmac.new(key, msg, hashlib.sha1).digest()
        
        offset = hmac_hash[-1] & 0x0f
        code = ((hmac_hash[offset] & 0x7f) << 24 |
                (hmac_hash[offset+1] & 0xff) << 16 |
                (hmac_hash[offset+2] & 0xff) << 8 |
                (hmac_hash[offset+3] & 0xff))
        return f"{code % 10**6:06d}"
    except Exception as e:
        print(f"Error generating TOTP token: {e}")
        return None

def write_kite_status(status, message=""):
    os.makedirs(SCRATCH_DIR, exist_ok=True)
    with open(STATUS_PATH, 'w') as f:
        json.dump({"status": status, "message": message, "timestamp": time.time()}, f)

def _resolve_totp_token(totp_secret=None):
    """Resolve 6-digit TOTP automatically from secret or poll up to 60s for manual dashboard input."""
    if totp_secret and totp_secret.strip():
        print("Generating TOTP token programmatically...")
        token = generate_totp(totp_secret)
        if token:
            return token

    print("No TOTP secret configured or generation failed. Awaiting manual input from dashboard...")
    write_kite_status("AWAITING_TOTP", "Please enter the 6-digit TOTP code from your Kite app on the dashboard.")

    start_wait = time.time()
    while time.time() - start_wait < 60:
        if os.path.exists(TOTP_CODE_PATH):
            with open(TOTP_CODE_PATH, 'r') as f:
                token = f.read().strip()
            os.remove(TOTP_CODE_PATH)
            print("Received manual TOTP token.")
            return token
        time.sleep(1.0)

    raise TimeoutError("Awaiting TOTP token timed out after 60 seconds.")

def _extract_request_token_from_url(url):
    parsed = urlparse(url)
    tokens = parse_qs(parsed.query).get("request_token")
    return tokens[0] if tokens else None

def _automate_kite_login_http(api_key, api_secret, user_id, password, totp_secret=None):
    """
    Lightweight (~5 MB RAM) direct HTTP Kite login without Chromium.
    Follows Kite login + twofa API endpoints and captures request_token from redirects.
    """
    write_kite_status("INITIALIZING", "Authenticating via lightweight HTTP session...")
    kite = KiteConnect(api_key=api_key)
    login_url = kite.login_url()

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "X-Kite-Version": "3"
    })

    # Step 1: Prime session cookies & extract sess_id from login redirect
    resp = session.get(login_url, timeout=15, allow_redirects=True)
    if resp.status_code >= 400:
        raise RuntimeError(f"Kite login page returned HTTP {resp.status_code}")

    sess_id = parse_qs(urlparse(resp.url).query).get("sess_id", [""])[0]

    # Step 2: Submit user_id + password
    login_res = session.post(
        "https://kite.zerodha.com/api/login",
        data={"user_id": user_id, "password": password},
        timeout=15
    )
    login_json = login_res.json()
    if login_json.get("status") != "success":
        raise RuntimeError(f"HTTP /api/login failed: {login_json.get('message', login_json)}")
    request_id = login_json["data"]["request_id"]

    # Step 3: Submit TOTP 2FA
    totp_token = _resolve_totp_token(totp_secret)
    write_kite_status("LOGGING_IN", "Submitting 2FA TOTP (HTTP)...")
    twofa_res = session.post(
        "https://kite.zerodha.com/api/twofa",
        data={
            "user_id": user_id,
            "request_id": request_id,
            "twofa_value": totp_token,
            "twofa_type": "totp",
            "skip_session": ""
        },
        timeout=15
    )
    twofa_json = twofa_res.json()
    if twofa_json.get("status") != "success":
        raise RuntimeError(f"HTTP /api/twofa failed: {twofa_json.get('message', twofa_json)}")

    # Step 4: Follow finish/connect redirects manually until request_token appears
    finish_url = f"https://kite.zerodha.com/connect/finish?api_key={api_key}&sess_id={sess_id}" if sess_id else login_url
    next_url = finish_url
    request_token = None
    for _ in range(6):
        request_token = _extract_request_token_from_url(next_url)
        if request_token:
            break
        hop = session.get(next_url, timeout=15, allow_redirects=False)
        loc = hop.headers.get("Location")
        if not loc:
            request_token = _extract_request_token_from_url(hop.url)
            break
        next_url = urljoin(next_url, loc)
        request_token = _extract_request_token_from_url(next_url)
        if request_token:
            break

    if not request_token:
        raise ValueError("request_token not found in HTTP login redirect chain.")

    write_kite_status("GENERATING_SESSION", "Exchanging request token...")
    session_data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = session_data["access_token"]
    print("SUCCESS: Daily access token generated via lightweight HTTP auth!")
    write_kite_status("CONNECTED", "Successfully authenticated.")
    return access_token

def _automate_kite_login_playwright(api_key, api_secret, user_id, password, totp_secret=None):
    """Fallback Playwright Chromium login if direct HTTP flow is challenged or changes."""
    write_kite_status("INITIALIZING", "Launching fallback browser...")
    kite = KiteConnect(api_key=api_key)
    login_url = kite.login_url()
    access_token = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--disable-http2', '--no-sandbox', '--disable-setuid-sandbox'])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        try:
            print(f"Navigating to login URL: {login_url}")
            page.goto(login_url, timeout=30000)
            page.wait_for_load_state("networkidle")

            print("Entering User ID and Password...")
            page.fill("input#userid", user_id)
            page.fill("input#password", password)
            page.click("button[type='submit']")
            time.sleep(2)

            totp_token = _resolve_totp_token(totp_secret)
            print("Entering TOTP code...")
            write_kite_status("LOGGING_IN", "Submitting 2FA TOTP...")
            page.wait_for_selector("input", timeout=10000)
            totp_input = page.locator("input[maxlength='6'], input[type='number'], input[type='text']").first
            totp_input.fill(totp_token)

            try:
                page.click("button[type='submit']", timeout=1500)
            except Exception:
                pass

            print("Waiting for redirect with request_token...")
            page.wait_for_url(lambda url: "request_token" in url, timeout=20000, wait_until="commit")
            request_token = _extract_request_token_from_url(page.url)
            if not request_token:
                raise ValueError("request_token parameter not found in redirect URL.")

            write_kite_status("GENERATING_SESSION", "Exchanging request token...")
            session_data = kite.generate_session(request_token, api_secret=api_secret)
            access_token = session_data["access_token"]
            print("SUCCESS: Daily access token generated via Playwright fallback!")
            write_kite_status("CONNECTED", "Successfully authenticated.")
        except Exception as e:
            print(f"Login failed: {e}")
            try:
                page.screenshot(path=os.path.join(SCRATCH_DIR, "login_error.png"))
            except Exception:
                pass
            write_kite_status("FAILED", f"Authentication failed: {str(e)}")
            raise e
        finally:
            browser.close()

    return access_token

def automate_kite_login(api_key, api_secret, user_id, password, totp_secret=None):
    """
    Logs in to Zerodha Kite and returns a daily access_token.
    Uses lightweight direct HTTP session first (~5 MB RAM) with Playwright fallback.
    """
    print(f"Starting Zerodha Kite Auto-Login for user {user_id}...")
    if os.path.exists(TOTP_CODE_PATH):
        os.remove(TOTP_CODE_PATH)

    try:
        return _automate_kite_login_http(api_key, api_secret, user_id, password, totp_secret=totp_secret)
    except Exception as http_err:
        print(f"Lightweight HTTP Kite auth failed ({http_err}); falling back to Playwright...")
        return _automate_kite_login_playwright(api_key, api_secret, user_id, password, totp_secret=totp_secret)
