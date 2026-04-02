import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/useAuth";

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const POST_LOGIN_PATH_KEY = "sciona_post_login_path";

export default function AuthCallback() {
  const { handleToken } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [message, setMessage] = useState("Completing login...");

  useEffect(() => {
    let cancelled = false;

    async function completeLogin() {
      try {
        const hashParams = new URLSearchParams(
          window.location.hash.startsWith("#")
            ? window.location.hash.slice(1)
            : "",
        );
        const searchParams = new URLSearchParams(location.search);

        const accessToken =
          hashParams.get("access_token") ?? searchParams.get("access_token");
        const refreshToken =
          hashParams.get("refresh_token") ?? searchParams.get("refresh_token");
        if (accessToken) {
          await handleToken(accessToken, refreshToken ?? undefined);
          if (cancelled) {
            return;
          }
          const storedPath = sessionStorage.getItem(POST_LOGIN_PATH_KEY);
          if (storedPath) {
            sessionStorage.removeItem(POST_LOGIN_PATH_KEY);
          }
          const from = (
            location.state as { from?: { pathname?: string } } | null
          )?.from?.pathname;
          navigate(storedPath || from || "/", { replace: true });
          return;
        }

        const code = searchParams.get("code");
        const state = searchParams.get("state");
        if (code && state) {
          const response = await fetch(
            `${API_BASE}/auth/enterprise/callback?code=${encodeURIComponent(
              code,
            )}&state=${encodeURIComponent(state)}`,
          );
          if (!response.ok) {
            throw new Error(
              `Enterprise callback failed with ${response.status}`,
            );
          }

          const tokenResponse = (await response.json()) as {
            access_token: string;
            refresh_token?: string;
          };
          await handleToken(
            tokenResponse.access_token,
            tokenResponse.refresh_token,
          );
          if (cancelled) {
            return;
          }
          const storedPath = sessionStorage.getItem(POST_LOGIN_PATH_KEY);
          if (storedPath) {
            sessionStorage.removeItem(POST_LOGIN_PATH_KEY);
          }
          const from = (
            location.state as { from?: { pathname?: string } } | null
          )?.from?.pathname;
          navigate(storedPath || from || "/", { replace: true });
          return;
        }

        setMessage("No login token found. Redirecting home...");
        navigate("/", { replace: true });
      } catch (error) {
        if (cancelled) {
          return;
        }
        setMessage(error instanceof Error ? error.message : "Login failed");
        window.setTimeout(() => {
          navigate("/", { replace: true });
        }, 2000);
      }
    }

    void completeLogin();
    return () => {
      cancelled = true;
    };
  }, [handleToken, location.search, location.state, navigate]);

  return <p className="text-muted p-8">{message}</p>;
}
