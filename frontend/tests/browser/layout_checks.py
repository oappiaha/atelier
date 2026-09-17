"""Layout assertions shared by independent real-browser acceptance."""
from pathlib import Path

def assert_no_overflow(page):
 result=page.evaluate('''() => ({width:innerWidth, body:document.body.scrollWidth, root:document.documentElement.scrollWidth})''')
 assert result['body']<=result['width']+1 and result['root']<=result['width']+1,result
 return result

def assert_controls_fit(page, panel):
 box=panel.bounding_box(); assert box, 'Panel not visible'
 viewport=page.viewport_size
 assert box['x']>=0 and box['x']+box['width']<=viewport['width']+1,box
 assert box['y']>=0 and box['y']+box['height']<=viewport['height']+1,box
 results=[]
 for c in panel.locator('button,a').all():
  if not c.is_visible():continue
  b=c.bounding_box(); label=c.get_attribute('aria-label') or c.inner_text()
  assert b['height']>=40,(label,b)
  assert b['x']>=box['x']-1 and b['x']+b['width']<=box['x']+box['width']+1,(label,b,box)
  results.append(dict(label=label,box=b))
 return results

def screenshot(page,path):
 page.screenshot(path=str(path),full_page=False)
