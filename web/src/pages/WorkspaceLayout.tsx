import { useEffect, useRef } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { DEFAULT_SAMPLE } from "../api/sample";
import { CompanyPanel } from "../components/CompanyPanel";
import { Icon } from "../components/Icon";
import { Logo } from "../components/Logo";
import { Footer, ServicePill } from "../components/Shell";
import { REPO_URL } from "../config";
import { DESKTOP, useMediaQuery } from "../lib/media";

export function WorkspaceLayout() {
  const desktop = useMediaQuery(DESKTOP);
  const location = useLocation();
  const drawer = useRef<HTMLDialogElement>(null);
  const isHome = location.pathname.replace(/\/$/, "") === "/app";

  const closeDrawer = () => drawer.current?.close();
  useEffect(() => {
    drawer.current?.close();
  }, [location.pathname, desktop]);

  return (
    <div className="workspace">
      <a className="skip-link" href="#main">Skip to the analysis</a>
      <header className="topbar">
        <Logo />
        <nav aria-label="Workspace">
          <ul>
            <li><NavLink to="/app" end>Analyze</NavLink></li>
            <li><NavLink to="/app/reports">Reports</NavLink></li>
            <li><NavLink to={`/app/sample/${DEFAULT_SAMPLE}`}>Sample</NavLink></li>
          </ul>
        </nav>
        <div className="topbar-side">
          <ServicePill />
          <a className="icon-link" href={REPO_URL} target="_blank" rel="noreferrer noopener" aria-label="FinSight on GitHub (opens in a new tab)">
            <Icon name="github" size={18} />
          </a>
          {!desktop && !isHome && (
            <button type="button" className="btn btn-sm btn-primary" onClick={() => drawer.current?.showModal()} aria-haspopup="dialog">
              <Icon name="plus" size={14} /> New analysis
            </button>
          )}
        </div>
      </header>

      <div className={`workspace-body${desktop ? " with-panel" : ""}`}>
        {desktop && (
          <aside className="side-panel" aria-label="Company selection">
            <CompanyPanel />
          </aside>
        )}
        <main id="main" tabIndex={-1}>
          <Outlet context={{ desktop }} />
        </main>
      </div>
      <Footer />

      {!desktop && (
        <dialog ref={drawer} className="drawer" aria-label="New analysis" onClick={(e) => e.target === e.currentTarget && closeDrawer()}>
          <div className="drawer-inner">
            <button type="button" className="btn btn-sm btn-quiet drawer-close" onClick={closeDrawer}>
              <Icon name="x" size={14} /> Close
            </button>
            <CompanyPanel onSubmitted={closeDrawer} />
          </div>
        </dialog>
      )}
    </div>
  );
}

export interface WorkspaceContext {
  desktop: boolean;
}
