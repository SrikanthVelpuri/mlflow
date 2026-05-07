# Architecture Docs

A self-contained Jekyll site explaining how MLflow’s **traditional ML platform** and **GenAI/AI platform** are built. Independent of the Sphinx site under [`/docs`](../docs); this one is published to GitHub Pages.

## Layout

```
architecture-docs/
├── _config.yml              # Jekyll config (theme: cayman, baseurl: /mlflow)
├── index.md                 # Landing page
├── ml-platform.md           # Traditional ML platform deep dive
├── ai-platform.md           # GenAI / AI platform deep dive
└── concepts/                # Per-pillar deep dives
    ├── tracking.md
    ├── models-and-flavors.md
    ├── registry.md
    ├── projects.md
    ├── serving.md
    ├── data-and-artifacts.md
    ├── tracing.md
    ├── auto-tracing.md
    ├── chatmodel-agents.md
    ├── gateway.md
    └── genai-evaluation.md
```

## Local preview

```bash
cd architecture-docs
bundle install
bundle exec jekyll serve
# open http://127.0.0.1:4000/mlflow/
```

## How it gets published

A GitHub Actions workflow ([.github/workflows/architecture-docs.yml](../.github/workflows/architecture-docs.yml)) builds this folder with Jekyll and deploys the result to GitHub Pages on every push to `master` that touches `architecture-docs/`.

To enable publishing once on this fork:

1. Push these files to `origin/master`.
2. In GitHub: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. Trigger the workflow once (push, or **Actions → Publish Architecture Docs → Run workflow**).
4. The site goes live at `https://srikanthvelpuri.github.io/mlflow/`.

The workflow is path-filtered, so changes elsewhere in the repo don’t rebuild the docs.

## Editing

Pages are plain Markdown with optional YAML front matter (`title:` only). Cross-page links use relative `.html` paths (`ml-platform.html`, `concepts/tracking.html`) so they resolve correctly under the `/mlflow/` base path.
