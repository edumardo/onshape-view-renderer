# Onshape Renderer

A small Python script for grabbing PNG images of [Onshape](https://www.onshape.com)
models from the terminal, without opening the browser. You point it at a document
(or browse your account) and it returns whatever views you want —front, isometric,
and so on— of a Part Studio, an Assembly, or a single part.

Under the hood it uses the `shadedviews` endpoint of Onshape's REST API. The only
hard dependency is `requests`.

## Install

```bash
git clone https://github.com/<your-user>/onshape-view-renderer.git
cd onshape-view-renderer
pip install -r requirements.txt
```

You need Python 3.8 or later. `python-dotenv` and `Pillow` come with the install
above but are optional: without `dotenv` the `.env` is still read by a small
built-in parser, and without `Pillow` you just don't get the combined montage.

## Credentials

You need an Onshape API key pair. Create one at
<https://dev-portal.onshape.com>.

Copy the template and paste your keys in:

```bash
cp .env.example .env
```

```dotenv
ONSHAPE_ACCESS_KEY=...
ONSHAPE_SECRET_KEY=...
# Only for Onshape Enterprise on a custom domain:
# ONSHAPE_BASE_URL=https://cad.onshape.com
```

The `.env` is read on startup and is listed in `.gitignore`, so it won't end up in
the repository. System environment variables work too, and take priority over the
`.env`.

## Usage

There are three ways to tell it what to render.

**No arguments** — browse your account from the terminal:

```bash
python onshape_render.py
```

You search for the document by name (or hit Enter for the 20 most recent), pick a
workspace if there's more than one, then the Part Studio or Assembly, and if it's a
Part Studio you can drill down to a single part. In each menu: a number to pick, `b`
to go back, `q` to quit.

**With the URL** you copy from the address bar in Onshape:

```bash
python onshape_render.py "https://cad.onshape.com/documents/DID/w/WID/e/EID"
```

**With the raw IDs**, if you already have them:

```bash
python onshape_render.py --did DID --wvmid WID --eid EID
```

By default it renders all 7 views at 2048 px. A few ways to narrow it down:

```bash
# just the usual four views
python onshape_render.py URL --views front,top,right,iso

# 16:9 and skip the combined montage
python onshape_render.py URL --width 1920 --height 1080 --no-montage

# include the hidden parts of the Part Studio
python onshape_render.py URL --all-parts

# list the parts, then render only one
python onshape_render.py URL --list-parts
python onshape_render.py URL --part "top plate"
```

## Options

| Flag                   | What it does                                                 | Default     |
|------------------------|--------------------------------------------------------------|-------------|
| `--views`              | Comma-separated views, or `all`. Available: `front`, `back`, `top`, `bottom`, `left`, `right`, `iso` | `all` |
| `--type`               | `auto`, `assembly`, or `partstudio`                          | `auto`      |
| `--size`               | Image side in pixels (square). Real cap is ~2375             | `2048`      |
| `--width` / `--height` | Non-square resolution (override `--size`)                    | —           |
| `--pixel-size`         | mm per pixel: `0` auto-fits; `>0` fixes the zoom             | `0`         |
| `--edges`              | `show` or `hide`                                             | `show`      |
| `--no-antialiasing`    | Turn off smoothing                                           | off         |
| `--include-surfaces`   | Include surface bodies                                       | off         |
| `--bg`                 | Background: `white`, `black`, or `transparent`               | `white`     |
| `--all-parts`          | Also include hidden parts of the Part Studio                 | off         |
| `--part`               | Render only the part with this name                          | —           |
| `--part-id`            | Render only the part with this partId                        | —           |
| `--list-parts`         | List the Part Studio's parts and exit                        | —           |
| `--no-montage`         | Don't generate the combined montage                          | off         |
| `--outdir`             | Output folder                                                | `renders`   |
| `--prefix`             | Extra prefix on the file name                                | —           |
| `--wvm`                | `w` workspace, `v` version, `m` microversion                 | `w`         |

## Where the images go

```
renders/
└── <document>/
    └── <part studio or assembly>/
        ├── <document>_<element>_front.png
        ├── <document>_<element>_iso.png
        └── <document>_<element>_montage.png
```

If you pick a single part, one more level is added:
`renders/<document>/<part studio>/<part>/`.

## A few things worth knowing

- Resolution has a real cap around **2375 px per side**; past that the API returns a
  500. The script catches it and tells you to lower `--size`.
- `--all-parts`, `--part`, and `--part-id` are a Part Studio thing. In Assemblies,
  visibility is driven by the assembly's own display state.
- Onshape returns renders with a **transparent** background (which looks black in
  many viewers). By default the script flattens them onto **white**; use
  `--bg transparent` to keep the alpha channel, or `--bg black`. Flattening needs
  Pillow — without it, images are saved transparent.
- `shadedviews` respects the model's colors and appearances, but it doesn't do
  photorealistic lighting or materials. That's what Render Studio is for — a
  separate tool.
- Auth uses the API keys as Basic Auth; there's no HMAC signing to deal with.

## License

MIT — see [`LICENSE`](LICENSE).

Personal project, not affiliated with Onshape or PTC.
