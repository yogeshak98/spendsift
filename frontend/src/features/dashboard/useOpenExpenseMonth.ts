import { useContext } from "react";
import { useNavigate } from "react-router-dom";
import { usePeriod } from "@/shared/period/usePeriod";
import { TooltipContext } from "@/shared/theme/tooltipContext";
import { yearMonth } from "./format";

/**
 * Open Expense · Overview on one month ("YYYY-MM"): select that month in the
 * shared period, then navigate. The dashboard itself never reads the period
 * (it is always as of today), so this only steers the destination screen.
 */
export function useOpenExpenseMonth() {
  const { setPeriod } = usePeriod();
  const navigate = useNavigate();
  const setTip = useContext(TooltipContext);
  return (ym: string) => {
    setTip(null);
    setPeriod(yearMonth(ym));
    navigate("/expense/overview");
  };
}
