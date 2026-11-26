"""Probe that the microsandbox was created with networking disabled."""

import urllib.request

try:
    urllib.request.urlopen("https://pypi.org", timeout=3)
except Exception:
    print("NETWORK_BLOCKED")
else:
    print("NETWORK_OPEN")
