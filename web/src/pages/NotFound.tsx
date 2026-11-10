import { Link } from "react-router-dom";
import { EmptyState } from "../components/common";
import { Logo } from "../components/Logo";
import { useDocumentTitle } from "../lib/hooks";

export function NotFound({ inWorkspace }: { inWorkspace?: boolean }) {
  useDocumentTitle("Page not found");
  const body = (
    <EmptyState icon="search" title="There is no page at this address"
      actions={<><Link className="btn btn-primary" to="/app">Open the workspace</Link><Link className="btn" to="/">Go to the home page</Link></>}>
      <p>The link may be mistyped, or it may point to a page that was renamed.</p>
    </EmptyState>
  );
  if (inWorkspace) return <div className="page-narrow">{body}</div>;
  return (
    <div className="standalone">
      <Logo />
      <main>{body}</main>
    </div>
  );
}
