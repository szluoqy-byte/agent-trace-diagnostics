"""Snapshot a completed diagnosis and run the pinned official feedback module."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys

from .config import ROOT
from .llm import ModelError
from .model_concurrency import model_slot_environment
from .trace_feedback import trace_feedback_context, uses_trace_feedback
from .trajdebug import UPSTREAM_COMMIT, UPSTREAM_URL, verify_upstream


def feedback_source(job, settings):
    result = job.get('result') or {}
    if job['status'] != 'completed':
        raise ValueError('请先完成诊断，再生成改进建议')
    if job['mode'] != 'live' or result.get('schema_version') != 2:
        raise ValueError('该历史报告不支持生成改进建议，请用当前版本重新诊断')
    provenance = result.get('provenance') or {}
    if provenance.get('commit') != UPSTREAM_COMMIT:
        raise ValueError('该记录使用了旧版诊断引擎，请用当前版本重新诊断')
    run_id = provenance.get('run_id')
    if not isinstance(run_id, str) or not re.fullmatch(r'[0-9a-f]{32}', run_id):
        raise ValueError('缺少生成建议所需的诊断记录，请重新诊断')
    directory = settings.data_dir / 'trajdebug' / 'runs' / run_id
    if not all((directory / name).is_file() for name in ('input.json', 'input_final.json')):
        raise ValueError('该诊断的原始输入或最终产物不存在，请重新诊断')
    try:
        raw = (directory / 'input.json').read_bytes()
        unified = json.loads(raw)
        final = json.loads((directory / 'input_final.json').read_bytes())
    except (OSError, ValueError) as exc:
        raise ValueError('该诊断的运行产物无法读取，请重新诊断') from exc
    if unified.get('metadata', {}).get('reward') != 0:
        raise ValueError('仅支持为业务失败的轨迹生成改进建议')
    step = (final.get('critical_error') or {}).get('unified_critical_step')
    messages = unified.get('messages') or []
    if (type(step) is not int or not 0 <= step < len(messages)
            or messages[step].get('role') == 'user' or messages[step].get('judgable') is False):
        raise ValueError('本次未定位到有效的关键失败步骤，无法生成改进建议')
    expected = result.get('unified_input_sha256')
    if (expected and hashlib.sha256(raw).hexdigest() != expected) or final != result.get('official_output'):
        raise ValueError('运行产物与原始诊断不一致，请重新诊断')
    return directory


def feedback_availability(store, job, settings):
    try:
        if uses_trace_feedback(job):
            trace_feedback_context(store, job)
        else:
            feedback_source(job, settings)
        if not settings.ready:
            return False, '请先配置诊断模型后再生成改进建议'
        return True, ''
    except ValueError as exc:
        return False, str(exc)


def present_feedback(report):
    def text(value):
        return value if isinstance(value, str) else ''
    suggestion = report.get('fix_suggestion')
    suggestion = suggestion if isinstance(suggestion, dict) else {}
    hint = text(suggestion.get('hint_sentence')).strip()
    analysis = report.get('critical_error_analysis')
    analysis = analysis if isinstance(analysis, dict) else {}
    if not hint or analysis.get('parse_failed'):
        raise ModelError('模型未返回可用的改进建议，请重试')
    alternatives = report.get('alternative_critical_steps')
    alternatives = alternatives if isinstance(alternatives, list) else []
    return {'hint_sentence': hint, 'narrative_summary': text(suggestion.get('narrative_summary')),
            'critical_step': analysis.get('critical_step') if type(analysis.get('critical_step')) is int else None,
            'alternatives': [{'step': item.get('step') if type(item.get('step')) is int else None,
                              **{key: text(item.get(key)) for key in ('taxonomy_tag', 'reason', 'hint_sentence')}}
                             for item in alternatives if isinstance(item, dict)]}


async def generate_feedback(job, settings, feedback_id):
    verify_upstream()
    if not settings.ready:
        raise ModelError('请先配置诊断模型')
    source = feedback_source(job, settings)
    if not re.fullmatch(r'[0-9a-f]{32}', feedback_id):
        raise ValueError('无效的建议任务标识')
    directory = settings.data_dir / 'trajdebug' / 'feedback' / feedback_id
    directory.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name in ('input.json', 'input_final.json', 'input_stage_a.json'):
        if (source / name).is_file():
            shutil.copyfile(source / name, directory / name)
            hashes[name] = hashlib.sha256((directory / name).read_bytes()).hexdigest()
    config = {key: getattr(settings, key) for key in ('base_url', 'model', 'prompt_language', 'max_tokens', 'timeout', 'max_retries')}
    if settings.request_parameters:
        config['request_parameters'] = settings.request_parameters
    headers = dict(settings.headers)
    # The official client caches even HTTP-200 responses that fail JSON parsing.
    # A manual retry gets a fresh cache; completed tasks are reused by the API.
    namespace = hashlib.sha256(json.dumps([hashes, config, headers, UPSTREAM_COMMIT, 'official-feedback-v1', feedback_id], sort_keys=True).encode()).hexdigest()
    config['cache_path'] = str(settings.data_dir / 'trajdebug' / 'cache' / namespace / 'feedback.pkl')
    (directory / 'settings.json').write_text(json.dumps(config), encoding='utf-8')
    env = {**os.environ, **model_slot_environment(settings), 'OPENAI_API_KEY': settings.api_key or 'EMPTY',
           'TRACELENS_MODEL_HEADERS': json.dumps(headers, ensure_ascii=False),
           'PYTHONIOENCODING': 'utf-8',
           'PYTHONPATH': str(ROOT / 'backend') + os.pathsep + os.environ.get('PYTHONPATH', '')}
    process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'tracelens.feedback_worker', str(directory),
                                                 env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        code = await process.wait()
        result_path = directory / 'result.json'
        payload = json.loads(result_path.read_text(encoding='utf-8')) if result_path.is_file() else {}
        usage = payload.get('usage')
        if code or not isinstance(payload.get('official_report'), dict):
            raise ModelError('改进建议生成失败。请检查模型配置和服务状态后重试。', usage=usage)
        try:
            view = present_feedback(payload['official_report'])
        except ModelError as exc:
            exc.usage = usage
            raise
        return {**view, 'official_report': payload['official_report'], 'usage': usage,
                'seconds': payload.get('seconds'), 'model': settings.model,
                'provenance': {'commit': UPSTREAM_COMMIT, 'repository': UPSTREAM_URL,
                               'implementation': 'vendored_unmodified', 'module': 'applications/generate_feedback.py',
                               'diagnosis_job_id': job['id'], 'diagnosis_run_id': source.name,
                               'feedback_run_id': feedback_id, 'input_sha256': hashes}}
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
