"""
Downloads the Latin subsets of the three project typefaces and inlines them
as base64 into vendor/fonts.css.

Why base64 and not linked files: the dashboard must render identically with
no network -- pitch recording on unknown wifi, and a judge opening the repo
offline. Latin-only keeps the payload to a few hundred KB instead of ~2MB.
"""

import base64
import os
import re
import urllib.request

VENDOR = os.path.join(os.path.dirname(__file__), "..", "vendor")
URL = ("https://fonts.googleapis.com/css2"
       "?family=Space+Grotesk:wght@500;700"
       "&family=Inter:wght@400;500;600"
       "&family=JetBrains+Mono:wght@400;500&display=swap")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"


def main():
    css = urllib.request.urlopen(
        urllib.request.Request(URL, headers={"User-Agent": UA})).read().decode()

    # Google emits one @font-face per unicode subset, each preceded by a
    # /* subset */ comment. Keep only latin and latin-ext.
    blocks = re.split(r"(?=/\*\s*[\w-]+\s*\*/)", css)
    keep = [b for b in blocks
            if re.match(r"/\*\s*latin(-ext)?\s*\*/", b.strip()) and "@font-face" in b]

    out = []
    for block in keep:
        m = re.search(r"src:\s*url\((https://[^)]+\.woff2)\)", block)
        if not m:
            continue
        data = urllib.request.urlopen(m.group(1)).read()
        b64 = base64.b64encode(data).decode()
        out.append(block.replace(
            m.group(1), f"data:font/woff2;base64,{b64}").strip())
        fam = re.search(r"font-family:\s*'([^']+)'", block).group(1)
        wt = re.search(r"font-weight:\s*(\d+)", block).group(1)
        print(f"  embedded {fam} {wt} ({len(data)//1024} KB)")

    path = os.path.join(VENDOR, "fonts.css")
    with open(path, "w") as f:
        f.write("\n".join(out))
    print(f"Wrote {os.path.abspath(path)} ({os.path.getsize(path)//1024} KB)")


if __name__ == "__main__":
    main()
