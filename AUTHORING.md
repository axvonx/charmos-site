# Authoring charmOS docs

This site is **built from the charmOS source**. Most reference content is
extracted from C headers; you author it by writing structured doc-comments next
to the code. This file documents those conventions.

There are two authoring surfaces:

1. **In-source doc-comments** (the *Idea Framework*) — live in the charmOS repo,
   parsed by `make_json.py`, rendered by `make_md.py`.
2. **Resident pages** — hand-written `.mdx` under `content/` in this repo
   (splash, section indexes, guides, design notes, blog posts).

---

## 1. In-source doc-comments (the Idea Framework)

### Page title

```c
/* @title: Interrupt Request Levels */
```

Sets the rendered page title for the file. One per file.

### Idea signature

An **Idea** is a documentation section attached to code. It has two parts: a
one-line *signature* and a following comment *body*.

```c
/* @idea:big Interrupt Request Levels */
/* # Big Idea
 * Interrupt Request Levels (STABLE)
 *
 * ## Credits
 * gummi
 *
 * ## Audience:
 *   Kernel developers
 *
 * ## Overview
 *   IRQLs provide a centralized preemption and interrupt control mechanism.
 *   `irql_raise()` disables preemption; `irql_lower()` restores it.
 */
```

- **Signature:** `/* @idea:<size> <Name> */` where `<size>` is `small`, `big`,
  or `huge`. The size controls how prominently the Idea renders.
- **Body:** the immediately-following block comment. Its first heading declares
  the size again (`# Big Idea`), and the line under it is the **name**, with an
  optional **status** in parentheses: `Name (STATUS)`.

> The build **warns** if the signature and body disagree — e.g. `@idea:big`
> paired with a `# Small Idea` heading (size mismatch), or the signature name
> not matching the body name. Cosmetic differences (hyphens, spacing,
> punctuation) are ignored, so `Real-time` and `Realtime` are treated as equal.

### Metadata sections

| Section | Meaning |
| --- | --- |
| `## Credits` | Author; the following line is the name. |
| `## Audience` | Who the Idea is for (inline `## Audience: …` or on the next line). |
| `(STATUS)` | On the name line, e.g. `(STABLE)`, `(EXPERIMENTAL)`. |

### Standard content sections

Author freely, but these section names are recognized (and the build warns on
likely **typos** of them — e.g. `## Overveiw` → "did you mean `## Overview`?").
Genuinely custom section names are left alone.

```
Overview   Background   Summary      Rationale    Motivation
Context    Constraints  Strategy     Internals    Design
API        Errors       Usage        Examples     Caveats
Notes      Changelog    References   Bugs         Commits
```

A trailing colon is optional (`## Overview` and `## Overview:` are equivalent).

### Cross-links, bugs, and commits

- **Idea cross-link (footnote idiom):** reference another Idea by name using a
  footnote definition. The quoted string is resolved to the target Idea:

  ```
  ## Notes
  [^1]: "Thread Lifecycle"
  ```

- **Bugs:** list known issues under a `## Bugs` section; entries are extracted
  into the page's bug list.

- **Commits:** mention `commit <hash>` (7–40 hex chars) anywhere in a body and
  it is captured as a source-history reference.

### Code inside Ideas

Fenced code blocks work inside comment bodies — write the fence flush with the
comment margin:

```c
/* ## Overview
 * A common IRQL usage pattern:
 *
 * ```c
 * enum irql old = irql_raise(IRQL_DISPATCH_LEVEL);
 * // ... critical section ...
 * irql_lower(old);
 * ```
 */
```

The comment margin (`* `) is stripped before the fence is detected, so the code
renders as a real block. **Don't** hang-indent prose continuation lines by four
or more spaces after a blank line unless you mean an indented block — a wrapped
line that merely continues a paragraph is fine and its indentation is collapsed.

---

## 2. Resident pages (`content/`)

`content/` mirrors the final site layout 1:1 and is overlaid onto the generated
tree by `generate.py`'s `assemble_site_content()`. Drop files in place:

| Path | Becomes | Purpose |
| --- | --- | --- |
| `content/index.mdx` | `/` | Splash page. |
| `content/reference/index.mdx` | `/reference/` | Reference landing. |
| `content/guides/*.mdx` | `/guides/…` | Hand-written guides. |
| `content/design/index.mdx` | `/design/` | Architecture notes. |
| `content/blog/*.mdx` | `/blog/…` | Blog posts (see below). |

Generated pages (API reference, `guides/cmdline.mdx`) are overlaid on top, so
resident pages provide the fixed scaffolding and landing pages.

### Blog posts

Blog posts are `.mdx` files under `content/blog/`, rendered by
[`starlight-blog`](https://starlight-blog-docs.vercel.app/). Required
frontmatter:

```yaml
---
title: Clickable C, all the way down
date: 2026-07-02          # a valid YAML date
excerpt: One-line summary shown in the post list.
authors:
  - name: gummi
    title: charmOS
    url: https://github.com/axvonx
---
```

The blog index lives at `/blog`; posts at `/blog/<filename>`.

---

## Building & checking your work

```bash
python3 generate.py        # regenerate docs from source
./build.sh                 # regenerate + build the site
./build.sh --serve         # …and serve a local preview
```

Watch the `generate.py` / `make_json` output for `warning:` lines — orphaned
Idea bodies (a `# Big Idea` block with no `@idea:` signature), size/name
mismatches, and section-name typos are all reported there without failing the
build.
