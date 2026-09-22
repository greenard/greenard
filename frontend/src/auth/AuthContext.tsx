import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { get, post, refreshAccessToken, setAccessToken, setLoggedOutHandler } from "../api/client";
import type { User } from "../api/types";
import { setLanguage } from "../i18n";

interface AuthState {
  user: User | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  const loadMe = useCallback(async () => {
    const me = await get<User>("/auth/me");
    setUser(me);
    setLanguage(me.locale);
  }, []);

  useEffect(() => {
    setLoggedOutHandler(() => setUser(null));
    // Session existante ? (cookie de rafraîchissement HttpOnly)
    refreshAccessToken()
      .then(async (ok) => {
        if (ok) await loadMe();
      })
      .catch(() => undefined)
      .finally(() => setReady(true));
  }, [loadMe]);

  const login = useCallback(
    async (email: string, password: string) => {
      const tok = await post<{ access_token: string }>("/auth/login", { email, password });
      setAccessToken(tok.access_token);
      await loadMe();
    },
    [loadMe],
  );

  const logout = useCallback(async () => {
    await post("/auth/logout").catch(() => undefined);
    setAccessToken(null);
    setUser(null);
  }, []);

  return <Ctx.Provider value={{ user, ready, login, logout }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
