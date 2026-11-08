import { type ReactNode } from "react";

export function Panel(props: {
  title?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  glow?: boolean;
  // when laid out in an equal-height row, fill makes the body stretch to the
  // panel's full height instead of leaving empty space below the content.
  fill?: boolean;
}) {
  return (
    <div className={`panel${props.glow ? " glow" : ""}${props.fill ? " fill" : ""}`}>
      {props.title && (
        <div className="panel-head">
          {props.title}
          {props.right && <span className="right">{props.right}</span>}
        </div>
      )}
      {props.children}
    </div>
  );
}
