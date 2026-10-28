import os
import time
import base64
import hmac
import hashlib
import struct
import json
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

def automate_kite_login(api_key, api_secret, user_id, password, totp_secret=None):
    """
    Runs Playwright in headless mode to log in to Zerodha Kite and exchange
    the request token for a daily access token.
    Supports both automated TOTP generation and manual dashboard input.
    """
    print(f"Starting Zerodha Kite Auto-Login for user {user_id}...")
    write_kite_status("INITIALIZING", "Launching browser...")
    
    # Delete stale TOTP files
    if os.path.exists(TOTP_CODE_PATH):
        os.remove(TOTP_CODE_PATH)
        
    kite = KiteConnect(api_key=api_key)
    login_url = kite.login_url()
    
    access_token = None
    
    with sync_playwright() as p:
        # Launch Chromium headless
        browser = p.chromium.launch(headless=True, args=['--disable-http2', '--no-sandbox', '--disable-setuid-sandbox'])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        try:
            print(f"Navigating to login URL: {login_url}")
            page.goto(login_url, timeout=30000)
            page.wait_for_load_state("networkidle")
            
            # 1. Enter Credentials
            print("Entering User ID and Password...")
            page.fill("input#userid", user_id)
            page.fill("input#password", password)
            page.click("button[type='submit']")
            
            time.sleep(2)
            
            # 2. Handle 2FA (TOTP)
            totp_token = None
            if totp_secret and totp_secret.strip():
                print("Generating TOTP token programmatically...")
                totp_token = generate_totp(totp_secret)
                
            if not totp_token:
                # Manual entry required
                print("No TOTP secret configured or generation failed. Awaiting manual input from dashboard...")
                write_kite_status("AWAITING_TOTP", "Please enter the 6-digit TOTP code from your Kite app on the dashboard.")
                
                # Poll for up to 60 seconds waiting for user input
                start_wait = time.time()
                while time.time() - start_wait < 60:
                    if os.path.exists(TOTP_CODE_PATH):
                        with open(TOTP_CODE_PATH, 'r') as f:
                            totp_token = f.read().strip()
                        os.remove(TOTP_CODE_PATH)
                        print(f"Received manual TOTP token: {totp_token}")
                        break
                    time.sleep(1.0)
                    
                if not totp_token:
                    raise TimeoutError("Awaiting TOTP token timed out after 60 seconds.")
                    
            print(f"Entering TOTP code...")
            write_kite_status("LOGGING_IN", "Submitting 2FA TOTP...")
            page.wait_for_selector("input", timeout=10000)
            totp_input = page.locator("input[maxlength='6'], input[type='number'], input[type='text']").first
            totp_input.fill(totp_token)
            
            # Since Zerodha TOTP form auto-submits on 6-digit input, the submit button
            # might become disabled or the page might start redirecting immediately.
            # We try to click it but catch any error/timeout.
            try:
                page.click("button[type='submit']", timeout=1500)
            except Exception:
                pass
            
            # Wait for redirect to happen
            print("Waiting for redirect with request_token...")
            page.wait_for_url(lambda url: "request_token" in url, timeout=20000, wait_until="commit")
            
            # Extract request_token from URL
            redirect_url = page.url
            print(f"Redirected URL: {redirect_url}")
            
            # Extract request_token query param
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(redirect_url)
            params = parse_qs(parsed.query)
            request_tokens = params.get('request_token')
            
            if not request_tokens:
                raise ValueError("request_token parameter not found in redirect URL.")
                
            request_token = request_tokens[0]
            print(f"Kite Request Token extracted: {request_token}")
            
            # 3. Generate Session
            write_kite_status("GENERATING_SESSION", "Exchanging request token...")
            session_data = kite.generate_session(request_token, api_secret=api_secret)
            access_token = session_data["access_token"]
            print("SUCCESS: Daily access token generated successfully!")
            write_kite_status("CONNECTED", "Successfully authenticated.")
            
        except Exception as e:
            print(f"Login failed: {e}")
            try:
                print(f"Current page URL: {page.url}")
            except Exception as ue:
                print(f"Failed to get page URL: {ue}")
            try:
                page.screenshot(path=os.path.join(SCRATCH_DIR, "login_error.png"))
                print(f"Saved failure screenshot to: {os.path.join(SCRATCH_DIR, 'login_error.png')}")
            except Exception as se:
                print(f"Failed to capture screenshot: {se}")
            write_kite_status("FAILED", f"Authentication failed: {str(e)}")
            raise e
        finally:
            browser.close()
            
    return access_token


