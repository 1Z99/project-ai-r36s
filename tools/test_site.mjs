import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';

// Optional development tooling; the static site itself needs no Node packages.
let chromium;
try { ({ chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')); }
catch { console.error('Install Playwright for browser tests, or set PLAYWRIGHT_MODULE to its index.mjs path.'); process.exit(1); }
const launch = { headless: true, args: ['--mute-audio'] };
if (process.env.CHROMIUM_EXECUTABLE) launch.executablePath = process.env.CHROMIUM_EXECUTABLE;
if (process.env.SITE_TEST_NO_SANDBOX === '1') launch.args.push('--no-sandbox');
const browser = await chromium.launch(launch);
const origin = (process.env.SITE_URL || 'http://127.0.0.1:2270').replace(/\/$/, '');
const project = `${origin}/projects/227-ai`;
const tvProject = `${origin}/projects/227-tv`;
const output = process.env.SITE_TEST_OUTPUT || join(tmpdir(), '227-site-browser');
await mkdir(output, { recursive: true });
const errors = [];
const externalRequests = [];
let checks = 0;
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, permissions: ['clipboard-read', 'clipboard-write'] });
context.on('request', request => {
  if (new URL(request.url()).origin !== new URL(origin).origin) externalRequests.push(request.url());
});
context.on('page', page => {
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
});
const page = await context.newPage();
try {
  for (const width of [1440, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    for (const file of ['index.html', ...['227-ai', '227-tv'].flatMap(project =>
      ['index.html', 'guide.html', 'reference.html', 'gallery.html', 'development.html'].map(name => `projects/${project}/${name}`))]) {
      const response = await page.goto(`${origin}/${file}`, { waitUntil: 'networkidle' });
      assert.equal(response.status(), 200);
      const layout = await page.evaluate(() => ({ view: innerWidth, document: document.documentElement.scrollWidth, main: document.querySelector('main').getBoundingClientRect().width }));
      assert(layout.document <= layout.view + 1, `${file} @ ${width} overflow: ${JSON.stringify(layout)}`);
      assert(await page.getByRole('heading', { level: 1 }).isVisible());
      checks += 2;
      if (file === 'index.html') {
        const specs = page.locator('.workshop-device-specs dd');
        assert.equal(await specs.count(), 4);
        assert(await specs.evaluateAll(values => values.every(value => {
          const box = value.getBoundingClientRect();
          return parseFloat(getComputedStyle(value).fontSize) >= 18 && value.scrollWidth <= box.width + 1;
        })), `Device specs must remain readable without clipping @ ${width}`);
        checks += 2;
      }
      if (file.startsWith('projects/')) {
        assert(await page.getByRole('navigation', { name: 'Breadcrumb', exact: true }).isVisible());
        assert.equal(await page.locator('.project-nav a[aria-current="page"]').count(), 1);
        assert.equal(await page.locator('.site-header .brand').getAttribute('aria-label'), '1Z99 home');
        checks += 3;
      }
      if (file === 'projects/227-ai/guide.html') {
        assert.equal(await page.locator('[data-easy-install] > li').count(), 2);
        assert(await page.locator('#easy-install').isVisible());
        assert.match(await page.locator('[data-easy-install]').textContent(), /Only then restart the handheld/);
        checks += 3;
      }
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`${tvProject}/gallery.html`, { waitUntil: 'networkidle' });
  assert.equal(await page.locator('.gallery-grid .shot[data-loaded="true"]').count(), 9);
  assert.equal(await page.locator('[data-gallery-count]').textContent(), '9 images');
  assert.equal(await page.locator('.gallery-grid .capture-origin').filter({ hasText: 'PC playback / native capture' }).count(), 4);
  await page.locator('.gallery-grid img').evaluateAll(images => Promise.all(images.map(image => image.decode())));
  await page.screenshot({ path: join(output, 'tv-gallery-desktop.png'), fullPage: true });
  await page.getByRole('button', { name: 'App', exact: true }).click();
  assert.equal(await page.locator('.gallery-grid .shot:visible').count(), 7);
  await page.getByRole('button', { name: 'Installer', exact: true }).click();
  assert.equal(await page.locator('.gallery-grid .shot:visible').count(), 2);
  await page.getByRole('button', { name: 'All screenshots', exact: true }).click();
  const watchOpener = page.locator('[data-shot="watch"] button');
  await watchOpener.click();
  assert(await page.getByRole('dialog', { name: 'Screenshot viewer' }).isVisible());
  await page.keyboard.press('Escape');
  assert(await watchOpener.evaluate(button => button === document.activeElement));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: join(output, 'tv-gallery-mobile.png'), fullPage: true });
  await page.goto(`${tvProject}/index.html`, { waitUntil: 'networkidle' });
  assert.equal(await page.locator('.shot[data-loaded="true"]').count(), 3);
  await page.screenshot({ path: join(output, 'tv-overview-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: join(output, 'tv-overview-desktop.png'), fullPage: true });
  checks += 8;
  await page.goto(`${project}/index.html`, { waitUntil: 'networkidle' });
  assert.equal(await page.locator('.shot[data-loaded="true"]').count(), 1);
  await page.screenshot({ path: join(output, 'project-desktop.png'), fullPage: true });
  checks++;
  await page.goto(origin, { waitUntil: 'networkidle' });
  await page.screenshot({ path: join(output, 'desktop.png') });
  await page.screenshot({ path: join(output, 'overview-full.png'), fullPage: true });
  await page.locator('#device').screenshot({ path: join(output, 'device-desktop.png') });
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
  assert.match(await page.title(), /^1Z99/);
  assert(await page.locator('.handheld-illustration').isVisible());
  const demo = page.locator('.handheld-illustration image.device-demo-poster');
  assert(await demo.isVisible());
  const demoResponse = await page.request.get(new URL(await demo.getAttribute('href'), page.url()).href);
  assert.equal(demoResponse.status(), 200);
  assert.match(demoResponse.headers()['content-type'], /image\/png/);
  assert.match(await page.locator('#device').textContent(), /Panel 4/);
  assert(await page.locator('.project-cards > li .project-feature').count() >= 1);
  checks += 7;
  const walkthrough = page.locator('video.device-demo');
  await page.waitForFunction(() => {
    const video = document.querySelector('video.device-demo');
    return video.readyState >= 2 && !video.paused && video.currentTime > .2;
  });
  assert(await walkthrough.evaluate(video => video.muted && video.loop));
  await page.getByRole('button', { name: 'Pause walkthrough', exact: true }).click();
  assert(await walkthrough.evaluate(video => video.paused));
  await page.locator('.demo-position').evaluate(input => { input.value = '42'; input.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.waitForFunction(() => {
    const video = document.querySelector('video.device-demo');
    return !video.seeking && Math.abs(video.currentTime - 42) < .2;
  });
  await page.getByRole('button', { name: 'Expand', exact: true }).click();
  const player = page.getByRole('dialog', { name: '227 AI / AUTOMATED PC WALKTHROUGH' });
  assert(await player.isVisible());
  assert.equal(await page.locator('video.device-demo').count(), 1);
  assert(Math.abs(await walkthrough.evaluate(video => video.currentTime) - 42) < .3);
  await player.locator('summary').click();
  await player.getByRole('button', { name: /Read Aloud/ }).click();
  await page.waitForFunction(() => {
    const video = document.querySelector('video.device-demo');
    return !video.paused && video.currentTime >= 20 && video.currentTime < 23;
  });
  await player.getByRole('button', { name: 'Enable walkthrough sound' }).click();
  assert.equal(await walkthrough.evaluate(video => video.muted), false);
  await player.getByRole('button', { name: 'Captions off', exact: true }).click();
  assert.equal(await walkthrough.evaluate(video => video.textTracks[0].mode), 'showing');
  await player.getByRole('button', { name: 'Captions on', exact: true }).click();
  await player.getByRole('button', { name: 'Mute walkthrough sound' }).click();
  const expandedHeader = await player.locator('.demo-dialog-header').boundingBox();
  assert(expandedHeader.y >= 0 && expandedHeader.y + expandedHeader.height < 1000, 'Expanded header and close control must remain visible');
  await page.screenshot({ path: join(output, 'walkthrough-expanded.png') });
  await player.getByRole('button', { name: 'Close expanded walkthrough' }).click();
  await page.locator('.handheld-illustration video.device-demo').waitFor({ state: 'visible' });
  assert(await page.locator('.handheld-illustration video.device-demo').isVisible());
  assert(await walkthrough.evaluate(video => video.muted));
  await page.getByRole('button', { name: 'Replay', exact: true }).click();
  assert(await walkthrough.evaluate(video => video.currentTime < 1));
  await page.evaluate(() => {
    const spacer = document.createElement('div');
    spacer.dataset.testSpacer = '';
    spacer.style.height = '1200px';
    document.body.append(spacer);
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' });
  });
  await page.waitForFunction(() => document.querySelector('video.device-demo').paused);
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
  await page.waitForFunction(() => !document.querySelector('video.device-demo').paused);
  await page.locator('[data-test-spacer]').evaluate(element => element.remove());
  checks += 13;
  await page.goto(`${project}/guide.html`, { waitUntil: 'networkidle' });
  await page.getByLabel('SD2 /roms2').check();
  assert.match(await page.locator('[data-install-command]').textContent(), /^cd \/roms2\/tools/);
  assert.match(await page.locator('[data-install-launch]').textContent(), /sh \/roms2\/tools\/install227ai\.sh/);
  await page.locator('.code-block:has([data-install-command]) [data-copy]').click();
  assert.match(await page.evaluate(() => navigator.clipboard.readText()), /^cd \/roms2\/tools/);
  checks += 3;
  await page.goto(origin, { waitUntil: 'networkidle' });

  assert.equal(await page.locator('[data-theme-toggle]').count(), 0);
  const desktopMenu = page.getByRole('button', { name: 'Menu', exact: true });
  assert(await desktopMenu.isVisible());
  await desktopMenu.click();
  assert.equal(await desktopMenu.getAttribute('aria-expanded'), 'true');
  assert(await page.getByRole('navigation', { name: 'Site menu', exact: true }).isVisible());
  await page.keyboard.press('Escape');
  assert.equal(await desktopMenu.getAttribute('aria-expanded'), 'false');
  assert(await desktopMenu.evaluate(button => button === document.activeElement));
  await desktopMenu.click();
  await page.getByRole('heading', { level: 1 }).click();
  assert.equal(await desktopMenu.getAttribute('aria-expanded'), 'false');
  await page.evaluate(() => localStorage.setItem('227-site-theme', 'light'));
  await page.reload({ waitUntil: 'networkidle' });
  assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
  assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme), 'dark');
  await page.evaluate(() => window.scrollTo(0, 0));
  checks += 7;

  await page.keyboard.press('/');
  const search = page.getByRole('dialog', { name: 'SEARCH 1Z99', exact: true });
  assert(await search.isVisible());
  await search.getByRole('searchbox').fill('microphone');
  await page.waitForFunction(() => document.querySelector('#search-help').textContent.includes('matching sections'));
  assert(await search.getByRole('link').count() > 0);
  await search.getByRole('searchbox').fill('298');
  await page.waitForFunction(() => document.querySelector('.search-result-page')?.textContent.startsWith('227 TV /'));
  assert((await search.getByRole('link').first().getAttribute('href')).includes('projects/227-tv/'));
  checks++;
  await search.getByRole('searchbox').fill('zxqvdoesnotexist');
  await page.waitForFunction(() => document.querySelector('.search-empty')?.textContent.includes('No sections found'));
  await search.getByRole('searchbox').fill('c83568c');
  await page.waitForFunction(() => document.querySelector('#search-help').textContent.includes('matching sections'));
  assert((await search.getByRole('link').first().getAttribute('href')).includes('projects/227-ai/development.html'));
  assert.match(await search.locator('.search-result-page').first().textContent(), /^227 AI \/ /);
  await page.keyboard.press('ArrowDown');
  assert.equal(await page.evaluate(() => document.activeElement.tagName), 'A');
  await page.keyboard.press('Escape');
  assert(!(await search.isVisible()));
  checks += 5;
  await page.keyboard.press('Control+k');
  assert(await search.isVisible());
  await search.getByRole('checkbox', { name: 'Enable / shortcut' }).uncheck();
  await page.keyboard.press('Escape');
  await page.keyboard.press('/');
  assert(!(await search.isVisible()));
  await page.keyboard.press('Control+k');
  await search.getByRole('checkbox', { name: 'Enable / shortcut' }).check();
  await page.keyboard.press('Escape');
  checks += 2;

  await page.goto(`${project}/gallery.html`, { waitUntil: 'networkidle' });
  assert.equal(await page.locator('.gallery-grid .shot:visible').count(), 12);
  assert.equal(await page.locator('.gallery-grid .shot[data-loaded="true"]').count(), 12);
  assert.equal(await page.locator('.gallery-grid .capture-origin').count(), 12);
  assert.equal(await page.locator('[data-gallery-count]').textContent(), '12 images');
  assert.equal(await page.locator('.gallery-grid .shot').first().getAttribute('data-shot'), 'installer');
  const installerVideo = page.getByLabel('Installer PC demo', { exact: true });
  assert(await installerVideo.evaluate(video => video.paused && !video.autoplay));
  await installerVideo.evaluate(video => { video.muted = true; return video.play(); });
  await page.waitForFunction(() => document.querySelector('.installer-demo video').currentTime > .2);
  assert(await installerVideo.evaluate(video => video.videoWidth === 640 && video.videoHeight === 480 && video.duration === 14));
  await page.getByRole('button', { name: 'Installer', exact: true }).click();
  assert.equal(await page.locator('.gallery-grid .shot:visible').count(), 4);
  await page.getByRole('button', { name: 'App', exact: true }).click();
  assert.equal(await page.locator('.gallery-grid .shot:visible').count(), 8);
  assert(!(await page.locator('#captures').isVisible()));
  assert(await installerVideo.evaluate(video => video.paused));
  await page.evaluate(() => { location.hash = 'captures'; });
  await page.waitForFunction(() => !document.querySelector('#captures').hidden);
  assert(await page.locator('#captures').isVisible());
  await page.getByRole('button', { name: 'All screenshots' }).click();
  assert(await page.locator('#capture-plan a[href$="LICENSE.txt"]').isVisible());
  await page.locator('.gallery-grid img').evaluateAll(images => Promise.all(images.map(image => image.decode())));
  await installerVideo.evaluate(video => { video.preload = 'metadata'; video.load(); });
  await page.waitForFunction(() => document.querySelector('.installer-demo video').readyState >= 2);
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
  await page.screenshot({ path: join(output, 'gallery.png'), fullPage: true });
  checks += 18;

  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: join(output, 'gallery-mobile.png'), fullPage: true });
  await page.locator('.installer-preview').screenshot({ path: join(output, 'installer-demo-mobile.png') });
  await page.locator('[data-shot="prompt"]').screenshot({ path: join(output, 'prompt-mobile.png') });
  await page.setViewportSize({ width: 1440, height: 1000 });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(origin, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => {
    const video = document.querySelector('video.device-demo');
    return video.readyState >= 2 && !video.paused && video.currentTime > .1;
  });
  await page.screenshot({ path: join(output, 'mobile.png'), fullPage: true });
  await page.locator('#device').screenshot({ path: join(output, 'device-mobile.png') });
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
  const mobileMenu = page.getByRole('button', { name: 'Menu', exact: true });
  assert(await mobileMenu.isVisible());
  await mobileMenu.click();
  assert.equal(await mobileMenu.getAttribute('aria-expanded'), 'true');
  await page.getByRole('navigation', { name: 'Site menu', exact: true }).getByRole('link', { name: 'Projects', exact: true }).click();
  await page.waitForURL('**/#projects');
  assert.equal(await page.getByRole('button', { name: 'Menu', exact: true }).getAttribute('aria-expanded'), 'false');
  await page.getByRole('link', { name: /Explore 227 AI/ }).click();
  await page.waitForURL('**/projects/227-ai/index.html');
  await page.screenshot({ path: join(output, 'project-mobile.png'), fullPage: true });
  await page.getByRole('navigation', { name: '227 AI navigation', exact: true }).getByRole('link', { name: 'Manual', exact: true }).click();
  await page.waitForURL('**/projects/227-ai/reference.html');
  await page.screenshot({ path: join(output, 'manual-mobile.png'), fullPage: true });
  await page.locator('.docs-sidebar a[href="#speech"]').click();
  await page.waitForURL('**/reference.html#speech');
  assert(await page.locator('#speech h2').isVisible());
  checks += 3;

  const fixture = await browser.newContext({ viewport: { width: 390, height: 844 } });
  await fixture.route('**/screenshots.json', route => route.fulfill({ json: { chat: { src: 'media/screenshots/test.svg', alt: 'Test capture for image viewer', kind: 'screenshot' } } }));
  await fixture.route('**/media/screenshots/test.svg', route => route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#172b20"/><text x="30" y="60" fill="#33ff77" font-size="24">TEST FIXTURE ONLY</text></svg>' }));
  const imagePage = await fixture.newPage();
  await imagePage.goto(`${project}/gallery.html#%`, { waitUntil: 'networkidle' });
  const opener = imagePage.getByRole('button', { name: 'Enlarge: Test capture for image viewer' });
  assert(await opener.isVisible());
  assert.equal(await imagePage.locator('[data-gallery-count]').textContent(), '1 image / 11 pending');
  const imageSize = await opener.locator('img').boundingBox();
  assert(Math.abs(imageSize.height - imageSize.width * .75) < 3, 'Loaded screenshot must preserve a responsive 4:3 box');
  await opener.click();
  assert(await imagePage.getByRole('dialog', { name: 'Screenshot viewer' }).isVisible());
  await imagePage.keyboard.press('Escape');
  assert.equal(await opener.evaluate(el => el === document.activeElement), true);
  checks += 5;
  let otherManifestRequests = 0;
  await fixture.route('**/projects/example/screenshots.json', route => {
    otherManifestRequests++;
    return route.fulfill({ json: { chat: { src: 'media/screenshots/test.svg', alt: 'Independent project capture', kind: 'screenshot' } } });
  });
  await fixture.route('**/index.html', async route => {
    const response = await route.fetch();
    const card = '<article data-captures="projects/example/screenshots.json"><figure class="shot" data-shot="chat"><div class="shot-placeholder">Fixture</div></figure><figure class="shot" data-shot="chat"><div class="shot-placeholder">Fixture</div></figure></article>';
    await route.fulfill({ response, body: (await response.text()).replace('</main>', `${card}</main>`) });
  });
  await imagePage.goto(`${origin}/index.html`, { waitUntil: 'networkidle' });
  assert.equal(await imagePage.getByAltText('Independent project capture', { exact: true }).count(), 2);
  assert.equal(await imagePage.getByAltText('Test capture for image viewer', { exact: true }).count(), 1);
  assert.equal(otherManifestRequests, 1, 'Each project manifest should be fetched once per page');
  checks += 3;
  await fixture.close();

  const reducedContext = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 390, height: 844 } });
  const reducedPage = await reducedContext.newPage();
  await reducedPage.goto(origin, { waitUntil: 'networkidle' });
  assert(await reducedPage.locator('video.device-demo').evaluate(video => video.paused && video.currentTime === 0));
  await reducedPage.getByRole('button', { name: 'Play walkthrough', exact: true }).click();
  await reducedPage.waitForFunction(() => !document.querySelector('video.device-demo').paused);
  checks += 2;
  await reducedContext.close();

  const delayedContext = await browser.newContext();
  let releaseVideo;
  const videoGate = new Promise(resolve => { releaseVideo = resolve; });
  await delayedContext.route('**/227-ai-walkthrough.mp4', async route => { await videoGate; await route.continue(); });
  const delayedPage = await delayedContext.newPage();
  await delayedPage.goto(origin, { waitUntil: 'domcontentloaded' });
  await delayedPage.waitForFunction(() => document.querySelectorAll('[data-demo-chapters] button').length === 9);
  assert(await delayedPage.locator('video.device-demo').evaluate(video => Number.isNaN(video.duration)));
  await delayedPage.locator('.demo-chapters summary').click();
  await delayedPage.getByRole('button', { name: /Dictate and Review/ }).click();
  releaseVideo();
  await delayedPage.waitForFunction(() => {
    const video = document.querySelector('video.device-demo');
    return video.currentTime >= 50 && video.currentTime < 52 && !video.paused;
  });
  assert(await delayedPage.locator('video.device-demo').evaluate(video => video.seekable.length && video.seekable.end(video.seekable.length - 1) > 70));
  checks += 3;
  await delayedContext.close();

  const failedContext = await browser.newContext();
  await failedContext.route('**/227-ai-walkthrough.mp4', route => route.fulfill({ status: 404, body: 'Missing test fixture' }));
  const failedPage = await failedContext.newPage();
  await failedPage.goto(origin, { waitUntil: 'networkidle' });
  await failedPage.waitForFunction(() => document.querySelector('[data-demo-status]').textContent.includes('Video unavailable'));
  assert(await failedPage.locator('[data-demo-toggle]').isDisabled());
  assert(await failedPage.locator('video.device-demo').evaluate(video => video.paused));
  checks += 2;
  await failedContext.close();

  // Simulate a repository subpath without publishing or changing hosting settings.
  const subpathContext = await browser.newContext();
  await subpathContext.route('**/project-ai-r36s/**', async route => {
    const target = new URL(route.request().url());
    target.pathname = target.pathname.replace('/project-ai-r36s/', '/');
    const response = await route.fetch({ url: target.href });
    await route.fulfill({ response });
  });
  const nestedPage = await subpathContext.newPage();
  await nestedPage.goto(`${origin}/project-ai-r36s/projects/227-ai/index.html`, { waitUntil: 'networkidle' });
  assert.equal(await nestedPage.locator('html').getAttribute('data-theme'), 'dark');
  assert.equal(await nestedPage.locator('.shot[data-loaded="true"]').count(), 1);
  await nestedPage.getByRole('button', { name: 'Search site' }).click();
  await nestedPage.getByRole('searchbox').fill('speech');
  await nestedPage.waitForFunction(() => document.querySelector('#search-help').textContent.includes('matching sections'));
  await nestedPage.locator('.search-results a').first().click();
  await nestedPage.waitForURL('**/project-ai-r36s/projects/227-ai/reference.html#speech');
  assert(await nestedPage.locator('#speech h2').isVisible());
  await nestedPage.getByRole('button', { name: 'Search site' }).click();
  await nestedPage.getByRole('searchbox').fill('Projects');
  await nestedPage.waitForFunction(() => document.querySelector('.search-results a')?.getAttribute('href').endsWith('/index.html#projects'));
  await nestedPage.locator('.search-results a').first().click();
  await nestedPage.waitForURL('**/project-ai-r36s/index.html#projects');
  assert(await nestedPage.locator('#projects').isVisible());
  checks += 4;
  await subpathContext.close();

  const noJs = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
  const staticPage = await noJs.newPage();
  await staticPage.goto(`${project}/guide.html`);
  assert(await staticPage.getByRole('navigation', { name: 'Main navigation' }).isVisible());
  assert(await staticPage.getByRole('heading', { name: 'Installation', exact: true }).isVisible());
  assert((await staticPage.evaluate(() => document.documentElement.scrollWidth)) <= 390);
  assert(await staticPage.getByRole('navigation', { name: '227 AI navigation', exact: true }).isVisible());
  assert(!(await staticPage.getByRole('button', { name: 'Search site' }).count()));
  await staticPage.goto(origin);
  assert(await staticPage.locator('#projects').isVisible());
  checks += 5;
  await staticPage.goto(`${tvProject}/gallery.html`);
  const staticImages = staticPage.locator('noscript a[href$=".png"]');
  assert.equal(await staticImages.count(), 9);
  assert(await staticImages.first().isVisible());
  checks += 2;
  await noJs.close();

  await page.emulateMedia({ reducedMotion: 'reduce' });
  assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).scrollBehavior), 'auto');
  checks++;
  await page.goto(`${project}/guide.html`, { waitUntil: 'networkidle' });
  assert.equal(await page.locator('details[open]').count(), 0);
  const pdfPath = join(output, 'guide.pdf');
  await page.pdf({ path: pdfPath, format: 'A4', printBackground: false });
  assert.equal(await page.locator('details[open]').count(), 0, 'Printing must restore closed disclosures');
  const printed = spawnSync('pdftotext', [pdfPath, '-'], { encoding: 'utf8' });
  if (printed.status === 0) {
    assert.match(printed.stdout, /Python 3\.9 or newer/);
    checks++;
  } else console.log('pdftotext unavailable; PDF content-text assertion skipped.');
  checks++;
  assert.deepEqual(externalRequests, [], 'Site must not fetch third-party assets or analytics');
  assert.deepEqual(errors, []);
  console.log(`PASSED ${checks} browser checks. No console or page errors. No third-party requests. Screenshots: ${output}`);
} finally {
  await browser.close();
}
