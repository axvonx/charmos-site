#!/usr/bin/env python3
"""
charmos docs build pipeline
────────────────────────────
Clones the source repo, parses every header/source file into JSON in
parallel, then compiles the JSON into MDX documentation.
"""

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

REPO_URL = "https://github.com/axvonx/charmos.git"
CLONE_DIR = Path("./charmos")
JSON_OUT = Path("./json_output")
MD_OUT = Path("./docs")
LIMINE_URL = "https://github.com/limine-bootloader/limine"
LIMINE_DIR = Path("./limine")

# Clang-accurate semantic index (see index_clang.py). Configuring the kernel
# with CMake emits a compile_commands.json here, which libclang consumes to
# build clang_index.json — the single source of truth for symbol links.
CCDB_DIR = CLONE_DIR / "_ccdb"
CLANG_INDEX = Path("./clang_index.json")

# Woboq source browser (see build_source_browser). Best-effort: needs the
# codebrowser_generator binary (build from https://github.com/KDAB/codebrowser).
# Auto-discovered from .tools/woboq/ if present; overridable via env.
_WOBOQ_LOCAL = Path(".tools/woboq")
WOBOQ_GEN = os.environ.get("WOBOQ_GENERATOR") or str(_WOBOQ_LOCAL / "codebrowser_generator")
WOBOQ_IDX = os.environ.get("WOBOQ_INDEXGENERATOR") or str(
    _WOBOQ_LOCAL / "codebrowser_indexgenerator"
)
WOBOQ_DATA = os.environ.get("WOBOQ_DATA") or (
    str(_WOBOQ_LOCAL / "data") if (_WOBOQ_LOCAL / "data").exists() else None
)
# Served statically by Astro from site/public/ → available at /source/.
SOURCE_BROWSER_OUT = Path("./site/public/source")
SOURCE_BROWSER_URL = "/source"  # data assets live at /source/data
SOURCE_BROWSER_PROJECT = "charmos"  # → /source/charmos/<file>.html#<line>

# Resident (hand-authored) doc pages. This tree mirrors the final docs layout;
# generated content is overlaid on top of it during assembly.
CONTENT_SRC = Path("./content")
# Final Astro content collection. Fully rebuilt each run from CONTENT_SRC +
# generated reference (MD_OUT) + generated guides — never edited by hand.
SITE_DOCS = Path("./site/src/content/docs")

SOURCE_DIRS = [
    "include",
    # "kernel",
]

MAX_WORKERS = min(16, (os.cpu_count() or 4) * 2)

# ── ANSI palette ──────────────────────────────────────────────────────────────

ESC = "\033["


def _c(*codes):
    return f"{ESC}{';'.join(str(c) for c in codes)}m"


RESET = _c(0)
BOLD = _c(1)
DIM = _c(2)

# foreground colours
WHITE = _c(97)
GRAY = _c(90)
CYAN = _c(96)
GREEN = _c(92)
YELLOW = _c(93)
RED = _c(91)
MAGENTA = _c(95)
BLUE = _c(94)

# background colours
BG_DARK = _c(40)


def supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


USE_COLOR = supports_color()


def c(text: str, *codes) -> str:
    if not USE_COLOR:
        return text
    return "".join(codes) + text + RESET


# ── Layout constants ──────────────────────────────────────────────────────────

BAR_WIDTH = 28  # characters wide for the progress fill
STEP_INDENT = "  "

# ── Thread-safe output ────────────────────────────────────────────────────────

_print_lock = threading.Lock()


def term_width() -> int:
    return shutil.get_terminal_size((100, 24)).columns


def _clear_line():
    if USE_COLOR:
        sys.stdout.write(f"\r{ESC}2K")


def safe_print(*args, **kwargs):
    with _print_lock:
        _clear_line()
        print(*args, **kwargs)


# ── Progress bar ──────────────────────────────────────────────────────────────


class ProgressBar:
    """
    A single-line progress bar rendered on stdout.
    Thread-safe — callers update via .advance() from any thread.
    """

    FILL = "█"
    EMPTY = "░"
    SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, total: int, label: str):
        self.total = max(total, 1)
        self.label = label
        self._done = 0
        self._lock = threading.Lock()
        self._spin_i = 0
        self._active = True
        self._last = ""

        self._thread = threading.Thread(target=self._tick, daemon=True)
        self._thread.start()

    def advance(self, n: int = 1):
        with self._lock:
            self._done = min(self._done + n, self.total)

    def finish(self):
        with self._lock:
            self._done = self.total
            self._active = False
        self._thread.join()
        self._render(final=True)
        sys.stdout.write("\n")
        sys.stdout.flush()

    def _tick(self):
        while self._active:
            self._render()
            self._spin_i = (self._spin_i + 1) % len(self.SPIN)
            time.sleep(0.08)

    def _render(self, final: bool = False):
        with self._lock:
            done = self._done
            total = self.total

        pct = done / total
        filled = int(BAR_WIDTH * pct)
        empty = BAR_WIDTH - filled

        bar = c(self.FILL * filled, GREEN, BOLD) + c(self.EMPTY * empty, GRAY)

        spinner = c(self.SPIN[self._spin_i], CYAN, BOLD) if not final else c("✓", GREEN, BOLD)
        pct_str = c(f"{int(pct*100):3d}%", WHITE, BOLD)
        count = c(f"{done}/{total}", GRAY)
        label = c(self.label, CYAN)

        line = f"\r  {spinner} {label}  [{bar}] {pct_str}  {count}"

        # pad to clear any leftover characters
        tw = term_width()
        if len(line) < tw:
            line += " " * (tw - len(line) - 1)

        with _print_lock:
            sys.stdout.write(line)
            sys.stdout.flush()


# ── Step decorator ────────────────────────────────────────────────────────────

_STEP_NUM = 0
_STEP_LOCK = threading.Lock()


def begin_step(name: str, detail: str = "") -> float:
    global _STEP_NUM
    with _STEP_LOCK:
        _STEP_NUM += 1
        n = _STEP_NUM
    num_str = c(f"[{n:02d}]", BLUE, BOLD)
    name_str = c(name, WHITE, BOLD)
    det_str = (c(f"  {detail}", GRAY)) if detail else ""
    safe_print(f"\n{num_str} {name_str}{det_str}")
    return time.monotonic()


def end_step(t0: float, note: str = ""):
    elapsed = time.monotonic() - t0
    tick = c("✓", GREEN, BOLD)
    time_s = c(f"{elapsed:.1f}s", GRAY)
    note_s = c(f"  {note}", DIM) if note else ""
    safe_print(f"  {tick} done  {time_s}{note_s}")


def fail_step(msg: str):
    cross = c("✗", RED, BOLD)
    safe_print(f"\n  {cross} {c(msg, RED)}\n")
    sys.exit(1)


# ── Banner ────────────────────────────────────────────────────────────────────


def print_banner():
    lines = [
        "",
        c(" charmos  docs  builder ", WHITE, BOLD, BG_DARK),
        c(f" workers: {MAX_WORKERS}  •  python {sys.version.split()[0]} ", GRAY),
        "",
    ]
    for line in lines:
        print(line)


# ── Pipeline steps ────────────────────────────────────────────────────────────


def clone_repo():
    t0 = begin_step("Clone repository", REPO_URL)

    if CLONE_DIR.exists():
        safe_print(c("  ↩  already cloned — skipping", GRAY))
        end_step(t0, "cached")
        return

    _run(["git", "clone", "--depth=1", REPO_URL, str(CLONE_DIR)])
    _run(["git", "submodule", "init"], cwd=CLONE_DIR)
    _run(["git", "submodule", "update"], cwd=CLONE_DIR)

    tests_dir = CLONE_DIR / "kernel/uACPI/tests"
    if tests_dir.exists():
        shutil.rmtree(tests_dir)

    if not LIMINE_DIR.exists():
        _run(
            [
                "git",
                "clone",
                "--branch=v9.x-binary",
                "--depth=1",
                LIMINE_URL,
                str(LIMINE_DIR),
            ]
        )

    end_step(t0)


def _toolchain_file() -> Path | None:
    """Pick the CMake toolchain file the kernel uses for this platform."""
    import platform

    scripts = CLONE_DIR / "scripts"
    name = "macos_toolchain.cmake" if platform.system() == "Darwin" else "toolchain.cmake"
    tc = scripts / name
    return tc if tc.exists() else None


def generate_compile_commands():
    """Configure the kernel with CMake to emit compile_commands.json.

    Configure-only: a full kernel build is not required for clang tooling. This
    is best-effort — if CMake or the cross toolchain is unavailable, the docs
    still build, just without clang-accurate symbol links.
    """
    t0 = begin_step("Generate compile_commands", str(CCDB_DIR))

    if shutil.which("cmake") is None or not (CLONE_DIR / "CMakeLists.txt").exists():
        end_step(t0, c("skipped — no cmake/CMakeLists", GRAY))
        return False

    cmd = ["cmake", "-S", str(CLONE_DIR), "-B", str(CCDB_DIR), "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"]
    tc = _toolchain_file()
    if tc is not None:
        cmd.append(f"-DCMAKE_TOOLCHAIN_FILE={tc.resolve()}")

    result = subprocess.run(cmd, capture_output=True, text=True)
    ccjson = CCDB_DIR / "compile_commands.json"
    if result.returncode != 0 or not ccjson.exists():
        safe_print(c("  ⚠  configure failed — skipping clang index", YELLOW))
        safe_print(c(result.stderr.strip()[-400:], GRAY))
        end_step(t0, c("unavailable", YELLOW))
        return False

    end_step(t0, "ok")
    return True


def build_clang_index():
    """Build clang_index.json from compile_commands.json via libclang."""
    t0 = begin_step("Build clang index", str(CLANG_INDEX))
    try:
        import index_clang
    except Exception as e:  # libclang not importable
        safe_print(c(f"  ⚠  index_clang unavailable: {e}", YELLOW))
        end_step(t0, c("skipped", YELLOW))
        return False

    index = index_clang.index_compile_commands(CCDB_DIR, root=CLONE_DIR)
    index.save(CLANG_INDEX)
    end_step(t0, f"{len(index.symbols)} symbols, {len(index.references)} refs")
    return True


def _resolve_tool(name):
    """Find a tool on PATH or accept an explicit path from the env var."""
    return shutil.which(name) or (name if Path(name).exists() else None)


# Woboq bakes external hotlinks to its own logo into every page: the header
# logo (woboq-48.png, still served) and a footer attribution logo
# (woboq-16.png, which now 404s on their server → a broken image on every page).
# We already ship woboq-48.png under data/, so point both at the local copy —
# no broken image, no external image dependency, attribution text preserved.
_WOBOQ_LOGO_HOTLINKS = (
    "https://code.woboq.org/woboq-16.png",
    "https://code.woboq.org/data/woboq-48.png",
)


def _localize_woboq_logos():
    """Rewrite Woboq's external logo hotlinks to the local data/woboq-48.png."""
    local = f"{SOURCE_BROWSER_URL}/data/woboq-48.png"
    if not (SOURCE_BROWSER_OUT / "data" / "woboq-48.png").exists():
        return
    for html_path in SOURCE_BROWSER_OUT.rglob("*.html"):
        text = html_path.read_text(encoding="utf-8")
        new = text
        for hotlink in _WOBOQ_LOGO_HOTLINKS:
            new = new.replace(hotlink, local)
        if new != text:
            html_path.write_text(new, encoding="utf-8")


def build_source_browser():
    """Generate a Woboq clang cross-referenced source browser into site/public/source.

    Best-effort: skips cleanly if codebrowser_generator isn't available. Emits a
    static, clickable view of the whole codebase that docs deep-link into
    (``/source/charmos/<file>.html#<line>``). Returns True on success.
    """
    t0 = begin_step("Build source browser", str(SOURCE_BROWSER_OUT))
    gen = _resolve_tool(WOBOQ_GEN)
    ccjson = CCDB_DIR / "compile_commands.json"
    if gen is None or not ccjson.exists():
        end_step(t0, c("skipped — woboq/compile_commands unavailable", GRAY))
        return False

    if SOURCE_BROWSER_OUT.exists():
        shutil.rmtree(SOURCE_BROWSER_OUT)
    SOURCE_BROWSER_OUT.mkdir(parents=True)

    src_root = CLONE_DIR.resolve()
    # Woboq may emit per-file errors for the kernel's x86 flags on a non-x86
    # host; it still annotates everything it can parse, so we tolerate a
    # non-zero exit as long as HTML was produced.
    subprocess.run(
        [
            gen,
            "-b",
            str(CCDB_DIR.resolve()),
            "-a",
            "-o",
            str(SOURCE_BROWSER_OUT.resolve()),
            "-p",
            f"{SOURCE_BROWSER_PROJECT}:{src_root}",
            "-d",
            f"{SOURCE_BROWSER_URL}/data",
        ],
        capture_output=True,
        text=True,
    )

    html_count = len(list(SOURCE_BROWSER_OUT.rglob("*.html")))
    if html_count == 0:
        safe_print(c("  ⚠  source browser produced no output — skipping", YELLOW))
        end_step(t0, c("unavailable", YELLOW))
        return False

    idx = _resolve_tool(WOBOQ_IDX)
    if idx is not None:
        # The index generator needs the SAME -d data URL as the file generator;
        # without it, the directory-index pages reference /data/... at the site
        # root (404) instead of /source/data/..., so they render unstyled and
        # their [+] folder expanders (jquery) are dead.
        subprocess.run(
            [
                idx,
                str(SOURCE_BROWSER_OUT.resolve()),
                "-d",
                f"{SOURCE_BROWSER_URL}/data",
            ],
            capture_output=True,
            text=True,
        )

    # Copy Woboq's static assets (js/css) to /source/data.
    if WOBOQ_DATA and Path(WOBOQ_DATA).exists():
        shutil.copytree(WOBOQ_DATA, SOURCE_BROWSER_OUT / "data", dirs_exist_ok=True)

    _localize_woboq_logos()

    end_step(t0, f"{html_count} files")
    return True


def prepare_output_dirs():
    t0 = begin_step("Prepare output directories")
    if JSON_OUT.exists():
        shutil.rmtree(JSON_OUT)
    JSON_OUT.mkdir(parents=True)
    MD_OUT.mkdir(parents=True, exist_ok=True)
    end_step(t0)


def _json_path_for(file_path: Path) -> Path:
    parents = file_path.parts[-3:-1]
    name_bits = list(parents) + [file_path.stem]
    return JSON_OUT / ("_".join(name_bits) + ".json")


def run_make_json():
    files = []
    for dir_name in SOURCE_DIRS:
        dp = CLONE_DIR / dir_name
        if dp.exists():
            files.extend(dp.rglob("*.c"))
            files.extend(dp.rglob("*.h"))
    files = [f for f in files if f.is_file()]

    if not files:
        safe_print(c("  ⚠  no source files found", YELLOW))
        return

    t0 = begin_step("Parse source files → JSON", f"{len(files)} files  •  {MAX_WORKERS} workers")
    bar = ProgressBar(len(files), "parsing")
    errors: list[str] = []
    warnings: list[str] = []
    err_lock = threading.Lock()

    def parse_one(f: Path):
        out = _json_path_for(f)
        try:
            result = subprocess.run(
                ["python3", "make_json.py", str(f), str(out)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                with err_lock:
                    # Keep the full stderr — a clipped traceback is what masked
                    # the tree-sitter breakage that took CI down.
                    errors.append(f"{f.name}:\n{result.stderr.strip()}")
            elif result.stderr.strip():
                # Non-fatal diagnostics (e.g. orphaned idea bodies).
                with err_lock:
                    warnings.extend(result.stderr.strip().splitlines())
        except Exception as e:
            with err_lock:
                errors.append(f"{f.name}: {e}")
        finally:
            bar.advance()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(parse_one, f) for f in files]
        for _ in as_completed(futures):
            pass  # progress driven by bar.advance() inside parse_one

    bar.finish()

    succeeded = len(files) - len(errors)

    if errors:
        safe_print(c(f"  ⚠  {len(errors)} file(s) had errors:", YELLOW))
        for e in errors[:8]:
            safe_print(c(f"     • {e}", GRAY))
        if len(errors) > 8:
            safe_print(c(f"     … and {len(errors)-8} more", GRAY))

    if warnings:
        safe_print(c(f"  ⚠  {len(warnings)} warning(s):", YELLOW))
        for w in warnings[:10]:
            safe_print(c(f"     • {w.replace('[make_json] warning: ', '')}", GRAY))
        if len(warnings) > 10:
            safe_print(c(f"     … and {len(warnings)-10} more", GRAY))

    # A total wipe-out (nothing parsed) is a systemic failure — e.g. a broken
    # dependency — and must stop the build loudly rather than silently emit an
    # empty docs tree that only explodes later during assembly.
    if files and succeeded == 0:
        fail_step("all source files failed to parse — aborting")

    end_step(t0, f"{succeeded}/{len(files)} succeeded")


def run_make_md():
    t0 = begin_step("Compile JSON → MDX")

    # Show a spinner while the single-threaded md generator runs
    done_event = threading.Event()

    def spinner_thread():
        spin = ProgressBar.SPIN
        i = 0
        while not done_event.is_set():
            s = c(spin[i % len(spin)], CYAN, BOLD)
            msg = c("  compiling…", GRAY)
            with _print_lock:
                sys.stdout.write(f"\r  {s}{msg}")
                sys.stdout.flush()
            i += 1
            time.sleep(0.08)

    t = threading.Thread(target=spinner_thread, daemon=True)
    t.start()

    result = subprocess.run(
        ["python3", "make_md.py", str(JSON_OUT)],
        capture_output=False,
        text=True,
    )

    done_event.set()
    t.join()
    _clear_line()

    if result.returncode != 0:
        fail_step(f"make_md.py failed:\n{result.stderr.strip()}")

    end_step(t0)


def delete_empty_markdown():
    t0 = begin_step("Remove empty markdown files")
    paths = list(MD_OUT.glob("**/*.md"))
    count = 0
    for path in paths:
        if not path.read_text(encoding="utf-8").strip():
            path.unlink()
            count += 1
    end_step(t0, f"removed {count} empty file(s)")


def copy_directory_indexes():
    src_root = Path("charmos/include")
    docs_root = Path("docs")
    indexes = list(src_root.rglob("index.mdx"))
    if not indexes:
        return

    t0 = begin_step("Copy directory index files", f"{len(indexes)} found")
    bar = ProgressBar(len(indexes), "copying")

    # Route each landing page into the SAME label-named directory make_md writes
    # its pages to (so an index.mdx and its siblings never split across the
    # original and renamed dir). make_md owns the label map.
    from make_md import build_dir_label_map, dir_label_path

    label_map = build_dir_label_map(src_root)

    for index_file in indexes:
        rel = index_file.relative_to(src_root)
        label_dir = dir_label_path(rel.parent, label_map)
        dest = (docs_root / label_dir / rel.name) if label_dir else (docs_root / rel.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(index_file, dest)
        bar.advance()

    bar.finish()
    end_step(t0)


def run_make_cmdline():
    """Generate the command-line options guide from the cloned source."""
    t0 = begin_step("Generate command-line guide", "guides/cmdline.mdx")

    out = MD_OUT / "guides" / "cmdline.mdx"
    result = subprocess.run(
        ["python3", "make_cmdline.py", str(CLONE_DIR), "-o", str(out)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail_step(f"make_cmdline.py failed:\n{result.stderr.strip()}")

    note = result.stderr.strip() or "ok"
    end_step(t0, note)


def _copy_tree(src: Path, dest: Path):
    """Recursively copy ``src`` into ``dest`` (creating ``dest`` as needed)."""
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        target = dest / item.relative_to(src)
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def assemble_site_content():
    """Build ``site/src/content/docs`` from resident + generated sources.

    Layout of the final tree:
      * ``/``           — resident pages from ``content/`` (splash, section indexes)
      * ``/reference/`` — generated API docs from ``docs/`` (MD_OUT)
      * ``/guides/``    — resident guides + generated ``cmdline.mdx``
    """
    t0 = begin_step("Assemble site content", str(SITE_DOCS))

    if SITE_DOCS.exists():
        shutil.rmtree(SITE_DOCS)
    SITE_DOCS.mkdir(parents=True)

    # 1. Resident pages (content/ mirrors the final layout 1:1).
    if CONTENT_SRC.exists():
        _copy_tree(CONTENT_SRC, SITE_DOCS)

    # 2. Generated reference pages land under reference/ (index comes from
    #    content/reference/index.mdx, copied in step 1).
    if MD_OUT.exists():
        for item in MD_OUT.iterdir():
            # guides/ is generated separately below; everything else is reference
            if item.name == "guides":
                continue
            dest = SITE_DOCS / "reference" / item.name
            if item.is_dir():
                _copy_tree(item, dest)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest)

    # 3. Generated guides (e.g. cmdline.mdx) overlay the resident guides.
    gen_guides = MD_OUT / "guides"
    if gen_guides.exists():
        _copy_tree(gen_guides, SITE_DOCS / "guides")

    end_step(t0)


# Reverse link: each source page in the Woboq browser gets a "Go to docs" button
# back to its reference page. Styled to match the landing page's primary action
# card (light surface + evergreen pixel dissolve + accent border, docs sans font)
# so it reads as a first-class docs control dropped into Woboq's chrome. The
# button sits to the LEFT of Woboq's right-floated ~126px logo. The (large) pixel
# dissolve lives in ONE stylesheet at /source/, linked by every page rather than
# inlined 169× — see write_docs_backlink_css.
GLOBAL_CSS = Path("./site/src/styles/global.css")
DOCS_BACKLINK_CSS_NAME = "charm-docs-link.css"

_DOCS_BACKLINK_CSS = """\
/* Injected by generate.py (inject_docs_backlinks): a docs-styled "Go to docs"
   button on each Woboq source page, matching the landing page primary action. */
#header {{ position: relative; }}
.charm-docs-link {{
  position: absolute;
  left: 50%; /* centered in the header, between search and the woboq logo */
  top: 50%;
  transform: translate(-50%, -50%);
  display: inline-flex;
  align-items: center;
  gap: 0.5em;
  padding: 0.6rem 1.2rem;
  border: 1px solid #2f9355;
  border-radius: 12px;
  background-color: #f8f9fa;
  background-image: {dissolve};
  background-repeat: repeat-x;
  background-size: 256px 120px;
  background-position: top center;
  image-rendering: pixelated;
  color: #17181c !important;
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto,
    "Helvetica Neue", Arial, "Noto Sans", sans-serif;
  font-size: 1.05rem;
  font-weight: 600;
  text-decoration: none;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.18);
  transition: box-shadow 0.15s ease, transform 0.15s ease;
}}
.charm-docs-link:hover {{
  box-shadow: 0 3px 10px rgba(0, 0, 0, 0.28);
  transform: translate(-50%, -50%) scale(1.02);
}}
.charm-docs-arrow {{ transition: transform 0.15s ease; }}
.charm-docs-link:hover .charm-docs-arrow {{ transform: translateX(3px); }}
/* Woboq's header is only ~5.2em tall and, at phone widths, already packed with
   the search box (top-left), breadcrumb (row 2) and logo (top-right). A centred
   button lands on top of the breadcrumb, so on small screens tuck it into the
   one free corner — bottom-right, just under the logo — and drop the centring
   transform (base + hover) that positioned it. */
@media (max-width: 800px) {{
  .charm-docs-link {{
    left: auto;
    right: 0.4rem;
    top: auto;
    bottom: 0.35rem;
    transform: none;
    font-size: 0.8rem;
    padding: 0.28rem 0.6rem;
  }}
  .charm-docs-link:hover {{
    transform: none;
  }}
}}
"""


def _pixel_dissolve_card_url() -> str:
    """Pull the ``--pixel-dissolve-card`` ``url(...)`` value out of global.css so
    the Woboq button reuses the exact same gradient as the landing card. Falls
    back to a flat accent tint if the var can't be found."""
    try:
        css = GLOBAL_CSS.read_text(encoding="utf-8")
    except OSError:
        return "none"
    m = re.search(r"--pixel-dissolve-card:\s*(url\(.*?\));", css, re.S)
    return m.group(1) if m else "none"


def write_docs_backlink_css() -> str:
    """Write the single shared button stylesheet into the source browser and
    return its root-relative href."""
    css = _DOCS_BACKLINK_CSS.format(dissolve=_pixel_dissolve_card_url())
    (SOURCE_BROWSER_OUT / DOCS_BACKLINK_CSS_NAME).write_text(css, encoding="utf-8")
    return f"{SOURCE_BROWSER_URL}/{DOCS_BACKLINK_CSS_NAME}"


def _build_source_to_doc_map() -> dict:
    """Map ``<source rel path> → <doc page URL>`` for every generated reference
    page, read from the assembled MDX. Each page carries its ``slug:`` (the URL)
    and a ``page-source-path`` (the ``include/...`` file it documents); the file
    matches the Woboq page's own path, so this is the authoritative join and it
    only ever contains pages that actually survived generation."""
    ref_root = SITE_DOCS / "reference"
    if not ref_root.exists():
        return {}
    slug_re = re.compile(r"^slug:\s*(\S+)", re.MULTILINE)
    src_re = re.compile(r'<code class="page-source-path">([^<]+)</code>')
    mapping = {}
    for mdx in ref_root.rglob("*.mdx"):
        text = mdx.read_text(encoding="utf-8")
        slug = slug_re.search(text)
        src = src_re.search(text)
        if slug and src:
            mapping[src.group(1).strip()] = "/" + slug.group(1).strip("/") + "/"
    return mapping


def inject_docs_backlinks():
    """Add a "Go to docs" button to each Woboq source page that has a doc page."""
    t0 = begin_step("Inject docs backlinks", str(SOURCE_BROWSER_OUT))
    project_root = SOURCE_BROWSER_OUT / SOURCE_BROWSER_PROJECT
    if not project_root.exists():
        end_step(t0, c("skipped — no source browser", GRAY))
        return

    mapping = _build_source_to_doc_map()
    if not mapping:
        end_step(t0, c("skipped — no doc pages", GRAY))
        return

    css_href = write_docs_backlink_css()
    link_tag = f'<link rel="stylesheet" href="{css_href}"/>'

    injected = 0
    for src_rel, doc_url in mapping.items():
        html_path = project_root / (src_rel + ".html")
        if not html_path.exists():
            continue
        html = html_path.read_text(encoding="utf-8")
        if "charm-docs-link" in html:  # idempotent
            continue
        button = (
            f'<a class="charm-docs-link" href="{doc_url}">'
            f'Go to docs <span class="charm-docs-arrow">→</span></a>'
        )
        # The header holds only the breadcrumb <h1>; close the button inside it.
        new_html = html.replace("</h1></div>", "</h1>" + button + "</div>", 1)
        new_html = new_html.replace("</head>", link_tag + "</head>", 1)
        if new_html != html:
            html_path.write_text(new_html, encoding="utf-8")
            injected += 1

    end_step(t0, f"{injected} page(s)")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _run(cmd, **kwargs):
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        fail_step(f"Command failed: {' '.join(str(x) for x in cmd)}\n{result.stderr.strip()}")


# ── Entry point ───────────────────────────────────────────────────────────────


def main():
    print_banner()

    t_total = time.monotonic()

    # Clean previous build artefacts
    t0 = begin_step("Clean previous build")
    for p in [Path("json_output"), Path("docs")]:
        if p.exists():
            shutil.rmtree(p)
    end_step(t0)

    clone_repo()
    if generate_compile_commands():
        build_clang_index()
        # Symbol links point into the source browser when it's available.
        if build_source_browser():
            os.environ["CHARMOS_SOURCE_BROWSER"] = f"{SOURCE_BROWSER_URL}/{SOURCE_BROWSER_PROJECT}"
    prepare_output_dirs()
    run_make_json()
    run_make_md()
    run_make_cmdline()
    delete_empty_markdown()
    copy_directory_indexes()
    assemble_site_content()
    # Reverse link (source → docs): needs both the browser and the assembled
    # docs, so it runs last. Self-skips if either is absent.
    inject_docs_backlinks()

    total_elapsed = time.monotonic() - t_total
    safe_print(
        f"\n{c('  ✓  build complete', GREEN, BOLD)}" f"  {c(f'{total_elapsed:.1f}s total', GRAY)}\n"
    )


if __name__ == "__main__":
    main()
