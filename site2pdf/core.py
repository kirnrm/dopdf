"""Capture a website as desktop-width screenshots and lay them out on A4 sheets (PDF)."""
from __future__ import annotations

import io
import posixpath
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable
from urllib.parse import urldefrag, urljoin, urlparse

from PIL import Image
from playwright.sync_api import sync_playwright
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

Image.MAX_IMAGE_PIXELS = None

MM = 72 / 25.4  # points per millimetre
PAPER_MM = {"a4": (210, 297), "letter": (215.9, 279.4)}
SKIP_EXT = {
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".zip", ".doc", ".docx",
    ".xls", ".xlsx", ".ppt", ".pptx", ".mp4", ".mp3", ".css", ".js", ".ico", ".xml", ".txt",
}


@dataclass
class Options:
    width: int = 1440              # desktop viewport width in CSS px
    viewport_height: int = 900
    orientation: str = "portrait"  # portrait | landscape
    paper: str = "a4"
    margin_mm: float = 8.0
    scale: float = 2.0             # device scale factor (image sharpness)
    jpeg_quality: int = 85
    max_pages: int = 80
    home_only: bool = False
    exclude: list[str] = field(default_factory=list)   # regexes matched against URLs
    extra_urls: list[str] = field(default_factory=list)
    allowed_hosts: list[str] | None = None             # None = same host as start URL only
    smart_breaks: bool = True
    footer: bool = True
    settle_ms: int = 1500


Progress = Callable[[str, int, int], None]


def _noop(msg: str, i: int, n: int) -> None:
    print(f"[{i}/{n}] {msg}", flush=True)


# --------------------------------------------------------------------------- browser
def _launch(p):
    last = None
    for kwargs in ({}, {"channel": "chrome"}, {"channel": "msedge"}):
        try:
            return p.chromium.launch(args=["--no-sandbox", "--hide-scrollbars"], **kwargs)
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"No usable Chromium. Run: playwright install chromium\n{last}")


# --------------------------------------------------------------------------- discovery
def normalize(url: str) -> str:
    url, _ = urldefrag(url)
    pr = urlparse(url)
    return pr._replace(query="", fragment="").geturl()


def _is_page(url: str) -> bool:
    ext = posixpath.splitext(urlparse(url).path)[1].lower()
    return ext not in SKIP_EXT


def discover(page, start_url: str, opt: Options, log: Progress = _noop) -> list[str]:
    start = normalize(start_url)
    sp = urlparse(start)
    hosts = {h.lower() for h in (opt.allowed_hosts or [sp.netloc])}
    base_dir = sp.path if sp.path.endswith("/") else posixpath.dirname(sp.path) + "/"
    excl = [re.compile(x) for x in opt.exclude]

    def ok(u: str) -> bool:
        pr = urlparse(u)
        if pr.scheme not in ("http", "https") or pr.netloc.lower() not in hosts:
            return False
        if not pr.path.startswith(base_dir) or not _is_page(u):
            return False
        return not any(r.search(u) for r in excl)

    def same(a: str, b: str) -> bool:
        """Treat /dir/ and /dir/index.php|html as the same page."""
        def key(u: str) -> str:
            pr = urlparse(u)
            path = re.sub(r"/index\.(php|html?)$", "/", pr.path)
            return f"{pr.netloc}{path}"
        return key(a) == key(b)

    found: list[str] = [start]
    queue = [start]
    seen_fetch: set[str] = set()
    while queue and len(found) < opt.max_pages and not opt.home_only:
        cur = queue.pop(0)
        if cur in seen_fetch:
            continue
        seen_fetch.add(cur)
        try:
            page.goto(cur, wait_until="domcontentloaded", timeout=45000)
            hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        except Exception as e:  # noqa: BLE001
            log(f"discovery: could not read {cur}: {e}", len(found), len(found))
            continue
        for h in hrefs:
            u = normalize(h)
            if ok(u) and not any(same(u, f) for f in found):
                found.append(u)
                queue.append(u)
                if len(found) >= opt.max_pages:
                    break
    for u in opt.extra_urls:
        u = normalize(u)
        if not any(same(u, f) for f in found):
            found.append(u)
    return [start] if opt.home_only else found


# --------------------------------------------------------------------------- capture
_PREP_JS = """
async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  // scroll through the page so lazy images / reveal-on-scroll animations fire
  let h = document.documentElement.scrollHeight, y = 0;
  while (y < h) { window.scrollTo(0, y); await sleep(120); y += Math.max(300, innerHeight * 0.6);
                  h = document.documentElement.scrollHeight; }
  window.scrollTo(0, h); await sleep(300);
  document.querySelectorAll('img[loading=lazy]').forEach(i => i.loading = 'eager');
  await Promise.all([...document.images].filter(i => !i.complete).map(i =>
    new Promise(r => { i.onload = i.onerror = r; setTimeout(r, 4000); })));
  window.scrollTo(0, 0);
  // fixed bars: keep a top navbar (once, at the top); hide floating widgets / banners
  for (const el of document.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    if (cs.position === 'fixed') {
      const r = el.getBoundingClientRect();
      if (r.top <= 40 && r.width > innerWidth * 0.5) { el.style.position = 'absolute'; el.style.top = '0'; }
      else el.style.visibility = 'hidden';
    } else if (cs.position === 'sticky') el.style.position = 'relative';
  }
  document.body.style.minHeight = '0';
  return true;
}
"""

_BREAKS_JS = """
() => {
  const sy = window.scrollY, out = {atomic: [], blocks: []};
  const vis = el => { const cs = getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0'; };
  const rect = el => { const r = el.getBoundingClientRect(); return [r.top + sy, r.bottom + sy, r.height, r.width]; };
  const A = 'img,picture,video,iframe,canvas,p,h1,h2,h3,h4,h5,h6,li,tr,button,blockquote,figure,input,textarea,select,label,a.btn,.btn,.card';
  document.querySelectorAll(A).forEach(el => { if (!vis(el)) return;
    const [t, b, h, w] = rect(el); if (h > 4 && w > 4) out.atomic.push([t, b]); });
  document.querySelectorAll('section,header,footer,article,nav,main > *,body > *').forEach(el => {
    if (!vis(el)) return; const [t, b, h] = rect(el); if (h > 40) out.blocks.push(b); });
  out.height = Math.max(document.documentElement.scrollHeight, document.body.scrollHeight);
  return out;
}
"""


def capture_page(browser, url: str, opt: Options):
    ctx = browser.new_context(
        viewport={"width": opt.width, "height": opt.viewport_height},
        device_scale_factor=opt.scale,
    )
    page = ctx.new_page()
    try:
        try:
            page.goto(url, wait_until="networkidle", timeout=45000)
        except Exception:  # noqa: BLE001  (chatty sites never go idle)
            page.wait_for_load_state("load", timeout=45000)
        page.evaluate(_PREP_JS)
        page.wait_for_timeout(opt.settle_ms)
        title = page.title() or url
        info = page.evaluate(_BREAKS_JS)
        height = int(info["height"])
        # capture in tiles: Chrome caps a single texture at ~16k device px
        tile = max(1000, int(14000 / opt.scale))
        tiles = []
        y = 0
        while y < height:
            hh = min(tile, height - y)
            png = page.screenshot(
                clip={"x": 0, "y": y, "width": opt.width, "height": hh},
                full_page=True, type="png",
            )
            tiles.append(Image.open(io.BytesIO(png)).convert("RGB"))
            y += hh
        full = Image.new("RGB", (tiles[0].width, sum(t.height for t in tiles)), "white")
        off = 0
        for t in tiles:
            full.paste(t, (0, off))
            off += t.height
        return title, full, info
    finally:
        ctx.close()


# --------------------------------------------------------------------------- pagination
def plan_cuts(height: float, slice_h: float, info: dict, smart: bool) -> list[tuple[float, float]]:
    atomic = [(t, b) for t, b in info["atomic"] if (b - t) < slice_h * 0.6]
    block_bottoms = sorted(set(round(b) for b in info["blocks"]))
    cand = sorted(set([round(b) + 2 for _, b in atomic] + [b + 2 for b in block_bottoms]))

    def valid(y: float) -> bool:
        return not any(t + 3 < y < b - 3 for t, b in atomic)

    cuts, start = [], 0.0
    while start < height - 1:
        if height - start <= slice_h * 1.04:   # remainder fits (page is scaled a hair smaller)
            cuts.append((start, height))
            break
        end = start + slice_h
        if smart:
            lo_blk, lo_gen = start + slice_h * 0.45, start + slice_h * 0.65
            blk = [b + 2 for b in block_bottoms if lo_blk <= b + 2 <= end and valid(b + 2)]
            gen = [c for c in cand if lo_gen <= c <= end and valid(c)]
            if blk:
                end = max(blk)
            elif gen:
                end = max(gen)
        cuts.append((start, end))
        start = end
    return cuts


def build_pdf(shots: list[tuple[str, str, Image.Image, dict]], out_path: str, opt: Options,
              log: Progress = _noop) -> int:
    pw_mm, ph_mm = PAPER_MM[opt.paper]
    if opt.orientation == "landscape":
        pw_mm, ph_mm = ph_mm, pw_mm
    pw, ph = pw_mm * MM, ph_mm * MM
    m = opt.margin_mm * MM
    foot = 7 * MM if opt.footer else 0
    uw, uh = pw - 2 * m, ph - 2 * m - foot
    slice_css = opt.width * uh / uw              # CSS px of page height that fills one sheet

    c = canvas.Canvas(out_path, pagesize=(pw, ph))
    c.setTitle("Website printout")
    total_sheets = 0
    plans = []
    for url, title, img, info in shots:
        plans.append(plan_cuts(img.height / opt.scale, slice_css, info, opt.smart_breaks))
    grand = sum(len(p) for p in plans)

    n = 0
    for (url, title, img, info), cuts in zip(shots, plans):
        for k, (a, b) in enumerate(cuts, 1):
            n += 1
            crop = img.crop((0, int(a * opt.scale), img.width, int(round(b * opt.scale))))
            buf = io.BytesIO()
            crop.save(buf, "JPEG", quality=opt.jpeg_quality, optimize=True)
            buf.seek(0)
            s = min(uw / crop.width, uh / crop.height)
            dw, dh = crop.width * s, crop.height * s
            x, y = m, ph - m - dh
            c.drawImage(ImageReader(buf), x, y, dw, dh)
            c.setStrokeGray(0.82)
            c.setLineWidth(0.4)
            c.rect(x, y, dw, dh, stroke=1, fill=0)
            if opt.footer:
                c.setFont("Helvetica", 7)
                c.setFillGray(0.35)
                c.drawString(m, m * 0.55, url[:110])
                c.drawRightString(pw - m, m * 0.55, f"{title[:40]}  |  part {k}/{len(cuts)}  |  sheet {n}/{grand}")
            if k == 1:
                key = f"p{n}"
                c.bookmarkPage(key)
                c.addOutlineEntry(title[:80] or url, key, level=0)
            c.showPage()
    c.save()
    return n


# --------------------------------------------------------------------------- entry point
def run(start_url: str, out_path: str, opt: Options | None = None, log: Progress = _noop) -> dict:
    opt = opt or Options()
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            ctx = browser.new_context(viewport={"width": opt.width, "height": opt.viewport_height})
            probe = ctx.new_page()
            log("Finding pages…", 0, 1)
            urls = discover(probe, start_url, opt, log)
            ctx.close()
            log(f"Found {len(urls)} page(s)", 0, len(urls))
            shots, failed = [], []
            for i, u in enumerate(urls, 1):
                log(f"Capturing {u}", i, len(urls))
                try:
                    title, img, info = capture_page(browser, u, opt)
                    shots.append((u, title, img, info))
                except Exception as e:  # noqa: BLE001
                    failed.append((u, str(e)))
                    log(f"FAILED {u}: {e}", i, len(urls))
        finally:
            browser.close()
    if not shots:
        raise RuntimeError("No page could be captured: " + "; ".join(f"{u}: {e}" for u, e in failed))
    log("Building PDF…", len(urls), len(urls))
    sheets = build_pdf(shots, out_path, opt, log)
    return {"pages": len(shots), "sheets": sheets, "failed": failed, "urls": [s[0] for s in shots]}
