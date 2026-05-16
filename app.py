from flask import Flask, render_template, request, jsonify
import requests
import os
import re
from dotenv import load_dotenv
from datetime import datetime
from flask_limiter import Limiter
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_talisman import Talisman
from flask_cors import CORS

load_dotenv()
app = Flask(__name__)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# SECURITY: Limit incoming payload size to 16KB to prevent memory exhaustion
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024

# SECURITY: Trust the reverse proxy for accurate IP tracking (Crucial for Render)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# SECURITY: Add basic HTTP security headers
Talisman(app, content_security_policy=None)

# SECURITY: Enforce CORS to only allow your frontend to use the API
# Replace 'your-app-name' with your actual Render project URL
ALLOWED_ORIGINS = [
    "https://gits-viewer.onrender.com", 
    "http://127.0.0.1:5000",
    "http://localhost:5000"
]
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS}})

# Smart IP tracker that works locally AND on Render
def get_real_ip():
    if request.headers.get("X-Forwarded-For"):
        return request.headers.get("X-Forwarded-For").split(",")[0].strip()
    return request.remote_addr

# Initialize the Limiter
limiter = Limiter(
    key_func=get_real_ip,
    app=app,
    storage_uri="memory://"
)

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({"error": "Rate limit exceeded. You can only pull 15 times per second. Please slow down!"}), 429

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
        # SECURITY: Added timeout=10 to prevent server hangs
        res = requests.post("https://api.github.com/graphql", json={'query': query, 'variables': {"userName": username}}, headers=headers, timeout=10)
        return res.json().get('data', {}).get('user', {}).get('contributionsCollection', {}).get('contributionCalendar')
    except:
        return None

@app.route('/')
@limiter.limit("5 per second") # Front-end rate limiter
def home():
    return render_template('index.html')

@app.route('/api/analyze', methods=['POST'])
@limiter.limit("15 per second") # Strict API rate limiter
def analyze_user():
    username = request.json.get('username')
    if not username: return jsonify({"error": "Username required"}), 400

    # SECURITY: Validate and Sanitize Input (Blocks path traversal & malicious payloads)
    if not re.match(r"^[a-zA-Z0-9-]{1,39}$", username):
        return jsonify({"error": "Invalid GitHub username format"}), 400

    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
    try:
        # SECURITY: Added timeout=10 to all external calls
        user_res = requests.get(f"https://api.github.com/users/{username}", headers=headers, timeout=10)
        if user_res.status_code != 200: return jsonify({"error": "User not found"}), 404
        user_data = user_res.json()
        
        repo_res = requests.get(f"https://api.github.com/users/{username}/repos?sort=updated&per_page=100", headers=headers, timeout=10)
        repos_data = repo_res.json() if repo_res.status_code == 200 else []

        org_res = requests.get(f"https://api.github.com/users/{username}/orgs", headers=headers, timeout=10)
        orgs_data = [{"login": o["login"], "avatar": o["avatar_url"]} for o in (org_res.json() if org_res.status_code == 200 else [])]

        events_res = requests.get(f"https://api.github.com/users/{username}/events/public?per_page=100", headers=headers, timeout=10)
        events_data = events_res.json() if events_res.status_code == 200 else []
        
        recent_activity, punchcard_data, commit_messages = [], [], []
        pr_count = 0
        issue_resolution_times = []

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
                repo_name = e.get("repo", {}).get("name", "Unknown Repo")
                clean_date = dt.strftime("%b %d") if date_raw else ""
                action, icon = "Interacted with", "📌"
                if e_type == "PushEvent": action, icon = "Pushed commits to", "🔥"
                elif e_type == "PullRequestEvent": action, icon = "Opened a PR in", "🔄"
                elif e_type == "IssuesEvent": action, icon = "Opened an issue in", "🐛"
                elif e_type == "WatchEvent": action, icon = "Starred", "⭐"
                elif e_type == "CreateEvent": action, icon = "Created", "🌱"
                elif e_type == "ForkEvent": action, icon = "Forked", "🍴"
                recent_activity.append({"action": action, "repo": repo_name.split('/')[-1], "full_repo": repo_name, "date": clean_date, "icon": icon})

        langs, repos_by_year = {}, {}
        total_stars, original_repos, forked_repos = 0, 0, 0
        all_repos = []
        
        for r in repos_data:
            total_stars += r.get("stargazers_count", 0)
            lang = r.get("language")
            if lang: langs[lang] = langs.get(lang, 0) + 1
            
            created_at = r.get("created_at")
            if created_at:
                year = created_at[:4]
                repos_by_year[year] = repos_by_year.get(year, 0) + 1

            if r.get("fork"): forked_repos += 1
            else: original_repos += 1

            all_repos.append({
                "name": r["name"], "full_name": r["full_name"], "default_branch": r.get("default_branch", "main"), 
                "url": r["html_url"], "stars": r["stargazers_count"], "lang": lang or "N/A",
                "desc": r.get("description") or "No description provided.", "forks": r.get("forks_count", 0), "issues": r.get("open_issues_count", 0), "updated": r.get("updated_at") 
            })

        rage_words = ["fix", "bug", "hate", "fuck", "damn", "asdf", "finally", "stupid", "shit", "ugh", "wip"]
        zen_words = ["refactor", "docs", "test", "feat", "chore", "update", "clean", "initial"]
        rage_score = sum(1 for msg in commit_messages if any(w in msg for w in rage_words))
        zen_score = sum(1 for msg in commit_messages if any(w in msg for w in zen_words))
        persona = "🤬 The Rage Coder" if rage_score > zen_score and rage_score > 2 else "🧘‍♂️ The Zen Master" if zen_score > rage_score else "👻 The Ghost Committer" if len(commit_messages) == 0 else "🥷 The Mysterious Builder"

        total_projects = original_repos + forked_repos
        collab_status = "Lone Wolf 🐺" if total_projects > 0 and (original_repos / total_projects) * 100 > 75 and pr_count < 5 else "Team Player 🤝" if total_projects > 0 else "Just Starting 🌱"
        bug_hunter_score = f"{int(sum(issue_resolution_times)/len(issue_resolution_times))} hrs" if issue_resolution_times else "N/A"

        top_lang = sorted(langs.items(), key=lambda x: x[1], reverse=True)[0][0] if langs else "a variety of technologies"
        dev_name = user_data.get("name") or username
        ai_summary = f"✨ <b>System Analysis:</b> {dev_name} is recognized as a '{persona.split(' ', 1)[1]}' who primarily engineers solutions using {top_lang}. Displaying a '{collab_status.split(' ')[0]}' collaboration style, they maintain a portfolio of {len(all_repos)} recent projects, securing a total of {total_stars} stars."

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

        return jsonify({
            "profile": {
                "name": dev_name, "avatar": user_data.get("avatar_url"), "bio": user_data.get("bio") or "No bio available.",
                "repos": user_data.get("public_repos", 0), "followers": user_data.get("followers", 0), "total_stars": total_stars,
                "current_streak": current_streak, "longest_streak": longest_streak, "persona": persona, "collab_status": collab_status,
                "bug_hunter": bug_hunter_score, "ai_summary": ai_summary, "timeline": repos_by_year
            },
            "badges": badges, "recent_repos": all_repos, "organizations": orgs_data, "recent_activity": recent_activity, 
            "punchcard": punchcard_data, "languages": langs, "heatmap": heatmap
        })

    except Exception as e:
        # SECURITY: Stop Leaking Internal Errors to the front end
        print(f"Internal Analytics Error: {str(e)}") 
        return jsonify({"error": "An unexpected server error occurred. Please try again later."}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)