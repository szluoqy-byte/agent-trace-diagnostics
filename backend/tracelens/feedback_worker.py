"""Execute the official post-diagnosis feedback application in isolation."""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import sys
import time

from .trajdebug import VENDOR, verify_upstream
from .trajdebug_worker import Usage
from .model_headers import apply_headers, parse_headers
from .prompt_language import localize_model


async def run(directory):
    verify_upstream()
    sys.path.insert(0, str(VENDOR / 'detector'))
    from ._vendor.trajdebug.applications.generate_feedback import _process_one, _build_taxonomy_block
    from utils.model import APIModel
    from _stage_common import resolve_extra_params
    config = json.loads((directory / 'settings.json').read_text(encoding='utf-8'))
    usage, started = Usage(), time.monotonic()
    model = None
    try:
        with contextlib.redirect_stdout(sys.stderr):
            model = APIModel(config['cache_path'], config['base_url'], config['model'], os.environ['OPENAI_API_KEY'],
                             extra_params=resolve_extra_params(None))
            apply_headers(model, parse_headers(os.environ.get('TRACELENS_MODEL_HEADERS')))
            localize_model(model, config.get('prompt_language', 'en'))
            usage.attach(model, config)
            path = await _process_one(str(directory / 'input_final.json'), str(directory), str(directory), str(directory),
                                      model, _build_taxonomy_block(), config['max_tokens'], 0.0, asyncio.Semaphore(1))
        report = json.loads(Path(path).read_text(encoding='utf-8')) if path else None
        (directory / 'result.json').write_text(json.dumps({'official_report': report, 'usage': usage.snapshot(),
                                                         'seconds': round(time.monotonic() - started, 2)}, ensure_ascii=False), encoding='utf-8')
        return 0
    except Exception as exc:
        (directory / 'failure.json').write_text(json.dumps({'failure_type': type(exc).__name__, 'usage': usage.snapshot()}), encoding='utf-8')
        return 1
    finally:
        if model:
            model.close()


if __name__ == '__main__':
    raise SystemExit(asyncio.run(run(Path(sys.argv[1]))))
