#!/usr/bin/env python3
"""Automatically render views of a Part Studio, Assembly, or single part
using the Onshape API (shadedviews endpoint).

Usage:
    python onshape_render.py                # interactive mode (browse your account)
    python onshape_render.py "https://cad.onshape.com/documents/DID/w/WID/e/EID"
    python onshape_render.py --did DID --wvm w --wvmid WID --eid EID
    python onshape_render.py URL --views all --size 2048 --all-parts

Authentication (API keys from https://dev-portal.onshape.com):
    Read from a .env file (or from environment variables):
        ONSHAPE_ACCESS_KEY=...
        ONSHAPE_SECRET_KEY=...
        ONSHAPE_BASE_URL=https://cad.onshape.com   (optional)
    Copy .env.example to .env and fill in your keys.
"""

import argparse
import base64
import os
import re
import time
import sys
from urllib.parse import urlparse, quote


def load_dotenv():
    """Load variables from a .env file without overwriting existing ones.

    Uses python-dotenv if installed; otherwise falls back to a minimal parser.
    Looks for .env next to the script and in the current directory.
    """
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        os.path.join(os.getcwd(), ".env"),
    ]
    try:
        from dotenv import load_dotenv as _ld
        for path in candidates:
            if os.path.isfile(path):
                _ld(path)
        return
    except ImportError:
        pass
    # Dependency-free fallback: simple KEY=VALUE parsing.
    for path in candidates:
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)


# 3x4 view matrices (12 values, row-major) used by Onshape.
# The last column (translation) is left at 0: with pixelSize=0 the model is
# auto-fitted into the image. All are pure rotations (det=+1).
VIEWS = {
    "front":  "1,0,0,0,0,1,0,0,0,0,1,0",
    "back":   "-1,0,0,0,0,1,0,0,0,0,-1,0",
    "top":    "1,0,0,0,0,0,1,0,0,-1,0,0",
    "bottom": "1,0,0,0,0,0,-1,0,0,1,0,0",
    "right":  "0,0,-1,0,0,1,0,0,1,0,0,0",
    "left":   "0,0,1,0,0,1,0,0,-1,0,0,0",
    # Classic isometric (equal foreshortening on all 3 axes)
    "iso":    "0.707106781,0.707106781,0,0,"
              "-0.408248290,0.408248290,0.816496581,0,"
              "0.577350269,-0.577350269,0.577350269,0",
}

# Default resolution: high quality with some headroom. The endpoint returns
# HTTP 500 above ~2400 px per side, so 2048 is a high yet safe value.
DEFAULT_SIZE = 2048


def parse_document_url(url):
    """Extract (did, wvm, wvmid, eid) from an Onshape document URL.

    Typical format:
      https://cad.onshape.com/documents/{did}/w/{wid}/e/{eid}
      .../v/{vid}/e/{eid}  or  .../m/{mid}/e/{eid}
    """
    path = urlparse(url).path
    m = re.search(
        r"/documents/(?P<did>[0-9a-f]+)/(?P<wvm>[wvm])/(?P<wvmid>[0-9a-f]+)/e/(?P<eid>[0-9a-f]+)",
        path,
    )
    if not m:
        raise ValueError(f"Could not parse the Onshape URL:\n  {url}")
    return m.group("did"), m.group("wvm"), m.group("wvmid"), m.group("eid")


def make_session():
    try:
        import requests
    except ImportError:
        sys.exit("Missing 'requests'. Install with:  pip install -r requirements.txt")
    access = os.environ.get("ONSHAPE_ACCESS_KEY")
    secret = os.environ.get("ONSHAPE_SECRET_KEY")
    if not access or not secret:
        sys.exit(
            "Missing credentials. Set ONSHAPE_ACCESS_KEY and ONSHAPE_SECRET_KEY.\n"
            "Create your API keys at https://dev-portal.onshape.com"
        )
    s = requests.Session()
    s.auth = (access, secret)  # Onshape accepts API keys as Basic Auth
    s.headers.update({"Accept": "application/json"})
    return s


def sanitize_name(name, fallback):
    """Turn a name into something valid as a folder name."""
    name = (name or "").strip()
    if not name:
        return fallback
    # Remove characters that are invalid on Windows and collapse whitespace.
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name or fallback


def fetch_element_info(session, base, did, wvm, wvmid, eid):
    """Return (resource, element_name) for the given element.

    resource is 'assemblies' or 'partstudios'.
    """
    url = f"{base}/api/documents/d/{did}/{wvm}/{wvmid}/elements"
    r = session.get(url, params={"elementId": eid})
    r.raise_for_status()
    for el in r.json():
        if el.get("id") == eid:
            name = el.get("name")
            etype = (el.get("elementType") or el.get("type") or "").upper()
            if etype == "ASSEMBLY":
                return "assemblies", name
            if etype == "PARTSTUDIO":
                return "partstudios", name
            raise SystemExit(
                f"The element is of type '{etype}', not a Part Studio or Assembly."
            )
    raise SystemExit(f"Element {eid} was not found in the document.")


def fetch_document_name(session, base, did):
    """Return the document name (or None if it can't be fetched)."""
    try:
        r = session.get(f"{base}/api/documents/{did}")
        r.raise_for_status()
        return r.json().get("name")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Interactive terminal browser (when no URL or IDs are provided)
# ---------------------------------------------------------------------------

def _prompt(msg):
    """input() that exits cleanly on Ctrl-C / EOF."""
    try:
        return input(msg)
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)


def _pick(items, label_fn, title, allow_back=False):
    """Print a numbered list and return the chosen item.

    Returns None if the user presses 'b' (only when allow_back=True).
    """
    print(f"\n{title}:")
    for i, it in enumerate(items, 1):
        print(f"  {i:>3}. {label_fn(it)}")
    extra = ", 'b' back" if allow_back else ""
    while True:
        sel = _prompt(f"Pick [1-{len(items)}{extra}, 'q' quit]: ").strip().lower()
        if sel == "q":
            sys.exit(0)
        if allow_back and sel == "b":
            return None
        if sel.isdigit() and 1 <= int(sel) <= len(items):
            return items[int(sel) - 1]
        print("  Invalid input.")


def browse_documents(session, base):
    """Search/list documents (20 most recent) and return the chosen one."""
    while True:
        q = _prompt("\nSearch document (Enter = 20 most recent, 'q' quit): ").strip()
        if q.lower() == "q":
            sys.exit(0)
        params = {"limit": 20, "sortColumn": "modifiedAt", "sortOrder": "desc"}
        if q:
            params["q"] = q
        r = session.get(f"{base}/api/documents", params=params)
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            print("  (no results, try another search)")
            continue
        doc = _pick(items, lambda d: d.get("name", "(unnamed)"),
                    f"Documents ({len(items)})", allow_back=True)
        if doc is not None:
            return doc


def choose_workspace(session, base, did, default_wid):
    """Choose a workspace; if there is only one, return it directly."""
    r = session.get(f"{base}/api/documents/d/{did}/workspaces")
    r.raise_for_status()
    ws = r.json()
    if len(ws) <= 1:
        return ws[0]["id"] if ws else default_wid
    picked = _pick(
        ws,
        lambda w: w.get("name", "?") + (" (default)" if w.get("id") == default_wid else ""),
        "Workspaces",
    )
    return picked["id"]


def choose_element(session, base, did, wid):
    """List the workspace's Part Studios and Assemblies and return the chosen element."""
    r = session.get(f"{base}/api/documents/d/{did}/w/{wid}/elements")
    r.raise_for_status()
    els = [e for e in r.json()
           if (e.get("elementType") or "").upper() in ("PARTSTUDIO", "ASSEMBLY")]
    if not els:
        sys.exit("The document has no Part Studios or Assemblies.")
    return _pick(
        els,
        lambda e: f"{e.get('name', '?')}  [{(e.get('elementType') or '').lower()}]",
        "Part Studios / Assemblies",
    )


def list_parts(session, base, did, wvm, wvmid, eid):
    """Return the list of parts in a Part Studio."""
    r = session.get(f"{base}/api/parts/d/{did}/{wvm}/{wvmid}/e/{eid}")
    r.raise_for_status()
    return r.json()


def choose_part(session, base, did, wid, eid):
    """Offer to choose a single part or the whole Part Studio.

    Returns (part_id, part_name); (None, None) = the whole Part Studio.
    """
    parts = list_parts(session, base, did, "w", wid, eid)
    if not parts:
        return None, None
    # Synthetic option to render the whole Part Studio.
    options = [{"partId": None, "name": "(whole Part Studio)"}] + parts
    picked = _pick(
        options,
        lambda p: p["name"] if p["partId"] is None
        else f"{p.get('name', '?')}  [{p.get('bodyType', '?')}]",
        "Parts",
    )
    return picked.get("partId"), (picked.get("name") if picked.get("partId") else None)


def browse_account(session, base):
    """Full navigation: document -> workspace -> element -> (part).

    Returns (did, wvm, wvmid, eid, part_id, part_name).
    """
    doc = browse_documents(session, base)
    did = doc["id"]
    wid = choose_workspace(session, base, did,
                           (doc.get("defaultWorkspace") or {}).get("id"))
    element = choose_element(session, base, did, wid)
    eid = element["id"]
    is_partstudio = (element.get("elementType") or "").upper() == "PARTSTUDIO"
    part_id, part_name = (None, None)
    if is_partstudio:
        part_id, part_name = choose_part(session, base, did, wid, eid)
    return did, "w", wid, eid, part_id, part_name


def render_view(session, base, resource, did, wvm, wvmid, eid, view_matrix,
                width, height, edges, pixel_size, antialiasing, include_surfaces,
                all_parts, part_id=None):
    if part_id:
        # Render a single part of the Part Studio.
        url = (f"{base}/api/parts/d/{did}/{wvm}/{wvmid}/e/{eid}"
               f"/partid/{quote(part_id, safe='')}/shadedviews")
    else:
        url = (f"{base}/api/{resource}/d/{did}/{wvm}/{wvmid}/e/{eid}/shadedviews")
    params = {
        "viewMatrix": view_matrix,
        "outputHeight": height,
        "outputWidth": width,
        "pixelSize": pixel_size,          # 0 = auto-fit; >0 = mm/pixel (zoom)
        "edges": edges,                   # "show" | "hide"
        "useAntiAliasing": "true" if antialiasing else "false",
        "includeSurfaces": "true" if include_surfaces else "false",
        # true = also include hidden parts of the Part Studio
        "showAllParts": "true" if all_parts else "false",
    }
    r = session.get(url, params=params)
    r.raise_for_status()
    data = r.json()
    images = data.get("images") or []
    if not images:
        raise RuntimeError(f"Response has no image: {data}")
    return base64.b64decode(images[0])


def flatten_bg(png_bytes, bg):
    """Composite a (transparent) PNG onto a solid background color.

    bg is an (r, g, b, a) tuple, or None to leave the PNG untouched.
    Requires Pillow; if it's missing, returns the bytes unchanged.
    """
    if bg is None:
        return png_bytes
    try:
        from PIL import Image
    except ImportError:
        return png_bytes
    import io
    im = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    canvas = Image.new("RGBA", im.size, bg)
    canvas.paste(im, (0, 0), im)
    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def build_montage(image_paths, out_path, bg):
    """Compose a grid montage if Pillow is available (optional).

    bg is an (r, g, b, a) background tuple, or None for a transparent montage.
    """
    try:
        from PIL import Image
    except ImportError:
        print("  (Pillow not installed: skipping montage)")
        return
    import math
    imgs = [Image.open(p).convert("RGBA") for p in image_paths]
    if not imgs:
        return
    cols = math.ceil(math.sqrt(len(imgs)))
    rows = math.ceil(len(imgs) / cols)
    w = max(i.width for i in imgs)
    h = max(i.height for i in imgs)
    canvas = Image.new("RGBA", (w * cols, h * rows), bg or (0, 0, 0, 0))
    for idx, img in enumerate(imgs):
        x = (idx % cols) * w
        y = (idx // cols) * h
        canvas.paste(img, (x, y), img)
    if bg is None:
        canvas.save(out_path)              # keep transparency (RGBA PNG)
    else:
        canvas.convert("RGB").save(out_path)
    print(f"  Montage {cols}x{rows} -> {out_path}")


def main():
    # Document/part names may contain characters outside cp1252; force UTF-8
    # on output so the Windows console doesn't break.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(
        description="Render views of a Part Studio/Assembly via the Onshape API."
    )
    ap.add_argument("url", nargs="?", help="Onshape document URL")
    ap.add_argument("--did", help="Document ID")
    ap.add_argument("--wvm", choices=["w", "v", "m"], default="w",
                    help="Type: w=workspace, v=version, m=microversion (default: w)")
    ap.add_argument("--wvmid", help="Workspace/Version/Microversion ID")
    ap.add_argument("--eid", help="Element ID")
    ap.add_argument("--type", choices=["auto", "assembly", "partstudio"],
                    default="auto", help="Element type (default: auto-detect)")
    ap.add_argument("--views", default="all",
                    help="Comma-separated views, or 'all' (default). Available: "
                         + ", ".join(VIEWS))
    ap.add_argument("--size", type=int, default=DEFAULT_SIZE,
                    help=f"Image side in px, square (default: {DEFAULT_SIZE}, high quality). "
                         "Real max ~2375; above that the API fails. Ignored if you use --width/--height")
    ap.add_argument("--width", type=int, help="Width in px (overrides --size)")
    ap.add_argument("--height", type=int, help="Height in px (overrides --size)")
    ap.add_argument("--pixel-size", type=float, default=0.0,
                    help="mm per pixel: 0 = auto-fit; >0 fixes the zoom (default: 0)")
    ap.add_argument("--edges", choices=["show", "hide"], default="show",
                    help="Show edges (default: show)")
    ap.add_argument("--no-antialiasing", action="store_true",
                    help="Disable smoothing (enabled by default)")
    ap.add_argument("--include-surfaces", action="store_true",
                    help="Include surface bodies in the render")
    ap.add_argument("--all-parts", action="store_true",
                    help="Render ALL parts of the Part Studio, including hidden ones")
    ap.add_argument("--part-id", help="Render only the part with this partId")
    ap.add_argument("--part", help="Render only the part with this name (Part Studio)")
    ap.add_argument("--list-parts", action="store_true",
                    help="List the Part Studio's parts and exit")
    ap.add_argument("--bg", choices=["white", "black", "transparent"],
                    default="white",
                    help="Background color (default: white). Onshape renders are "
                         "transparent; 'white'/'black' flatten them (needs Pillow)")
    ap.add_argument("--no-montage", action="store_true",
                    help="Don't generate the combined montage")
    ap.add_argument("--outdir", default="renders", help="Output folder (default: renders)")
    ap.add_argument("--prefix", default="",
                    help="Extra prefix prepended to <document>_<element>_<view>")
    args = ap.parse_args()

    # Final resolution.
    width = args.width or args.size
    height = args.height or args.size

    # Background color. Onshape renders are transparent; white/black are
    # flattened client-side with Pillow.
    bg = {"white": (255, 255, 255, 255),
          "black": (0, 0, 0, 255),
          "transparent": None}[args.bg]
    if bg is not None:
        try:
            import PIL  # noqa: F401
        except ImportError:
            print(f"Warning: Pillow is not installed; can't apply the '{args.bg}' "
                  "background. Saving transparent PNGs instead.")
            bg = None

    # View selection.
    if args.views.strip().lower() == "all":
        selected = list(VIEWS)
    else:
        selected = [v.strip() for v in args.views.split(",") if v.strip()]
        unknown = [v for v in selected if v not in VIEWS]
        if unknown:
            ap.error(f"Invalid views: {', '.join(unknown)}. "
                     f"Available: {', '.join(VIEWS)}")

    load_dotenv()
    base = os.environ.get("ONSHAPE_BASE_URL", "https://cad.onshape.com").rstrip("/")

    session = make_session()

    part_id, part_name = args.part_id, None
    if args.url:
        did, wvm, wvmid, eid = parse_document_url(args.url)
    elif args.did and args.wvmid and args.eid:
        did, wvm, wvmid, eid = args.did, args.wvm, args.wvmid, args.eid
    else:
        # No URL or IDs: interactive terminal browser.
        print("No URL or IDs given: browsing your Onshape account...")
        did, wvm, wvmid, eid, part_id, part_name = browse_account(session, base)

    # Element name and type (also used to name the folders).
    resource, element_name = fetch_element_info(session, base, did, wvm, wvmid, eid)
    if args.type == "assembly":
        resource = "assemblies"
    elif args.type == "partstudio":
        resource = "partstudios"

    # --list-parts: list the Part Studio's parts and exit.
    if args.list_parts:
        if resource != "partstudios":
            sys.exit("--list-parts only applies to Part Studios.")
        print("partId\tname\t[type]")
        for p in list_parts(session, base, did, wvm, wvmid, eid):
            print(f"  {p.get('partId')}\t{p.get('name')}\t[{p.get('bodyType')}]")
        return

    # Select a part by name (non-interactive mode).
    if not part_id and args.part:
        matches = [p for p in list_parts(session, base, did, wvm, wvmid, eid)
                   if (p.get("name") or "").lower() == args.part.lower()]
        if not matches:
            sys.exit(f"No part named {args.part!r} in the Part Studio.")
        part_id, part_name = matches[0]["partId"], matches[0]["name"]

    if part_id:
        if resource != "partstudios":
            sys.exit("Rendering a single part only applies to Part Studios.")
        if not part_name:  # resolve the name from the partId
            m = [p for p in list_parts(session, base, did, wvm, wvmid, eid)
                 if p.get("partId") == part_id]
            part_name = m[0]["name"] if m else part_id

    type_label = "assembly" if resource == "assemblies" else "part studio"
    doc_name = fetch_document_name(session, base, did)
    print(f"Document: {doc_name or did}")
    print(f"Element: {element_name or eid}  ({type_label})")
    if part_id:
        print(f"Part: {part_name}")

    # Output layout: renders/<document>/<element>[/<part>]/
    doc_folder = sanitize_name(doc_name, did)
    element_folder = sanitize_name(element_name, eid)
    prefix_parts = [args.prefix, doc_folder, element_folder]
    outdir = os.path.join(args.outdir, doc_folder, element_folder)
    if part_id:
        part_folder = sanitize_name(part_name, part_id)
        outdir = os.path.join(outdir, part_folder)
        prefix_parts.append(part_folder)
    os.makedirs(outdir, exist_ok=True)

    # File prefix: <prefix_?><document>_<element>[_<part>]_<view>.png
    file_prefix = "_".join(p for p in prefix_parts if p)

    if part_id:
        parts_info = "1 part"
    else:
        parts_info = "all" if args.all_parts else "visible"
    print(f"Views: {', '.join(selected)}  |  {width}x{height}px  |  "
          f"antialiasing={'no' if args.no_antialiasing else 'yes'}  |  "
          f"parts={parts_info}")

    saved = []
    t_start = time.perf_counter()
    for name in selected:
        stamp = time.strftime("%H:%M:%S")
        print(f"[{stamp}] Rendering view '{name}'...")
        t0 = time.perf_counter()
        try:
            png = render_view(session, base, resource, did, wvm, wvmid, eid,
                              VIEWS[name], width, height, args.edges,
                              args.pixel_size, not args.no_antialiasing,
                              args.include_surfaces, args.all_parts, part_id)
        except Exception as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code == 500 and max(width, height) > 2000:
                sys.exit(f"  Error {code}: resolution {width}x{height} exceeds the "
                         "API limit (~2375 px per side). Lower --size.")
            raise
        dt = time.perf_counter() - t0
        png = flatten_bg(png, bg)
        path = os.path.join(outdir, f"{file_prefix}_{name}.png")
        with open(path, "wb") as f:
            f.write(png)
        print(f"  -> {path}  ({len(png)} bytes, {dt:.2f}s)")
        saved.append(path)

    if not args.no_montage:
        montage_path = os.path.join(outdir, f"{file_prefix}_montage.png")
        build_montage(saved, montage_path, bg)

    total = time.perf_counter() - t_start
    n = len(saved)
    avg = f", avg {total / n:.2f}s/view" if n else ""
    print(f"Done: {n} view(s) in {total:.2f}s{avg}.")


if __name__ == "__main__":
    main()
