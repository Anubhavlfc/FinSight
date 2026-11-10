import { useService } from "../lib/service";
import { ARCHITECTURE_URL, DOCS_URL, REPO_URL, SEC_EDGAR_URL } from "../config";
import { ExternalLink } from "./common";
import { Icon } from "./Icon";
import { LogoMark } from "./Logo";

/** Compact indicator of the analysis service and where its data comes from. */
export function ServicePill() {
  const { service, retry, checkedAt } = useService();
  const time = checkedAt ? checkedAt.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false }) : "";
  if (service.kind === "checking") {
    return (
      <span className="service-pill" role="status">
        <span className="spinner" aria-hidden="true" /> Checking service
      </span>
    );
  }
  if (service.kind === "offline") {
    return (
      <span className="service-pill is-off">
        <Icon name="alert" size={13} />
        <span role="status">Service offline</span>
        <button type="button" className="service-action" onClick={retry} aria-label="Service offline. Check again">
          Retry
        </button>
      </span>
    );
  }
  const live = service.health.live_data.available;
  return (
    <span
      className={`service-pill ${live ? "is-on" : "is-limited"}`}
      role="status"
      title={`${live ? service.health.data_source : (service.health.live_data.reason ?? "")} Checked ${time}.`}
    >
      <Icon name={live ? "check" : "half"} size={13} />
      {live ? "Live SEC data" : "Sample data only"}
      <span className="service-time num">as of {time}</span>
    </span>
  );
}

export function Footer() {
  return (
    <footer className="footer">
      <div className="wrap footer-grid">
        <div>
          <p className="footer-brand">
            <LogoMark size={22} /> FinSight AI
          </p>
          <p className="muted">
            An analytical tool for reading company filings. It is not investment advice, and its reports need analyst
            review before anyone relies on them.
          </p>
        </div>
        <nav aria-label="Project links">
          <p className="eyebrow">Project</p>
          <ul>
            <li><ExternalLink href={REPO_URL}>GitHub repository</ExternalLink></li>
            <li><ExternalLink href={DOCS_URL}>Documentation</ExternalLink></li>
            <li><ExternalLink href={ARCHITECTURE_URL}>Architecture and data policies</ExternalLink></li>
          </ul>
        </nav>
        <div>
          <p className="eyebrow">Data</p>
          <p className="muted">
            Financial data and filing text come from the U.S. Securities and Exchange Commission's{" "}
            <ExternalLink href={SEC_EDGAR_URL}>EDGAR APIs</ExternalLink>. FinSight AI is not affiliated with or endorsed
            by the SEC. Sample analyses use a synthetic fixture and say so.
          </p>
        </div>
      </div>
    </footer>
  );
}
