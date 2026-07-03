# CLAUDE.md

Guidance for working in this repository with Claude Code.

## What it is

A single-file CLI tool (`onshape_render.py`) that generates PNG renders of Onshape
Part Studios, Assemblies, or individual parts, using the `shadedviews` endpoint of
Onshape's REST API. No heavy dependencies: `requests` is required, `python-dotenv`
and `Pillow` are optional.

## Layout

- `onshape_render.py` — the whole program (CLI + interactive browser + API calls).
- `requirements.txt` — dependencies.
- `.env` / `.env.example` — credentials (the real `.env` is in `.gitignore`).
- `README.md` — user documentation.
- `renders/` — generated output (git-ignored).

There's no package or module split: it's a flat script. Keep it that way unless it
grows a lot.

## Running and testing

```bash
pip install -r requirements.txt          # or use the existing .venv/
python onshape_render.py --help
python onshape_render.py                  # interactive mode
```

- **Quick syntax check**: `python -m py_compile onshape_render.py`.
- **There is no test suite.** To validate changes you run against the real API,
  which needs a `.env` with valid keys. Test in the scratchpad with
  `--outdir <tmp> --views iso --size 500 --no-montage` so it's fast and doesn't
  clutter `renders/`.
- The interactive mode can be tested by piping input via stdin, e.g.
  `printf 'search\n1\n3\n2\n' | python onshape_render.py --views iso --size 500`.

## Key Onshape API details

- **Auth**: API keys as **Basic Auth** (`session.auth = (access, secret)`).
  Avoids HMAC. Keys from https://dev-portal.onshape.com.
- **Endpoints** used:
  - `GET /api/documents` — list documents (`limit` max **20**; `q` to search).
  - `GET /api/documents/d/{did}/workspaces`
  - `GET /api/documents/d/{did}/w/{wid}/elements` — filter `PARTSTUDIO`/`ASSEMBLY`.
  - `GET /api/parts/d/{did}/{wvm}/{wvmid}/e/{eid}` — list parts.
  - `GET /api/{partstudios|assemblies}/d/.../e/{eid}/shadedviews`
  - `GET /api/parts/d/.../e/{eid}/partid/{partid}/shadedviews` — single part.
- **`shadedviews`** returns JSON `{"images": ["<base64 png>"]}`.
  Params: `viewMatrix`, `outputWidth/Height`, `pixelSize` (0 = auto-fit),
  `edges` (show/hide), `useAntiAliasing`, `includeSurfaces`, `showAllParts`.
- **Resolution limit** (verified): **~2375 px per side**; above that it returns
  HTTP 500. The default is 2048. If you touch it, don't raise the default without
  re-verifying.
- **`showAllParts`** only affects Part Studios; in assemblies, visibility is driven
  by the assembly's own display state.
- The **view matrices** in `VIEWS` are pure 3×4 rotations (det=+1); don't change
  them without recomputing. The isometric one is the classic (0.707/0.408/0.577).

## Project conventions

- **Language**: comments, docstrings, and user-facing messages are in **English**.
- **UTF-8 output**: `main()` does `sys.stdout.reconfigure(encoding="utf-8")`
  because some document names have characters outside cp1252 (Windows). Don't
  remove this.
- **Output layout**: `renders/<document>/<element>[/<part>]/`, and files are named
  `<document>_<element>[_<part>]_<view>.png`. `sanitize_name()` strips characters
  that are invalid on Windows.
- **Platform**: primarily used on Windows (PowerShell); use `os.path.join` for
  paths, nothing POSIX-only.
- **Optional dependencies** (`dotenv`, `PIL`) are always imported lazily with a
  fallback, so the script works with just `requests`.

## When making changes

- Keep the script runnable with only `requests` installed.
- If you add an API parameter, verify its name/effect against the real API before
  documenting it (several params are poorly documented).
- Update `README.md` (options table + examples) when the CLI changes.
