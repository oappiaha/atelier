"""Additional state/mutation checks, run after worker hands back the checkout."""
import json,re,traceback
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from fixtures import Fixture,BASE,EVIDENCE
from layout_checks import assert_no_overflow
OUT=EVIDENCE;OUT.mkdir(exist_ok=True)
results=[]
def action(page,name):return page.locator('.context-panel .panel-actions').get_by_role('button',name=name,exact=True)
def goto(page,path):page.goto(BASE+path);expect(page.locator('.context-panel')).to_be_visible()
def close(page,selector):
 page.keyboard.press('Escape');expect(page.locator(selector+'.open')).to_have_count(0);expect(page.locator('.context-panel')).to_be_visible()
def inbox(browser):
 f=Fixture();ctx,page=f.context(browser)
 try:
  goto(page,'/inbox');action(page,'Sort next').click()
  sheet=page.locator('#sheet-triage.open');expect(sheet).to_be_visible()
  expect(page.locator('.context-panel')).not_to_be_visible()
  expect(page.locator('#triage-confirm')).to_be_disabled()
  sheet.get_by_role('button',name=re.compile('Third shirt')).click()
  sheet.get_by_role('button',name='Editorial',exact=True).click()
  page.locator('#triage-confirm').click()
  expect(page.locator('#inbox-empty')).to_be_visible()
  expect(action(page,'Sort next')).to_be_disabled()
  calls=[c for c in f.calls if c['path'].endswith('/triage')]
  assert calls[-1]['body']=={'design_id':'d3','phase':'editorial'},calls
  page.screenshot(path=str(OUT/'inbox-sorted.png'))
  assert not f.errors,f.errors
 finally:f.save(OUT/'inbox-mutation.json');ctx.close()
def context(browser):
 f=Fixture();ctx,page=f.context(browser)
 try:
  goto(page,'/d/d3');action(page,'Add').click()
  expect(page.locator('#sheet-capture.open #dest-name')).to_have_text('Third shirt')
  close(page,'#sheet-capture')
  action(page,'More').click()
  page.locator('#panel-menu').get_by_role('button',name='Studio',exact=True).click()
  expect(page).to_have_url(BASE+'/d/d3/studio')
  expect(page.locator('.tabbar [aria-current="page"]')).to_contain_text('Studies')
  action(page,'More').click();page.locator('#panel-menu').get_by_role('button',name='Add',exact=True).click()
  expect(page.locator('#sheet-capture.open #dest-name')).to_have_text('Third shirt')
  close(page,'#sheet-capture')
  page.locator('.tabbar').get_by_role('button',name='Archive',exact=True).click()
  action(page,'Capture').click();expect(page.locator('#sheet-capture.open #dest-name')).to_have_text('Inbox')
  close(page,'#sheet-capture')
  page.locator('.tabbar').get_by_role('button',name='Studies',exact=True).click()
  expect(page).to_have_url(BASE+'/studies')
  expect(page.locator('.tabbar [aria-current="page"]')).to_contain_text('Studies')
  assert not f.errors,f.errors
 finally:f.save(OUT/'capture-context.json');ctx.close()
def empty(browser):
 f=Fixture(empty=True);ctx,page=f.context(browser,width=320,height=568)
 try:
  goto(page,'/gallery');page.wait_for_timeout(300)
  for label in ['Open','Share']:
   candidate=action(page,label)
   assert candidate.count()==0 or candidate.is_disabled(),label
  assert_no_overflow(page);page.screenshot(path=str(OUT/'empty-gallery.png'))
  goto(page,'/inbox');expect(page.locator('#inbox-empty')).to_be_visible();expect(action(page,'Sort next')).to_be_disabled()
  goto(page,'/d/d3/studio');expect(action(page,'New study')).to_be_disabled()
  assert not f.errors,f.errors
 finally:f.save(OUT/'empty-states.json');ctx.close()
with sync_playwright() as p:
 b=p.chromium.launch(headless=True)
 for name,fn in [('inbox-sort-readback',inbox),('nested-capture-context',context),('empty-action-states',empty)]:
  try:fn(b);results.append(dict(test=name,passed=True));print('PASS',name,flush=True)
  except Exception as e:results.append(dict(test=name,passed=False,error=str(e)));print('FAIL',name,str(e),flush=True);traceback.print_exc()
 b.close()
(OUT/'results-extra.json').write_text(json.dumps(results,indent=2))
raise SystemExit(0 if all(r['passed'] for r in results) else 1)
