import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { DEFAULT_SAMPLE } from "../api/sample";
import { CompanyPanel } from "../components/CompanyPanel";
import { Icon } from "../components/Icon";
import { Logo } from "../components/Logo";
import { Footer, ServicePill } from "../components/Shell";
import { REPO_URL } from "../config";

export interface WorkspaceContext {
  /** Opens the New analysis sheet from anywhere in the workspace. */
  openNewAnalysis: () => void;
}

export function WorkspaceLayout() {
  const location = useLocation();
  const sheet = useRef<HTMLDialogElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  // The sheet's form is mounted only while it is open, so each visit starts clean.
  const [sheetOpen, setSheetOpen] = useState(false);
  const isHome = location.pathname.replace(/\/$/, "") === "/app";

  const openNewAnalysis = useCallback(() => {
    setSheetOpen(true);
    setMenuOpen(false);
    sheet.current?.showModal();
  }, []);
  const closeSheet = () => sheet.current?.close();

  useEffect(() => {
    sheet.current?.close();
    setMenuOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  return (
    <div className="workspace">
      <a className="skip-link" href="#main">Skip to the analysis</a>
      <header className="topbar">
        <div className="topbar-inner">
          <Logo />
          <nav id="workspace-nav" aria-label="Workspace" className={menuOpen ? "is-open" : ""}>
            <ul>
              <li><NavLink to="/app" end>Analyze</NavLink></li>
              <li><NavLink to="/app/reports">Reports</NavLink></li>
              <li><NavLink to={`/app/sample/${DEFAULT_SAMPLE}`}>Sample report</NavLink></li>
              <li className="nav-extra">
                <a href={REPO_URL} target="_blank" rel="noreferrer noopener">
                  <Icon name="github" size={16} /> GitHub<span className="sr-only"> (opens in a new tab)</span>
                </a>
              </li>
            </ul>
            <div className="nav-extra nav-status"><ServicePill /></div>
          </nav>
          <div className="topbar-side">
            <span className="wide-only"><ServicePill /></span>
            <a className="icon-link wide-only" href={REPO_URL} target="_blank" rel="noreferrer noopener" aria-label="FinSight on GitHub (opens in a new tab)">
              <Icon name="github" size={18} />
            </a>
            {!isHome && (
              <button type="button" className="btn btn-sm btn-primary new-analysis" onClick={openNewAnalysis} aria-haspopup="dialog">
                <Icon name="plus" size={14} /> New analysis
              </button>
            )}
            <button
              type="button"
              className="menu-btn"
              aria-expanded={menuOpen}
              aria-controls="workspace-nav"
              onClick={() => setMenuOpen((v) => !v)}
            >
              <Icon name={menuOpen ? "x" : "menu"} size={18} />
              <span className="sr-only">{menuOpen ? "Close menu" : "Menu"}</span>
            </button>
          </div>
        </div>
      </header>

      <main id="main" tabIndex={-1}>
        <div className="wrap">
          <Outlet context={{ openNewAnalysis } satisfies WorkspaceContext} />
        </div>
      </main>
      <Footer />

      <dialog
        ref={sheet}
        className="sheet"
        aria-label="New analysis"
        onClose={() => setSheetOpen(false)}
        onClick={(e) => e.target === e.currentTarget && closeSheet()}
      >
        <div className="sheet-inner">
          <div className="sheet-head">
            <h2>New analysis</h2>
            <button type="button" className="btn btn-sm btn-quiet" onClick={closeSheet}>
              <Icon name="x" size={14} /> Close
            </button>
          </div>
          <p className="muted">One company, its last three completed fiscal years.</p>
          {sheetOpen && <CompanyPanel variant="drawer" onSubmitted={closeSheet} />}
        </div>
      </dialog>
    </div>
  );
}
