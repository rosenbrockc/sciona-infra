import { useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";

const REFERRAL_STORAGE_KEY = "sciona_referral_code";

export default function ReferralLanding() {
  const { code } = useParams<{ code: string }>();
  const navigate = useNavigate();

  useEffect(() => {
    if (code) {
      localStorage.setItem(REFERRAL_STORAGE_KEY, code);
    }
    navigate("/auth/callback", { replace: true });
  }, [code, navigate]);

  return <p className="text-muted p-8">Processing invite link...</p>;
}
