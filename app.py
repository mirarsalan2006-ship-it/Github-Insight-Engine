from flask import Flask, render_template, request, jsonify, make_response
import requests
import os
import re
import logging
from dotenv import load_dotenv
from datetime import datetime
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

# Replace print() with proper production logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# SECURITY: Limit incoming payload size to 16KB
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024

# PERFORMANCE: Initialize Simple In-Memory Cache (900s = 15 mins)
app.config['CACHE_TYPE'] = 'SimpleCache'
app.config['CACHE_DEFAULT_TIMEOUT'] = 900 
cache = Cache(app)

# SECURITY: Trust the reverse proxy for accurate IP tracking (Render)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# SECURITY: Strict Content Security Policy
csp = {
    'default-src': ["'self'"],
    'script-src': [
        "'self'", "'unsafe-inline'",
        "https://cdnjs.cloudflare.com", "https://cdn.jsdelivr.net"      
    ],
    'style-src': ["'self'", "'unsafe-inline'"],
    'img-src': ["'self'", "data:", "https://avatars.githubusercontent.com"],
    'connect-src': ["'self'", "https://api.github.com", "https://cdn.jsdelivr.net"]
}
Talisman(app, content_security_policy=csp)

# SECURITY: Enforce CORS
ALLOWED_ORIGINS = [
    "https://gits-viewer.onrender.com", 
    "http://127.0.0.1:5000",
    "http://localhost:5000"
]
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS}})

# SECURITY: IP Rate Limiting
limiter = Limiter(key_func=get_remote_address, app=app, storage_uri="memory://")

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({"error": "Rate limit exceeded. Please wait a minute and try again!"}), 429

# ---------------------------------------------------------
# Helper Functions
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
    try:
        headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}
        res = requests.post("https://api.github.com/graphql", json={'query': query, 'variables': {"userName": username}}, headers=headers, timeout=10)
        res.raise_for_status() # Ensure HTTP errors are caught
        return res.json().get('data', {}).get('user', {}).get('contributionsCollection', {}).get('contributionCalendar')
    # STABILITY: Catch explicit requests and parsing errors, not base exceptions
    except (requests.RequestException, ValueError) as e:
        logger.warning(f"Heatmap fetch failed for {username}: {str(e)}")
        return None

# ---------------------------------------------------------
# Routes
# ---------------------------------------------------------
@app.route('/')
@limiter.limit("30 per minute")
def home():
    response = make_response(render_template('index.html'))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/api/analyze', methods=['POST'])
@limiter.limit("50 per minute")
def analyze_user():
    # STABILITY: Safely parse JSON to prevent crashes if Content-Type is missing/wrong
    data = request.get_json(silent=True) or {}
    username = data.get('username')
    
    if not username or not isinstance(username, str): 
        return jsonify({"error": "Valid username required"}), 400

    # SECURITY: Validate and Sanitize Input
    if not re.match(r"^[a-zA-Z0-9-]{1,39}$", username):
        return jsonify({"error": "Invalid GitHub username format"}), 400

    cache_key = f"github_stats_{username.lower()}"
    cached_data = cache.get(cache_key)
    if cached_data:
        logger.info(f"Cache hit for user: {username}")
        return jsonify(cached_data)

    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
    
    try:
        # STABILITY: Guard all API calls against network failures and bad JSON
        user_res = requests.get(f"https://api.github.com/users/{username}", headers=headers, timeout=10)
        if user_res.status_code == 404: 
            return jsonify({"error": "User not found"}), 404
        user_res.raise_for_status()
        user_data = user_res.json()
        
        repo_res = requests.get(f"https://api.github.com/users/{username}/repos?sort=updated&per_page=100", headers=headers, timeout=10)
        repos_data = repo_res.json() if repo_res.status_code == 200 else []

        org_res = requests.get(f"https://api.github.com/users/{username}/orgs", headers=headers, timeout=10)
        orgs_data = [{"login": o["login"], "avatar": o["avatar_url"]} for o in (org_res.json() if org_res.status_code == 200 else [])]

        events_res = requests.get(f"https://api.github.com/users/{username}/events/public?per_page=100", headers=headers, timeout=10)
        events_data = events_res.json() if events_res.status_code == 200 else []

    except requests.Timeout:
        logger.error(f"GitHub API timeout for {username}")
        return jsonify({"error": "GitHub API timeout. Please try again later."}), 504
    except requests.ConnectionError:
        logger.error(f"Connection failed when fetching {username}")
        return jsonify({"error": "Connection to GitHub failed. Check network."}), 503
    except ValueError:
        logger.error(f"Invalid JSON returned from GitHub for {username}")
        return jsonify({"error": "Invalid response from GitHub API."}), 502
    except requests.RequestException as e:
        logger.error(f"GitHub API Error for {username}: {str(e)}")
        return jsonify({"error": "An error occurred while contacting GitHub."}), 502

    try:
        recent_activity, punchcard_data, commit_messages = [], [], []
        pr_count, issue_resolution_times = 0, []

        for e in events_data:
            e_type = e.get("type")
            date_raw = e.get("created_at")
            if date_raw:
                dt = datetime.strptime(date_raw, "%Y-%m-%dT%H:%M:%SZ")
                punchcard_data.append({"x": dt.hour, "y": dt.weekday()})

            payload = e.get("payload", {})
            
            if e_type == "PushEvent":
                for c in payload.get("commits", []):
                    commit_messages.append(c.get("message", "").lower())
            
            if e_type in ["PullRequestEvent", "IssuesEvent"]:
                if e_type == "PullRequestEvent": pr_count += 1
                if payload.get("action") == "closed":
                    item = payload.get("pull_request") or payload.get("issue")
                    if item and item.get("created_at") and item.get("closed_at"):
                        created = datetime.strptime(item["created_at"], "%Y-%m-%dT%H:%M:%SZ")
                        closed = datetime.strptime(item["closed_at"], "%Y-%m-%dT%H:%M:%SZ")
                        diff_hours = (closed - created).total_seconds() / 3600
                        issue_resolution_times.append(diff_hours)

            if len(recent_activity) < 8: 
                # STABILITY: Guard against missing repo names
                repo_data = e.get("repo", {})
                full_repo_name = repo_data.get("name") if isinstance(repo_data, dict) else "Unknown/Repo"
                repo_short_name = full_repo_name.split('/')[-1] if full_repo_name and '/' in full_repo_name else "Unknown Repo"
                
                clean_date = dt.strftime("%b %d") if date_raw else ""
                action, icon = "Interacted with", "📌"
                
                if e_type == "PushEvent": action, icon = "Pushed commits to", "🔥"
                elif e_type == "PullRequestEvent": action, icon = "Opened a PR in", "🔄"
                elif e_type == "IssuesEvent": action, icon = "Opened an issue in", "🐛"
                elif e_type == "WatchEvent": action, icon = "Starred", "⭐"
                elif e_type == "CreateEvent": action, icon = "Created", "🌱"
                elif e_type == "ForkEvent": action, icon = "Forked", "🍴"
                
                recent_activity.append({
                    "action": action, 
                    "repo": repo_short_name, 
                    "full_repo": full_repo_name, 
                    "date": clean_date, 
                    "icon": icon
                })

        langs, repos_by_year = {}, {}
        total_stars, original_repos, forked_repos = 0, 0, 0
        all_repos = []
        
        for r in repos_data:
            total_stars += r.get("stargazers_count", 0)
            lang = r.get("language")
            if lang: langs[lang] = langs.get(lang, 0) + 1
            
            created_at = r.get("created_at")
            if created_at and isinstance(created_at, str):
                year = created_at[:4]
                repos_by_year[year] = repos_by_year.get(year, 0) + 1

            if r.get("fork"): forked_repos += 1
            else: original_repos += 1

            all_repos.append({
                "name": r.get("name", "Unknown"), 
                "full_name": r.get("full_name", "Unknown"), 
                "default_branch": r.get("default_branch", "main"), 
                "url": r.get("html_url", "#"), 
                "stars": r.get("stargazers_count", 0), 
                "lang": lang or "N/A",
                "desc": r.get("description") or "No description provided.", 
                "forks": r.get("forks_count", 0), 
                "issues": r.get("open_issues_count", 0), 
                "updated": r.get("updated_at", "") 
            })

        rage_words = ["fix", "bug", "hate", "fuck", "damn", "asdf", "finally", "stupid", "shit", "ugh", "wip"]
        zen_words = ["refactor", "docs", "test", "feat", "chore", "update", "clean", "initial"]
        rage_score = sum(1 for msg in commit_messages if any(w in msg for w in rage_words))
        zen_score = sum(1 for msg in commit_messages if any(w in msg for w in zen_words))
        
        # SYNTAX FIX: Multi-line string assignments securely bracketed
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
        dev_name = user_data.get("name") or username
        
        ai_summary = (
            f"✨ <b>System Analysis:</b> {dev_name} is recognized as a '{persona.split(' ', 1)[-1]}' "
            f"who primarily engineers solutions using {top_lang}. Displaying a '{collab_status.split(' ')[0]}' "
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
                if day.get("contributionCount", 0) > 0:
                    temp_streak += 1; longest_streak = max(longest_streak, temp_streak)
                else: temp_streak = 0
            for day in reversed(flattened_days):
                if day.get("contributionCount", 0) > 0: current_streak += 1
                else:
                    if current_streak > 0 or day != flattened_days[-1]: break

        final_payload = {
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
        logger.info(f"Successfully processed and cached data for: {username}")
        
        return jsonify(final_payload)

    except Exception as e:
        # Final catch-all for local processing logic errors, safe from leaking context
        logger.error(f"Internal Processing Error for {username}: {str(e)}") 
        return jsonify({"error": "An unexpected server error occurred while processing data."}), 500

if __name__ == '__main__':
    app.run(port=5000)