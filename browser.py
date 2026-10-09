import io
import time
from multiprocessing.util import Finalize
from typing import List, Tuple
from PIL import Image

_PW = None
_BROWSER = None
_FINALIZER_REGISTERED = False

FREEZE_ANIMATIONS_JS = """
() => {
    if (document.getElementById('visual-test-anti-animation')) return;
    const style = document.createElement('style');
    style.id = 'visual-test-anti-animation';
    style.innerHTML = `
        html, body {
            scroll-behavior: auto !important;
        }
        *, *::before, *::after {
            animation-duration: 0s !important;
            animation-delay: 0s !important;
            animation-iteration-count: 1 !important;
            transition-duration: 0s !important;
            transition-delay: 0s !important;
            caret-color: transparent !important;
            scroll-behavior: auto !important;
        }
        video, audio { opacity: 0 !important; visibility: hidden !important; }
    `;
    document.head.appendChild(style);
}
"""

SCROLL_THROUGH_JS = """
async () => {
    await new Promise((resolve) => {
        let pos = 0, ticks = 0;
        const distance = 450;
        const timer = setInterval(() => {
            const h = Math.max(document.body ? document.body.scrollHeight : 0,
                               document.documentElement.scrollHeight);
            window.scrollBy(0, distance);
            pos += distance; ticks++;
            if (pos >= h || ticks >= 200) {
                clearInterval(timer);
                window.scrollTo(0, 0);
                resolve();
            }
        }, 50);
    });
}
"""

PAUSE_CAROUSELS_JS = """
() => {
    document.querySelectorAll('.carousel').forEach((el) => {
        try {
            if (window.bootstrap && window.bootstrap.Carousel) {
                const c = window.bootstrap.Carousel.getOrCreateInstance(el);
                c.pause(); c.to(0);
            } else if (window.jQuery && window.jQuery.fn && window.jQuery.fn.carousel) {
                window.jQuery(el).carousel('pause').carousel(0);
            }
        } catch (e) {}
    });
}
"""

HIDE_FLOATING_JS = """
() => {
    const isiToken = /(^|[-_])isi($|[-_])/;
    for (const el of document.querySelectorAll('*')) {
        const style = window.getComputedStyle(el);
        if (style.position !== 'fixed' && style.position !== 'sticky') continue;
        const cls = (el.getAttribute('class') || '').toLowerCase();
        const id = (el.id || '').toLowerCase();
        const tag = el.tagName.toLowerCase();
        if (tag === 'footer' || cls.includes('footer') || id.includes('footer')) continue;
        const tokens = cls.split(/\\s+/).filter(Boolean);
        const hit = tokens.some(t => isiToken.test(t)) || isiToken.test(id)
                    || cls.includes('sticky-banner') || cls.includes('floating-tray');
        if (hit) el.style.setProperty('display', 'none', 'important');
    }
}
"""

PAGE_HEIGHT_JS = "() => Math.max(document.body ? document.body.scrollHeight : 0, document.documentElement.scrollHeight)"


def shutdown_browser():
    global _PW, _BROWSER
    try:
        if _BROWSER is not None:
            _BROWSER.close()
    except Exception:
        pass
    try:
        if _PW is not None:
            _PW.stop()
    except Exception:
        pass
    _BROWSER = None
    _PW = None


def get_browser(chrome_path: str = ""):
    global _PW, _BROWSER, _FINALIZER_REGISTERED
    if _BROWSER is not None:
        try:
            if _BROWSER.is_connected():
                return _BROWSER
        except Exception:
            pass
        shutdown_browser()

    from playwright.sync_api import sync_playwright

    _PW = sync_playwright().start()
    if not _FINALIZER_REGISTERED:
        Finalize(None, shutdown_browser, exitpriority=10)
        _FINALIZER_REGISTERED = True

    launch_opts = {
        "headless": True,
        "args": [
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--font-render-hinting=none",
            "--force-device-scale-factor=1",
            "--disable-subpixel-font-scaling",
            "--hide-scrollbars",  # Eliminates layout width shift during full-page captures
        ],
    }
    if chrome_path:
        launch_opts["executable_path"] = chrome_path
    _BROWSER = _PW.chromium.launch(**launch_opts)
    return _BROWSER


def wait_for_stable_height(page, attempts: int = 12, interval: float = 0.25):
    last, stable = -1, 0
    for _ in range(attempts):
        try:
            h = page.evaluate(PAGE_HEIGHT_JS)
        except Exception:
            return
        if h == last:
            stable += 1
            if stable >= 2:
                return
        else:
            stable, last = 0, h
        time.sleep(interval)


def wait_for_full_readiness(page):
    for state, timeout in (("domcontentloaded", 20000), ("load", 25000), ("networkidle", 8000)):
        try:
            page.wait_for_load_state(state, timeout=timeout)
        except Exception:
            pass

    page.evaluate(FREEZE_ANIMATIONS_JS)
    page.evaluate(SCROLL_THROUGH_JS)
    page.evaluate(PAUSE_CAROUSELS_JS)

    # Immediately invoke font readiness promise resolution
    page.evaluate("() => (document.fonts ? document.fonts.ready : Promise.resolve())")

    # Await image decoding
    page.evaluate("""
        () => Promise.all(
            Array.from(document.images).map(img => {
                if (img.complete) return img.decode ? img.decode().catch(() => {}) : Promise.resolve();
                return new Promise(res => { img.onload = img.onerror = res; setTimeout(res, 2000); });
            })
        )
    """)
    wait_for_stable_height(page)


def execute_pre_clicks(page, click_selectors: List[str], logger=print):
    for sel in click_selectors:
        try:
            locator = page.locator(sel)
            count = locator.count()
            for i in range(count):
                el = locator.nth(i)
                if el.is_visible():
                    logger(f"Executing pre-capture click: '{sel}'")
                    el.click(timeout=2500)
                    time.sleep(0.25)
        except Exception as e:
            logger(f"Pre-click '{sel}' skipped: {type(e).__name__}")


def extract_dom_landmarks(page, dom_extractor_script: str, logger=print) -> List[dict]:
    if not dom_extractor_script or not dom_extractor_script.strip():
        return []
    try:
        raw = page.evaluate(dom_extractor_script) or []
    except Exception as e:
        logger(f"DOM extraction error: {e}")
        return []

    landmarks = []
    for item in raw:
        box = item.get("box")
        if not box:
            continue
        try:
            # Floor origin to match top-left raster pixel, round dimensions
            norm = {
                "x": int(box["x"]),
                "y": int(box["y"]),
                "width": int(round(box["width"])),
                "height": int(round(box["height"])),
            }
        except (TypeError, KeyError, ValueError):
            continue
        if norm["width"] > 0 and norm["height"] > 0:
            landmarks.append(dict(item, box=norm))
    return landmarks


def prepare_and_capture(
    page,
    click_selectors: List[str],
    hide_css: str,
    dom_extractor_script: str,
    logger=print,
) -> Tuple[List[dict], Image.Image]:
    wait_for_full_readiness(page)

    if hide_css:
        page.add_style_tag(content=hide_css)

    execute_pre_clicks(page, click_selectors, logger=logger)
    if click_selectors:
        time.sleep(0.3)

    # Hard-clamp scroll to absolute origin and zero out scroll properties
    page.evaluate("""
        () => {
            window.scrollTo(0, 0);
            document.documentElement.scrollTop = 0;
            document.body.scrollTop = 0;
        }
    """)
    page.evaluate(HIDE_FLOATING_JS)
    wait_for_stable_height(page, attempts=6)

    # Re-verify scroll origin after hiding floating trays
    page.evaluate("""
        () => {
            window.scrollTo(0, 0);
            document.documentElement.scrollTop = 0;
            document.body.scrollTop = 0;
        }
    """)

    landmarks = extract_dom_landmarks(page, dom_extractor_script, logger=logger)
    raw_bytes = page.screenshot(full_page=True, timeout=60000, animations="disabled", caret="hide")
    image = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    return landmarks, image