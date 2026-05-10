import requests

# Make sure your Flask app is running on port 5000 before running this!
url = "http://127.0.0.1:5000/api/analyze"
payload = {"username": "torvalds"}

print("Starting stress test...")

for i in range(1, 30):
    response = requests.post(url, json=payload)
    
    if response.status_code == 200:
        print(f"Request {i}: ✅ Success (200 OK)")
    elif response.status_code == 429:
        print(f"Request {i}: 🛑 BLOCKED (429 Too Many Requests)")
        print(f"Message from server: {response.json().get('error')}")
        break
    else:
        print(f"Request {i}: ⚠️ Error {response.status_code}")