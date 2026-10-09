import urllib.parse
from collections import deque
from typing import List, Optional, Tuple
import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

IGNORED_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf", ".zip", ".css", ".js",
    ".ashx", ".axd", ".ico", ".woff", ".woff2", ".xml", ".json", ".mp4", ".mp3",
)
IGNORED_PREFIXES = ("/sitecore", "/-/", "/api/")


def normalize_route(path: str) -> str:
    path = path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    return path


def discover_internal_routes(prod_url: str, max_pages: int = 20, auth: Optional[Tuple[str, str]] = None, logger=print) -> List[str]:
    """Breadth-first crawl. Returned routes are relative to the base URL's path."""
    if prod_url.startswith("file://"):
        logger("Local file protocol detected. Crawl bypassed; evaluating root only.")
        return ["/"]

    logger(f"Initiating crawler from: {prod_url} (Max: {max_pages})")
    parsed_base = urllib.parse.urlparse(prod_url)
    origin = f"{parsed_base.scheme}://{parsed_base.netloc}"
    base_path = parsed_base.path.rstrip("/")

    session = requests.Session()
    session.verify = False
    session.headers["User-Agent"] = "EnterpriseVisualDiffCrawler/4.0"
    if auth:
        session.auth = auth

    visited = {"/"}
    pending = deque(["/"])
    routes = []

    while pending and len(routes) < max_pages:
        route = pending.popleft()
        target_url = origin + base_path + route
        try:
            resp = session.get(target_url, timeout=12)
            if resp.status_code != 200 or "text/html" not in resp.headers.get("Content-Type", "").lower():
                continue
            if urllib.parse.urlparse(resp.url).netloc != parsed_base.netloc:
                continue
            routes.append(route)

            soup = BeautifulSoup(resp.content, "html.parser")
            for link in soup.find_all("a", href=True):
                href = link["href"].strip()
                if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    continue
                child = urllib.parse.urlparse(urllib.parse.urljoin(resp.url, href))
                if child.netloc != parsed_base.netloc:
                    continue
                path = child.path or "/"
                if base_path:
                    if path != base_path and not path.startswith(base_path + "/"):
                        continue
                    path = path[len(base_path):] or "/"
                lowered = path.lower()
                if lowered.endswith(IGNORED_EXTENSIONS) or lowered.startswith(IGNORED_PREFIXES):
                    continue
                path = normalize_route(path)
                if path not in visited and len(visited) < max_pages * 5:
                    visited.add(path)
                    pending.append(path)
        except Exception as e:
            logger(f"Crawl notice for {target_url}: {e}")

    logger(f"Route discovery completed: {len(routes)} path(s) ready.")
    return routes