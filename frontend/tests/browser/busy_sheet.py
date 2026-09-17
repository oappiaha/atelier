import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from fixtures import Fixture, BASE, EVIDENCE
OUT=EVIDENCE
results=[]
with sync_playwright() as pw:
 b=pw.chromium.launch(headless=True)
 for width,height in [(320,568),(1440,1000)]:
  f=Fixture(); c,p=f.context(b,width,height); pending=[]
  def hold(route):
   if route.request.method=='POST': pending.append(route)
   else: f.route(route)
  c.route('**/api/designs',hold)
  try:
   p.goto(BASE+'/'); dock=p.locator('.context-panel .panel-actions')
   dock.get_by_role('button',name='New design',exact=True).click()
   p.wait_for_timeout(350); expect(p.locator('#nd-name')).to_be_focused()
   p.locator('#nd-projects').get_by_role('button',name='Second collection').click()
   p.locator('#nd-name').fill('Delayed specimen'); p.locator('#nd-create').click()
   p.wait_for_timeout(300); assert len(pending)==1
   expect(p.locator('#nd-create')).to_be_disabled()
   expect(p.locator('#sheet-newdesign .sheet-close')).to_be_disabled()
   p.keyboard.press('Escape'); expect(p.locator('#sheet-newdesign.open')).to_be_visible()
   p.locator('#sheet-newdesign .backdrop').click(position={'x':5,'y':5},force=True)
   expect(p.locator('#sheet-newdesign.open')).to_be_visible()
   p.locator('#nd-name').press('Enter'); p.wait_for_timeout(150); assert len(pending)==1
   f.route(pending.pop()); expect(p.locator('#sheet-newdesign.open')).to_have_count(0)
   dock.get_by_role('button',name='New design',exact=True).click()
   p.wait_for_timeout(350); expect(p.locator('#nd-name')).to_be_focused()
   expect(p.locator('#nd-name')).to_have_value('')
   p.keyboard.press('Escape'); expect(p.locator('#sheet-newdesign.open')).to_have_count(0)
   assert not f.errors, f.errors
   assert not f.unhandled, f.unhandled
   results.append({'width':width,'passed':True,'checks':['delayed autofocus','pending request disables create/close','Escape and backdrop preserve busy sheet','Enter does not duplicate pending POST','completion closes sheet','reopen resets focus/name/busy and Escape closes']})
  finally:
   f.save(OUT/f'delayed-busy-{width}.json'); c.close()
 b.close()
(OUT/'delayed-busy-results.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results,indent=2))
