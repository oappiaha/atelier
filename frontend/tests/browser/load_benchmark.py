import asyncio,json,threading,time,os
from urllib.parse import urlsplit,parse_qs
from pathlib import Path
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from playwright.async_api import async_playwright
ROOT=Path(__file__).resolve().parents[3]
class Static(SimpleHTTPRequestHandler):
 def __init__(self,*a,**kw):super().__init__(*a,directory=str(ROOT/'frontend/dist'),**kw)
 def do_GET(self):
  if not Path(self.translate_path(self.path)).is_file():self.path='/index.html'
  super().do_GET()
 def log_message(self,*a):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Static)
threading.Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}'
stamp='2026-09-01T12:00:00Z'
image=(ROOT/'frontend/src/assets/sample.jpeg').read_bytes()
async def scenario(browser,n,delay,path,selector):
 ctx=await browser.new_context(viewport={'width':390,'height':844},service_workers='block')
 await ctx.add_init_script("localStorage.setItem('atelier.jwt','perf-fixture')")
 projects=[dict(id='p1',name='Collection',kicker=None,design_count=n)]
 designs=[dict(id=f'd{i}',project_id='p1',name=f'Design {i:03}',status='final',index_no=i,materials=None,category=None,cover_media_id=None,cover_url=f'/perf-image/{i}.jpg',entry_count=1,media_count=1,created_at=stamp) for i in range(n)]
 media=[dict(id=f'm{i}',design_id=f'd{i}',entry_id=None,kind='image',phase='final',url=f'/perf-image/{i}.jpg',thumb_url=f'/perf-image/{i}.jpg',width=600,height=800,duration_ms=None,caption=None,source_url=None,source_app=None,created_at=stamp) for i in range(n)]
 calls=[];images=[];errors=[]
 async def route(r):
  p=r.request.url.split('/api')[1]
  calls.append(p);await asyncio.sleep(delay/1000)
  data=projects if p=='/projects' else [] if p=='/inbox' else designs if p=='/projects/p1/designs' else [media[int(p.split('/')[2][1:])]] if p.endswith('/media') else None
  if p.startswith('/gallery?'):
   q=parse_qs(urlsplit(p).query);start=int(q.get('cursor',['0'])[0]);limit=24
   data=dict(items=[dict(media=media[i],design=designs[i],project=projects[0]) for i in range(start,min(n,start+limit))],projects=projects,counts=dict(final=n,editorial=0),total=n,next_cursor=str(start+limit) if start+limit<n else None)
  if data is None:errors.append(p);await r.fulfill(status=500,json={})
  else:await r.fulfill(json=data)
 async def img(r):
  images.append(r.request.url);await r.fulfill(body=image,content_type='image/jpeg')
 await ctx.route('**/api/**',route)
 await ctx.route('**/perf-image/**',img)
 # Avoid external font network variability; measure app/data behavior independently.
 await ctx.route('https://fonts.googleapis.com/**',lambda r:r.fulfill(body='',content_type='text/css'))
 page=await ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
 t=time.perf_counter();await page.goto(base+path,wait_until='domcontentloaded')
 await page.locator(selector).first.wait_for(state='visible')
 ready=round((time.perf_counter()-t)*1000)
 await page.wait_for_timeout(800)
 cold_calls=len(calls)
 cold_images=len(images)
 if path=='/gallery':
  await page.get_by_role('button',name='Next',exact=True).click()
  await page.locator('.tabbar').get_by_role('button',name='Archive',exact=True).click()
  await page.locator('.proj-row').wait_for()
  t=time.perf_counter()
  await page.locator('.tabbar').get_by_role('button',name='Gallery',exact=True).click()
  await page.locator('.fan-card.center').wait_for()
  warm=round((time.perf_counter()-t)*1000)
 else:warm=None
 result=dict(n=n,delay_ms=delay,path=path,content_ready_ms=ready,api_requests=cold_calls,image_requests=cold_images,warm_return_ms=warm,errors=errors)
 await ctx.close();return result
async def main():
 async with async_playwright() as p:
  b=await p.chromium.launch()
  results=[]
  for path,sel in [('/','.proj-row'),('/p/p1','#dgrid .dname'),('/gallery','.fan-card.center')]:
   for delay in [0,150]:
    for n in ([100] if path!=' /gallery'.strip() else [10,100]):
     for rep in range(3):
      r=await scenario(b,n,delay,path,sel);results.append(r);print(json.dumps(r),flush=True)
  await b.close()
 Path(os.environ.get('ATELIER_PERF_REPORT', '/tmp/atelier-performance.json')).write_text(json.dumps(results,indent=2))
asyncio.run(main());server.shutdown()
