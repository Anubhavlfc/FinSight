# Deploying FinSight AI

**Status: not deployed.** This document describes how to launch the app. Nothing here has
been run against a hosting account, so treat the first deployment as the test of these
steps. What has been verified locally: the container image builds, starts as an
unprivileged user, passes its health check, serves the app and the API, runs an analysis,
and keeps its runs across a restart when `/data` is a volume.

## Two different things

* **Where the code lives:** GitHub. Pushing to the repository publishes the source, the
  README and the screenshots. It does not run anything.
* **Where the app runs:** a host that can run the Python backend. The backend downloads SEC
  data, runs analyses in the background and stores runs in SQLite, so it needs a
  long-running process and a disk.

GitHub Pages serves static files only. It can host the frontend but not the backend, so on
its own it gives you the landing page and the bundled sample analyses, and an honest
"Service offline" for new runs. That is an optional extra (see the end of this document),
not the launch path.

## Recommended: one Docker web service with a disk (Render)

One container serves both the API and the built frontend from the same origin. That means
no CORS, no base-path handling, and deep links such as `/app/runs/<id>` work on refresh
because FastAPI returns the app shell for them.

### Cost

| Item | Price |
|---|---|
| Render web service, Starter instance (512 MB RAM, 0.5 CPU) | $7 per month |
| Render persistent disk, 1 GB | $0.25 per month |
| SEC EDGAR data | free |
| Claude API (only if you set `ANTHROPIC_API_KEY` and allow it for visitors) | billed per use by Anthropic |
| **Total without Claude** | **about $7.25 per month** |

The Render prices are the published list prices as of November 2026 as reported by pricing
summaries; render.com could not be reached from the environment this was written in, so
confirm them on <https://render.com/pricing> before you subscribe. Render's free instance
type would cost nothing, but it cannot attach a disk and it sleeps when idle, so the report
archive would be lost on every restart. It is fine for a quick look, not for a launch.

Claude cost is bounded per run by `max_model_calls` and `max_total_tokens` in
`config/settings.toml` (60 calls and 1.5 million tokens). The blueprint sets
`FINSIGHT_PUBLIC_INVESTIGATOR=rule_based`, so public visitors cannot spend your key unless
you change it. If you switch it to `auto`, set a spending limit in the Anthropic console
first: the app has no sign-in, and each visitor address may start
`FINSIGHT_RUNS_PER_HOUR` analyses per hour.

### Steps

1. Merge this work to `main` on GitHub.
2. Create an account at <https://render.com> and connect your GitHub account.
3. In the Render dashboard choose **New → Blueprint** and pick the `FinSight` repository.
   Render reads [`render.yaml`](../render.yaml): one Docker web service named `finsight`
   on the Starter plan with a 1 GB disk mounted at `/data`.
4. When asked for the unset variables, enter:
   * `FINSIGHT_SEC_USER_AGENT`: an app or personal name and a contact address, for example
     `FinSight AI yourname@example.com`. The SEC asks automated clients for this. It is
     sent to sec.gov with each request and nowhere else. Without it only the synthetic
     sample company can be analysed.
   * `ANTHROPIC_API_KEY`: leave empty unless you want the Claude investigator.
5. Apply. The first build takes a few minutes. Render then shows the service URL
   (`https://<name>.onrender.com`).
6. Copy the generated `FINSIGHT_REVIEWER_TOKEN` from the service's Environment page. It is
   what the report review form asks for before it records an approval or a change request.
7. Verify the deployment before telling anyone about it:
   * `https://<url>/api/health` returns `"status":"ok"` and `"live_data":{"available":true`.
   * The landing page loads, and "Explore sample analysis" opens the sample.
   * In the workspace, run `DEMO`, then a real ticker such as `MSFT`. Both should reach
     "Awaiting analyst review".
   * Refresh the run page: the same report comes back.
   * Open Reports, open the report, download the Markdown report.
   * Trigger a manual redeploy in Render and confirm the reports are still listed (this
     proves the disk is attached).
8. Optional: add a custom domain in Render's settings. Nothing in the app needs changing.

### Settings that matter in production

| Variable | Value | Why |
|---|---|---|
| `FINSIGHT_HOME` | `/data` (set in the image) | Database, SEC cache and reports go on the disk |
| `FINSIGHT_SEC_USER_AGENT` | your identification | Required for live data |
| `FINSIGHT_PUBLIC_INVESTIGATOR` | `rule_based` | Visitors cannot spend an API key |
| `FINSIGHT_REVIEWER_TOKEN` | generated | Only you can approve reports |
| `FINSIGHT_TRUSTED_PROXY_HOPS` | `1` | Rate limits count the visitor, not Render's proxy. If limits seem to apply to everyone at once, the proxy chain is longer than this value; if set too high, visitors could spoof their address. With `0` all visitors share one allowance, which is the safe failure. |
| `FINSIGHT_RUNS_PER_HOUR` | `6` (default) | Per-address cap on new analyses |
| `FINSIGHT_CORS_ORIGINS` | unset | Same-origin deployment needs no CORS |

Keep it to **one instance and one worker**. Analyses run inside the web process, and the
duplicate guard and rate limits are in memory. Scaling out would need a job queue and a
shared store first.

### Things to know before launching publicly

* There are no accounts. Every visitor sees the same report archive and can open any
  report. The form says so next to the analysis question.
* Analyses of real companies call sec.gov from your server under your identification.
  The client paces itself (5 requests per second by default, under the SEC's limit of 10)
  and caches responses on the disk.
* Reports are analytical aids, not investment advice, and say so. Each one stays "awaiting
  analyst review" until you approve it.
* A redeploy stops running analyses. They show as "interrupted" afterwards and can be
  resumed from the page.

### Any other Docker host

The image is not specific to Render. Anything that runs a container with a volume works:

```bash
docker build -t finsight .
docker run -d -p 8000:8000 -v finsight-data:/data \
  -e FINSIGHT_SEC_USER_AGENT="Your App Name contact@your-domain.example" \
  -e FINSIGHT_PUBLIC_INVESTIGATOR=rule_based \
  -e FINSIGHT_REVIEWER_TOKEN="$(openssl rand -hex 16)" \
  finsight
```

The server listens on `$PORT` (default 8000). Put it behind HTTPS and set
`FINSIGHT_TRUSTED_PROXY_HOPS` to the number of proxies in front.

## Optional: static showcase on GitHub Pages

Useful as an always-on preview next to the repository. It shows the landing page and the
bundled sample analyses (including their downloads). New analyses need a backend.

1. Repository **Settings → Pages → Build and deployment → Source: GitHub Actions**.
2. **Actions → pages → Run workflow** ([`.github/workflows/pages.yml`](../.github/workflows/pages.yml)).
   It builds the frontend with `VITE_BASE=/<repository name>/` so asset and route paths
   carry the project prefix, and publishes `web/dist`. The build also writes `404.html`
   as a copy of the app shell, which is how a refresh on a deep link such as
   `/FinSight/app/sample/demo` still loads on Pages.
3. To connect the Pages site to a hosted backend, add a repository variable
   `FINSIGHT_API_URL` (for example `https://<name>.onrender.com`), set
   `FINSIGHT_CORS_ORIGINS=https://<user>.github.io` on the backend, and run the workflow
   again. The API key and all other secrets stay on the backend; the frontend build
   contains only the API's public address.

GitHub Pages is free for public repositories. Locally, the Pages build was checked under a
`/FinSight/` prefix with a server that imitates Pages' 404 behaviour: assets, routing,
refresh on a deep link, sample downloads and the offline message all worked. The workflow
itself has not been run.
