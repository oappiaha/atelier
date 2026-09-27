"""Bounded requests/images, lazy chunks, pagination, filters and Studio entry."""
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect
from fixtures import Fixture, BASE, EVIDENCE

class GalleryFixture(Fixture):
    fail = False
    def route(self, route):
        if self.fail and '/api/gallery' in route.request.url:
            route.fulfill(status=503, json={'detail': 'Try again'})
        else:
            super().route(route)

with sync_playwright() as pw:
    b = pw.chromium.launch()
    f = GalleryFixture()
    f.designs = [dict(f.designs[0], id=f'd{i}', name=f'Design {i:03}', index_no=i) for i in range(100)]
    original = f.media[0]
    f.media = [dict(original, id=f'm{i:03}', design_id=f'd{i}', phase='final' if i < 80 else 'editorial',
                    url=f'/test-image.jpg?id={i}', thumb_url=f'/test-image.jpg?id={i}') for i in range(100)]
    ctx, page = f.context(b)
    image_urls = set()
    scripts = set()
    from fixtures import ROOT
    def image(route):
        image_urls.add(route.request.url)
        route.fulfill(path=str(ROOT / 'frontend/src/assets/sample.jpeg'), content_type='image/jpeg')
    ctx.route('**/test-image.jpg?*', image)
    page.on('request', lambda r: scripts.add(urlsplit(r.url).path) if r.resource_type == 'script' else None)
    page.goto(BASE + '/gallery')
    expect(page.locator('.fan-card.center')).to_contain_text('Design 079')
    expect(page.get_by_role('button', name='Everything · 100', exact=True)).to_be_visible()
    page.wait_for_timeout(200)
    assert len(f.calls) == 1, f.calls
    assert len(image_urls) == 3, image_urls
    assert page.locator('.fan-card').count() <= 5
    assert not any('/Studio-' in u or '/StudioHub-' in u for u in scripts), scripts
    # Approach and cross the first page's boundary.
    for _ in range(25):
        page.get_by_role('button', name='Next', exact=True).click()
    expect(page.locator('.fan-card.center')).to_contain_text('Design 054')
    assert len([c for c in f.calls if c['path'] == '/gallery']) == 2
    assert page.locator('.fan-card').count() <= 5
    page.locator('.panel-actions').get_by_role('button', name='Open', exact=True).click()
    page.locator('.panel-actions').get_by_role('button', name='Back', exact=True).click()
    expect(page.locator('.fan-card.center')).to_contain_text('Design 054')
    for _ in range(74):
        page.get_by_role('button', name='Next', exact=True).click()
    expect(page.locator('#fan-idx')).to_have_text('100')
    expect(page.locator('.fan-card.center')).to_contain_text('Design 080')
    assert len([c for c in f.calls if c['path'] == '/gallery']) == 5
    page.get_by_role('button', name='Editorial · 20', exact=True).click()
    expect(page.locator('.fan-card.center')).to_contain_text('Design 099')
    page.get_by_role('button', name='Ring', exact=True).click()
    expect(page.locator('.ring-item')).to_have_count(16)
    page.locator('.ring-item').nth(2).click()
    expect(page.locator('.fan-card.center')).to_contain_text('Design 097')
    page.locator('.panel-actions').get_by_role('button', name='Share', exact=True).click()
    expect(page.locator('#share-copy')).to_be_enabled()
    assert [c for c in f.calls if c['path']=='/share'][-1]['body']['design_id']=='d97'
    page.keyboard.press('Escape')
    page.screenshot(path=str(EVIDENCE/'gallery-lazy.png'))
    page.goto(BASE + '/d/d1/studio')
    expect(page.locator('.panel-actions').get_by_role('button', name='New study', exact=True)).to_be_visible()
    page.wait_for_timeout(300)
    assert not any(c['path'].endswith('/segment') for c in f.calls)
    assert any('/StudioHub-' in u for u in scripts)
    assert not f.errors, f.errors
    assert not f.unhandled, f.unhandled
    f.save(EVIDENCE/'gallery-loading.json')
    ctx.close()
    # Failure is recoverable and not misrepresented as an empty archive.
    f = GalleryFixture(); f.fail = True
    ctx, page = f.context(b)
    page.goto(BASE+'/gallery')
    expect(page.get_by_role('alert')).to_contain_text('Could not load')
    f.fail = False
    page.get_by_role('button', name='Retry', exact=True).click()
    expect(page.locator('.fan-card.center')).to_be_visible()
    assert not f.errors, f.errors
    ctx.close()
    b.close()
print('PASS bounded images/API; page boundary; return/filter/share; Ring bound; lazy Studio; no prewarm; retry')
