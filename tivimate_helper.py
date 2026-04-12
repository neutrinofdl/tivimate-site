from flask import Flask, request, render_template_string
import requests
import re

app = Flask(__name__)

# =========================
# CONFIG
# =========================

BASE_URL = "https://alt.sanctumpanel.com"
ADMIN_PASSWORD = "CHANGE_THIS_PASSWORD"  # 🔐 CHANGE THIS

TOKENS = {
    "xsrf": "",
    "session": "",
    "csrf": ""
}

# =========================
# KEEP ALIVE (UPTIMEROBOT)
# =========================
@app.route("/ping")
def ping():
    return "OK"

# =========================
# PARSE CURL (AUTO TOKEN EXTRACTION)
# =========================
def parse_curl(curl_text):
    xsrf = ""
    session = ""
    csrf = ""

    cookie_match = re.search(r"-b '([^']+)'", curl_text)
    if cookie_match:
        cookies = cookie_match.group(1)

        xsrf_match = re.search(r"XSRF-TOKEN=([^;]+)", cookies)
        session_match = re.search(r"laravel_session=([^;]+)", cookies)

        if xsrf_match:
            xsrf = xsrf_match.group(1)

        if session_match:
            session = session_match.group(1)

    csrf_match = re.search(r"x-csrf-token: ([^']+)", curl_text)
    if csrf_match:
        csrf = csrf_match.group(1)

    return xsrf, session, csrf

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
# GET ALL USERS (PAGINATION)
# =========================
def get_users():
    url = f"{BASE_URL}/lines/data"

    headers = {
        "accept": "application/json, text/javascript, */*; q=0.01",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "x-csrf-token": TOKENS["csrf"],
        "x-requested-with": "XMLHttpRequest",
    }

    cookies = {
        "XSRF-TOKEN": TOKENS["xsrf"],
        "laravel_session": TOKENS["session"]
    }

    all_users = []
    start = 0
    length = 100

    while True:
        payload = {
            "draw": 1,
            "start": start,
            "length": length,
            "search[value]": "",
            "id": "users",
            "reseller": 59
        }

        r = requests.post(url, headers=headers, cookies=cookies, data=payload)

        if r.status_code != 200:
            print("ERROR:", r.text)
            return []

        try:
            data = r.json()
            users = data.get("data", [])
        except:
            print("JSON ERROR:", r.text)
            return []

        if not users:
            break

        all_users.extend(users)

        if len(users) < length:
            break

        start += length

    print(f"Loaded {len(all_users)} users")
    return all_users

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

        # FULL NAME MATCH
        if len(parts) >= 2:
            if search in note:
                matches.append(user)

        # LAST NAME ONLY (STRICT)
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
        name = request.form.get("name")

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

                result += "<br>⚠️ Please enter FULL first and last name."

            else:
                u = matches[0]

                username = u.get("username")
                password = u.get("password")
                exp = u.get("exp_date")

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

                <hr>

                <b>⚠️ Still not working?</b><br><br>

                Some internet providers (Spectrum, Xfinity, AT&T, etc.) block IPTV connections by default.<br><br>

                👉 <b>Click here for ISP unblock guide:</b><br>
                <a href="https://epg.run/unblock/" target="_blank">
                https://epg.run/unblock/
                </a><br><br>

                Select your provider and follow the steps to disable security blocking.<br><br>

                If you still need help after this, please contact support.
                """

    return render_template_string("""
    <h2>TiviMate Support</h2>

    <h4>⚠️ Enter your FULL first and last name</h4>

    <form method="post">
        <input name="name" placeholder="John Smith" required>
        <button type="submit">Lookup</button>
    </form>

    <br>

    <b>Having trouble connecting?</b><br>
    <a href="https://epg.run/unblock/" target="_blank">
    Click here for ISP unblock guide
    </a>

    <p>{{result|safe}}</p>
    """, result=result)

# =========================
# ADMIN PAGE (SECURE)
# =========================
@app.route("/admin", methods=["GET", "POST"])
def admin():
    message = ""

    if request.method == "POST":
        password = request.form.get("password")

        if password != ADMIN_PASSWORD:
            return "❌ Unauthorized"

        curl = request.form.get("curl")

        xsrf, session, csrf = parse_curl(curl)

        if xsrf and session and csrf:
            TOKENS["xsrf"] = xsrf
            TOKENS["session"] = session
            TOKENS["csrf"] = csrf
            message = "✅ Tokens updated successfully"
        else:
            message = "❌ Failed to extract tokens"

    return render_template_string("""
    <h2>Admin - Paste Full cURL</h2>

    <form method="post">
        Password:<br>
        <input type="password" name="password"><br><br>

        Paste full cURL:<br>
        <textarea name="curl" rows="10" cols="100"></textarea><br><br>

        <button type="submit">Update Tokens</button>
    </form>

    <p>{{message}}</p>
    """, message=message)

# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050)
