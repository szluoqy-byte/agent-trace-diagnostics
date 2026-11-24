"""Load the pinned, locally downloaded official corpus without model calls."""
import hashlib
import json
from pathlib import Path
from .normalize import normalize
from .trajdebug import UPSTREAM_COMMIT


def load_official_samples(directory: Path):
    manifest = directory / "trajdebug-testset.json"
    if not manifest.is_file():
        raise ValueError("请先运行 .venv/bin/python scripts/fetch_samples.py 下载官方测试集")
    catalog = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict):
        raise ValueError("官方测试集清单必须是 JSON 对象")
    cases = catalog.get("cases", [])
    if catalog.get("commit") != UPSTREAM_COMMIT or not isinstance(cases, list) or not 1 <= len(cases) <= 50:
        raise ValueError("官方测试集清单版本或数量不合法，请重新下载")
    traces, seen = [], set()
    root = directory.resolve()
    for entry in cases:
        path = (root / entry["file"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("官方测试集文件缺失或路径不合法，请重新下载")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != entry["sha256"]:
            raise ValueError("官方测试集文件哈希不匹配，请重新下载")
        blob = hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()
        if blob != entry["git_blob_sha1"]:
            raise ValueError("官方测试集与固定提交不一致，请重新下载")
        trace = normalize(json.loads(body), path.name)
        if trace.task_id != entry["task_id"] or trace.task_id in seen:
            raise ValueError("官方测试集存在任务标识不一致或重复")
        gold = trace.annotation.get("critical_error_step")
        if (trace.outcome != "failed" or type(gold) is not int or not 0 <= gold < len(trace.messages)
                or trace.messages[gold].role != "assistant"):
            raise ValueError("官方测试集缺少可用失败标注")
        seen.add(trace.task_id)
        trace.title = entry["title"]
        trace.source = entry["source_url"]
        traces.append(trace)
    return traces
