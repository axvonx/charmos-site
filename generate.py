#!/usr/bin/env python3
"""
charmos docs build pipeline
────────────────────────────
Clones the source repo, parses every header/source file into JSON in
parallel, then compiles the JSON into MDX documentation.
"""

import subprocess
import shutil
import threading
import time
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

REPO_URL   = "https://github.com/axvonx/charmos.git"
CLONE_DIR  = Path("./charmos")
JSON_OUT   = Path("./json_output")
MD_OUT     = Path("./docs")
LIMINE_URL = "https://github.com/limine-bootloader/limine"
LIMINE_DIR = Path("./limine")

SOURCE_DIRS = [
    "include",
    # "kernel",
]

MAX_WORKERS = min(16, (os.cpu_count() or 4) * 2)

# ── ANSI palette ──────────────────────────────────────────────────────────────

ESC = "\033["

def _c(*codes):
    return f"{ESC}{';'.join(str(c) for c in codes)}m"

RESET   = _c(0)
BOLD    = _c(1)
DIM     = _c(2)

# foreground colours
WHITE   = _c(97)
GRAY    = _c(90)
CYAN    = _c(96)
GREEN   = _c(92)
YELLOW  = _c(93)
RED     = _c(91)
MAGENTA = _c(95)
BLUE    = _c(94)

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

BAR_WIDTH   = 28        # characters wide for the progress fill
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
    FILL  = "█"
    EMPTY = "░"
    SPIN  = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, total: int, label: str):
        self.total    = max(total, 1)
        self.label    = label
        self._done    = 0
        self._lock    = threading.Lock()
        self._spin_i  = 0
        self._active  = True
        self._last    = ""

        self._thread = threading.Thread(target=self._tick, daemon=True)
        self._thread.start()

    def advance(self, n: int = 1):
        with self._lock:
            self._done = min(self._done + n, self.total)

    def finish(self):
        with self._lock:
            self._done    = self.total
            self._active  = False
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
            done  = self._done
            total = self.total

        pct   = done / total
        filled = int(BAR_WIDTH * pct)
        empty  = BAR_WIDTH - filled

        bar = (
            c(self.FILL * filled,  GREEN, BOLD) +
            c(self.EMPTY * empty,  GRAY)
        )

        spinner = c(self.SPIN[self._spin_i], CYAN, BOLD) if not final else c("✓", GREEN, BOLD)
        pct_str = c(f"{int(pct*100):3d}%", WHITE, BOLD)
        count   = c(f"{done}/{total}", GRAY)
        label   = c(self.label, CYAN)

        line = f"\r  {spinner} {label}  [{bar}] {pct_str}  {count}"

        # pad to clear any leftover characters
        tw = term_width()
        if len(line) < tw:
            line += " " * (tw - len(line) - 1)

        with _print_lock:
            sys.stdout.write(line)
            sys.stdout.flush()


# ── Step decorator ────────────────────────────────────────────────────────────

_STEP_NUM  = 0
_STEP_LOCK = threading.Lock()

def begin_step(name: str, detail: str = "") -> float:
    global _STEP_NUM
    with _STEP_LOCK:
        _STEP_NUM += 1
        n = _STEP_NUM
    num_str  = c(f"[{n:02d}]", BLUE, BOLD)
    name_str = c(name, WHITE, BOLD)
    det_str  = (c(f"  {detail}", GRAY)) if detail else ""
    safe_print(f"\n{num_str} {name_str}{det_str}")
    return time.monotonic()

def end_step(t0: float, note: str = ""):
    elapsed = time.monotonic() - t0
    tick    = c("✓", GREEN, BOLD)
    time_s  = c(f"{elapsed:.1f}s", GRAY)
    note_s  = c(f"  {note}", DIM) if note else ""
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
    for l in lines:
        print(l)


# ── Pipeline steps ────────────────────────────────────────────────────────────

def clone_repo():
    t0 = begin_step("Clone repository", REPO_URL)

    if CLONE_DIR.exists():
        safe_print(c("  ↩  already cloned — skipping", GRAY))
        end_step(t0, "cached")
        return

    _run(["git", "clone", "--depth=1", REPO_URL, str(CLONE_DIR)])
    _run(["git", "submodule", "init"],   cwd=CLONE_DIR)
    _run(["git", "submodule", "update"], cwd=CLONE_DIR)

    tests_dir = CLONE_DIR / "kernel/uACPI/tests"
    if tests_dir.exists():
        shutil.rmtree(tests_dir)

    if not LIMINE_DIR.exists():
        _run([
            "git", "clone",
            "--branch=v9.x-binary", "--depth=1",
            LIMINE_URL, str(LIMINE_DIR),
        ])

    end_step(t0)


def prepare_output_dirs():
    t0 = begin_step("Prepare output directories")
    if JSON_OUT.exists():
        shutil.rmtree(JSON_OUT)
    JSON_OUT.mkdir(parents=True)
    MD_OUT.mkdir(parents=True, exist_ok=True)
    end_step(t0)


def _json_path_for(file_path: Path) -> Path:
    parents  = file_path.parts[-3:-1]
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

    t0  = begin_step("Parse source files → JSON", f"{len(files)} files  •  {MAX_WORKERS} workers")
    bar = ProgressBar(len(files), "parsing")
    errors: list[str] = []
    err_lock = threading.Lock()

    def parse_one(f: Path):
        out = _json_path_for(f)
        try:
            result = subprocess.run(
                ["python3", "make_json.py", str(f), str(out)],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                with err_lock:
                    errors.append(f"{f.name}: {result.stderr.strip()[:120]}")
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

    if errors:
        safe_print(c(f"  ⚠  {len(errors)} file(s) had errors:", YELLOW))
        for e in errors[:8]:
            safe_print(c(f"     • {e}", GRAY))
        if len(errors) > 8:
            safe_print(c(f"     … and {len(errors)-8} more", GRAY))

    end_step(t0, f"{len(files) - len(errors)}/{len(files)} succeeded")


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
        capture_output=False, text=True,
    )

    done_event.set()
    t.join()
    _clear_line()

    if result.returncode != 0:
        fail_step(f"make_md.py failed:\n{result.stderr.strip()}")

    end_step(t0)


def delete_empty_markdown():
    t0    = begin_step("Remove empty markdown files")
    paths = list(MD_OUT.glob("**/*.md"))
    count = 0
    for path in paths:
        if not path.read_text(encoding="utf-8").strip():
            path.unlink()
            count += 1
    end_step(t0, f"removed {count} empty file(s)")


def copy_directory_indexes():
    src_root  = Path("charmos/include")
    docs_root = Path("docs")
    indexes   = list(src_root.rglob("index.mdx"))
    if not indexes:
        return

    t0  = begin_step("Copy directory index files", f"{len(indexes)} found")
    bar = ProgressBar(len(indexes), "copying")

    for index_file in indexes:
        dest = docs_root / index_file.relative_to(src_root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(index_file, dest)
        bar.advance()

    bar.finish()
    end_step(t0)


def rename_directories_from_namefiles():
    src_root  = Path("charmos/include")
    docs_root = Path("docs")

    t0      = begin_step("Rename directories from name-files")
    renamed = 0

    for src_dir in src_root.rglob("*"):
        if not src_dir.is_dir():
            continue
        name_file = src_dir / "dir_doc_name"
        if not name_file.is_file():
            continue
        new_name = name_file.read_text(encoding="utf-8").strip()
        if not new_name:
            continue

        docs_equiv   = docs_root / src_dir.relative_to(src_root)
        new_docs_path = docs_equiv.parent / new_name

        if not docs_equiv.exists() or docs_equiv.name == new_name or new_docs_path.exists():
            continue

        docs_equiv.rename(new_docs_path)
        renamed += 1

    end_step(t0, f"{renamed} director{'ies' if renamed != 1 else 'y'} renamed")


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
    prepare_output_dirs()
    run_make_json()
    run_make_md()
    rename_directories_from_namefiles()
    delete_empty_markdown()
    copy_directory_indexes()

    total_elapsed = time.monotonic() - t_total
    safe_print(
        f"\n{c('  ✓  build complete', GREEN, BOLD)}"
        f"  {c(f'{total_elapsed:.1f}s total', GRAY)}\n"
    )


if __name__ == "__main__":
    main()
