(() => {
    'use strict';

    // Force zeroed scroll base to prevent drift
    const scrollY = window.scrollY || window.pageYOffset || 0;
    const scrollX = window.scrollX || window.pageXOffset || 0;

    const MIN_W = 60;
    const MIN_H = 25;
    const MAX_BLOCK_H = Math.max(900, Math.round((window.innerHeight || 800) * 1.2));

    const SELECTOR = [
        '[data-rendering-id]',
        '[data-component]',
        'header',
        'nav',
        'footer',
        'main',
        'section',
        'article',
        '.card',
        '.component',
        '.list-group-item',
        '.alert',
        'main > div',
        '#maincontent > div',
        '.container > div'
    ].join(',');

    const docWidth = Math.max(
        document.documentElement.scrollWidth,
        document.body ? document.body.scrollWidth : 0
    );

    const isDynamicId = (id) => {
        if (!id) return true;
        if (/^\{?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\}?$/i.test(id)) {
            return false;
        }
        return /^(react-|ember-|ext-|ng-|_ngcontent|headlessui-)/i.test(id) || /\d{5,}/.test(id);
    };

    const info = new Map();

    const getInfo = (el) => {
        let i = info.get(el);
        if (i) return i;

        const rect = el.getBoundingClientRect();
        const renderingId = el.getAttribute('data-rendering-id') || el.getAttribute('data-component') || '';
        const rawId = (el.id || '').trim();
        const id = rawId && !isDynamicId(rawId) ? rawId : '';
        const headingEl = el.querySelector('h1, h2, h3, h4, h5, h6, strong, b');
        const heading = headingEl
            ? (headingEl.innerText || headingEl.textContent || '').replace(/\s+/g, ' ').trim().substring(0, 60)
            : '';

        const score = renderingId ? 4 : id ? 3 : heading ? 2 : 1;

        i = { rect, renderingId, id, heading, score };
        info.set(el, i);
        return i;
    };

    const candidates = [];
    document.querySelectorAll(SELECTOR).forEach((el) => {
        if (!(el instanceof HTMLElement)) return;

        const style = window.getComputedStyle(el);
        if (
            style.display === 'none' ||
            style.visibility === 'hidden' ||
            parseFloat(style.opacity) === 0 ||
            style.position === 'fixed'
        ) {
            return;
        }

        const rect = el.getBoundingClientRect();
        if (rect.width < MIN_W || rect.height < MIN_H) return;
        if (rect.right + scrollX <= 0 || rect.left + scrollX >= docWidth) return;

        candidates.push(el);
    });

    const candidateSet = new Set(candidates);

    // Fixed math operations
    const sameFootprint = (a, b) => {
        const areaA = a.width * a.height;
        const areaB = b.width * b.height;
        if (areaA <= 0 || areaB <= 0) return false;
        const overlapRatio = Math.min(areaA, areaB) / Math.max(areaA, areaB);
        return (overlapRatio > 0.88) && (Math.abs(a.top - b.top) <= 2);
    };

    const dropped = new Set();
    for (const node of candidates) {
        const nodeInfo = getInfo(node);
        for (let anc = node.parentElement; anc; anc = anc.parentElement) {
            if (!candidateSet.has(anc)) continue;
            const ancInfo = getInfo(anc);
            if (!sameFootprint(nodeInfo.rect, ancInfo.rect)) continue;
            if (nodeInfo.score > ancInfo.score) dropped.add(anc);
            else dropped.add(node);
        }
    }

    const kept = candidates.filter((el) => !dropped.has(el));
    const keptSet = new Set(kept);

    const children = new Map();
    const roots = [];
    for (const node of kept) {
        let anc = node.parentElement;
        while (anc && !keptSet.has(anc)) anc = anc.parentElement;
        if (anc) {
            if (!children.has(anc)) children.set(anc, []);
            children.get(anc).push(node);
        } else {
            roots.push(node);
        }
    }

    const landmarks = [];
    const emit = (node) => {
        const kids = children.get(node) || [];
        if (kids.length && getInfo(node).rect.height > MAX_BLOCK_H) {
            kids.forEach(emit);
        } else {
            landmarks.push(node);
        }
    };
    roots.forEach(emit);

    // Sort strictly by top position, resolving ties by larger element
    landmarks.sort((a, b) => {
        const ra = getInfo(a).rect;
        const rb = getInfo(b).rect;
        return (ra.top - rb.top) || (rb.height - ra.height);
    });

    return landmarks.map((el, index) => {
        const i = getInfo(el);
        const rect = i.rect;
        const snippet = (el.innerText || '').replace(/\s+/g, ' ').trim().substring(0, 120);

        let key;
        if (i.renderingId) {
            key = `RENDERING::${i.renderingId}`;
        } else if (i.id) {
            key = `ID::#${i.id}`;
        } else if (i.heading) {
            key = `HEAD::${i.heading.toLowerCase()}`;
        } else {
            const classes = [...el.classList]
                .filter((c) => !/(active|show|open|collapse|fade|visible|selected)/i.test(c))
                .slice(0, 2)
                .join('.');
            key = `TAG::${el.tagName.toLowerCase()}::${classes}`;
        }

        // Integer snapping: floor top/left and round width/height to ensure continuous bounds
        const x = Math.max(0, Math.floor(rect.left + scrollX));
        const y = Math.max(0, Math.floor(rect.top + scrollY));
        const width = Math.max(1, Math.round(rect.width));
        const height = Math.max(1, Math.round(rect.height));

        return {
            index,
            key,
            heading: i.heading,
            snippet,
            tag: el.tagName.toLowerCase(),
            box: { x, y, width, height }
        };
    });
})();