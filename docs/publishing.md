# Publish the Mintlify documentation site

The public site is prepared for Mintlify Starter. It is a documentation frontend, not the EveryBench application or execution API. No paid Mintlify agent/assistant feature is required for Markdown delivery, the generated LLM indexes or the hosted search MCP.

## Source of truth

- `docs-site/docs.json` defines navigation and theme. It uses Mintlify's built-in theme primitives and neutral color/Geist type tokens, not a parallel custom stylesheet.
- `docs-site/index.mdx` and `connect-your-agent.mdx` are the handwritten entry points.
- `python scripts/build_docs.py` renders an explicit allowlist of canonical guides, portable skill references and the examples index into `docs-site/guides/`.
- Edit the original Markdown/skill files, then regenerate. Do not independently edit the generated pages.
- Internal published links are rewritten to site routes; code, repository-contributor and historical-record links point to GitHub. Executable fenced examples remain unchanged. Internal plans, research, acceptance records and local notes are not published as site pages.

- `uv run python scripts/build_references.py` generates the first-class Python evaluation/tracing API, public types and model schemas, every CLI command/group's help, and HTTP/OpenAPI references into `docs-site/reference/`. It uses the frozen base package environment but never constructs clients, runs business callbacks, enters application lifespan, opens a database or invokes providers. Help formatting is fixed so terminal and CI settings do not change the snapshot. Keep these outputs committed with interface changes.
- Reference facts are generated; workflows and command-specific output/exit semantics remain curated in `docs/cli.md` and the SDK/HTTP guides. In particular, `compare --json` returning exit 0 does not certify compatibility or no regression. CI watches source-code and lockfile changes as well as documentation, and rejects stale interface references.

## Preview and validate

Use Node.js 22 and Python 3.12+:

```bash
uv sync --frozen
uv run python scripts/build_references.py
uv run python scripts/build_docs.py
npm --prefix docs-site ci --ignore-scripts
npm --prefix docs-site run validate
npm --prefix docs-site run check:links
npm --prefix docs-site run dev
```

The local preview defaults to localhost:3000 and does not start EveryBench, a database or an evaluation. `mint validate` downloads its own documentation rendering client for the strict build; it makes no agent/judge calls. The pinned CLI and its npm lock are separate from the web application's dependencies.

CI checks generated-page drift, the strict Mintlify build and internal anchors. Regenerate and commit derived pages with canonical-source changes. No deployment credentials are needed for validation.

## Connect a permanent Starter site

1. Open [Mintlify onboarding](https://mintlify.com/start), create or select a project, and use the free Starter plan.
2. Connect only the `trycedar0x/benchmark-tracing` repository through Mintlify's GitHub App. Repository-owner authorization may be required.
3. Set the publishing branch to `main` and documentation directory to `docs-site`. The `docs.json` and generated pages must already be committed on that branch.
4. Start with the assigned `.mintlify.site` URL; adding a custom domain is optional and requires separate DNS authorization.
5. GitHub App synchronization publishes documentation when the configured source branch changes. The validation workflow does not provision an account or deploy by itself.

A temporary anonymous trial can also be provisioned with the pinned CLI's `mint deploy`. It expires unless claimed. The command provisions a separate hosted Git repository: copy only `docs.json` and the public MDX pages into it, commit and push to trigger a build. Never copy `.git`, `.env`, credentials or `node_modules`. Supply its short-lived write token only to the relevant Git process, never in a remote URL, repository file or report. Account claim requires the user's chosen email and verification; do not infer either from Git configuration.

## Verify hosted agent access

After the hosted build has completed, check the actual deployment URL, not an assumed domain:

- Home, coding-agent onboarding, evaluation, TypeScript tracing and finance pages render.
- `/llms.txt` lists the intended pages; `/llms-full.txt` returns the combined documentation.
- A guide's `.md` URL returns Markdown rather than an HTML error page.
- `/mcp` completes an MCP initialization and tool listing using a compatible client. This is documentation search/retrieval only, not benchmark execution.
- Portable skills still install from the source repository without paid hosted skill-generation features.

A successful CLI provision or Git push alone is not evidence of a successful site build, a populated search index or an active permanent Starter account. Keep account ownership and custom-domain setup separate from local source validation.
