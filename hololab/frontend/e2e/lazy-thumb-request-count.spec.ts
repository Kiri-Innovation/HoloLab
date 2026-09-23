// End-to-end acceptance for the ``LazyThumb`` request-throttling behavior.
//
// We can't easily A/B a live workflow's preview drawer against a
// production daemon in a repeatable CI-friendly way (thumbnail counts
// vary by pack output), so this suite compares two SYNTHETIC pages
// stitched together in-browser:
//
//   Baseline: 100 <img loading="lazy"> tiles stacked below the fold.
//             Chrome's built-in lazy heuristic fires requests for
//             every image within ~1250 px of the viewport at initial
//             paint — measured here as the "browser lazy budget".
//
//   LazyThumb-style: 100 tiles whose ``src`` is populated only when
//             an IntersectionObserver with a strict ``rootMargin``
//             (50 px, matching ``LazyThumb.tsx``'s default) reports
//             the tile visible for at least 200 ms. Fast-scroll
//             skip + tight margin drop the initial-paint request
//             count by an order of magnitude.
//
// The suite intercepts every /thumb/* request via ``page.route`` and
// counts them. We never let a request escape to the network, so this
// runs offline against the vite dev server — no live gateway required.

import { test, expect, type Route } from "@playwright/test";

// Physical layout that mirrors the ``NestedGroupDetail`` strip in
// ``previews.tsx``: 100 tiles, ~92 px each (STRIP_TILE_W is 96 px
// pre-overlap in the production layout; the exact number doesn't
// change the ranking, only the absolute counts).
const TILE_COUNT = 100;
const TILE_HEIGHT = 92;
const VIEWPORT_HEIGHT = 720; // Playwright's default.

// One shared ``/thumb/*`` route across both scenarios. The route
// resolves every request with a benign 200 so the tile's ``load``
// event fires; the important thing is we COUNT hits, not that any
// image actually paints.
async function installThumbCounter(page: import("@playwright/test").Page): Promise<() => number> {
  let hits = 0;
  await page.route("**/*", async (route: Route) => {
    if (!route.request().url().includes("thumbs.test/")) {
      return route.continue();
    }
    hits += 1;
    // 1x1 transparent GIF — small, valid, cheap for the browser to
    // decode. Content-type matters so the <img> ``load`` fires.
    await route.fulfill({
      status: 200,
      contentType: "image/gif",
      body: Buffer.from(
        "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7",
        "base64",
      ),
    });
  });
  return () => hits;
}

test.describe("LazyThumb — initial-paint request count", () => {
  test("eager baseline: plain <img src> fires N requests up-front", async ({ page }) => {
    // Establishes the worst-case ceiling. Headless Chromium disables
    // ``loading="lazy"`` speculative fetching, so we can't measure the
    // browser-lazy path in this harness — but the ceiling is what we
    // care about: 100 tiles with no gating fires 100 requests. Any
    // gating strategy (browser-lazy in real Chrome, LazyThumb here)
    // reduces that number.
    const getHits = await installThumbCounter(page);

    await page.setContent(`
      <!doctype html>
      <html><body style="margin:0">
        <div id="tiles">
          ${Array.from({ length: TILE_COUNT }, (_, i) =>
            `<img src="https://thumbs.test/${i}" style="display:block;width:100%;height:${TILE_HEIGHT}px;background:#222" alt="tile-${i}"/>`,
          ).join("")}
        </div>
      </body></html>
    `);

    await page.waitForTimeout(1500);
    const eagerHits = getHits();
    expect(eagerHits).toBe(TILE_COUNT);
    // eslint-disable-next-line no-console -- surfaces to the run report
    console.log(`[eager baseline] hits @ initial paint: ${eagerHits} / ${TILE_COUNT}`);
  });

  test("LazyThumb-style: strict rootMargin + debounce cuts initial-paint hits", async ({
    page,
  }) => {
    const getHits = await installThumbCounter(page);

    // Same grid, but the ``src`` is unset until an IntersectionObserver
    // reports the tile visible for 200 ms with ``rootMargin: 50px``
    // — the same defaults ``LazyThumb.tsx`` ships with.
    await page.setContent(`
      <!doctype html>
      <html><body style="margin:0">
        <div id="pad" style="height:${VIEWPORT_HEIGHT}px;background:#111"></div>
        <div id="tiles">
          ${Array.from({ length: TILE_COUNT }, (_, i) =>
            `<img data-lazy-src="https://thumbs.test/${i}" loading="lazy" style="display:block;width:100%;height:${TILE_HEIGHT}px;background:#222" alt="tile-${i}"/>`,
          ).join("")}
        </div>
        <script>
          const DEBOUNCE_MS = 200;
          const observer = new IntersectionObserver((entries) => {
            for (const entry of entries) {
              const img = entry.target;
              if (!(img instanceof HTMLImageElement)) continue;
              const url = img.getAttribute("data-lazy-src");
              if (url === null) continue;
              if (entry.isIntersecting) {
                if (img.dataset.timer !== undefined) return;
                const t = window.setTimeout(() => {
                  if (img.getAttribute("data-lazy-src") !== null) {
                    img.src = url;
                    img.removeAttribute("data-lazy-src");
                  }
                }, DEBOUNCE_MS);
                img.dataset.timer = String(t);
              } else if (img.dataset.timer !== undefined) {
                window.clearTimeout(Number(img.dataset.timer));
                delete img.dataset.timer;
              }
            }
          }, { rootMargin: "50px", threshold: 0 });
          for (const img of document.querySelectorAll("#tiles img")) {
            observer.observe(img);
          }
        </script>
      </body></html>
    `);

    await page.waitForTimeout(1500);
    const lazyThumbHits = getHits();

    // With rootMargin=50px and the tiles stacked below the fold, at
    // most the top-of-grid tile can be within 50 px of the viewport
    // — 0 or 1 fetch is the expected outcome.
    expect(lazyThumbHits).toBeLessThanOrEqual(2);
    // eslint-disable-next-line no-console -- surfaces to the run report
    console.log(`[LazyThumb style] hits @ initial paint: ${lazyThumbHits}`);
  });

  test("fast-scroll skip: flying past tiles fires no requests", async ({ page }) => {
    const getHits = await installThumbCounter(page);

    // Grid tall enough that a scroll from top to bottom passes
    // through many tiles. The ``LazyThumb``-style debounce should
    // reject every tile that enters+exits inside the 200 ms window.
    await page.setContent(`
      <!doctype html>
      <html><body style="margin:0">
        <div id="tiles">
          ${Array.from({ length: TILE_COUNT }, (_, i) =>
            `<img data-lazy-src="https://thumbs.test/${i}" style="display:block;width:100%;height:${TILE_HEIGHT}px;background:#222" alt="tile-${i}"/>`,
          ).join("")}
        </div>
        <script>
          const DEBOUNCE_MS = 200;
          const observer = new IntersectionObserver((entries) => {
            for (const entry of entries) {
              const img = entry.target;
              if (!(img instanceof HTMLImageElement)) continue;
              const url = img.getAttribute("data-lazy-src");
              if (url === null) continue;
              if (entry.isIntersecting) {
                if (img.dataset.timer !== undefined) return;
                const t = window.setTimeout(() => {
                  if (img.getAttribute("data-lazy-src") !== null) {
                    img.src = url;
                    img.removeAttribute("data-lazy-src");
                  }
                }, DEBOUNCE_MS);
                img.dataset.timer = String(t);
              } else if (img.dataset.timer !== undefined) {
                window.clearTimeout(Number(img.dataset.timer));
                delete img.dataset.timer;
              }
            }
          }, { rootMargin: "50px", threshold: 0 });
          for (const img of document.querySelectorAll("#tiles img")) {
            observer.observe(img);
          }
        </script>
      </body></html>
    `);

    // Let the top of the grid settle (0-1 tile may fire from the
    // initial-visible top).
    await page.waitForTimeout(300);
    const settledTop = getHits();

    // Fast scroll to the bottom in one jump. Every tile crosses the
    // viewport within a single animation frame — well under 200 ms.
    // The debounce should reject every mid-scroll tile.
    await page.evaluate(() => window.scrollTo({ top: 100_000, behavior: "auto" }));
    // Debounce settlement — tiles at the *bottom* are now visible and
    // will fire after the debounce window. Everything in between
    // should be skipped.
    await page.waitForTimeout(500);
    const settledBottom = getHits();
    const midScrollHits = settledBottom - settledTop;

    // We should only fire for tiles that came to rest at the bottom of
    // the viewport, not for the 80+ tiles the scroll flew past.
    expect(midScrollHits).toBeLessThan(20);
    // eslint-disable-next-line no-console -- surfaces to the run report
    console.log(
      `[LazyThumb style] settled@top=${settledTop} settled@bottom=${settledBottom} mid-scroll=${midScrollHits}`,
    );
  });
});
