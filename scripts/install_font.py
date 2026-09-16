"""Install the checksum-pinned font used by local tests, API and worker."""
import hashlib
import os
import urllib.request
from pathlib import Path

VERSION = "1.3.9"
SHA256 = "3090ccde0442bb347aa7685d9ba8b17436a60682df6e8f92a9a670de14056e22"
URL = f"https://cdn.jsdelivr.net/npm/pretendard@{VERSION}/dist/public/variable/PretendardVariable.ttf"


def main():
    target = Path(os.environ.get("PRETENDARD_FONT_PATH") or "data/fonts/PretendardVariable.ttf")
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == SHA256:
        print(f"Font verified: {target}")
        return
    with urllib.request.urlopen(URL, timeout=60) as response:
        blob = response.read()
    if hashlib.sha256(blob).hexdigest() != SHA256:
        raise ValueError("Pretendard checksum mismatch")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".download")
    temporary.write_bytes(blob)
    temporary.replace(target)
    print(f"Font installed: {target}")


if __name__ == "__main__":
    main()
