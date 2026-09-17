import json,re
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from fixtures import Fixture,BASE,EVIDENCE
from layout_checks import assert_no_overflow,assert_controls_fit
OUT=EVIDENCE
with sync_playwright() as p:
 b=p.chromium.launch(headless=True);f=Fixture();ctx,page=f.context(b,width=390,height=568)
 page.goto(BASE+'/gallery');page.get_by_role('button',name='Ring',exact=True).click()
 panel=page.locator('.context-panel');expect(panel.get_by_role('button',name='Browse pieces',exact=True)).to_be_visible()
 expect(panel.get_by_role('button',name='Share design',exact=True)).to_have_count(0)
 panel.get_by_role('button',name='Share project…',exact=True).click()
 picker=page.locator('#gallery-project-picker');expect(picker).to_be_visible()
 assert not [c for c in f.calls if c['path']=='/share'],f.calls
 picker.get_by_role('button',name='Share project: Second collection',exact=True).click()
 sheet=page.locator('#sheet-share.open');expect(sheet).to_contain_text('Second collection')
 expect(page.locator('#share-copy')).to_be_enabled()
 assert [c for c in f.calls if c['path']=='/share'][-1]['body']=={'project_id':'p2','scope':'finals'}
 sheet.get_by_role('button',name='Full timeline',exact=True).click()
 expect(page.locator('#share-copy')).to_be_enabled()
 page.keyboard.press('Escape');expect(page.locator('#sheet-share.open')).to_have_count(0)
 panel.get_by_role('button',name='Browse pieces',exact=True).click()
 panel.get_by_role('button',name='Share design',exact=True).click();expect(page.locator('#share-copy')).to_be_enabled()
 calls=[c for c in f.calls if c['path']=='/share' and c['body'].get('design_id')]
 assert calls==[dict(method='POST',path='/share',body=dict(design_id='d1',scope='finals'))],calls
 page.keyboard.press('Escape')
 # Ring view survives tab switching, and context-specific panel follows it.
 page.get_by_role('button',name='Ring',exact=True).click()
 page.locator('.tabbar').get_by_role('button',name='Archive',exact=True).click()
 page.locator('.tabbar').get_by_role('button',name='Gallery',exact=True).click()
 expect(page.locator('#gm-ring')).to_have_class(re.compile('on'))
 expect(panel.get_by_role('button',name='Browse pieces',exact=True)).to_be_visible()
 panel.get_by_role('button',name='View / filter',exact=True).click()
 menu=page.locator('#panel-menu');expect(menu).to_be_visible()
 menu.get_by_role('button',name='Share project…',exact=True).click()
 expect(menu).to_contain_text('Share project: First collection')
 for _ in range(6):
  page.keyboard.press('Tab');assert page.evaluate("document.querySelector('#panel-menu').contains(document.activeElement)")
 page.keyboard.press('Escape');expect(menu).to_have_count(0)
 assert_no_overflow(page);assert_controls_fit(page,panel)
 page.screenshot(path=str(OUT/'ring-panel.png'))
 assert not f.errors,f.errors
 f.save(OUT/'ring-scope-focus.json');ctx.close();b.close()
 print('PASS ring-specific actions, explicit project selection, scope reset, mode retention, nested-menu focus')
