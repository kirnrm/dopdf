# site2pdf – website → desktop-width screenshots → printable A4 PDF

Each page is rendered in Chromium at 1440 px wide, captured full-length, and cut into A4 portrait sheets
(8 mm margins, URL + sheet number in the footer). Cuts are placed between sections, never through a
paragraph, image or card where avoidable. The fixed top menu is kept once at the top of the page; floating
widgets (WhatsApp buttons, cookie bars) are hidden. A bookmark per page is added to the PDF.

## Install (Linux server)
    pip install -r requirements.txt
    playwright install --with-deps chromium

## Command line
    python -m site2pdf https://jnanakshi.org/sjvn/ --home-only -o home.pdf   # quick check
    python -m site2pdf https://jnanakshi.org/sjvn/ -o sjvn.pdf               # all pages
Useful flags: --orientation landscape, --width 1920, --paper letter, --max-pages 40,
--exclude 'privacy|terms', --urls-file extra.txt (add pages not linked in the menu), --scale 3 (sharper, bigger file).

## Web UI
    SITE2PDF_ALLOWED_HOSTS=jnanakshi.org SITE2PDF_PASSWORD=choose-one python app.py
Open http://SERVER:8080 → Generate PDF → Download. Only hosts in SITE2PDF_ALLOWED_HOSTS can be captured and,
if SITE2PDF_PASSWORD is set, a login is required. Put it behind nginx/HTTPS if it is public.

## Docker
    docker build -t site2pdf . && docker run -p 8080:8080 -e SITE2PDF_PASSWORD=choose-one site2pdf

Notes: pages found by following links under the start URL (/sjvn/); pages behind a login are not captured.
Expect ~10 s per page; a 30-page site is a PDF of roughly 60–100 sheets.
