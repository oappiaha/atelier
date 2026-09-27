import threading,json
from pathlib import Path
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from playwright.sync_api import sync_playwright,expect
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
try:
 with sync_playwright() as p:
  b=p.chromium.launch();ctx=b.new_context(service_workers='allow');page=ctx.new_page()
  requests=[];page.on('request',lambda r: requests.append(r.url))
  page.goto(base+'/login');page.evaluate('navigator.serviceWorker.ready')
  page.wait_for_function('navigator.serviceWorker.controller !== null')
  assert not any('/Studio-' in u or '/Gallery-' in u for u in requests),requests
  cache=page.evaluate("""async () => {
   const names=await caches.keys();
   return (await Promise.all(names.map(async name=>(await (await caches.open(name)).keys()).map(r=>r.url)))).flat()
  }""")
  assert any('/assets/index-' in u for u in cache)
  assert any('/assets/api-' in u for u in cache)
  assert not any('/Studio-' in u or '/Gallery-' in u for u in cache)
  ctx.set_offline(True)
  page.reload()
  expect(page.get_by_role('button',name='Send magic link')).to_be_visible()
  print('PASS service worker precaches shell, excludes unvisited routes; offline shell reload')
  Path('/tmp/atelier-pwa-cache-check.json').write_text(json.dumps(dict(requests=requests,cache=cache,offline_shell=True),indent=2))
  ctx.close();b.close()
finally:server.shutdown()
