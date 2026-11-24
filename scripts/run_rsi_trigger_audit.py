"""Run an independent high-recall Stage B audit on saved reports."""
import argparse
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from tracelens.config import Settings
from tracelens.model_connections import ModelConnections
from tracelens._vendor.trajdebug.detector.utils.model import APIModel

TAGS = ('plan.BadDecomposition, plan.WrongOrder, plan.UnrealisticPlan, plan.GoalDrift, '
        'plan.OverExplore, reason.WrongChoice, reason.InvalidInference, reason.MissingAssumptionCheck, '
        'act.WrongTool, act.ToolSchemaMismatch, act.WrongActionSequence, act.UnsafeOrForbiddenAction, '
        'obs.MisreadOutput, obs.IgnoreOutput, obs.TimingIssue, obs.GroundingFail, verify.NoVerification, '
        'verify.WrongVerification, verify.PrematureTermination, verify.InfiniteRetry, env.AdOverlayHijack, '
        'env.ContentFilterBlock, env.ToolExtractorDegenerate, env.RateLimitOrTransient, env.EmptyOrRepeatedPayload')
PROMPT = """You are a high-recall auditor for Stage B of a failed agent trajectory diagnosis.
The official detector already emitted the triggers listed below. Find additional trigger steps it missed.
Apply the official conflict rule. A trigger needs a concrete conflict with the task, visible history, agent self-claim, or an environment anomaly, with verbatim wrong and reference quotes.
Do not repeat an official trigger step. Do not report ordinary exploration, harmless retries, later symptoms, or merely suboptimal actions.
Focus on load-bearing errors that could remain active at the terminal step. Return at most three additional triggers.
Allowed taxonomy tags are: {tags}
Return only JSON with this schema:
{{"additional_triggers":[{{"step":integer,"category":"cat-1"|"cat-2"|"cat-3"|"env","taxonomy_tag":"tag","attribution":"agent"|"env","wrong_content_quote":"verbatim quote","reference_quote":"verbatim quote","confidence":"high"|"medium"|"low","reason":"short explanation"}}]}}
If no additional trigger is justified, return {{"additional_triggers":[]}}.

=== OFFICIAL TRIGGERS ===
{official}

=== TASK AND TRAJECTORY ===
{trajectory}
"""
_tls=threading.local()

def model_for(settings, cache_dir):
    model=getattr(_tls,'model',None)
    if model is None:
        cache_dir.mkdir(parents=True,exist_ok=True)
        model=APIModel(str(cache_dir/f'{threading.get_ident()}.pkl'),settings.base_url,settings.model,settings.api_key or 'EMPTY')
        _tls.model=model
    return model

def trim(value,limit=1400):
    value=str(value or '').replace('\x00','')
    return value if len(value)<=limit else value[:limit]+'\n...[truncated]'

def make_prompt(trace,report):
    official=[{'step':t.get('step'),'taxonomy_tag':t.get('taxonomy_tag'),'category':t.get('category'),'description':t.get('description')} for t in report.get('triggers',[])]
    messages=[{'step':m.get('step'),'role':m.get('role'),'judgable':m.get('judgable'),'content':trim(m.get('content'))} for m in trace.get('messages',[])]
    trajectory={'task_description':trim(trace.get('metadata',{}).get('task_description'),2400),'messages':messages}
    return PROMPT.format(tags=TAGS,official=json.dumps(official,ensure_ascii=False),trajectory=json.dumps(trajectory,ensure_ascii=False))

def parse(text):
    match=re.search(r'\{.*\}',text or '',re.S)
    if not match: raise ValueError('audit response is not JSON')
    data=json.loads(match.group(0));items=data.get('additional_triggers')
    if not isinstance(items,list): raise ValueError('additional_triggers is not an array')
    out=[]
    for item in items[:3]:
        if not isinstance(item,dict) or type(item.get('step')) is not int or item['step']<0: continue
        out.append({'step':item['step'],'category':item.get('category',''),'taxonomy_tag':item.get('taxonomy_tag',''),
                    'attribution':item.get('attribution','agent'),'wrong_content_quote':trim(item.get('wrong_content_quote'),500),
                    'reference_quote':trim(item.get('reference_quote'),500),'confidence':item.get('confidence','low'),
                    'reason':trim(item.get('reason'),700)})
    return out

def one(path,settings,root,cache_dir):
    report=json.loads(path.read_text(encoding='utf-8')); run=root/'trajdebug/runs'/report['provenance']['run_id'];trace=json.loads((run/'input.json').read_text(encoding='utf-8'))
    model=model_for(settings,cache_dir);text,usage=model.generate(make_prompt(trace,report),max_tokens=2048,temperature=0.0,response_format={'type':'json_object'},return_usage=True)
    return {'input_sha256':path.stem,'split':path.parent.name,'additional_triggers':parse(text),'usage':usage or {}}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--split',required=True,choices=('train','dev','heldout','external_eval'));parser.add_argument('--output',type=Path,required=True);parser.add_argument('--data-dir',type=Path,default=Path('data/validation/rsi-round-1'));parser.add_argument('--workers',type=int,default=4);args=parser.parse_args()
    settings=Settings();connection=ModelConnections(settings.data_dir).default()
    if connection:settings=replace(settings,**{k:connection[k] for k in ('model','base_url','api_key')})
    reports=sorted((args.data_dir/'reports'/args.split).glob('*.json'));results=[];errors=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(one,path,settings,args.data_dir,args.output.parent/'trigger-audit-cache') for path in reports]
        for f in as_completed(futures):
            try: results.append(f.result())
            except Exception as exc: errors.append({'error':str(exc)})
    results.sort(key=lambda x:x['input_sha256']);args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps({'split':args.split,'model':settings.model,'predictions':results,'errors':errors},ensure_ascii=False,indent=2)+'\n', encoding='utf-8');print(json.dumps({'split':args.split,'completed':len(results),'errors':len(errors)},ensure_ascii=False))
if __name__=='__main__':main()
