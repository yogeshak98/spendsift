import { Link } from "react-router-dom";

// A drill-down trail rendered above a screen's content — e.g.
// "Net Worth / Investments / Stock / SBIN". Every ancestor is a link back to
// that level; the current (last) item is plain text, matching the mockup's
// `data-crumb` convention (ancestors as `<a>`, leaf wrapped in `<b>`).
export type BreadcrumbItem = { label: string; to?: string };

export function Breadcrumb({ items }: { items: BreadcrumbItem[] }) {
  return (
    <div className="crumb">
      {items.map((item, i) => {
        const isLast = i === items.length - 1;
        return (
          <span key={`${item.label}-${i}`}>
            {i > 0 && " / "}
            {isLast || !item.to ? <b>{item.label}</b> : <Link to={item.to}>{item.label}</Link>}
          </span>
        );
      })}
    </div>
  );
}
