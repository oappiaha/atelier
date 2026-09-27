"""Isolated browser API fixtures. Unknown requests fail rather than escape to an API."""
import copy
import json
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import os
BASE = os.environ.get('ATELIER_TEST_URL', 'http://127.0.0.1:5178')
assert urlsplit(BASE).hostname in ('127.0.0.1', 'localhost', '::1'), 'Use an isolated local server'
EVIDENCE = Path(os.environ.get('ATELIER_TEST_EVIDENCE', '/tmp/atelier-context-panel-evidence'))
EVIDENCE.mkdir(parents=True, exist_ok=True)
STAMP = '2026-09-01T12:00:00Z'
ROOT = Path(__file__).resolve().parents[3]
class Fixture:
 def __init__(self, empty=False):
  self.calls=[]; self.unhandled=[]; self.errors=[]; self.failed=[]
  self.projects=[dict(id='p1',name='First collection',kicker='BROWSER TEST',design_count=2),dict(id='p2',name='Second collection',kicker='BROWSER TEST',design_count=1)]
  self.designs=[dict(id='d'+str(i),project_id='p1' if i<3 else 'p2',name=n,status='final',index_no=i,materials='Cotton',category='garments',cover_media_id=None,cover_url='/test-image.jpg',entry_count=1,media_count=1,created_at=STAMP) for i,n in enumerate(['First coat','Second bag','Third shirt'],1)]
  self.media=[] if empty else [dict(id='m'+str(i),design_id=d['id'],entry_id='e'+str(i),kind='image',phase='editorial' if i==2 else 'final',url='/test-image.jpg',thumb_url='/test-image.jpg',width=600,height=800,duration_ms=None,caption=d['name'],source_url=None,source_app=None,created_at=f'2026-09-01T12:00:0{4-i}Z') for i,d in enumerate(self.designs,1)]
  self.inbox=[] if empty else [dict(self.media[0],id='inbox1',design_id=None,entry_id=None,caption='Unsorted image')]
  self.studies=[]
 def route(self, route):
  req=route.request; url=urlsplit(req.url); path=url.path.removeprefix('/api'); query=parse_qs(url.query); method=req.method
  body=req.post_data_json if req.post_data and 'application/json' in req.headers.get('content-type','') else None
  self.calls.append(dict(method=method,path=path,body=body))
  data=None
  if path=='/gallery':
   eligible=[m for m in self.media if m['kind']=='image' and m['phase'] in ('final','editorial')]
   eligible.sort(key=lambda m:(m['created_at'],m['id']),reverse=True)
   eligible.sort(key=lambda m:0 if m['phase']=='final' else 1)
   phase=query.get('phase',['all'])[0]
   selected=[m for m in eligible if phase=='all' or m['phase']==phase]
   start=int(query.get('cursor',['0'])[0]); limit=int(query.get('limit',['24'])[0])
   items=[]
   for m in selected[start:start+limit]:
    d=next(d for d in self.designs if d['id']==m['design_id'])
    p=next(p for p in self.projects if p['id']==d['project_id'])
    items.append(dict(media=m,design=d,project=p))
   data=dict(items=items,projects=self.projects,counts={ph:sum(m['phase']==ph for m in eligible) for ph in ['final','editorial']},total=len(selected),next_cursor=str(start+limit) if start+limit<len(selected) else None)
  elif path=='/projects': data=self.projects
  elif path=='/inbox': data=self.inbox
  elif path.startswith('/inbox/') and path.endswith('/triage'):
   selected=next(m for m in self.inbox if m['id']==path.split('/')[2]); self.inbox.remove(selected)
   data=dict(selected,design_id=body['design_id'],phase=body['phase']); self.media.append(data)
  elif path.startswith('/projects/') and path.endswith('/designs'):
   data=[d for d in self.designs if d['project_id']==path.split('/')[2] and ('status' not in query or d['status']==query['status'][0])]
  elif path=='/designs' and method=='POST':
   data=dict(self.designs[0],**body,id='created-design',index_no=4,status='developing'); self.designs.append(data)
  elif path.startswith('/designs/'):
   did=path.split('/')[2]
   if path.endswith('/media'): data=[m for m in self.media if m['design_id']==did]
   elif path.endswith('/timeline'): data=[dict(id='e1',design_id=did,phase='final',body='Final photo',study_id=None,occurred_at=STAMP,is_open=False,media=[m for m in self.media if m['design_id']==did])]
   elif path.endswith('/studies'): data=[]
   elif len(path.split('/'))==3: data=next((d for d in self.designs if d['id']==did),None)
  elif path=='/share': data=dict(id='share1',project_id=body.get('project_id'),design_id=body.get('design_id'),slug='test',scope=body['scope'],url='/s/test',revoked_at=None,view_count=0,created_at=STAMP)
  elif path=='/studies' and method=='GET': data=self.studies
  elif path.startswith('/media/') and path.endswith('/segment'): data=dict(status='complete',regions=[])
  elif path.startswith('/media/') and path.endswith('/regions'): data=[]
  elif path=='/palettes': data=[]
  if data is None:
   self.unhandled.append(dict(method=method,path=path)); route.fulfill(status=501,json={'detail':'Unhandled browser fixture: '+path}); return
  route.fulfill(json=copy.deepcopy(data))
 def context(self,browser,width=390,height=844,authed=True):
  ctx=browser.new_context(viewport={'width':width,'height':height},is_mobile=width<900,has_touch=width<900,service_workers='block',reduced_motion='reduce')
  if authed: ctx.add_init_script("localStorage.setItem('atelier.jwt','isolated-ui-fixture')")
  ctx.route('**/api/**',self.route)
  ctx.route('**/test-image.jpg',lambda r:r.fulfill(path=str(ROOT/'frontend/src/assets/sample.jpeg'),content_type='image/jpeg'))
  page=ctx.new_page(); page.set_default_timeout(5000); page.on('pageerror',lambda e:self.errors.append(str(e)))
  page.on('requestfailed',lambda r:self.failed.append({'url':r.url,'failure':r.failure}))
  return ctx,page
 def save(self,path): Path(path).write_text(json.dumps(dict(calls=self.calls,unhandled=self.unhandled,errors=self.errors,failed=self.failed),indent=2))
