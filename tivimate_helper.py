from flask import Flask, request, render_template_string
import requests
import re
import time
import threading

app = Flask(__name__)

# =========================
# CONFIG
# =========================

BASE_URL       = "https://alt.sanctumpanel.com"
ADMIN_USERNAME = "mattdrudge"
ADMIN_PASSWORD = "TestPass2026"
APP_PASSWORD   = "TiviMateAdmin!2026"
RESELLER_ID    = 59

# =========================
# SESSION STATE
# =========================

_session = requests.Session()
_session_lock = threading.Lock()
_last_auth_time = 0
AUTH_TTL = 1800

COLUMNS_PAYLOAD = (
    "columns[0][data]=id&columns[0][name]=id&columns[0][searchable]=true&columns[0][orderable]=true&columns[0][search][value]=&columns[0][search][regex]=false"
    "&columns[1][data]=expired&columns[1][name]=username&columns[1][searchable]=true&columns[1][orderable]=true&columns[1][search][value]=&columns[1][search][regex]=false"
    "&columns[2][data]=password&columns[2][name]=password&columns[2][searchable]=true&columns[2][orderable]=true&columns[2][search][value]=&columns[2][search][regex]=false"
    "&columns[3][data]=exp_date_show&columns[3][name]=users.exp_date&columns[3][searchable]=true&columns[3][orderable]=true&columns[3][search][value]=&columns[3][search][regex]=false"
    "&columns[4][data]=admin_notes_show&columns[4][name]=reseller_notes&columns[4][searchable]=true&columns[4][orderable]=true&columns[4][search][value]=&columns[4][search][regex]=false"
    "&columns[5][data]=speed&columns[5][name]=speed&columns[5][searchable]=false&columns[5][orderable]=false&columns[5][search][value]=&columns[5][search][regex]=false"
    "&columns[6][data]=connections&columns[6][name]=active_connections&columns[6][searchable]=false&columns[6][orderable]=true&columns[6][search][value]=&columns[6][search][regex]=false"
    "&columns[7][data]=display_name&columns[7][name]=streams.stream_display_name&columns[7][searchable]=true&columns[7][orderable]=true&columns[7][search][value]=&columns[7][search][regex]=false"
    "&columns[8][data]=watch_ip_show&columns[8][name]=con_activities.user_ip&columns[8][searchable]=false&columns[8][orderable]=false&columns[8][search][value]=&columns[8][search][regex]=false"
    "&columns[9][data]=owner&columns[9][name]=members.username&columns[9][searchable]=false&columns[9][orderable]=true&columns[9][search][value]=&columns[9][search][regex]=false"
    "&columns[10][data]=vpn&columns[10][name]=users.is_restreamer&columns[10][searchable]=false&columns[10][orderable]=true&columns[10][search][value]=&columns[10][search][regex]=false"
    "&columns[11][data]=created_at&columns[11][name]=created_at&columns[11][searchable]=false&columns[11][orderable]=true&columns[11][search][value]=&columns[11][search][regex]=false"
    "&columns[12][data]=action&columns[12][name]=action&columns[12][searchable]=false&columns[12][orderable]=false&columns[12][search][value]=&columns[12][search][regex]=false"
    "&order[0][column]=0&order[0][dir]=desc"
    "&filter=&id=users&search[regex]=false"
)

# =========================
# KEEP ALIVE
# =========================

@app.route("/ping")
def ping():
    return "OK"

# =========================
# SELENIUM AUTH
# =========================

def _do_login():
    """
    Use a real headless Chrome browser to log in.
    Steal the cookies after login and inject them into the requests session.
    This bypasses any bot detection the panel uses.
    """
    global _last_auth_time, _session

    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from webdriver_manager.chrome import ChromeDriverManager

        print("AUTH: Starting headless Chrome...")

        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1280,800")
        options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36")

        # Auto-detect browser: Brave (Mac), Chromium (Linux/Render), fallback
        import os
        brave_path    = "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
        chromium_path = "/usr/bin/chromium"
        chromium_alt  = "/usr/bin/chromium-browser"
        if os.path.exists(brave_path):
            options.binary_location = brave_path
            print(f"AUTH: Using Brave browser")
        elif os.path.exists(chromium_path):
            options.binary_location = chromium_path
            print(f"AUTH: Using Chromium")
        elif os.path.exists(chromium_alt):
            options.binary_location = chromium_alt
            print(f"AUTH: Using Chromium (alt path)")
        else:
            print(f"AUTH: Using default browser")

        from selenium.webdriver.chrome.service import Service as ChromeService
        try:
            # Try Selenium Manager first (built into Selenium 4.6+, auto-matches browser version)
            driver = webdriver.Chrome(options=options)
        except Exception:
            # Fallback to webdriver_manager
            driver = webdriver.Chrome(
                service=ChromeService(ChromeDriverManager(driver_version="146").install()),
                options=options
            )

        try:
            # Navigate to login page
            driver.get(f"{BASE_URL}/login")
            wait = WebDriverWait(driver, 10)

            # Fill in credentials
            wait.until(EC.presence_of_element_located((By.NAME, "username")))
            driver.find_element(By.NAME, "username").send_keys(ADMIN_USERNAME)
            driver.find_element(By.NAME, "password").send_keys(ADMIN_PASSWORD)
            driver.find_element(By.CSS_SELECTOR, "button[type=submit]").click()

            # Wait for redirect away from login page
            wait.until(EC.url_changes(f"{BASE_URL}/login"))
            time.sleep(1)  # let cookies settle

            final_url = driver.current_url
            print(f"AUTH: Browser landed on {final_url}")

            if "/login" in final_url:
                print("AUTH ERROR: Still on login page - wrong credentials")
                return False

            # Steal cookies from the browser
            _session = requests.Session()
            csrf_token = None

            for cookie in driver.get_cookies():
                _session.cookies.set(cookie["name"], cookie["value"])
                if cookie["name"] == "XSRF-TOKEN":
                    # URL-decode the encrypted token - but we need the meta tag version
                    pass

            # Get the plaintext CSRF token from the page meta tag
            try:
                csrf_token = driver.execute_script(
                    "return document.querySelector('meta[name=\"csrf-token\"]').getAttribute('content')"
                )
            except:
                pass

            if csrf_token:
                print(f"AUTH: CSRF token from browser: {csrf_token[:20]}...")
            else:
                # Try scraping from page source
                page = driver.page_source
                m = re.search(r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)["\']', page)
                if m:
                    csrf_token = m.group(1)
                    print(f"AUTH: CSRF token scraped: {csrf_token[:20]}...")

            if csrf_token:
                _session.headers.update({
                    "x-csrf-token":     csrf_token,
                    "x-requested-with": "XMLHttpRequest",
                    "origin":           BASE_URL,
                    "referer":          BASE_URL + "/",
                    "user-agent":       "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
                    "accept":           "application/json, text/javascript, */*; q=0.01",
                })
            else:
                print("AUTH WARNING: No CSRF token found")

            print(f"AUTH: Cookies stolen: {list(_session.cookies.keys())}")

        finally:
            driver.quit()

        _last_auth_time = time.time()
        print(f"AUTH: Login successful at {time.strftime('%H:%M:%S')}")
        return True

    except Exception as e:
        print(f"AUTH EXCEPTION: {e}")
        return False


def ensure_authed(force=False):
    with _session_lock:
        age = time.time() - _last_auth_time
        if force or _last_auth_time == 0 or age > AUTH_TTL:
            return _do_login()
        return True


# Authenticate at startup
threading.Thread(target=ensure_authed, daemon=True).start()

# =========================
# CLEAN NAME
# =========================

def extract_name(raw):
    if not raw:
        return ""
    clipboard_match = re.search('data-clipboard-text=.([^"\']+)', raw)
    if clipboard_match:
        return clipboard_match.group(1).strip().lower()
    clean = re.sub('<.*?>', '', raw)
    clean = " ".join(clean.strip().split())
    return clean.lower()

# =========================
# SEARCH USERS
# =========================

def search_users(name):
    import urllib.parse
    url  = f"{BASE_URL}/lines/data"
    # Use only the first word of the search for server-side query
    # so minor typos in last name still return candidates
    first_word = name.strip().split()[0] if name.strip() else name.strip()
    body_str = (
        f"draw=1&start=0&length=50"
        f"&search[value]={urllib.parse.quote(first_word)}"
        f"&reseller={RESELLER_ID}"
        f"&{COLUMNS_PAYLOAD}"
    )

    def _fetch():
        try:
            r = _session.post(
                url,
                data=body_str,
                headers={"content-type": "application/x-www-form-urlencoded; charset=UTF-8"},
                timeout=20,
                allow_redirects=False,
            )
        except Exception as e:
            print(f"FETCH EXCEPTION: {e}")
            return []

        print(f"DEBUG status: {r.status_code}")

        if r.status_code in (301, 302, 303, 307, 308):
            print(f"DEBUG redirect to: {r.headers.get('location', '')}")
            return None

        if r.status_code in (401, 419):
            return None

        if r.status_code != 200:
            print(f"USERS ERROR {r.status_code}: {r.text[:300]}")
            return []

        try:
            data = r.json()
        except Exception as e:
            print(f"JSON ERROR: {e} - {r.text[:200]}")
            return []

        if "redirect" in data:
            print(f"DEBUG redirect response: {data['redirect']}")
            return None

        users = data.get("data", [])
        print(f"DEBUG: got {len(users)} users")
        for u in users[:3]:
            print(f"DEBUG user: notes={repr(u.get('admin_notes_show'))} username={u.get('username')}")
        return users

    ensure_authed()
    result = _fetch()

    if result is None:
        print("Session expired - re-authenticating...")
        if ensure_authed(force=True):
            result = _fetch()
        else:
            result = []

    return result or []

# =========================
# FIND USER (fuzzy matching via difflib)
# =========================

from difflib import SequenceMatcher

def _fuzzy_score(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def find_user(search, users):
    search     = " ".join(search.lower().strip().split())
    parts      = search.split()
    matches    = []
    THRESHOLD  = 0.75

    for user in users:
        note = extract_name(user.get("admin_notes_show", ""))
        if not note:
            continue

        note_parts = note.split()

        if len(parts) >= 2:
            # Try exact match first
            if search in note:
                matches.append(user)
            else:
                # Fuzzy match each word against note words
                score = sum(
                    max(_fuzzy_score(p, n) for n in note_parts)
                    for p in parts
                ) / len(parts)
                if score >= THRESHOLD:
                    matches.append(user)
        else:
            # Single word: fuzzy match against any word in note
            best = max(_fuzzy_score(parts[0], n) for n in note_parts) if note_parts else 0
            if best >= THRESHOLD:
                matches.append(user)

    return matches

# =========================
# MAIN PAGE
# =========================

@app.route("/", methods=["GET", "POST"])
def home():
    result = ""

    if request.method == "POST":
        name  = request.form.get("name", "").strip()
        users = search_users(name)

        if not users and users is not None:
            result = "❌ No match found. Please check your name spelling and try again."
        elif users is None:
            result = "❌ System error — please try again later"
        else:
            matches = find_user(name, users)

            if not matches:
                result = "❌ No match found. Please check your name spelling and try again."
            elif len(matches) > 1:
                names  = [extract_name(u.get("admin_notes_show")) for u in matches[:5]]
                result = "<b>Multiple matches found:</b><br>"
                for n in names:
                    result += f"- {n}<br>"
                result += "<br>⚠️ Please enter your FULL first and last name."
            else:
                u        = matches[0]
                username = u.get("username", "")
                password = u.get("password", "")
                exp      = u.get("exp_date_show", u.get("exp_date", ""))

                result = f"""
                <h3>Setup Instructions</h3>
                1. Go to settings on your onn homescreen (gear icon very top)<br>
                2. Go to All Settings<br>
                3. Applications &rarr; find ZAKTV and/or TiviMate<br>
                4. Uninstall one or both (if present)<br>
                5. Open Downloader app<br>
                6. Enter code: <b>8101812</b><br>
                7. Install and open TiviMate<br>
                8. Click <b>Add Playlist</b><br>
                9. Choose <b>Service-S</b><br><br>
                <b>Username:</b> {username}<br>
                <b>Password:</b> {password}<br>
                <b>Expires:</b> {exp}<br><br>
                If this does not work, please contact support.<br><br>
                <b>&#128227; Join our Facebook group for updates and support:</b><br>
                <a href="https://www.facebook.com/share/g/1Fv6ZeWn2N/" target="_blank" style="font-size:1.1em;">&#128279; Join the Group</a><br><br>
                <hr>
                <b>&#128276; Getting an "Error Processing Playlist"?</b><br>
                Your internet provider may be blocking the stream. Visit the link below, find your provider, and follow the instructions to fix it:<br><br>
                <a href="https://epg.run/unblock/" target="_blank" style="font-size:1.1em;">&#128279; https://epg.run/unblock/</a>
                """

    return render_template_string("""
    <h2>TiviMate Support</h2>
    <h4>&#9888;&#65039; Enter your FULL first and last name</h4>
    <form method="post">
        <input name="name" placeholder="John Smith" required>
        <button type="submit">Lookup</button>
    </form>
    <p>{{result|safe}}</p>
    """, result=result)

# =========================
# ADMIN PAGE
# =========================

@app.route("/admin", methods=["GET", "POST"])
def admin():
    message = ""

    if request.method == "POST":
        password = request.form.get("password", "")
        if password != APP_PASSWORD:
            return "Unauthorized"
        if request.form.get("action") == "reauth":
            success = ensure_authed(force=True)
            message = "Re-authentication successful" if success else "Failed - check credentials in config"

    age = int(time.time() - _last_auth_time) if _last_auth_time else None
    if age is None:
        auth_status = "Not authenticated yet"
    elif age < AUTH_TTL:
        auth_status = f"Authenticated ({age // 60}m ago)"
    else:
        auth_status = f"Token may be stale ({age // 60}m ago)"

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
    """, auth_status=auth_status, message=message)

# =========================
# RUN
# =========================

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port)
