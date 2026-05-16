from flask import Flask, render_template, request, jsonify, make_response, redirect
import requests
import os
import re
import logging
import time
from functools import wraps
from dotenv import load_dotenv
from datetime import datetime
from markupsafe import escape
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_talisman import Talisman
from flask_cors import CORS
from flask_caching import Cache

# ---------------------------------------------------------
# Configuration & Setup
# ---------------------------------------------------------
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
API_KEY = os.getenv("API_KEY")

# SECURITY: Input size limits
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024
app.config['CACHE_TYPE'] = 'SimpleCache'
app.config['CACHE_DEFAULT_TIMEOUT'] = 900 
cache = Cache(app)

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# SECURITY: Strict Content Security Policy & Headers
csp = {
    'default-src': ["'self'"],
    'script-src': ["'self'", "'unsafe-inline'", "https://cdnjs.cloudflare.com", "https://cdn.jsdelivr.net"],
    'style-src': ["'self'", "'unsafe-inline'"],
    'img-src': ["'self'", "data:", "https://avatars.githubusercontent.com"],
    'connect-src': ["'self'", "https://api.github.com", "https://cdn.jsdelivr.net"],
    'frame-ancestors': ["'none'"]
}

Talisman(
    app, 
    content_security_policy=csp,
    force_https=(ENVIRONMENT == "production"),
    strict_transport_security=True,
    strict_transport_security_max_age=31536000, 
    x_content_type_options=True,
    x_frame_options='DENY',
    referrer_policy='strict-origin-when-cross-origin'
)

# SECURITY: Environment-Based CORS
if ENVIRONMENT == "production":
    ALLOWED_ORIGINS = ["https://gits-viewer.onrender.com"]
    app.config['PREFERRED_URL_SCHEME'] = 'https'
else:
    ALLOWED_ORIGINS = ["http://127.0.0.1:5000", "http://localhost:5000"]

CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS}})

# SECURITY: Advanced Rate Limiting Key
def get_rate_limit_key():
    username = request.json.get('username', '') if request.is_json and request.method == 'POST' else ''
    ip = get_remote_address()
    return f"{ip}:{username}" if username else ip

limiter = Limiter(key_func=get_rate_limit_key, app=app, storage_uri="memory://")

# ---------------------------------------------------------
# Error Handlers & Middleware
# ---------------------------------------------------------
@app.before_request
def enforce_https():
    # SECURITY: Explicit HTTPS redirect for production
    if ENVIRONMENT == "production" and not request.is_secure and request.headers.get('X-Forwarded-Proto', 'http') == 'http':
        url = request.url.replace('http://', 'https://', 1)
        return redirect(url, code=301)

@app.errorhandler(429)
def ratelimit_handler(e):
    logger.warning(f"RATE_LIMIT_EXCEEDED: {get_remote_address()}")
    return jsonify({"error": "Rate limit exceeded. Please wait a minute and try again!"}), 429

@app.errorhandler(500)
def internal_server_error(e):
    # SECURITY: Catch-all for unhandled exceptions, preventing stack trace leaks
    logger.error(f"INTERNAL_SERVER_ERROR: {str(e)}")
    return jsonify({"error": "An internal server error occurred."}), 500

# ---------------------------------------------------------
# Security & Utility Helpers
# ---------------------------------------------------------
def require_auth(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        req_api_key = request.headers.get('X-API-Key')
        # SECURITY: API Key enforced in ALL environments to prevent dev-mode bypasses
        if not req_api_key or req_api_key != API_KEY:
            logger.warning(f"UNAUTHORIZED_ACCESS_ATTEMPT from {get_remote_address()}")
            return jsonify({"error": "Invalid or missing API key"}), 401
        return f(*args, **kwargs)
    return decorated_function

def sanitize_headers(headers):
    sanitized = headers.copy()
    if 'Authorization' in sanitized:
        sanitized['Authorization'] = 'Bearer [REDACTED]'
    return sanitized

def safe_json_parse(response, default=None):
    try:
        data = response.json()
        if not isinstance(data, (dict, list)):
            logger.warning(f"Unexpected JSON type from GitHub: {type(data)}")
            return default
        return data
    except ValueError as e:
        logger.error(f"Failed to parse GitHub response: {e}")
        return default

def safe_parse_datetime(date_str):
    if not date_str or not isinstance(date_str, str): return None
    formats = ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    logger.warning(f"Could not parse date: {date_str}")
    return None

# ---------------------------------------------------------
# Core Logic
# ---------------------------------------------------------
def get_heatmap_data(username):
    if not GITHUB_TOKEN: return None
    query = """
    query($userName:String!) {
      user(login:$userName) {
        contributionsCollection {
          contributionCalendar {
            totalContributions
            weeks { contributionDays { contributionCount, date, color } }
          }
        }
      }
    }
    """
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}
    try:
        res = requests.post("https://api.github.com/graphql", json={'query': query, 'variables': {"userName": username}}, headers=headers, timeout=10)
        res.raise_for_status()
        data = safe_json_parse(res, {})
        
        # SECURITY: GraphQL returns 200 OK even on errors. Check explicitly.
        if data and data.get('errors'):
            logger.error(f"GraphQL error for {username}: {data.get('errors')}")
            return None
            
        return data.get('data', {}).get('user', {}).get('contributionsCollection', {}).get('contributionCalendar')
    except requests.RequestException as e:
        logger.warning(f"Heatmap fetch failed for {username}: {str(e)} | Headers: {sanitize_headers(headers)}")
        return None

@app.route('/')
@limiter.limit("30 per minute")
def home():
    response = make_response(render_template('index.html'))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/api/analyze', methods=['POST'])
@require_auth
@limiter.limit("50 per minute")
def analyze_user():
    processing_start_time = time.time()
    MAX_PROCESSING_TIME = 25 # Abort if local computation takes too long

    data = request.get_json(silent=True) or {}
    username = data.get('username')
    
    if not username or not isinstance(username, str): 
        return jsonify({"error": "Valid username required"}), 400

    # SECURITY: Strict GitHub Username Validation (Handles ReDoS & Bounds)
    if len(username) > 39 or not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,38}$", username):
        logger.warning(f"INVALID_USERNAME_ATTEMPT: {username}")
        return jsonify({"error": "Invalid GitHub username format"}), 400

    cache_key = f"github_stats_{username.lower()}"
    cached_data = cache.get(cache_key)
    
    # SECURITY: Explicit `is True` check prevents cache poisoning from partial/failed data
    if cached_data and cached_data.get("success") is True:
        logger.info(f"Cache hit for user: {username}")
        return jsonify(cached_data)

    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
    
    try:
        user_res = requests.get(f"https://api.github.com/users/{username}", headers=headers, timeout=10)
        if user_res.status_code == 404: 
            return jsonify({"error": "User not found"}), 404
        user_res.raise_for_status()
        user_data = safe_json_parse(user_res, {})
        if not user_data: return jsonify({"error": "Invalid response from GitHub"}), 502
        
        MAX_REPOS = 100
        repo_res = requests.get(f"https://api.github.com/users/{username}/repos?sort=updated&per_page={MAX_REPOS}", headers=headers, timeout=10)
        repos_data = safe_json_parse(repo_res, [])[:MAX_REPOS]

        org_res = requests.get(f"https://api.github.com/users/{username}/orgs", headers=headers, timeout=10)
        orgs_data = [{"login": o.get("login"), "avatar": o.get("avatar_url")} for o in safe_json_parse(org_res, [])]

        events_res = requests.get(f"https://api.github.com/users/{username}/events/public?per_page={MAX_REPOS}", headers=headers, timeout=10)
        events_data = safe_json_parse(events_res, [])[:MAX_REPOS]

    except requests.Timeout:
        logger.error(f"GitHub API timeout for {username}")
        return jsonify({"error": "GitHub API timeout. Please try again later."}), 504
    except requests.ConnectionError:
        logger.error(f"Connection failed when fetching {username}")
        return jsonify({"error": "Connection to GitHub failed. Check network."}), 503
    except requests.RequestException as e:
        logger.error(f"GitHub API Error for {username}: {str(e)} | Headers: {sanitize_headers(headers)}")
        return jsonify({"error": "An error occurred while contacting GitHub."}), 502

    try:
        recent_activity, punchcard_data, commit_messages = [], [], []
        pr_count, issue_resolution_times = 0, []

        for e in events_data:
            # PERFORMANCE/SECURITY: Abort if looping takes too long
            if time.time() - processing_start_time > MAX_PROCESSING_TIME:
                raise TimeoutError("Data processing exceeded maximum time limit")

            if not isinstance(e, dict): continue
            e_type = e.get("type")
            dt = safe_parse_datetime(e.get("created_at"))
            
            if dt:
                punchcard_data.append({"x": dt.hour, "y": dt.weekday()})

            payload = e.get("payload", {})
            if not isinstance(payload, dict): continue
            
            if e_type == "PushEvent":
                for c in payload.get("commits", []):
                    if isinstance(c, dict) and "message" in c and isinstance(c["message"], str):
                        commit_messages.append(c["message"].lower())
            
            if e_type in ["PullRequestEvent", "IssuesEvent"]:
                if e_type == "PullRequestEvent": pr_count += 1
                if payload.get("action") == "closed":
                    item = payload.get("pull_request") or payload.get("issue")
                    if isinstance(item, dict):
                        created = safe_parse_datetime(item.get("created_at"))
                        closed = safe_parse_datetime(item.get("closed_at"))
                        if created and closed:
                            diff_hours = (closed - created).total_seconds() / 3600
                            issue_resolution_times.append(diff_hours)

            if len(recent_activity) < 8 and dt: 
                repo_data = e.get("repo", {})
                full_repo_name = repo_data.get("name") if isinstance(repo_data, dict) else "Unknown/Repo"
                repo_short_name = full_repo_name.split('/')[-1] if full_repo_name and '/' in full_repo_name else "Unknown Repo"
                
                action, icon = "Interacted with", "📌"
                if e_type == "PushEvent": action, icon = "Pushed commits to", "🔥"
                elif e_type == "PullRequestEvent": action, icon = "Opened a PR in", "🔄"
                elif e_type == "IssuesEvent": action, icon = "Opened an issue in", "🐛"
                elif e_type == "WatchEvent": action, icon = "Starred", "⭐"
                elif e_type == "CreateEvent": action, icon = "Created", "🌱"
                elif e_type == "ForkEvent": action, icon = "Forked", "🍴"
                
                recent_activity.append({
                    "action": action, "repo": repo_short_name, "full_repo": full_repo_name, 
                    "date": dt.strftime("%b %d"), "icon": icon
                })

        langs, repos_by_year = {}, {}
        total_stars, original_repos, forked_repos = 0, 0, 0
        all_repos = []
        
        for r in repos_data:
            if time.time() - processing_start_time > MAX_PROCESSING_TIME:
                raise TimeoutError("Data processing exceeded maximum time limit")

            if not isinstance(r, dict): continue
            total_stars += r.get("stargazers_count", 0) if isinstance(r.get("stargazers_count"), int) else 0
            
            lang = r.get("language")
            if isinstance(lang, str): langs[lang] = langs.get(lang, 0) + 1
            
            # SECURITY: Type check created_at to prevent slicing crashes
            created_at = r.get("created_at")
            if created_at and isinstance(created_at, str) and len(created_at) >= 4:
                year = created_at[:4]
                repos_by_year[year] = repos_by_year.get(year, 0) + 1

            if r.get("fork"): forked_repos += 1
            else: original_repos += 1

            all_repos.append({
                "name": r.get("name", "Unknown"), "full_name": r.get("full_name", "Unknown"), 
                "default_branch": r.get("default_branch", "main"), "url": r.get("html_url", "#"), 
                "stars": r.get("stargazers_count", 0) if isinstance(r.get("stargazers_count"), int) else 0, 
                "lang": lang if isinstance(lang, str) else "N/A",
                "desc": r.get("description") if isinstance(r.get("description"), str) else "No description provided.", 
                "forks": r.get("forks_count", 0) if isinstance(r.get("forks_count"), int) else 0, 
                "issues": r.get("open_issues_count", 0) if isinstance(r.get("open_issues_count"), int) else 0, 
                "updated": r.get("updated_at", "") if isinstance(r.get("updated_at"), str) else ""
            })

        rage_words = ["fix", "bug", "hate", "fuck", "damn", "asdf", "finally", "stupid", "shit", "ugh", "wip"]
        zen_words = ["refactor", "docs", "test", "feat", "chore", "update", "clean", "initial"]
        rage_score = sum(1 for msg in commit_messages if any(w in msg for w in rage_words))
        zen_score = sum(1 for msg in commit_messages if any(w in msg for w in zen_words))
        
        persona = (
            "🤬 The Rage Coder" if rage_score > zen_score and rage_score > 2 
            else "🧘‍♂️ The Zen Master" if zen_score > rage_score 
            else "👻 The Ghost Committer" if len(commit_messages) == 0 
            else "🥷 The Mysterious Builder"
        )

        total_projects = original_repos + forked_repos
        collab_status = (
            "Lone Wolf 🐺" if total_projects > 0 and (original_repos / total_projects) * 100 > 75 and pr_count < 5 
            else "Team Player 🤝" if total_projects > 0 
            else "Just Starting 🌱"
        )
        
        bug_hunter_score = f"{int(sum(issue_resolution_times)/len(issue_resolution_times))} hrs" if issue_resolution_times else "N/A"
        top_lang = sorted(langs.items(), key=lambda x: x[1], reverse=True)[0][0] if langs else "a variety of technologies"
        dev_name = user_data.get("name") if isinstance(user_data.get("name"), str) else username
        
        # SECURITY: Markupsafe escape applied
        ai_summary = (
            f"✨ <b>System Analysis:</b> {escape(dev_name)} is recognized as a '{escape(persona.split(' ', 1)[-1])}' "
            f"who primarily engineers solutions using {escape(top_lang)}. Displaying a '{escape(collab_status.split(' ')[0])}' "
            f"collaboration style, they maintain a portfolio of {len(all_repos)} recent projects, "
            f"securing a total of {total_stars} stars."
        )

        badges = []
        if total_stars >= 50: badges.append({"icon": "🌟", "title": "Star Catcher", "desc": "Earned over 50 repository stars"})
        if len(langs) >= 5: badges.append({"icon": "🗣️", "title": "Polyglot", "desc": "Codes in 5+ different languages"})
        if user_data.get("followers", 0) >= 20: badges.append({"icon": "👑", "title": "Influencer", "desc": "Has 20+ followers"})
        if issue_resolution_times: badges.append({"icon": "🪲", "title": "Bug Hunter", "desc": "Actively resolves issues & PRs"})

        heatmap = get_heatmap_data(username)
        longest_streak, current_streak = 0, 0
        if heatmap:
            flattened_days = [day for week in heatmap.get("weeks", []) for day in week.get("contributionDays", [])]
            temp_streak = 0
            for day in flattened_days:
                if isinstance(day, dict) and day.get("contributionCount", 0) > 0:
                    temp_streak += 1; longest_streak = max(longest_streak, temp_streak)
                else: temp_streak = 0
            for day in reversed(flattened_days):
                if isinstance(day, dict) and day.get("contributionCount", 0) > 0: current_streak += 1
                else:
                    if current_streak > 0 or day != flattened_days[-1]: break

        final_payload = {
            "success": True, # For cache poisoning prevention
            "profile": {
                "name": dev_name, "avatar": user_data.get("avatar_url"), "bio": user_data.get("bio") or "No bio available.",
                "repos": user_data.get("public_repos", 0), "followers": user_data.get("followers", 0), "total_stars": total_stars,
                "current_streak": current_streak, "longest_streak": longest_streak, "persona": persona, "collab_status": collab_status,
                "bug_hunter": bug_hunter_score, "ai_summary": ai_summary, "timeline": repos_by_year
            },
            "badges": badges, "recent_repos": all_repos, "organizations": orgs_data, "recent_activity": recent_activity, 
            "punchcard": punchcard_data, "languages": langs, "heatmap": heatmap
        }

        cache.set(cache_key, final_payload)
        logger.info(f"Successfully processed and cached data for: {username} (Took {time.time() - processing_start_time:.2f}s)")
        
        return jsonify(final_payload)

    except TimeoutError as e:
        logger.error(f"Processing Timeout for {username}: {str(e)}")
        return jsonify({"error": "Data processing took too long. The profile may be too large."}), 504
    except Exception as e:
        logger.error(f"Internal Processing Error for {username}: {str(e)}") 
        return jsonify({"error": "An unexpected server error occurred while processing data."}), 500

if __name__ == '__main__':
    app.run(port=5000)