import argparse
import sys

from .core import Options, run


def main() -> int:
    ap = argparse.ArgumentParser(prog="site2pdf", description="Website -> desktop-width screenshots -> printable A4 PDF")
    ap.add_argument("url", help="start URL, e.g. https://jnanakshi.org/sjvn/")
    ap.add_argument("-o", "--out", default="site.pdf")
    ap.add_argument("--home-only", action="store_true", help="only the start page (quick check)")
    ap.add_argument("--width", type=int, default=1440, help="desktop viewport width in px (default 1440)")
    ap.add_argument("--orientation", choices=["portrait", "landscape"], default="portrait")
    ap.add_argument("--paper", choices=["a4", "letter"], default="a4")
    ap.add_argument("--margin", type=float, default=8.0, help="margin in mm")
    ap.add_argument("--scale", type=float, default=2.0, help="image sharpness (1-3)")
    ap.add_argument("--max-pages", type=int, default=80)
    ap.add_argument("--exclude", action="append", default=[], help="regex of URLs to skip (repeatable)")
    ap.add_argument("--urls-file", help="text file with extra URLs, one per line")
    ap.add_argument("--allow-host", action="append", help="extra host allowed during crawl (repeatable)")
    ap.add_argument("--no-smart-breaks", action="store_true", help="cut strictly at sheet height")
    ap.add_argument("--no-footer", action="store_true")
    a = ap.parse_args()

    extra = []
    if a.urls_file:
        extra = [l.strip() for l in open(a.urls_file) if l.strip() and not l.startswith("#")]
    opt = Options(width=a.width, orientation=a.orientation, paper=a.paper, margin_mm=a.margin,
                  scale=a.scale, max_pages=a.max_pages, home_only=a.home_only, exclude=a.exclude,
                  extra_urls=extra, allowed_hosts=a.allow_host, smart_breaks=not a.no_smart_breaks,
                  footer=not a.no_footer)
    res = run(a.url, a.out, opt)
    print(f"\nDone: {res['pages']} page(s) -> {res['sheets']} sheet(s) -> {a.out}")
    for u, e in res["failed"]:
        print("  failed:", u, e, file=sys.stderr)
    return 0


sys.exit(main())
