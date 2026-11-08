import type { ReactNode } from "react";

// Sub-navigation (Accounts / Import Templates) lives in the main nav rail,
// consistent with every other domain. This layout is now just the padded
// content shell.
export function SettingsLayout({ children }: { children: ReactNode }) {
  return <div className="screen-main">{children}</div>;
}
