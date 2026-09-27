from playwright.sync_api import sync_playwright, expect
from fixtures import Fixture, BASE, EVIDENCE

class WordmarkFixture(Fixture):
    def route(self, route):
        if route.request.method == 'PATCH' and route.request.url.endswith('/projects/p1/wordmark'):
            self.projects[0]['wordmark'] = route.request.post_data_json['wordmark']
            route.fulfill(status=204)
        else:
            super().route(route)

with sync_playwright() as pw:
    b = pw.chromium.launch()
    for width in [320, 390, 1440]:
        f = WordmarkFixture(); ctx, page = f.context(b, width, 844)
        page.goto(BASE + '/p/p1')
        expect(page.locator('.collection-title')).to_have_text('First collection')
        expect(page.locator('#project-share')).to_have_count(0)
        assert page.locator('.collection-header').bounding_box()['height'] < 85
        page.screenshot(path=str(EVIDENCE / f'compact-header-{width}.png'))
        def open_sheet():
            page.locator('.panel-actions').get_by_role('button', name='More', exact=True).click()
            page.locator('#panel-menu').get_by_role('button', name='Wordmark', exact=True).click()
        open_sheet()
        file = page.locator('#wordmark-sheet input[type=file]')
        file.set_input_files({'name':'bad.txt','mimeType':'text/plain','buffer':b'not an image'})
        expect(page.get_by_role('alert')).to_contain_text('Choose a PNG')
        file.set_input_files({'name':'mark.svg','mimeType':'image/svg+xml','buffer':b'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="140"><text x="0" y="110" font-family="sans-serif" font-size="100" font-weight="bold">COLLECTION</text></svg>'})
        expect(page.locator('.wordmark-preview img')).to_be_visible()
        page.get_by_role('button', name='Save', exact=True).click()
        expect(page.locator('#wordmark-sheet')).to_have_count(0)
        page.reload()
        expect(page.locator('.collection-wordmark')).to_have_attribute('alt', 'First collection')
        assert page.locator('.collection-wordmark').bounding_box()['height'] <= 64
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(EVIDENCE / f'wordmark-{width}.png'))
        open_sheet()
        page.get_by_role('button', name='Use text', exact=True).click()
        page.get_by_role('button', name='Save', exact=True).click()
        page.reload()
        expect(page.locator('.collection-title')).to_have_text('First collection')
        page.locator('.panel-actions').get_by_role('button', name='More', exact=True).click()
        page.locator('#panel-menu').get_by_role('button', name='Share', exact=True).click()
        expect(page.locator('#sheet-share.open')).to_contain_text('First collection')
        assert not f.errors, f.errors
        assert not f.unhandled, f.unhandled
        ctx.close()
    b.close()
print('PASS compact header; upload SVG rasterization/preview/save/reload/remove; invalid file; More sharing; 320/390/1440')
