import time
import httpx

proxies = {"http://": "http://04c2a2c98864ae89:QnjrV4d8T3k61U0S@res.geonix.com:10000", "https://": "http://04c2a2c98864ae89:QnjrV4d8T3k61U0S@res.geonix.com:10000"}
url = "https://www.zillow.com/profile/Pardee-Properties/"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Sec-Ch-Ua": "\"Chromium\";v=\"122\", \"Not(A:Brand\";v=\"24\", \"Google Chrome\";v=\"122\"",
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": "\"Windows\"",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1"
}

start = time.time()
with httpx.Client(proxies=proxies, verify=False, http2=True) as client:
    resp = client.get(url, headers=headers)
print(f"HTTPX Status: {resp.status_code}, Time: {time.time() - start:.3f}s")
