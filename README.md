# 🚀 GitHub Insight Engine

![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![Flask](https://img.shields.io/badge/Flask-2.0+-green.svg)
![JavaScript](https://img.shields.io/badge/JavaScript-Vanilla-yellow.svg)
![Chart.js](https://img.shields.io/badge/Chart.js-Data_Viz-FF6384.svg)

**GitHub Insight Engine** is a highly interactive, full-stack analytics dashboard that transforms standard GitHub profiles into deep, gamified developer portfolios. 

Built with a Python (Flask) backend and a sleek, glassmorphism UI, it leverages both GitHub's REST and GraphQL APIs to extract, analyze, and visualize developer data in ways the native GitHub UI doesn't.

## ✨ Advanced Features

Beyond standard metrics like repository counts and total stars, the Insight Engine features 13 unique analytical tools:

* 🤖 **AI-Powered Summary:** Generates a professional, recruiter-ready executive summary based on the developer's tech stack, collaboration style, and commit history.
* 🎭 **Dev Persona Analysis:** Runs sentiment analysis on recent commit messages to classify the developer (e.g., *The Zen Master*, *The Rage Coder*).
* ⏱️ **The "Bug Hunter" Metric:** Calculates the average turnaround time for resolving issues and closing Pull Requests.
* 🕸️ **Domain Expertise Radar:** A visual radar chart mapping the developer's proficiency across their top 6 programming languages.
* 📅 **Work Habits Punchcard:** A bubble chart tracking the days of the week and hours of the day the developer is most active over a 90-day period.
* 🔥 **Streak Tracking:** Computes both the current active coding streak and the all-time maximum streak.
* 🤝 **Collaboration Matrix:** Analyzes original vs. forked repositories and PR frequency to determine if the developer is a "Lone Wolf" or a "Team Player."
* 🕰️ **GitHub Wrapped (Time Machine):** A dynamic, animated playback of the developer's chronological project history.
* 💻 **Hacker Terminal Mode:** A hidden, interactive easter egg that displays profile data via an animated, green-on-black CLI interface.
* 📖 **In-App README Reader:** Click on any repository to instantly fetch, parse, and render its `README.md` inside a stylized modal.
* 🔗 **Shareable Links:** URL routing allows users to share direct links to specific developer dashboards.
* 📱 **Fully Responsive:** Adapts seamlessly from massive ultrawide monitors down to mobile phone screens.

## 🛠️ Tech Stack

* **Backend:** Python, Flask, Requests (REST & GraphQL integration)
* **Frontend:** HTML5, CSS3 (Glassmorphism), Vanilla JavaScript
* **Data Visualization:** Chart.js
* **Animations:** Vanta.js (Net background), Three.js
* **Markdown Parsing:** marked.js

## ⚙️ Installation & Setup

To run this project locally, you will need Python installed on your machine and a GitHub Personal Access Token.

### 1. Clone the repository
```bash
git clone [https://github.com/YOUR_USERNAME/github-insight-engine.git](https://github.com/YOUR_USERNAME/github-insight-engine.git)
cd github-insight-engine

2. Install dependencies
Bash
pip install flask requests python-dotenv
3. Setup your GitHub Token
To prevent rate-limiting and enable the GraphQL API, you must provide a GitHub token.

Go to your GitHub Settings > Developer Settings > Personal Access Tokens > Tokens (classic).

Generate a new token with repo and read:user scopes.

Create a file named .env in the root directory of this project.

Add your token to the .env file:

Plaintext
GITHUB_TOKEN=ghp_your_actual_token_here
(Note: .env is included in the .gitignore to keep your token safe.)

4. Run the Engine
Bash
python app.py
Open your browser and navigate to http://127.0.0.1:5000/.

👨‍💻 Author
Developed by Arsalan Mir.

Feel free to fork this project, submit pull requests, or use it to analyze your own GitHub footprint!

📜 License
This project is licensed under the MIT License.


### Next Steps:
1. Create a new file in your project folder named `README.md`.
2. Paste this text inside.
3. *Important:* Make sure to replace `YOUR_USERNAME` in the clone link under the Installation section with your actual GitHub username! 
4. Run your `git add .`, `git commit -m "Added README"`, and `git push` to upload it.