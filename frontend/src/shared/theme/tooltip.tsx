import { type ReactNode, useContext, useState } from "react";
import { TooltipContext, type TipState } from "./tooltipContext";

export function TooltipProvider({ children }: { children: ReactNode }) {
  const [tip, setTip] = useState<TipState>(null);
  return (
    <TooltipContext.Provider value={setTip}>
      {children}
      {tip && (
        <div className="tip" style={{ left: `calc(${tip.x}px + 1.0769rem)`, top: `calc(${tip.y}px + 1.0769rem)` }}>
          {tip.content}
        </div>
      )}
    </TooltipContext.Provider>
  );
}

export function Tip({ content, children }: { content: ReactNode; children: ReactNode }) {
  const setTip = useContext(TooltipContext);
  return (
    <span
      onMouseEnter={(e) => setTip({ content, x: e.clientX, y: e.clientY })}
      onMouseMove={(e) => setTip({ content, x: e.clientX, y: e.clientY })}
      onMouseLeave={() => setTip(null)}
    >
      {children}
    </span>
  );
}
