from curl_cffi import requests as curl_requests

# Configuration & Headers setup
URL = "https://www.zillow.com"
# Include full browser headers matching Chrome user-agent for JA3 consistency
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.5",
    "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24"',
    # ... additional security headers
}

def get_zillow_data():
    # 'impersonate' mimics the browser's TLS handshake
    resp = curl_requests.get(URL, impersonate="chrome", headers=HEADERS)
    print(f"Status: {resp.status_code}")

get_zillow_data()
