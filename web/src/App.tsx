import { useEffect } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { DEFAULT_SAMPLE } from "./api/sample";
import { Landing } from "./pages/Landing";
import { NotFound } from "./pages/NotFound";
import { ReportsPage } from "./pages/ReportsPage";
import { RunPage } from "./pages/RunPage";
import { SamplePage } from "./pages/SamplePage";
import { WorkspaceHome } from "./pages/WorkspaceHome";
import { WorkspaceLayout } from "./pages/WorkspaceLayout";

/** Scroll to the top on navigation, or to the element named by the hash. */
function ScrollManager() {
  const { pathname, hash } = useLocation();
  useEffect(() => {
    if (hash) {
      const el = document.getElementById(decodeURIComponent(hash.slice(1)));
      if (el) {
        el.scrollIntoView();
        return;
      }
    }
    window.scrollTo(0, 0);
  }, [pathname, hash]);
  return null;
}

export function App() {
  return (
    <>
      <ScrollManager />
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/app" element={<WorkspaceLayout />}>
          <Route index element={<WorkspaceHome />} />
          <Route path="runs/:runId" element={<RunPage />} />
          <Route path="reports" element={<ReportsPage />} />
          <Route path="sample" element={<Navigate to={`/app/sample/${DEFAULT_SAMPLE}`} replace />} />
          <Route path="sample/:sampleId" element={<SamplePage />} />
          <Route path="*" element={<NotFound inWorkspace />} />
        </Route>
        <Route path="*" element={<NotFound />} />
      </Routes>
    </>
  );
}
