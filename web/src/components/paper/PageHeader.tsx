import { Fragment, type ReactNode } from "react";
import { Link } from "react-router-dom";
import "../../styles/paper.css";

// The top of a page: breadcrumb, the page's one <h1> in the display serif, a muted subtitle that
// names where the page's content comes from, and an optional actions slot beside them. A page
// renders exactly one PageHeader, so it has exactly one <h1>; sections below it start at <h2>.

export interface Crumb {
  label: string;
  /** A route. The last crumb is the current page and has none. */
  to?: string;
}

export interface PageHeaderProps {
  title: string;
  /** Home first, the current page last: [{ label: "Applications", to: "/applications" }, { label: "Northstar" }]. */
  breadcrumb?: readonly Crumb[];
  subtitle?: ReactNode;
  /** Secondary controls, never a primary one: the cyan fill belongs to the next-step band. */
  actions?: ReactNode;
}

export function PageHeader({ title, breadcrumb, subtitle, actions }: PageHeaderProps) {
  const crumbs = breadcrumb ?? [];
  return (
    <header className="bj-page-header">
      {crumbs.length > 0 && (
        <nav className="bj-breadcrumb" aria-label="Breadcrumb">
          <ol>
            {crumbs.map((crumb, index) => {
              const current = index === crumbs.length - 1;
              return (
                <Fragment key={`${index}-${crumb.label}`}>
                  <li>
                    {crumb.to !== undefined && !current ? (
                      <Link to={crumb.to}>{crumb.label}</Link>
                    ) : (
                      <span aria-current={current ? "page" : undefined}>{crumb.label}</span>
                    )}
                  </li>
                </Fragment>
              );
            })}
          </ol>
        </nav>
      )}
      <div className="bj-page-header-row">
        <div className="bj-page-header-text">
          <h1 className="bj-page-title">{title}</h1>
          {subtitle !== undefined && subtitle !== null && <p className="bj-page-subtitle">{subtitle}</p>}
        </div>
        {actions !== undefined && actions !== null && <div className="bj-page-actions">{actions}</div>}
      </div>
    </header>
  );
}
