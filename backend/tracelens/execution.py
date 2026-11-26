"""Freeze analysis settings and resolve credentials from the selected connection."""
from dataclasses import replace
from .diagnosis import AGENTDEBUGX_COMMIT, AGENTDEBUGX_VERSION, DEFAULT_MODE
from .diagnosis.agentdebugx_prompts import prompt_profile
from .trajdebug import ENGINE_VERSION, UPSTREAM_COMMIT

FIELDS = ('base_url', 'model', 'prompt_language', 'max_retries', 'timeout', 'llm_concurrency', 'max_tokens', 'cluster_json_mode')
RUNTIME_FIELDS = ('concurrency', 'max_retries', 'timeout', 'llm_concurrency', 'max_tokens', 'cluster_json_mode')


def active_execution(store, settings):
    connection = store.model_connections.default()
    if connection is None:
        return settings, None
    return replace(settings, base_url=connection['base_url'], model=connection['model'],
                   api_key=connection['api_key'], headers=connection.get('headers', {})), connection['id']


def load_runtime_settings(store, settings):
    values = store.runtime_settings(settings, RUNTIME_FIELDS)
    return replace(settings, **values)


def runtime_snapshot(settings):
    return {key: getattr(settings, key) for key in RUNTIME_FIELDS}


def snapshot(settings, connection_id=None, prompt_language=None, diagnosis_mode=None):
    config = {**{k: getattr(settings, k) for k in FIELDS}, 'concurrency': settings.concurrency,
              'engine': ENGINE_VERSION, 'upstream_commit': UPSTREAM_COMMIT}
    if prompt_language is not None:
        config['prompt_language'] = prompt_language
    if connection_id is not None:
        config['model_connection_id'] = connection_id
    if diagnosis_mode is not None:
        config['diagnosis_mode'] = diagnosis_mode
        if diagnosis_mode != DEFAULT_MODE:
            config.update(agentdebugx_version=AGENTDEBUGX_VERSION, agentdebugx_commit=AGENTDEBUGX_COMMIT,
                          agentdebugx_prompt_profile=prompt_profile(config['prompt_language']))
        if diagnosis_mode == 'agentdebugx':
            config.update(engine=f'agentdebugx-{AGENTDEBUGX_VERSION}', upstream_commit=AGENTDEBUGX_COMMIT)
    return config


def job_settings(store, settings, job_id):
    return run_settings(store, settings, store.job_config(job_id))


def run_settings(store, settings, config):
    if not config or 'base_url' not in config:
        return settings
    # The shared slot limit is a live workspace policy, including for old rounds.
    frozen = {k: config[k] for k in FIELDS if k in config and k != 'llm_concurrency'}
    if connection_id := config.get('model_connection_id'):
        connection = store.model_connections.get(connection_id)
        if connection and connection['base_url'] == frozen['base_url'] and connection['model'] == frozen.get('model'):
            return replace(settings, **frozen, api_key=connection['api_key'], headers=connection.get('headers', {}))
    elif frozen['base_url'].rstrip('/') == settings.base_url.rstrip('/'):
        return replace(settings, **frozen)
    return replace(settings, **{**frozen, 'base_url': '', 'api_key': '', 'headers': {}})
