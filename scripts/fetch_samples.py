"""Download the pinned 20-case official test set. Does not invoke a model."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import time
import urllib.request
from tracelens.config import Settings
from tracelens.official_samples import load_official_samples
from tracelens.trajdebug import UPSTREAM_COMMIT


def fetch_file(directory, entry):
    path = directory / entry["file"]
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]:
        return
    for attempt in range(3):
        try:
            with urllib.request.urlopen(entry["source_url"], timeout=40) as reply:
                body = reply.read()
            blob = hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()
            if hashlib.sha256(body).hexdigest() != entry["sha256"] or blob != entry["git_blob_sha1"]:
                raise ValueError("Official file checksum mismatch: " + entry["file"])
            temporary = path.with_suffix(".partial")
            temporary.write_bytes(body)
            temporary.replace(path)
            return
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Settings().data_dir / "sources")
    args = parser.parse_args()
    directory = args.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    catalog = json.loads(Path(__file__).with_name("official_testset.json").read_text())
    assert catalog["commit"] == UPSTREAM_COMMIT
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda entry: fetch_file(directory, entry), catalog["cases"]))
    base = "https://raw.githubusercontent.com/THU-KEG/TrajDebug/" + UPSTREAM_COMMIT + "/"
    for relative, filename in [("LICENSE", "TrajDebug-LICENSE.txt"), ("data/README.md", "TrajDebug-DATA-LICENSES.md")]:
        path = directory / filename
        if not path.is_file():
            with urllib.request.urlopen(base + relative, timeout=40) as reply:
                path.write_bytes(reply.read())
    (directory / "trajdebug-testset.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
    traces = load_official_samples(directory)
    print(json.dumps({"downloaded_and_verified": len(traces), "commit": UPSTREAM_COMMIT,
                      "counts": catalog["selection"]["counts"], "directory": str(directory),
                      "model_requests": 0}, ensure_ascii=False))
    print("在工作台点击‘加载公开样例’即可导入。重复导入会按内容去重，不会启动诊断。")


if __name__ == "__main__":
    main()

