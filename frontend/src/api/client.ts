import type {
  AtomDetailResponse,
  AtomSummaryResponse,
  AtomVersionResponse,
  BenchmarkRecord,
  BountyResponse,
  BountySummaryResponse,
  ComputePreserved,
  LeaderboardEntry,
  OriginatorImpact,
  PaginatedResponse,
  SettlementInfo,
  SubmissionLeaderboardEntry,
  TokenResponse,
  UserProfile,
  WorkflowStatus,
} from "./types";

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const ACCESS_TOKEN_KEY = "sciona_access_token";

function getStoredAccessToken(): string | null {
  return window.localStorage.getItem(ACCESS_TOKEN_KEY);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const token = getStoredAccessToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${path}`);
  }
  return res.json() as Promise<T>;
}

async function requestText(path: string, init?: RequestInit): Promise<string> {
  const headers = new Headers(init?.headers);
  const token = getStoredAccessToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${path}`);
  }
  return res.text();
}

function withQuery(
  path: string,
  params: Record<string, string | number | undefined>,
): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") {
      continue;
    }
    query.set(key, String(value));
  }
  const suffix = query.toString();
  return suffix ? `${path}?${suffix}` : path;
}

export const api = {
  async getAuthLoginUrl(): Promise<string> {
    const data = await request<{ url: string }>("/auth/login");
    return data.url;
  },

  async exchangeEnterpriseCallback(
    code: string,
    state: string,
  ): Promise<TokenResponse> {
    return request<TokenResponse>(
      withQuery("/auth/enterprise/callback", { code, state }),
    );
  },

  async getMe(): Promise<UserProfile> {
    return request<UserProfile>("/auth/me");
  },

  async getBounties(params?: {
    status?: string;
    domain_tag?: string;
    limit?: number;
    offset?: number;
  }): Promise<PaginatedResponse<BountySummaryResponse>> {
    return request<PaginatedResponse<BountySummaryResponse>>(
      withQuery("/bounties", {
        status: params?.status,
        domain_tag: params?.domain_tag,
        limit: params?.limit,
        offset: params?.offset,
      }),
    );
  },

  async getBounty(id: string): Promise<BountyResponse> {
    return request<BountyResponse>(`/bounties/${id}`);
  },

  async getBountyLeaderboard(
    id: string,
  ): Promise<PaginatedResponse<SubmissionLeaderboardEntry>> {
    return request<PaginatedResponse<SubmissionLeaderboardEntry>>(
      `/bounties/${id}/leaderboard`,
    );
  },

  async getBountySettlement(id: string): Promise<SettlementInfo> {
    return request<SettlementInfo>(`/bounties/${id}/settlement`);
  },

  async getSubmissionStatus(submissionId: string): Promise<WorkflowStatus> {
    return request<WorkflowStatus>(`/submissions/${submissionId}/status`);
  },

  async getAtoms(params?: {
    search?: string;
    domain_tag?: string;
    limit?: number;
    offset?: number;
  }): Promise<PaginatedResponse<AtomSummaryResponse>> {
    return request<PaginatedResponse<AtomSummaryResponse>>(
      withQuery("/atoms", {
        q: params?.search,
        domain_tag: params?.domain_tag,
        limit: params?.limit,
        offset: params?.offset,
      }),
    );
  },

  async getAtom(fqdn: string): Promise<AtomDetailResponse> {
    return request<AtomDetailResponse>(`/atoms/${fqdn}`);
  },

  async getAtomVersions(fqdn: string): Promise<AtomVersionResponse[]> {
    return request<AtomVersionResponse[]>(`/atoms/${fqdn}/versions`);
  },

  async getAtomBenchmarks(fqdn: string): Promise<BenchmarkRecord[]> {
    return request<BenchmarkRecord[]>(`/dashboard/atom/${fqdn}/benchmarks`);
  },

  async getAtomBibtex(fqdn: string): Promise<string> {
    const response = await request<{ fqdn: string; bibtex: string }>(
      `/dashboard/atom/${fqdn}/bibtex`,
    );
    return response.bibtex;
  },

  async getLeaderboard(limit?: number): Promise<LeaderboardEntry[]> {
    return request<LeaderboardEntry[]>(
      withQuery("/dashboard/leaderboard", { limit }),
    );
  },

  async getComputePreserved(): Promise<ComputePreserved> {
    return request<ComputePreserved>("/dashboard/compute-preserved");
  },

  async getOriginatorImpact(id: string): Promise<OriginatorImpact> {
    return request<OriginatorImpact>(`/dashboard/originator/${id}/impact`);
  },

  async fetchRawBibtex(fqdn: string): Promise<string> {
    return requestText(`/dashboard/atom/${fqdn}/bibtex`);
  },
};
