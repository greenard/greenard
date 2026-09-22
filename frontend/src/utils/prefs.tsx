import { createContext, useContext, useState, type ReactNode } from "react";
import type { TimeDisplay } from "./format";

interface Prefs {
  tz: TimeDisplay;
  setTz: (tz: TimeDisplay) => void;
}

const Ctx = createContext<Prefs>({ tz: "UTC", setTz: () => undefined });

function load(): TimeDisplay {
  try {
    return localStorage.getItem("greenard.tz") === "Africa/Casablanca" ? "Africa/Casablanca" : "UTC";
  } catch {
    return "UTC";
  }
}

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [tz, setTzState] = useState<TimeDisplay>(load);
  const setTz = (v: TimeDisplay) => {
    setTzState(v);
    try {
      localStorage.setItem("greenard.tz", v);
    } catch {
      /* stockage indisponible */
    }
  };
  return <Ctx.Provider value={{ tz, setTz }}>{children}</Ctx.Provider>;
}

export const usePrefs = () => useContext(Ctx);
