"""
Diagnose WHERE curl_cffi spends ~10 seconds.
Tests: Session vs no-session, streaming vs full download, regex-only vs BeautifulSoup parsing.
"""
import time
import re
from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup

proxy_url = "http://8119445ce034ab2e:HbjMw3kt@res.geonix.com:10000"
proxies = {"http": proxy_url, "https": proxy_url}
test_url = "https://www.zillow.com/profile/Pardee-Properties/"

ZUID_PATTERN = re.compile(r'"encodedZuid":"([^"]+)"')


def test_1_baseline():
    """Current approach: one-shot request, no session."""
    print("=" * 60)
    print("TEST 1: Baseline (no session, full download)")
    t0 = time.time()
    resp = cffi_requests.get(test_url, impersonate="chrome", proxies=proxies, timeout=30)
    t_download = time.time()
    
    text = resp.text
    t_decode = time.time()
    
    soup = BeautifulSoup(text, 'lxml')
    t_parse = time.time()
    
    match = ZUID_PATTERN.search(text)
    t_regex = time.time()
    
    print(f"  HTTP {resp.status_code}, length={len(text)}")
    print(f"  Download:    {t_download - t0:.3f}s")
    print(f"  .text decode:{t_decode - t_download:.3f}s")
    print(f"  BS4 parse:   {t_parse - t_decode:.3f}s")
    print(f"  Regex ZUID:  {t_regex - t_parse:.3f}s")
    print(f"  TOTAL:       {t_regex - t0:.3f}s")
    print(f"  ZUID found:  {match.group(1) if match else 'NO'}")
    return t_regex - t0


def test_2_session():
    """Use a persistent Session (connection reuse)."""
    print("=" * 60)
    print("TEST 2: With Session (connection reuse)")
    
    with cffi_requests.Session() as s:
        # First request - establishes connection
        t0 = time.time()
        resp = s.get(test_url, impersonate="chrome", proxies=proxies, timeout=30)
        t1 = time.time()
        text = resp.text
        match = ZUID_PATTERN.search(text)
        print(f"  1st request: {t1 - t0:.3f}s, HTTP {resp.status_code}, ZUID={bool(match)}")
        
        # Second request - should reuse connection
        t2 = time.time()
        resp2 = s.get(test_url, impersonate="chrome", proxies=proxies, timeout=30)
        t3 = time.time()
        print(f"  2nd request: {t3 - t2:.3f}s, HTTP {resp2.status_code}")


def test_3_stream_early_stop():
    """Stream response and stop as soon as ZUID is found."""
    print("=" * 60)
    print("TEST 3: Streaming with early ZUID extraction")
    t0 = time.time()
    
    resp = cffi_requests.get(
        test_url,
        impersonate="chrome",
        proxies=proxies,
        timeout=30,
        stream=True
    )
    t_connect = time.time()
    print(f"  Connection + headers: {t_connect - t0:.3f}s (TTFB)")
    
    # Read chunks and search for ZUID
    accumulated = ""
    bytes_read = 0
    zuid = None
    
    for chunk in resp.iter_content(chunk_size=8192):
        if chunk:
            bytes_read += len(chunk)
            accumulated += chunk.decode('utf-8', errors='ignore')
            
            match = ZUID_PATTERN.search(accumulated)
            if match:
                zuid = match.group(1)
                t_found = time.time()
                print(f"  ZUID found after {bytes_read} bytes in {t_found - t0:.3f}s")
                print(f"  ZUID: {zuid}")
                # Stop reading - we have what we need
                break
    
    if not zuid:
        t_found = time.time()
        print(f"  ZUID NOT found after {bytes_read} bytes in {t_found - t0:.3f}s")
    
    resp.close()
    t_end = time.time()
    print(f"  TOTAL:       {t_end - t0:.3f}s")
    return t_end - t0


def test_4_regex_only_no_bs4():
    """Skip BeautifulSoup entirely, use regex on raw text."""
    print("=" * 60)
    print("TEST 4: Regex-only (skip BeautifulSoup)")
    t0 = time.time()
    resp = cffi_requests.get(test_url, impersonate="chrome", proxies=proxies, timeout=30)
    t_download = time.time()
    
    text = resp.text
    match = ZUID_PATTERN.search(text)
    t_done = time.time()
    
    print(f"  HTTP {resp.status_code}, length={len(text)}")
    print(f"  Download:    {t_download - t0:.3f}s")
    print(f"  Regex only:  {t_done - t_download:.3f}s")
    print(f"  TOTAL:       {t_done - t0:.3f}s")
    print(f"  ZUID found:  {match.group(1) if match else 'NO'}")


def test_5_content_vs_text():
    """Compare resp.content (bytes) vs resp.text (decoded string)."""
    print("=" * 60)
    print("TEST 5: .content (bytes) vs .text (string)")
    
    resp = cffi_requests.get(test_url, impersonate="chrome", proxies=proxies, timeout=30)
    
    t0 = time.time()
    raw = resp.content  # bytes, no decoding
    t_content = time.time()
    
    t1 = time.time()
    text = resp.text  # string, charset decoding
    t_text = time.time()
    
    # Search bytes directly
    t2 = time.time()
    match_bytes = re.search(rb'"encodedZuid":"([^"]+)"', raw)
    t_regex_bytes = time.time()
    
    print(f"  .content:     {t_content - t0:.6f}s ({len(raw)} bytes)")
    print(f"  .text:        {t_text - t1:.6f}s ({len(text)} chars)")
    print(f"  Regex bytes:  {t_regex_bytes - t2:.6f}s found={bool(match_bytes)}")


if __name__ == "__main__":
    print(f"Target: {test_url}\n")
    
    test_1_baseline()
    print()
    test_3_stream_early_stop()
    print()
    test_4_regex_only_no_bs4()
    print()
    test_5_content_vs_text()
    print()
    test_2_session()