import {
  createContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import type { UserProfile } from "../api/types";

export interface AuthContextValue {
  user: UserProfile | null;
  token: string | null;
  loading: boolean;
  login: () => Promise<void>;
  loginEnterprise: (orgSlug: string) => Promise<void>;
  logout: () => void;
  handleToken: (accessToken: string, refreshToken?: string) => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | undefined>(
  undefined,
);

const TOKEN_KEY = "sciona_access_token";
const REFRESH_KEY = "sciona_refresh_token";
const POST_LOGIN_PATH_KEY = "sciona_post_login_path";
const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function fetchMe(token: string): Promise<UserProfile> {
  const response = await fetch(`${API_BASE}/auth/me`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });

  if (!response.ok) {
    throw new Error(`/auth/me failed with ${response.status}`);
  }

  return response.json() as Promise<UserProfile>;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const storedToken = localStorage.getItem(TOKEN_KEY);
    if (!storedToken) {
      setLoading(false);
      return;
    }

    setToken(storedToken);
    fetchMe(storedToken)
      .then((profile) => {
        setUser(profile);
      })
      .catch(() => {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(REFRESH_KEY);
        setToken(null);
        setUser(null);
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  async function handleToken(accessToken: string, refreshToken?: string) {
    localStorage.setItem(TOKEN_KEY, accessToken);
    if (refreshToken) {
      localStorage.setItem(REFRESH_KEY, refreshToken);
    } else {
      localStorage.removeItem(REFRESH_KEY);
    }

    setToken(accessToken);
    try {
      const profile = await fetchMe(accessToken);
      setUser(profile);
    } catch (error) {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(REFRESH_KEY);
      setToken(null);
      setUser(null);
      throw error;
    }
  }

  async function login() {
    sessionStorage.setItem(
      POST_LOGIN_PATH_KEY,
      `${window.location.pathname}${window.location.search}${window.location.hash}`,
    );
    const response = await fetch(`${API_BASE}/auth/login`);
    if (!response.ok) {
      throw new Error(`Login request failed with ${response.status}`);
    }

    const data = (await response.json()) as { url?: string };
    if (!data.url) {
      throw new Error("Login URL missing from backend response");
    }

    window.location.assign(data.url);
  }

  async function loginEnterprise(orgSlug: string) {
    const trimmed = orgSlug.trim();
    if (!trimmed) {
      throw new Error("Organization slug is required");
    }

    sessionStorage.setItem(
      POST_LOGIN_PATH_KEY,
      `${window.location.pathname}${window.location.search}${window.location.hash}`,
    );
    window.location.assign(
      `${API_BASE}/auth/enterprise/login?org_slug=${encodeURIComponent(trimmed)}`,
    );
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
    setToken(null);
    setUser(null);
  }

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        loading,
        login,
        loginEnterprise,
        logout,
        handleToken,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
