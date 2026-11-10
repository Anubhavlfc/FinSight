import { Link } from "react-router-dom";
import { DEFAULT_SAMPLE, sample } from "../api/sample";
import type { RunResult } from "../api/types";
import { ChartCard } from "../components/Charts";
import { Badge, EvidenceBadge, ExternalLink, ReviewBadge, SyntheticBadge } from "../components/common";
import { Icon, type IconName } from "../components/Icon";
import { Logo } from "../components/Logo";
import { MetricTile } from "../components/ResultView";
import { Footer } from "../components/Shell";
import { ARCHITECTURE_URL, REPO_URL } from "../config";
import { displayCompany, formatDate, periodRange } from "../lib/format";
import { useDocumentTitle, useLoad } from "../lib/hooks";

const SAMPLE_PATH = `/app/sample/${DEFAULT_SAMPLE}`;

const WORKFLOW: { icon: IconName; title: string; text: string }[] = [
  { icon: "download", title: "Collect", text: "Finds the company at the SEC, picks its last three completed fiscal years from the 10-K filing history, and downloads the reported figures and filings." },
  { icon: "shield", title: "Validate", text: "Checks that each figure is annual, in US dollars and for the right period. Restated values replace older ones, and the accounting has to add up. Problems are listed, not patched." },
  { icon: "calc", title: "Analyze", text: "Python calculates margins, cash flow, debt and year-over-year changes, then ranks the changes that are large enough to need an explanation." },
  { icon: "search", title: "Investigate", text: "For each flagged change, the investigator searches the company's 10-K for management's own explanation. A quote counts only if code finds it word for word in the filing." },
  { icon: "doc", title: "Report", text: "Writes a report with charts and citations, re-checks every number and quote, and then waits for an analyst to review and approve it." },
];

const CAPABILITIES: { title: string; text: string; where: string }[] = [
  { title: "Three-year comparisons", text: "Revenue, profit, margins, cash flow, cash and debt side by side for three fiscal years, with the change from each year to the next.", where: "Key metrics, charts and the financial table" },
  { title: "Traceable calculations", text: "Every metric records its formula and the XBRL facts it came from, down to the filing accession number. Calculated measures such as free cash flow are labeled as calculated.", where: "Sources and limitations, Calculations" },
  { title: "Investigation of major changes", text: "Changes beyond configurable thresholds are ranked and investigated one at a time, within a fixed budget of steps, so the work is bounded and repeatable.", where: "Biggest changes" },
  { title: "Sources behind explanations", text: "Each explanation shows the quoted sentence, the passage around it, the 10-K section it sits in and a link to the filing. Changes the filings do not explain are reported as unexplained.", where: "Evidence status on every finding" },
  { title: "Downloadable reports", text: "The report as HTML and Markdown, the dataset, metrics and audit trail as CSV, findings and review records as JSON, and each chart as PNG with its data.", where: "Downloads" },
];

function Preview() {
  const res = useLoad<RunResult>((signal) => sample.result(DEFAULT_SAMPLE, signal), "landing-preview");
  if (res.state !== "ready") {
    return (
      <div className="preview-frame is-empty">
        <p className="muted">{res.state === "loading" ? "Loading the sample analysis…" : "The sample preview could not be loaded."}</p>
        <Link className="btn" to={SAMPLE_PATH}>Open the sample analysis</Link>
      </div>
    );
  }
  const r = res.data;
  const chart = r.charts.find((c) => c.id === "chart:cash_flow") ?? r.charts[0];
  return (
    <div className="preview-frame">
      <div className="preview-bar">
        <span className="mono">finsight / sample / {DEFAULT_SAMPLE}</span>
        <SyntheticBadge />
      </div>
      <div className="preview-body">
        <div className="preview-head">
          <div>
            <p className="eyebrow">Sample analysis</p>
            <p className="preview-company">{displayCompany(r.overview.company.name)}</p>
            <p className="run-meta">
              <span className="mono">{r.overview.company.ticker}</span>
              <span>{periodRange(r.overview.periods.map((p) => p.label))}</span>
              <span>Latest 10-K filed {formatDate(r.overview.periods[r.overview.periods.length - 1]?.filed)}</span>
            </p>
          </div>
          <ReviewBadge review={r.status.review} />
        </div>
        <div className="metrics preview-metrics">
          {r.key_metrics.filter((m) => ["revenue", "operating_margin", "free_cash_flow", "interest_bearing_debt"].includes(m.name)).map((m) => (
            <MetricTile key={m.name} metric={m} />
          ))}
        </div>
        <div className="preview-split">
          {chart && <ChartCard chart={chart} compact />}
          <div className="panel preview-changes">
            <div className="panel-head"><h3>Biggest changes</h3><span className="muted">top {Math.min(4, r.changes.length)} of {r.changes.length}</span></div>
            <ol>
              {r.changes.slice(0, 4).map((c) => (
                <li key={c.id}>
                  <span className="change-rank num">{String(c.rank).padStart(2, "0")}</span>
                  <span className="preview-change-text">{c.description}</span>
                  <EvidenceBadge evidence={c.evidence} label={c.evidence_label} />
                </li>
              ))}
            </ol>
          </div>
        </div>
      </div>
      <div className="preview-foot">
        <p className="muted">This is the real results screen, showing the bundled synthetic sample.</p>
        <Link to={SAMPLE_PATH}>Open the full sample <Icon name="arrow" size={13} className="inline-icon" /></Link>
      </div>
    </div>
  );
}

export function Landing() {
  useDocumentTitle("");
  return (
    <div className="landing">
      <a className="skip-link" href="#content">Skip to content</a>
      <header className="landing-nav">
        <div className="wrap landing-nav-inner">
          <Logo />
          <nav aria-label="Main">
            <ul>
              <li><a href="#how">How it works</a></li>
              <li><a href="#preview">Preview</a></li>
              <li><a href={REPO_URL} target="_blank" rel="noreferrer noopener">GitHub<span className="sr-only"> (opens in a new tab)</span></a></li>
            </ul>
          </nav>
          <Link className="btn btn-primary" to="/app">Launch workspace</Link>
        </div>
      </header>

      <main id="content">
        <section className="hero">
          <div className="wrap">
            <p className="eyebrow">Agentic financial analysis on SEC filings</p>
            <h1>Three years of financial performance. <span>A clearer picture.</span></h1>
            <p className="hero-lede">
              FinSight AI turns a company's annual filings into validated metrics, charts and explanations you can trace
              back to the sentence in the 10-K. Every report stops for analyst review.
            </p>
            <div className="hero-actions">
              <Link className="btn btn-primary btn-lg" to="/app">Analyze a company <Icon name="arrow" /></Link>
              <Link className="btn btn-lg" to={SAMPLE_PATH}>Explore sample analysis</Link>
            </div>
            <ul className="hero-facts">
              <li><Icon name="calc" size={14} /> Calculations run in Python, not in a language model</li>
              <li><Icon name="quote" size={14} /> Quotes are verified word for word against the filing</li>
              <li><Icon name="shield" size={14} /> Reports wait for a person to approve them</li>
            </ul>
          </div>
          <div className="wrap" id="preview">
            <Preview />
          </div>
        </section>

        <section className="band" id="how" aria-labelledby="how-h">
          <div className="wrap">
            <p className="eyebrow">How it works</p>
            <h2 id="how-h">From a ticker to a reviewed report, in five steps</h2>
            <p className="band-lede">One orchestrator runs the steps in order, saves a checkpoint after each, and retries or stops with a reason when something goes wrong.</p>
            <ol className="workflow">
              {WORKFLOW.map((step, i) => (
                <li key={step.title}>
                  <p className="workflow-n num">{String(i + 1).padStart(2, "0")}</p>
                  <h3><Icon name={step.icon} size={16} className="inline-icon" /> {step.title}</h3>
                  <p>{step.text}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="band" aria-labelledby="cap-h">
          <div className="wrap">
            <p className="eyebrow">What you get</p>
            <h2 id="cap-h">Built to be checked, not just read</h2>
            <dl className="ledger">
              {CAPABILITIES.map((c) => (
                <div key={c.title}>
                  <dt>{c.title}</dt>
                  <dd>
                    <p>{c.text}</p>
                    <p className="ledger-where"><Badge icon="arrow">In the app</Badge> {c.where}</p>
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </section>

        <section className="band" id="trust" aria-labelledby="trust-h">
          <div className="wrap">
            <p className="eyebrow">Trust and limitations</p>
            <h2 id="trust-h">What the numbers are, and what they are not</h2>
            <div className="trust">
              <article>
                <h3>Data sources</h3>
                <p>Figures come from the SEC's XBRL company facts, and explanations from the company's own 10-K text on EDGAR. Each report lists the documents it read, when it fetched them and a hash of their contents.</p>
              </article>
              <article>
                <h3>Reporting dates</h3>
                <p>Fiscal years are defined by the period each 10-K reports on, not the calendar year. Every report shows the period dates, the filing dates and the cutoff after which filings were not considered.</p>
              </article>
              <article>
                <h3>Analyst review</h3>
                <p>An automated reviewer re-checks every number and quote, but it cannot approve a report. Reports are marked as awaiting review until a person approves them, and partial results are labeled as partial.</p>
              </article>
              <article>
                <h3>Supported companies</h3>
                <p>US-listed, nonfinancial operating companies that file Form 10-K in US dollars. Banks, insurers, broker-dealers, REITs and foreign filers on Form 20-F or 40-F are declined with an explanation.</p>
              </article>
            </div>
            <div className="limits">
              <h3>Known limits</h3>
              <ul className="bullets">
                <li>Explanations are the company's own account from its filings. They are quoted, not independently verified.</li>
                <li>Only consolidated totals are analysed. Segment data, peers and quarterly figures are not.</li>
                <li>Missing figures are reported as missing. Nothing is estimated or filled in.</li>
                <li>Without a language model configured, a rule-based investigator is used. It is labeled, and it leaves more changes unexplained.</li>
                <li>There is no overall financial-health score and no buy or sell view. The details are in the <ExternalLink href={ARCHITECTURE_URL}>architecture notes</ExternalLink>.</li>
              </ul>
            </div>
          </div>
        </section>

        <section className="cta">
          <div className="wrap cta-inner">
            <div>
              <h2>See it on a company you know</h2>
              <p className="muted">Start with the sample, or search for a ticker in the workspace.</p>
            </div>
            <div className="hero-actions">
              <Link className="btn btn-primary btn-lg" to="/app">Launch workspace</Link>
              <Link className="btn btn-lg" to={SAMPLE_PATH}>Explore sample analysis</Link>
            </div>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
