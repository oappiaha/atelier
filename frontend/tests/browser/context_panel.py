import json,re,traceback
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from fixtures import Fixture,BASE,EVIDENCE
from layout_checks import assert_no_overflow,assert_controls_fit
OUT=EVIDENCE;OUT.mkdir(exist_ok=True)
results=[]
def run(name,fn):
 try:
  fn();results.append({'test':name,'passed':True});print('PASS',name,flush=True)
 except Exception as e:
  results.append({'test':name,'passed':False,'error':str(e)});print('FAIL',name,str(e),flush=True)
  traceback.print_exc()
def button(panel,name):return panel.get_by_role('button',name=name,exact=True)
def wait_panel(page):
 panel=page.locator('.context-panel');expect(panel).to_be_visible();return panel

def screens(browser):
 for width,height in [(390,844),(320,568),(1440,1000)]:
  fixture=Fixture();ctx,page=fixture.context(browser,width,height)
  try:
   for path in ['/','/p/p1','/d/d1','/gallery','/inbox','/studies','/d/d1/studio']:
    page.goto(BASE+path);page.wait_for_timeout(400)
    assert_no_overflow(page)
    panel=wait_panel(page)
    assert_controls_fit(page,panel)
    assert panel.locator('.panel-actions button').count()>0,path
    page.screenshot(path=str(OUT/f'layout-{width}-{path.replace("/","_") or "home"}.png'))
   assert not fixture.errors,fixture.errors
   assert not fixture.unhandled,fixture.unhandled
  finally:fixture.save(OUT/f'layout-{width}.json');ctx.close()

def gallery_journey(browser):
 fixture=Fixture();ctx,page=fixture.context(browser)
 try:
  page.goto(BASE+'/gallery');panel=wait_panel(page)
  page.get_by_role('button',name='Editorial · 1',exact=True).click()
  expect(page.locator('.fan-card.center')).to_contain_text('Second bag')
  button(panel,'Open').click()
  expect(page).to_have_url(re.compile('/d/d2$'))
  expect(page.locator('.tabbar [aria-current="page"]')).to_contain_text('Gallery')
  page.locator('.back-inline').click()
  expect(page).to_have_url(re.compile('/gallery'))
  expect(page.locator('.chips .on').first).to_contain_text('Editorial')
  expect(page.locator('.fan-card.center')).to_contain_text('Second bag')
  button(wait_panel(page),'Open').click();page.go_back()
  expect(page.locator('.fan-card.center')).to_contain_text('Second bag')
  page.screenshot(path=str(OUT/'gallery-restored.png'))
  assert not fixture.errors,fixture.errors
 finally:fixture.save(OUT/'gallery-journey.json');ctx.close()

def focused_share(browser):
 fixture=Fixture();ctx,page=fixture.context(browser)
 try:
  page.goto(BASE+'/gallery');panel=wait_panel(page)
  page.get_by_role('button',name='Next',exact=True).click()
  expect(page.locator('.fan-card.center')).to_contain_text('Third shirt')
  button(panel,'Share').click()
  expect(page.locator('#sheet-share.open')).to_contain_text('Third shirt')
  expect(page.locator('#share-copy')).to_be_enabled()
  calls=[c for c in fixture.calls if c['path']=='/share']
  assert calls and calls[-1]['body']=={'design_id':'d3','scope':'finals'},calls
  expect(page.locator('.context-panel')).not_to_be_visible()
  page.keyboard.press('Escape');expect(page.locator('#sheet-share.open')).to_have_count(0)
  expect(page.locator('.context-panel')).to_be_visible()
  assert not fixture.errors,fixture.errors
 finally:fixture.save(OUT/'focused-share.json');ctx.close()

def direct_entry(browser):
 fixture=Fixture();ctx,page=fixture.context(browser)
 try:
  page.goto(BASE+'/d/d3');wait_panel(page)
  page.locator('.back-inline').click();expect(page).to_have_url(BASE+'/p/p2')
  button(wait_panel(page),'New design').click()
  expect(page.locator('#sheet-newdesign.open')).to_contain_text('Second collection')
  expect(page.locator('.context-panel')).not_to_be_visible()
  page.locator('#nd-name').fill('Browser-created design')
  page.locator('#sheet-newdesign button[type="submit"]').click()
  expect(page.locator('#sheet-newdesign.open')).to_have_count(0)
  expect(page.locator('#dgrid')).to_contain_text('Browser-created design')
  create=[c for c in fixture.calls if c['path']=='/designs' and c['method']=='POST']
  assert create[-1]['body']['project_id']=='p2',create
  assert not fixture.errors,fixture.errors
 finally:fixture.save(OUT/'direct-create.json');ctx.close()

def unauth(browser):
 fixture=Fixture();ctx,page=fixture.context(browser,authed=False)
 try:
  page.goto(BASE+'/gallery');expect(page).to_have_url(BASE+'/login')
  expect(page.get_by_role('button',name='Send magic link')).to_be_visible()
  assert not fixture.calls,fixture.calls
 finally:ctx.close()
with sync_playwright() as p:
 b=p.chromium.launch(headless=True)
 for name,fn in [('responsive-screen-panels',screens),('gallery-return-origin',gallery_journey),('focused-design-share',focused_share),('direct-fallback-create',direct_entry),('unauthenticated-preserved',unauth)]:run(name,lambda fn=fn:fn(b))
 b.close()
(OUT/'results.json').write_text(json.dumps(results,indent=2))
raise SystemExit(0 if all(r['passed'] for r in results) else 1)
