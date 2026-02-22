"""Submit eBird sample data download requests for US states.

Usage:
    1. Open browser dev tools on ebird.org/data/download/ebd
    2. Copy your Cookie header value and CSRF token from a request
    3. Paste them below or pass as arguments:
       python request_ebird_downloads.py "<cookie>" "<csrf_token>"
"""

import sys
import time
from urllib.request import Request, urlopen
from urllib.parse import urlencode

# All 50 US states + DC, alphabetical by code
ALL_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DC", "DE", "FL",
    "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
    "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
    "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI",
    "WY",
]

# Already requested (alphabetically through HI)
ALREADY_REQUESTED = {"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DC", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
    "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "VT"}

REMAINING = [s for s in ALL_STATES if s not in ALREADY_REQUESTED]

if len(sys.argv) != 3:
    print("Usage: python request_ebird_downloads.py <session_id> <csrf_token>")
    print("\nTo get these values:")
    print("  1. Go to ebird.org/data/download/ebd in your browser")
    print("  2. Open dev tools, submit a request, look at the POST request")
    print("  3. Copy EBIRD_SESSIONID from Request Cookies")
    print("  4. Copy _csrf value from the Form Data")
    print(f"\nWill request {len(REMAINING)} states: {', '.join(REMAINING)}")
    sys.exit(1)

session_id = sys.argv[1]
csrf_token = sys.argv[2]
cookie = f"EBIRD_SESSIONID={session_id}"

print(f"Submitting download requests for {len(REMAINING)} states...")
print(f"States: {', '.join(REMAINING)}\n")

for i, state in enumerate(REMAINING):
    region_code = f"US-{state}"
    data = urlencode({
        "key": "",
        "speciesCode": "",
        "regionCode": region_code,
        "fileUrl": "",
        "species": "on",
        "locations": "on",
        "date-range": "on",
        "bcd.month": "",
        "bcd.year": "",
        "ecd.month": "",
        "ecd.year": "",
        "includeSampling": "true",
        "_includeSampling": "on",
        "_includeProvisional": "on",
        "_csrf": csrf_token,
    }).encode()

    req = Request(
        "https://ebird.org/data/download/ebd",
        data=data,
        headers={
            "Cookie": cookie,
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://ebird.org",
            "Referer": "https://ebird.org/data/download/ebd",
        },
    )

    try:
        with urlopen(req, timeout=30) as resp:
            status = resp.status
            print(f"  [{i+1}/{len(REMAINING)}] {region_code}: {status}")
    except Exception as e:
        print(f"  [{i+1}/{len(REMAINING)}] {region_code}: FAILED - {e}")

    # Be polite - wait between requests
    if i < len(REMAINING) - 1:
        time.sleep(15)

print("\nDone! Check your eBird email for download links.")
