import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { DEFAULT_SAMPLE, sample } from "../api/sample";
import type { KeyMetric, RunResult } from "../api/types";
import { ChartCard } from "../components/Charts";
import { ELIGIBILITY } from "../components/CompanyPanel";
import { EvidenceBadge, ExternalLink, Skeleton, SyntheticBadge } from "../components/common";
import { changeTitle } from "../components/Findings";
import { Icon, type IconName } from "../components/Icon";
import { Logo } from "../components/Logo";
import { MetricTile } from "../components/ResultView";
import { Footer } from "../components/Shell";
import { ARCHITECTURE_URL, REPO_URL } from "../config";
import { displayCompany, periodRange } from "../lib/format";
import { useDocumentTitle, useLoad, type Loadable } from "../lib/hooks";

const SAMPLE_PATH = `/app/sample/${DEFAULT_SAMPLE}`;

const BENEFITS: { icon: IconName; title: string; text: string }[] = [
  { icon: "layers", title: "Compare performance", text: "Revenue, margins, profit, cash flow, cash and debt for three fiscal years, side by side, with the change from each year to the next." },
  { icon: "spark", title: "Understand major changes", text: "The largest year-over-year moves are picked out and looked into one at a time, so you start with what actually changed." },
  { icon: "quote", title: "Inspect supporting evidence", text: "Each explanation shows the sentence from the company's 10-K, where it sits in the filing, and whether the evidence supports it." },
];

const STEPS: { title: string; text: string }[] = [
  { title: "Choose a company", text: "Search by name or ticker. FinSight picks the last three completed fiscal years from the company's annual filings." },
  { title: "Explore the analysis", text: "Read the summary, check the key metrics and charts, and see which changes were large enough to look into." },
  { title: "Review the evidence", text: "Open any finding to read what management said, view the passage in the filing, and see what remains unexplained." },
];

function HeroPreview({ res }: { res: Loadable<RunResult> }) {
  if (res.state !== "ready") {
    return (
      <div className="preview-frame is-loading">
        {res.state === "loading" ? (
          <>
            <span className="sr-only" role="status">Loading the sample preview</span>
            <Skeleton height={20} width="60%" />
            <div className="preview-metrics"><Skeleton height={84} /><Skeleton height={84} /><Skeleton height={84} /></div>
            <Skeleton height={236} />
          </>
        ) : (
          <>
            <p className="muted">The sample preview could not be loaded.</p>
            <Link className="btn" to={SAMPLE_PATH}>Explore sample report</Link>
          </>
        )}
      </div>
    );
  }
  const r = res.data;
  const chart = r.charts.find((c) => c.id === "chart:revenue_profit") ?? r.charts[0];
  const metrics = ["revenue", "operating_margin", "free_cash_flow"]
    .map((n) => r.key_metrics.find((m) => m.name === n))
    .filter((m): m is KeyMetric => Boolean(m));
  const finding = r.changes[0];
  return (
    <div className="preview-frame">
      <div className="preview-head">
        <div>
          <p className="preview-company">{displayCompany(r.overview.company.name)}</p>
          <p className="muted num">{periodRange(r.overview.periods.map((p) => p.label))}</p>
        </div>
        <SyntheticBadge />
      </div>
      <div className="preview-metrics">
        {metrics.map((m) => <MetricTile key={m.name} metric={m} compact />)}
      </div>
      {chart && <ChartCard chart={chart} compact />}
      {finding && (
        <div className="preview-finding">
          <div>
            <p className="preview-finding-label">Biggest change</p>
            <p className="preview-finding-text">{changeTitle(finding)}</p>
            <p className="muted num">{finding.comparison} · {finding.magnitude}</p>
          </div>
          <EvidenceBadge evidence={finding.evidence} label={finding.evidence_label} />
        </div>
      )}
      <p className="preview-foot muted">
        The real report screen, showing a synthetic sample company. No real company's figures are shown here.
      </p>
    </div>
  );
}

/** One finding from the sample, laid out the way the report shows it. */
function FindingPreview({ res }: { res: Loadable<RunResult> }) {
  if (res.state !== "ready") return <div className="finding-preview is-loading"><Skeleton height={260} /></div>;
  const r = res.data;
  const change = r.changes.find((c) => c.finding && c.finding.quotes.length > 0) ?? r.changes[0];
  const f = change?.finding;
  if (!change || !f) return null;
  const quote = f.quotes[0];
  const passage = quote ? r.passages.find((p) => p.id === quote.passage_id) : undefined;
  return (
    <div className="finding-preview">
      <div className="finding-preview-head">
        <div>
          <p className="finding-preview-title">{changeTitle(change)}</p>
          <p className="muted num">{change.metric_label} · {change.comparison} · {change.magnitude}</p>
        </div>
        <SyntheticBadge />
      </div>
      <ol className="finding-preview-parts">
        <li>
          <p className="fpart-title"><Icon name="calc" size={15} /> What the numbers show</p>
          <p>{f.calculated_observation}</p>
        </li>
        <li>
          <p className="fpart-title"><Icon name="quote" size={15} /> What management says</p>
          {quote ? <blockquote>“{quote.text}”</blockquote> : <p className="muted">No explanation was found in the filing text.</p>}
          {passage && <p className="muted small">{passage.form} for {passage.fiscal_period ?? "the period"} · {passage.section}</p>}
        </li>
        <li>
          <p className="fpart-title"><Icon name="shield" size={15} /> Does the filing evidence support it?</p>
          <p><EvidenceBadge evidence={change.evidence} label={change.evidence_label} /></p>
          <p className="secondary">
            A matching quote confirms what the company wrote. It does not prove the explanation is correct.
          </p>
        </li>
      </ol>
    </div>
  );
}

export function Landing() {
  useDocumentTitle("");
  const res = useLoad<RunResult>((signal) => sample.result(DEFAULT_SAMPLE, signal), "landing-preview");
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  return (
    <div className="landing">
      <a className="skip-link" href="#content">Skip to content</a>
      <header className="topbar landing-nav">
        <div className="topbar-inner">
          <Logo />
          <nav id="landing-menu" aria-label="Main" className={menuOpen ? "is-open" : ""}>
            <ul onClick={() => setMenuOpen(false)}>
              <li><a href="#how">How it works</a></li>
              <li><a href="#report">Report preview</a></li>
              <li><a href="#scope">What it covers</a></li>
              <li><a href={REPO_URL} target="_blank" rel="noreferrer noopener">GitHub<span className="sr-only"> (opens in a new tab)</span></a></li>
            </ul>
          </nav>
          <div className="topbar-side">
            <Link className="btn btn-sm btn-primary" to="/app">Analyze a company</Link>
            <button type="button" className="menu-btn" aria-expanded={menuOpen} aria-controls="landing-menu" onClick={() => setMenuOpen((v) => !v)}>
              <Icon name={menuOpen ? "x" : "menu"} size={18} />
              <span className="sr-only">{menuOpen ? "Close menu" : "Menu"}</span>
            </button>
          </div>
        </div>
      </header>

      <main id="content">
        <section className="hero">
          <div className="wrap hero-grid">
            <div className="hero-copy">
              <p className="eyebrow">Financial analysis from SEC filings</p>
              <h1>Understand what changed. <span>See why it matters.</span></h1>
              <p className="hero-lede">
                Explore three years of company financials, uncover the biggest changes, and trace explanations back to
                the filings.
              </p>
              <div className="hero-actions">
                <Link className="btn btn-primary btn-lg" to="/app">Analyze a company <Icon name="arrow" /></Link>
                <Link className="btn btn-lg" to={SAMPLE_PATH}>Explore sample report</Link>
              </div>
              <p className="hero-note muted">No sign-up. The sample report works without the live service.</p>
            </div>
            <div className="hero-preview" id="preview" aria-label="Product preview">
              <HeroPreview res={res} />
            </div>
          </div>
        </section>

        <section className="band" aria-labelledby="benefits-h">
          <div className="wrap">
            <h2 id="benefits-h" className="sr-only">What you get</h2>
            <ul className="benefits">
              {BENEFITS.map((b) => (
                <li key={b.title}>
                  <span className="benefit-icon"><Icon name={b.icon} size={18} /></span>
                  <h3>{b.title}</h3>
                  <p>{b.text}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section className="band" id="how" aria-labelledby="how-h">
          <div className="wrap">
            <p className="eyebrow">How it works</p>
            <h2 id="how-h">From a company name to the evidence, in three steps</h2>
            <ol className="steps3">
              {STEPS.map((s, i) => (
                <li key={s.title}>
                  <span className="step-num num">{i + 1}</span>
                  <h3>{s.title}</h3>
                  <p>{s.text}</p>
                </li>
              ))}
            </ol>
            <details className="more">
              <summary><Icon name="chevron" size={14} /> What happens behind the scenes</summary>
              <div className="more-body">
                <p>
                  Each analysis runs through eight stages: planning, collecting, validating, calculating, investigating,
                  writing, reviewing, and waiting for analyst review. Figures are calculated in Python, not by a language
                  model. A quotation counts only when code finds it word for word in the filing. The details are in
                  the <ExternalLink href={ARCHITECTURE_URL}>architecture notes</ExternalLink>.
                </p>
              </div>
            </details>
          </div>
        </section>

        <section className="band" id="report" aria-labelledby="report-h">
          <div className="wrap report-band">
            <div className="report-band-copy">
              <p className="eyebrow">Report preview</p>
              <h2 id="report-h">Every finding keeps three things apart</h2>
              <p className="band-lede">
                What the numbers show, what management says about it, and whether the filing evidence supports that
                account. You can open the passage behind any quotation.
              </p>
              <Link className="btn" to={`${SAMPLE_PATH}#changes`}>See all findings in the sample <Icon name="arrow" size={14} /></Link>
            </div>
            <FindingPreview res={res} />
          </div>
        </section>

        <section className="band" id="scope" aria-labelledby="scope-h">
          <div className="wrap">
            <p className="eyebrow">What it covers</p>
            <h2 id="scope-h">Clear about its sources and its limits</h2>
            <div className="scope">
              <article>
                <h3><Icon name="building" size={16} /> Supported companies</h3>
                <p>{ELIGIBILITY}</p>
              </article>
              <article>
                <h3><Icon name="doc" size={16} /> Sources</h3>
                <p>Figures come from the SEC's reported company data and explanations from the company's own 10-K text. Every report lists the filings it read.</p>
              </article>
              <article>
                <h3><Icon name="shield" size={16} /> Analyst review</h3>
                <p>Automated checks re-verify every number and quotation, but they cannot approve a report. Each report is marked as awaiting review until a person approves it.</p>
              </article>
            </div>
            <details className="more">
              <summary><Icon name="chevron" size={14} /> Known limits</summary>
              <div className="more-body">
                <ul className="bullets">
                  <li>Explanations are the company's own account from its filings. They are quoted, not independently verified.</li>
                  <li>Only consolidated totals are analysed. Segment data, peers and quarterly figures are not.</li>
                  <li>Missing figures are reported as missing. Nothing is estimated or filled in.</li>
                  <li>Without an AI model configured, a rule-based investigator is used. It is labeled, and it leaves more changes unexplained.</li>
                  <li>There is no overall financial-health score and no buy or sell view. FinSight is an analytical tool, not investment advice.</li>
                </ul>
              </div>
            </details>
          </div>
        </section>

        <section className="cta">
          <div className="wrap cta-inner">
            <div>
              <h2>Start with a company you know</h2>
              <p className="secondary">Or open the sample report to see a finished analysis first.</p>
            </div>
            <div className="hero-actions">
              <Link className="btn btn-primary btn-lg" to="/app">Analyze a company <Icon name="arrow" /></Link>
              <Link className="btn btn-lg" to={SAMPLE_PATH}>Explore sample report</Link>
            </div>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
