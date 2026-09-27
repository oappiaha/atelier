"""Long labels, keyboard return focus and repeated Gallery history restoration."""
from playwright.sync_api import sync_playwright, expect
from fixtures import Fixture, BASE, EVIDENCE
from layout_checks import assert_controls_fit, assert_no_overflow

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    fixture = Fixture()
    fixture.projects[0]['name'] = 'Spring summer collection of very long project titles ' * 4
    context, page = fixture.context(browser, 320, 568)
    page.goto(BASE + '/d/d1')
    panel = page.locator('.context-panel')
    expect(panel).to_be_visible()
    page.wait_for_timeout(200)
    assert panel.bounding_box()['height'] <= 125
    assert_controls_fit(page, panel)
    assert_no_overflow(page)
    page.screenshot(path=str(EVIDENCE / 'long-label.png'))
    context.close()

    fixture = Fixture()
    context, page = fixture.context(browser, 390, 480)
    page.goto(BASE + '/gallery')
    expect(page.locator('.fan-card.center')).to_be_visible()
    page.wait_for_timeout(200)
    page.evaluate('scrollTo(0, 120)')
    page.wait_for_timeout(150)
    original = page.evaluate('scrollY')
    assert original > 25, 'Fixture must be scrollable to verify distinct positions'
    page.locator('.panel-actions').get_by_role('button', name='Open', exact=True).click()
    page.locator('.back-inline').click()
    page.wait_for_timeout(250)
    assert abs(page.evaluate('scrollY') - original) < 2
    page.evaluate('scrollTo(0, 25)')
    page.wait_for_timeout(150)
    page.locator('.panel-actions').get_by_role('button', name='Open', exact=True).click()
    page.go_back()
    page.wait_for_timeout(300)
    assert abs(page.evaluate('scrollY') - 25) < 2

    page.goto(BASE + '/d/d1')
    more = page.locator('.panel-actions').get_by_role('button', name='More', exact=True)
    more.focus()
    page.keyboard.press('Enter')
    page.wait_for_timeout(150)
    page.locator('#panel-menu').get_by_role('button', name='Share', exact=True).focus()
    page.keyboard.press('Enter')
    page.wait_for_timeout(200)
    page.keyboard.press('Escape')
    expect(more).to_be_focused()
    assert not fixture.errors, fixture.errors
    assert not fixture.unhandled, fixture.unhandled
    fixture.save(EVIDENCE / 'navigation-edges.json')
    context.close()
    browser.close()
print('PASS compact long labels, repeated Gallery scroll restoration, keyboard return focus')
