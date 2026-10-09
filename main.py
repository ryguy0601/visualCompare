#!/usr/bin/env python3
"""Enterprise Visual Regression Testing Engine - Master Orchestrator."""

import argparse
import concurrent.futures
import hashlib
import os
from pathlib import Path
import re
import shutil
import sys
import time
from datetime import datetime
from typing import List, Optional, Tuple

import browser
import crawler
import diff_engine
import report

# ---------------------------------------------------------------------------
# Console configuration
# ---------------------------------------------------------------------------

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except AttributeError:
        pass


def log(msg: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enterprise Visual Regression Engine (.NET / Sitecore)"
    )

    parser.add_argument(
        "--prod-url",
        required=True,
        help="Production base environment URL",
    )
    parser.add_argument(
        "--stg-url",
        required=True,
        help="Staging base environment URL",
    )
    parser.add_argument(
        "--pages",
        default="",
        help="Pipe- or comma-separated relative routes (e.g. /,/about,/contact)",
    )
    parser.add_argument(
        "--crawl",
        action="store_true",
        help="Auto-crawl internal routes from production base URL",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=20,
        help="Maximum routes to discover when crawling",
    )
    parser.add_argument(
        "--click-selectors",
        default="",
        help=(
            "Selectors to click before capture (e.g. cookie buttons). "
            "Use '|' as separator if a selector contains commas."
        ),
    )
    parser.add_argument(
        "--device",
        choices=["desktop", "mobile", "both"],
        default="desktop",
        help="Viewport profile",
    )
    parser.add_argument(
        "--desktop-res",
        default="1920x1080",
        help="Desktop resolution (WidthxHeight)",
    )
    parser.add_argument(
        "--mobile-res",
        default="375x812",
        help="Mobile resolution (WidthxHeight)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=2,
        help="Parallel worker processes",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.1,
        help="Color diff threshold (0.0 to 1.0)",
    )
    parser.add_argument(
        "--max-mismatch",
        type=float,
        default=0.5,
        help="Mismatch percentage above which a route fails",
    )
    parser.add_argument(
        "--noise-floor",
        type=float,
        default=0.002,
        help="Ignore landmark blocks below this mismatch ratio",
    )
    parser.add_argument(
        "--max-v-shift",
        type=int,
        default=32,
        help="Maximum vertical jitter in pixels searched per landmark block",
    )
    parser.add_argument(
        "--skip-gap-diff",
        action="store_true",
        help="Only diff matched landmarks; ignore pixels in the gaps between them",
    )
    parser.add_argument(
        "--save-sections",
        action="store_true",
        help="Also save per-landmark section images",
    )
    parser.add_argument(
        "--prod-auth",
        default="",
        help="HTTP basic auth for production as user:password",
    )
    parser.add_argument(
        "--stg-auth",
        default="",
        help="HTTP basic auth for staging as user:password",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=45,
        help="Navigation timeout in seconds",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        help="Retries per route/device on failure",
    )
    parser.add_argument(
        "--chrome-path",
        default="",
        help="Path to local Chromium / Chrome / Edge executable",
    )
    parser.add_argument(
        "--output-dir",
        default="test-results",
        help="Directory for artifacts and reports",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the HTML report automatically",
    )
    parser.add_argument(
        "--exit-zero",
        action="store_true",
        help="Always exit 0 even if routes failed",
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------

def split_list(value: str) -> List[str]:
    if not value:
        return []
    separator = "|" if "|" in value else ","
    return [part.strip() for part in value.split(separator) if part.strip()]


def parse_auth(value: str) -> Optional[Tuple[str, str]]:
    if not value:
        return None

    username, separator, password = value.partition(":")
    if not separator:
        raise SystemExit("Invalid authentication value. Expected user:password.")
    if not username:
        raise SystemExit("Invalid authentication value. Username cannot be empty.")

    return username, password


def parse_resolution(value: str, label: str) -> Tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", value)
    if not match:
        raise SystemExit(
            f"Invalid {label} resolution '{value}'. Expected WidthxHeight, e.g. 1920x1080."
        )

    width = int(match.group(1))
    height = int(match.group(2))

    if width <= 0 or height <= 0:
        raise SystemExit(
            f"Invalid {label} resolution '{value}'. Width and height must be greater than zero."
        )

    return width, height


def validate_config(config: dict) -> None:
    if config["workers"] < 1:
        raise SystemExit("--workers must be at least 1.")
    if config["max_pages"] < 1:
        raise SystemExit("--max-pages must be at least 1.")
    if config["timeout"] < 1:
        raise SystemExit("--timeout must be at least 1 second.")
    if config["retries"] < 0:
        raise SystemExit("--retries cannot be negative.")
    if not 0.0 <= config["threshold"] <= 1.0:
        raise SystemExit("--threshold must be between 0.0 and 1.0.")
    if not 0.0 <= config["max_mismatch"] <= 100.0:
        raise SystemExit("--max-mismatch must be between 0.0 and 100.0.")
    if not 0.0 <= config["noise_floor"] <= 1.0:
        raise SystemExit("--noise-floor must be between 0.0 and 1.0.")
    if config["max_v_shift"] < 0:
        raise SystemExit("--max-v-shift cannot be negative.")


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------

def read_static_asset(filename: str, required: bool = True) -> str:
    file_path = Path(__file__).resolve().parent / filename
    if not file_path.exists():
        if required:
            raise FileNotFoundError(
                f"Required asset '{filename}' was not found at '{file_path}'."
            )
        log(f"Warning: optional asset '{filename}' was not found at '{file_path}'.")
        return ""

    return file_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Output handling
# ---------------------------------------------------------------------------

def prepare_output_directory(output_dir: str) -> None:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    screenshots_path = output_path / "screenshots"
    if screenshots_path.is_dir():
        log(f"Clearing previous screenshots in: {screenshots_path}")
        shutil.rmtree(screenshots_path, ignore_errors=True)

    for filename in ("report.html", "results.json"):
        file_path = output_path / filename
        try:
            file_path.unlink()
        except FileNotFoundError:
            pass

    screenshots_path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# URL / filename helpers
# ---------------------------------------------------------------------------

def build_url(base: str, route: str) -> str:
    base = base.rstrip("/")
    route = route.lstrip("/")
    return f"{base}/{route}" if route else base


def build_target_url(base: str, route: str) -> str:
    """Build a URL handling both normal HTTP URLs and file:// URLs."""
    if base.startswith("file://"):
        base_path = base.rstrip("/")
        if not route or route == "/":
            return base_path
        route_path = route.lstrip("/")
        if base_path.lower().endswith(".html"):
            return base_path
        return f"{base_path}/{route_path}"

    return build_url(base, route)


def safe_file_prefix(route: str, device: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", route.strip("/")).strip("_")[:80] or "root_home"
    digest = hashlib.md5(route.encode("utf-8")).hexdigest()[:6]
    return f"{slug}_{digest}_{device}"


def normalize_routes(routes: List[str]) -> List[str]:
    normalized = []
    for route in routes:
        route = route.strip()
        if not route:
            continue
        if not route.startswith("/"):
            route = "/" + route
        route = re.sub(r"/+", "/", route)
        if len(route) > 1:
            route = route.rstrip("/")
        normalized.append(route)

    return list(dict.fromkeys(normalized))


# ---------------------------------------------------------------------------
# Browser task
# ---------------------------------------------------------------------------

def process_route_task(
    task_spec: dict,
    config: dict,
    preload_js: str,
    hide_css: str,
    dom_extractor_script: str,
) -> dict:
    route = task_spec["route"]
    device_mode = task_spec["device"]
    task_start = time.time()

    file_prefix = safe_file_prefix(route, device_mode)
    prod_target_url = build_target_url(config["prod_url"], route)
    stg_target_url = build_target_url(config["stg_url"], route)

    log(f"[{device_mode.upper()}] Scanning: {route}")

    is_mobile = device_mode == "mobile"
    user_agent = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
        if is_mobile
        else (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        )
    )

    base_context = {
        "viewport": {
            "width": task_spec["width"],
            "height": task_spec["height"],
        },
        "user_agent": user_agent,
        "is_mobile": is_mobile,
        "has_touch": is_mobile,
        "device_scale_factor": 1,
        "ignore_https_errors": True,
        "locale": "en-US",
        "timezone_id": "UTC",  # Prevent localized clock shifts
        "color_scheme": "light",
        "reduced_motion": "reduce",
    }

    active_browser = None
    ctx_prod = None
    ctx_stg = None
    prod_page = None
    stg_page = None

    try:
        active_browser = browser.get_browser(config["chrome_path"])

        def new_context(auth):
            context_kwargs = dict(base_context)
            if auth:
                context_kwargs["http_credentials"] = {
                    "username": auth[0],
                    "password": auth[1],
                }
            return active_browser.new_context(**context_kwargs)

        ctx_prod = new_context(config.get("prod_auth"))
        ctx_stg = new_context(config.get("stg_auth"))

        nav_timeout = config["timeout"] * 1000

        if preload_js:
            ctx_prod.add_init_script(preload_js)
            ctx_stg.add_init_script(preload_js)

        prod_page = ctx_prod.new_page()
        stg_page = ctx_stg.new_page()

        # Navigate concurrently to minimize chronological drift
        log(f"[{device_mode.upper()}] Loading environments in parallel: {route}")
        
        # Navigate both pages
        prod_resp = prod_page.goto(prod_target_url, wait_until="commit", timeout=nav_timeout)
        stg_resp = stg_page.goto(stg_target_url, wait_until="commit", timeout=nav_timeout)

        if prod_resp and prod_resp.status >= 400:
            log(f"Warning: prod returned HTTP {prod_resp.status} for {prod_target_url}")
        if stg_resp and stg_resp.status >= 400:
            log(f"Warning: stg returned HTTP {stg_resp.status} for {stg_target_url}")

        # Capture both environments
        prod_landmarks, raw_prod_img = browser.prepare_and_capture(
            prod_page,
            config["click_selectors"],
            hide_css,
            dom_extractor_script,
            logger=log,
        )

        stg_landmarks, raw_stg_img = browser.prepare_and_capture(
            stg_page,
            config["click_selectors"],
            hide_css,
            dom_extractor_script,
            logger=log,
        )

    finally:
        for page in (prod_page, stg_page):
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

        for context in (ctx_prod, ctx_stg):
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass

    out_dir = config["output_dir"]
    screenshots_dir = os.path.join(out_dir, "screenshots")
    os.makedirs(screenshots_dir, exist_ok=True)

    prod_rel = f"screenshots/{file_prefix}_prod.png"
    stg_rel = f"screenshots/{file_prefix}_stg.png"
    diff_rel = f"screenshots/{file_prefix}_diff.png"

    landmark_pairs = diff_engine.match_landmarks(
        prod_landmarks,
        stg_landmarks,
        logger=log,
    )

    (
        aligned_prod,
        aligned_stg,
        diff_img,
        mismatch_pixels,
    ) = diff_engine.compute_component_isolated_diff(
        raw_prod_img,
        raw_stg_img,
        landmark_pairs=landmark_pairs,
        threshold=config["threshold"],
        max_v_shift=config["max_v_shift"],
        noise_floor=config["noise_floor"],
        diff_gaps=not config["skip_gap_diff"],
        save_sections=config["save_sections"],
        file_prefix=file_prefix,
        out_dir=out_dir,
    )

    aligned_prod.save(os.path.join(out_dir, prod_rel), format="PNG")
    aligned_stg.save(os.path.join(out_dir, stg_rel), format="PNG")
    diff_img.save(os.path.join(out_dir, diff_rel), format="PNG")

    width, height = aligned_prod.size
    total_pixels = width * height

    mismatch_pct = (
        round(min(100.0, mismatch_pixels / total_pixels * 100), 3)
        if total_pixels
        else 0.0
    )

    is_failed = mismatch_pct > config["max_mismatch"]
    elapsed = round(time.time() - task_start, 2)

    log(
        f"[{device_mode.upper()}] Completed: {route} in {elapsed}s -> "
        f"{'FAILED' if is_failed else 'PASSED'} "
        f"({mismatch_pct}% mismatch, {len(landmark_pairs)} blocks paired)"
    )

    return {
        "route": route,
        "device": device_mode,
        "status": "failed" if is_failed else "passed",
        "mismatch_pct": mismatch_pct,
        "block_count": max(1, len(landmark_pairs)),
        "render_time": elapsed,
        "prod_img": prod_rel,
        "stg_img": stg_rel,
        "diff_img": diff_rel,
        "error": "",
    }


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def _worker_entrypoint(
    task: dict,
    config: dict,
    preload_js: str,
    hide_css: str,
    dom_extractor_script: str,
) -> dict:
    last_error = ""
    attempts = config["retries"] + 1

    for attempt in range(1, attempts + 1):
        try:
            return process_route_task(
                task,
                config,
                preload_js,
                hide_css,
                dom_extractor_script,
            )
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            log(
                f"Error scanning {task['route']} ({task['device']}) "
                f"[attempt {attempt}/{attempts}]: {last_error}"
            )
            try:
                browser.shutdown_browser()
            except Exception:
                pass

            if attempt < attempts:
                time.sleep(1)

    return {
        "route": task["route"],
        "device": task["device"],
        "status": "failed",
        "mismatch_pct": 100.0,
        "block_count": 0,
        "render_time": 0.0,
        "prod_img": "",
        "stg_img": "",
        "diff_img": "",
        "error": last_error,
    }


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(config: dict):
    start_time = time.time()

    validate_config(config)
    prepare_output_directory(config["output_dir"])

    preload_js = read_static_asset("preload_cleanup.js", required=False)
    hide_css = read_static_asset("hide_overlays.css", required=False)
    dom_extractor_script = read_static_asset("dom_extractor.js")
    card_template = read_static_asset("card_template.html", required=False)
    report_template = read_static_asset("report_template.html", required=False)

    # -----------------------------------------------------------------------
    # Discover routes
    # -----------------------------------------------------------------------

    routes = []
    if config["crawl"]:
        routes = crawler.discover_internal_routes(
            config["prod_url"],
            config["max_pages"],
            config.get("prod_auth"),
            logger=log,
        )
    elif config["pages"]:
        routes = split_list(config["pages"])

    routes = normalize_routes(routes)
    if not routes:
        routes = ["/"]

    log(f"Discovered {len(routes)} route(s).")

    # -----------------------------------------------------------------------
    # Build tasks
    # -----------------------------------------------------------------------

    desktop_width, desktop_height = parse_resolution(config["desktop_res"], "desktop")
    mobile_width, mobile_height = parse_resolution(config["mobile_res"], "mobile")

    tasks = []
    for route in routes:
        if config["device"] in ("desktop", "both"):
            tasks.append({
                "route": route,
                "device": "desktop",
                "width": desktop_width,
                "height": desktop_height,
            })
        if config["device"] in ("mobile", "both"):
            tasks.append({
                "route": route,
                "device": "mobile",
                "width": mobile_width,
                "height": mobile_height,
            })

    if not tasks:
        raise RuntimeError("No regression tasks were created.")

    worker_count = max(1, min(config["workers"], len(tasks)))
    log(f"Queued {len(tasks)} task(s) across {worker_count} worker process(es).")

    # -----------------------------------------------------------------------
    # Run workers
    # -----------------------------------------------------------------------

    results: List[Optional[dict]] = [None] * len(tasks)

    with concurrent.futures.ProcessPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(
                _worker_entrypoint,
                task,
                config,
                preload_js,
                hide_css,
                dom_extractor_script,
            ): index
            for index, task in enumerate(tasks)
        }

        for future in concurrent.futures.as_completed(future_map):
            index = future_map[future]
            try:
                results[index] = future.result()
            except Exception as exc:
                task = tasks[index]
                error_message = f"{type(exc).__name__}: {exc}"
                log(f"Worker crashed for {task['route']} ({task['device']}): {error_message}")
                results[index] = {
                    "route": task["route"],
                    "device": task["device"],
                    "status": "failed",
                    "mismatch_pct": 100.0,
                    "block_count": 0,
                    "render_time": 0.0,
                    "prod_img": "",
                    "stg_img": "",
                    "diff_img": "",
                    "error": error_message,
                }

    final_results = [result for result in results if result is not None]

    # -----------------------------------------------------------------------
    # Generate report
    # -----------------------------------------------------------------------

    report_path = report.generate_html_report(
        final_results,
        config,
        card_template_str=card_template,
        report_template_str=report_template,
        logger=log,
    )

    elapsed_total = round(time.time() - start_time, 2)
    log(f"Regression suite finished in {elapsed_total}s.")

    failed = [result for result in final_results if result["status"] == "failed"]
    passed = len(final_results) - len(failed)

    log(f"Summary: {passed} passed, {len(failed)} failed.")

    for result in failed:
        reason = result["error"] or f"{result['mismatch_pct']}% mismatch"
        log(f"  FAILED {result['route']} [{result['device']}]: {reason}")

    # -----------------------------------------------------------------------
    # Open report
    # -----------------------------------------------------------------------

    if not config["no_open"]:
        import webbrowser

        report_uri = Path(report_path).resolve().as_uri()
        log(f"Opening report: {report_uri}")
        try:
            webbrowser.open(report_uri)
        except Exception as exc:
            log(f"Warning: could not open report automatically: {exc}")

    return report_path, final_results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_arguments()
    config = vars(args).copy()

    config.update({
        "prod_url": args.prod_url.rstrip("/"),
        "stg_url": args.stg_url.rstrip("/"),
        "click_selectors": split_list(args.click_selectors),
        "prod_auth": parse_auth(args.prod_auth),
        "stg_auth": parse_auth(args.stg_auth),
    })

    _, results = run_pipeline(config)

    if any(result["status"] == "failed" for result in results) and not args.exit_zero:
        sys.exit(1)


if __name__ == "__main__":
    main()