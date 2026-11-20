"""Small web UI around site2pdf.  Run:  python app.py   (or gunicorn -w1 --threads 4 app:app)

Environment:
  SITE2PDF_ALLOWED_HOSTS  comma list of hosts that may be captured (default: jnanakshi.org)
  SITE2PDF_PASSWORD       if set, HTTP basic auth (any username) is required
  PORT                    default 8080
"""
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request, send_file
from urllib.parse import urlparse

from site2pdf import Options, run

app = Flask(__name__)
ALLOWED = [h.strip().lower() for h in os.environ.get("SITE2PDF_ALLOWED_HOSTS", "jnanakshi.org").split(",") if h.strip()]
PASSWORD = os.environ.get("SITE2PDF_PASSWORD")
WORK = Path(os.environ.get("SITE2PDF_WORKDIR", tempfile.gettempdir())) / "site2pdf_jobs"
WORK.mkdir(parents=True, exist_ok=True)
JOBS: dict[str, dict] = {}
RUN_LOCK = threading.Lock()          # one browser job at a time keeps memory predictable


@app.before_request
def _auth():
    if PASSWORD:
        a = request.authorization
        if not a or not secrets.compare_digest(a.password or "", PASSWORD):
            return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="site2pdf"'})


def _worker(job_id: str, url: str, opt: Options):
    job = JOBS[job_id]
    out = str(WORK / f"{job_id}.pdf")

    def log(msg, i, n):
        job.update(msg=msg, done=i, total=n)
        job["log"].append(msg)

    try:
        job["msg"] = "Waiting for a free slot…"
        with RUN_LOCK:
            res = run(url, out, opt, log)
        job.update(state="done", file=out, result=res, msg=f"{res['pages']} pages → {res['sheets']} A4 sheets")
    except Exception as e:  # noqa: BLE001
        job.update(state="error", msg=str(e))


@app.get("/")
def index():
    return render_template("index.html", hosts=ALLOWED)


@app.post("/start")
def start():
    url = request.form.get("url", "").strip()
    pr = urlparse(url)
    if pr.scheme not in ("http", "https") or pr.netloc.lower() not in ALLOWED:
        return jsonify(error=f"Host not allowed. Allowed: {', '.join(ALLOWED)}"), 400
    opt = Options(
        width=int(request.form.get("width", 1440)),
        orientation=request.form.get("orientation", "portrait"),
        paper=request.form.get("paper", "a4"),
        home_only=request.form.get("home_only") == "on",
        max_pages=min(int(request.form.get("max_pages", 80)), 200),
        scale=min(float(request.form.get("scale", 2)), 3),
        allowed_hosts=ALLOWED,
    )
    # prune old jobs
    for jid, j in list(JOBS.items()):
        if time.time() - j["created"] > 6 * 3600:
            Path(j.get("file", "")).unlink(missing_ok=True)
            JOBS.pop(jid, None)
    job_id = secrets.token_urlsafe(8)
    JOBS[job_id] = dict(state="running", msg="Starting…", done=0, total=1, log=[], created=time.time())
    threading.Thread(target=_worker, args=(job_id, url, opt), daemon=True).start()
    return jsonify(id=job_id)


@app.get("/status/<job_id>")
def status(job_id):
    j = JOBS.get(job_id) or abort(404)
    return jsonify(state=j["state"], msg=j["msg"], done=j["done"], total=j["total"], log=j["log"][-8:])


@app.get("/download/<job_id>")
def download(job_id):
    j = JOBS.get(job_id) or abort(404)
    if j["state"] != "done":
        abort(409)
    return send_file(j["file"], as_attachment=True, download_name="website-printout.pdf")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
