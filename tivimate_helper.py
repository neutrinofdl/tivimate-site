from flask import Flask, request, render_template_string
import requests
import re
import time
import threading

app = Flask(__name__)

# =========================
# CONFIG — only edit this section
# =========================

BASE_URL = "https://alt.sanctumpanel.com"
ADMIN_USERNAME = "mattdrudge"   # 🔐 Panel login username
ADMIN_PASSWORD = "B4-&G>e3509K"   # 🔐 Panel login password
APP_PASSWORD   = "TiviMateAdmin!2026"    # 🔐 Password for /admin page
RESELLER_ID    = 59

# =========================
# SESSION STATE
# =========================

_session = requests.Session()
_session_lock = threading.Lock()
_last_auth_time = 0
AUTH_TTL = 1800  # re-auth after 30 minutes max (tokens usually last longer)

# =========================
# KEEP ALIVE (UPTIMEROBOT)
# =========================

@app.route("/ping")
def ping():
    return "OK"

# =========================
# AUTO AUTHENTICATION
# =========================

def _do_login():
    """
    Full Laravel login flow:
      1. GET /login  → scrape CSRF token from the page, grab XSRF-TOKEN cookie
      2. POST /login → submit credentials, get authenticated laravel_session
    Returns True on success, False on failure.
    """
    global _last_auth_time

    try:
        # Step 1 — load login page to get initial CSRF token
        get_resp = _session.get(f"{BASE_URL}/login", timeout=15)
        get_resp.raise_for_status()

        # Extract _token hidden input (Laravel CSRF)
        token_match = re.search(r'<input[^>]+name=["\']_token["\'][^>]+value=["\']([^"\']+)["\']', get_resp.text)
        if not token_match:
            # Also try the reverse attribute order
            token_match = re.search(r'<input[^>]+value=["\']([^"\']+)["\'][^>]+name=["\']_token["\']', get_resp.text)

        if not token_match:
            print("AUTH ERROR: Could not find _token on login page")
            return False

        csrf_token = token_match.group(1)

        # Step 2 — POST credentials
        post_resp = _session.post(
            f"{BASE_URL}/login",
            data={
                "_token": csrf_token,
                "username": ADMIN_USERNAME,
                "password": ADMIN_PASSWORD,
            },
            timeout=15,
            allow_redirects=True,
        )

        # A successful Laravel login redirects to dashboard (non-login URL)
        if "/login" in post_resp.url:
            print("AUTH ERROR: Still on login page after POST — check credentials")
            return False

        _last_auth_time = time.time()
        print(f"AUTH: Login successful at {time.strftime('%H:%M:%S')}")
        return True

    except Exception as e:
        print(f"AUTH EXCEPTION: {e}")
        return False


def ensure_authed(force=False):
    """
    Call this before any panel request.
    Re-authenticates if:
      - never logged in yet
      - forced (e.g. after a 401/419 response)
      - token TTL has elapsed
    Thread-safe.
    """
    with _session_lock:
        age = time.time() - _last_auth_time
        if force or _last_auth_time == 0 or age > AUTH_TTL:
            return _do_login()
        return True


# Authenticate at startup so the first real request is instant
threading.Thread(target=ensure_authed, daemon=True).start()

# =========================
# CLEAN NAME
# =========================

def extract_name(raw):
    if not raw:
        return ""
    clean = re.sub('<.*?>', '', raw)
    clean = " ".join(clean.strip().split())
    return clean.lower()

# =========================
# GET ALL USERS (PAGINATION + AUTO RETRY)
# =========================

def get_users():
    url = f"{BASE_URL}/lines/data"

    def _fetch(start=0):
        all_users = []
        length = 100

        while True:
            payload = {
                "draw": 1,
                "start": start,
                "length": length,
                "search[value]": "",
                "id": "users",
                "reseller": RESELLER_ID,
            }

            # requests.Session carries cookies automatically — no manual headers needed
            # but the panel also needs the XSRF-TOKEN as a header (Laravel CSRF for AJAX)
            xsrf = _session.cookies.get("XSRF-TOKEN", "")

            r = _session.post(
                url,
                data=payload,
                headers={
                    "accept": "application/json, text/javascript, */*; q=0.01",
                    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "x-csrf-token": requests.utils.unquote(xsrf),  # cookie is URL-encoded
                    "x-requested-with": "XMLHttpRequest",
                },
                timeout=20,
            )

            # 401 or 419 = session expired mid-flight
            if r.status_code in (401, 419):
                return None  # signal caller to re-auth

            if r.status_code != 200:
                print(f"USERS ERROR {r.status_code}: {r.text[:200]}")
                return []

            try:
                data = r.json()
                users = data.get("data", [])
            except Exception as e:
                print(f"USERS JSON ERROR: {e} — {r.text[:200]}")
                return []

            if not users:
                break

            all_users.extend(users)

            if len(users) < length:
                break

            start += length

        return all_users

    # First attempt
    ensure_authed()
    result = _fetch()

    # If session expired mid-request, re-auth once and retry
    if result is None:
        print("Session expired — re-authenticating...")
        if ensure_authed(force=True):
            result = _fetch()
        else:
            result = []

    if result is None:
        result = []

    print(f"Loaded {len(result)} users")
    return result

# =========================
# FIND USER (SAFE MATCHING)
# =========================

def find_user(search, users):
    search = " ".join(search.lower().strip().split())
    parts = search.split()
    matches = []

    for user in users:
        note = extract_name(user.get("admin_notes_show", ""))
        if not note:
            continue

        if len(parts) >= 2:
            if search in note:
                matches.append(user)
        else:
            name_parts = note.split()
            if parts[0] == name_parts[-1]:
                matches.append(user)

    return matches

# =========================
# MAIN PAGE (CUSTOMER)
# =========================

@app.route("/", methods=["GET", "POST"])
def home():
    result = ""

    if request.method == "POST":
        name = request.form.get("name", "").strip()

        users = get_users()

        if not users:
            result = "❌ System error — please try again later"
        else:
            matches = find_user(name, users)

            if not matches:
                result = "❌ No match found"

            elif len(matches) > 1:
                names = [extract_name(u.get("admin_notes_show")) for u in matches[:5]]
                result = "<b>Multiple matches found:</b><br>"
                for n in names:
                    result += f"- {n}<br>"
                result += "<br>⚠️ Please enter your FULL first and last name."

            else:
                u = matches[0]
                username = u.get("username", "")
                password = u.get("password", "")
                exp = u.get("exp_date", "")

                result = f"""
                <h3>Setup Instructions</h3>

                1. Go to settings on your onn homescreen (gear icon very top)<br>
                2. Go to All Settings<br>
                3. Applications → find ZAKTV and/or TiviMate<br>
                4. Uninstall one or both (if present)<br>
                5. Open Downloader app<br>
                6. Enter code: <b>3309760</b><br>
                7. Install and open TiviMate<br>
                8. Click <b>Add Playlist</b><br>
                9. Choose <b>Service-S</b><br><br>

                <b>Username:</b> {username}<br>
                <b>Password:</b> {password}<br>
                <b>Expires:</b> {exp}<br><br>

                If this does not work, please contact support.
                """

    return render_template_string("""
    <h2>TiviMate Support</h2>
    <h4>⚠️ Enter your FULL first and last name</h4>
    <form method="post">
        <input name="name" placeholder="John Smith" required>
        <button type="submit">Lookup</button>
    </form>
    <p>{{result|safe}}</p>
    """, result=result)

# =========================
# ADMIN PAGE — now just shows auth status; no more manual cURL needed
# =========================

@app.route("/admin", methods=["GET", "POST"])
def admin():
    message = ""

    if request.method == "POST":
        password = request.form.get("password", "")

        if password != APP_PASSWORD:
            return "❌ Unauthorized"

        action = request.form.get("action", "")

        if action == "reauth":
            success = ensure_authed(force=True)
            if success:
                message = "✅ Re-authentication successful"
            else:
                message = "❌ Re-authentication failed — check credentials in config"

    # Show current session age
    age = int(time.time() - _last_auth_time) if _last_auth_time else None
    if age is None:
        auth_status = "⚠️ Not authenticated yet"
    elif age < AUTH_TTL:
        mins = age // 60
        auth_status = f"✅ Authenticated ({mins}m ago)"
    else:
        auth_status = f"⚠️ Token may be stale ({age // 60}m ago)"

    return render_template_string("""
    <h2>Admin Panel</h2>

    <p>Auth Status: <b>{{auth_status}}</b></p>

    <form method="post">
        Password:<br>
        <input type="password" name="password"><br><br>
        <input type="hidden" name="action" value="reauth">
        <button type="submit">Force Re-authenticate</button>
    </form>

    <p>{{message}}</p>

    <hr>
    <small>Credentials are set in config at the top of the script. No more cURL paste needed.</small>
    """, auth_status=auth_status, message=message)

# =========================
# RUN (LOCAL / RENDER)
# =========================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050)
