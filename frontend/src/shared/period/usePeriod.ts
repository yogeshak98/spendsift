import { useContext } from "react";
import { PeriodContext } from "./periodContext";

export const usePeriod = () => useContext(PeriodContext);
