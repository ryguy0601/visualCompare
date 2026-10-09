import html
import json
import os
from datetime import datetime
from typing import List

DEFAULT_CARD = """
<section class="card {{STATUS_CLASS}}">
  <h3>{{ROUTE_NAME}} <small>[{{DEVICE}}] {{STATUS_LABEL}}</small></h3>
  <p>{{MISMATCH_PERCENT}}% mismatch &middot; {{BLOCK_COUNT}} blocks &middot; {{TOTAL_TIME}}s</p>
  <div class="row">
    <figure><figcaption>Production</figcaption><img src="{{PROD_IMAGE_PATH}}" loading="lazy"></figure>
    <figure><figcaption>Staging</figcaption>
      <div class="ov"><img src="{{STG_IMAGE_PATH}}" loading="lazy"><img class="d" src="{{DIFF_IMAGE_PATH}}" loading="lazy"></div>
    </figure>
  </div>
</section>
"""

DEFAULT_REPORT = """<!doctype html><html><head><meta charset="utf-8"><title>Visual Regression Report</title>
<style>
body{font-family:system-ui,sans-serif;margin:24px;background:#f5f6f8;color:#111}
.card{background:#fff;border-left:6px solid #16a34a;padding:12px 16px;margin:16px 0;border-radius:6px;box-shadow:0 1px 3px rgba(0,0,0,0.08)}
.card.failed{border-left-color:#e11d48}
.row{display:flex;gap:12px;align-items:flex-start}.row figure{flex:1;margin:0;min-width:0}
img{max-width:100%;height:auto;border:1px solid #e5e7eb}.ov{position:relative}.ov .d{position:absolute;left:0;top:0}
</style></head><body>
<h1>Visual Regression Report</h1>
<p>{{TIMESTAMP}} &middot; Prod: {{PROD_BASE_URL}} &middot; Staging: {{STG_BASE_URL}}</p>
<p><strong>{{TOTAL_ROUTES}} total &middot; {{TOTAL_PASSED}} passed &middot; {{TOTAL_FAILED}} failed</strong></p>
{{CARDS_CONTENT}}
</body></html>"""


def generate_html_report(results: List[dict], config: dict, card_template_str: str = "", report_template_str: str = "", logger=print) -> str:
    card_template = card_template_str or DEFAULT_CARD
    report_template = report_template_str or DEFAULT_REPORT
    esc = html.escape

    total_passed = sum(1 for r in results if r["status"] == "passed")
    total_failed = len(results) - total_passed

    cards = []
    for res in results:
        card = card_template
        for placeholder, value in (
            ("{{STATUS_CLASS}}", res["status"]),
            ("{{STATUS_LABEL}}", res["status"].upper()),
            ("{{DEVICE}}", res["device"].upper()),
            ("{{DEVICE_CLASS}}", f"dev-{res['device']}"),
            ("{{ROUTE_NAME}}", esc(res["route"])),
            ("{{ROUTE_PATH}}", esc(res["route"])),
            ("{{MISMATCH_PERCENT}}", str(res["mismatch_pct"])),
            ("{{BLOCK_COUNT}}", str(res["block_count"])),
            ("{{TOTAL_TIME}}", str(res["render_time"])),
            ("{{PROD_IMAGE_PATH}}", esc(res["prod_img"])),
            ("{{STG_IMAGE_PATH}}", esc(res["stg_img"])),
            ("{{DIFF_IMAGE_PATH}}", esc(res["diff_img"])),
        ):
            card = card.replace(placeholder, value)
        cards.append(card)

    report = report_template
    for placeholder, value in (
        ("{{TIMESTAMP}}", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("{{TOTAL_ROUTES}}", str(len(results))),
        ("{{TOTAL_PASSED}}", str(total_passed)),
        ("{{TOTAL_FAILED}}", str(total_failed)),
        ("{{PROD_BASE_URL}}", esc(config["prod_url"])),
        ("{{STG_BASE_URL}}", esc(config["stg_url"])),
        ("{{CARDS_CONTENT}}", "\n".join(cards)),
    ):
        report = report.replace(placeholder, value)

    out_dir = config["output_dir"]
    report_path = os.path.join(out_dir, "report.html")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    with open(os.path.join(out_dir, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    logger(f"Report generated: {report_path}")
    return report_path