import { type ReactNode } from "react";

export function Callout(props: { kind: "warn" | "bad" | "info"; children: ReactNode }) {
  return <div className={`callout ${props.kind}`}>{props.children}</div>;
}
