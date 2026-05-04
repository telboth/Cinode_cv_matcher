const API_BASE = "http://127.0.0.1:8000/api/v1";

export type Employee = { id: string; full_name: string; email: string };
export type Opportunity = { id: string; title: string; source_text: string };
export type Requirement = { id: string; category: string; text: string; weight: number };
export type OpportunityTextExtract = { filename: string; detected_type: "txt" | "docx" | "pdf" | string; text: string; warnings: string[] };
export type Snapshot = { id: string; employee_id: string; raw_payload_json: Record<string, unknown> };
export type Variant = { id: string; status: string; title: string };
export type Suggestion = {
  id: string;
  section_type: string;
  original_text: string;
  suggested_text: string;
  rationale: string;
  evidence_json: Record<string, unknown>;
  status: "pending" | "accepted" | "rejected";
};
export type OpenAIModels = {
  default_model: string;
  allowed_models: string[];
  suggestion_mode: "llm" | "heuristic" | string;
  suggestion_mode_reason: string;
  use_openai_analysis: boolean;
  has_openai_api_key: boolean;
};
export type CinodeCredential = {
  id: string;
  label: string;
  base_url: string;
  authorization_masked: string;
  is_default: boolean;
  last_test_status?: string | null;
  last_test_message?: string | null;
};
export type CinodeConsultant = {
  external_id?: string | null;
  full_name: string;
  email?: string | null;
  location?: string | null;
  source?: string | null;
};
export type CinodeConsultantCv = {
  credential_id: string;
  consultant_id: string;
  company_id: string;
  source_path: string;
  full_name: string;
  email?: string | null;
  title?: string | null;
  location?: string | null;
  resumes: Record<string, unknown>[];
  selected_resume_id?: number | null;
  profile: Record<string, unknown>;
  resume?: Record<string, unknown> | null;
};
export type CinodePublicStatus = {
  credential_id: string;
  consultant_id: string;
  resume_id?: number | null;
  checked_url?: string | null;
  public_ready: boolean;
  status_code?: number | null;
  detail?: string | null;
};
export type CinodeEnrichment = {
  credential_id: string;
  consultant_id: string;
  resume_id?: number | null;
  full_name: string;
  linkedin_url?: string | null;
  linkedin_profile_text?: string | null;
  github_url?: string | null;
  orcid_url?: string | null;
  researchgate_url?: string | null;
  scholar_url?: string | null;
  scholar_publications: string[];
  candidate_facts: string[];
  external_findings: Array<{ source_type?: string; url?: string; confidence?: number; facts?: string[] }>;
  sources: string[];
  warnings: string[];
};
export type CinodeBrowserCreateCvResult = {
  ok: boolean;
  mode: string;
  detail: string;
  title_used: string;
  target_url?: string | null;
  created_resume_url?: string | null;
  screenshot_path?: string | null;
  debug_trace?: string[];
};
export type CompanySlug = "xlent" | "differ" | "folden";
export type CinodeBrowserLoginResult = {
  ok: boolean;
  detail: string;
  current_url?: string | null;
  debug_trace?: string[];
};
export type CinodeBrowserPreflightResult = {
  ok: boolean;
  detail: string;
  consultant_id: string;
  resume_id?: number | null;
  edit_url?: string | null;
  current_url?: string | null;
  debug_trace?: string[];
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

async function requestFormData<T>(path: string, formData: FormData): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export const api = {
  createEmployee: (full_name: string, email: string) =>
    request<Employee>("/employees", {
      method: "POST",
      body: JSON.stringify({ full_name, email }),
    }),
  findEmployeeByEmail: (email: string) =>
    request<Employee[]>(`/employees?email=${encodeURIComponent(email)}`),

  importCinode: (employee_id: string, payload: Record<string, unknown>) =>
    request<Snapshot>("/sources/cinode/import", {
      method: "POST",
      body: JSON.stringify({ employee_id, payload }),
    }),

  createOpportunity: (title: string, source_text: string) =>
    request<Opportunity>("/opportunities", {
      method: "POST",
      body: JSON.stringify({ title, source_text, language: "nb" }),
    }),
  extractOpportunityText: (file: File) => {
    const data = new FormData();
    data.append("file", file, file.name);
    return requestFormData<OpportunityTextExtract>("/opportunities/extract-text", data);
  },

  analyzeOpportunity: (opportunity_id: string) =>
    request<{ requirements_created: number }>(`/opportunities/${opportunity_id}/analyze`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  analyzeOpportunityWithModel: (
    opportunity_id: string,
    model_override?: string,
    openai_api_key_override?: string
  ) =>
    request<{ requirements_created: number }>(`/opportunities/${opportunity_id}/analyze`, {
      method: "POST",
      body: JSON.stringify({ model_override, openai_api_key_override }),
    }),

  listRequirements: (opportunity_id: string) =>
    request<Requirement[]>(`/opportunities/${opportunity_id}/requirements`),

  latestProfile: (employee_id: string) =>
    request<{ snapshot_id: string; payload: Record<string, unknown> }>(`/sources/profiles/${employee_id}/latest`),

  createVariant: (employee_id: string, opportunity_id: string, base_snapshot_id: string, title: string) =>
    request<Variant>("/cv-variants", {
      method: "POST",
      body: JSON.stringify({ employee_id, opportunity_id, base_snapshot_id, title }),
    }),

  generateSuggestions: (variant_id: string) =>
    request<{ suggestions_created: number }>(`/cv-variants/${variant_id}/suggest`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  generateSuggestionsWithModel: (
    variant_id: string,
    model_override?: string,
    suggestion_prompt_override?: string,
    force_heuristic = false,
    openai_api_key_override?: string
  ) =>
    request<{ suggestions_created: number }>(`/cv-variants/${variant_id}/suggest`, {
      method: "POST",
      body: JSON.stringify({ model_override, suggestion_prompt_override, force_heuristic, openai_api_key_override }),
    }),

  listSuggestions: (variant_id: string) =>
    request<Suggestion[]>(`/cv-variants/${variant_id}/suggestions`),

  updateSuggestion: (
    variant_id: string,
    suggestion_id: string,
    status: "pending" | "accepted" | "rejected",
    suggested_text?: string
  ) =>
    request<Suggestion>(`/cv-variants/${variant_id}/suggestions/${suggestion_id}`, {
      method: "PATCH",
      body: JSON.stringify({ status, suggested_text }),
    }),

  exportCinodePayload: (variant_id: string) =>
    request<{ cinode_payload: Record<string, unknown>; status: string }>(
      `/cv-variants/${variant_id}/export/cinode-payload`,
      { method: "POST" }
    ),

  publishCinode: (variant_id: string, dry_run: boolean, credential_id?: string, title_override?: string) =>
    request<{
      variant_id: string;
      title_used: string;
      published: boolean;
      dry_run: boolean;
      target_url: string | null;
      external_id: string | null;
      detail: string | null;
    }>(`/cv-variants/${variant_id}/publish/cinode`, {
      method: "POST",
      body: JSON.stringify({ dry_run, credential_id, title_override }),
    }),

  getOpenAIModels: () => request<OpenAIModels>("/config/openai-models"),

  listCinodeCredentials: () => request<CinodeCredential[]>("/cinode/credentials"),
  createCinodeCredential: (payload: { label: string; base_url: string; authorization: string; is_default: boolean }) =>
    request<CinodeCredential>("/cinode/credentials", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  setDefaultCinodeCredential: (credential_id: string) =>
    request<CinodeCredential>(`/cinode/credentials/${credential_id}/set-default`, {
      method: "POST",
    }),
  deleteCinodeCredential: (credential_id: string) =>
    request<void>(`/cinode/credentials/${credential_id}`, {
      method: "DELETE",
    }),
  testCinodeCredential: (credential_id: string) =>
    request<{ ok: boolean; message: string; status_code?: number; whoami?: Record<string, unknown> }>(
      `/cinode/credentials/${credential_id}/test`,
      { method: "POST" }
    ),
  fetchCinodeConsultants: (
    credential_id: string,
    oslo_only = true,
    limit = 500,
    path_override?: string,
    cinode_token_override?: string
  ) =>
    request<{
      total: number;
      source_path?: string;
      company_id?: string;
      restricted_to_self?: boolean;
      current_user_id?: string;
      current_user_name?: string;
      access_reason?: string;
      consultants: CinodeConsultant[];
    }>(
      `/cinode/credentials/${credential_id}/consultants`,
      {
        method: "POST",
        body: JSON.stringify({ oslo_only, limit, path_override, cinode_token_override }),
      }
    ),
  fetchCinodeConsultantCv: (
    credential_id: string,
    consultant_id: string,
    resume_id?: number,
    cinode_token_override?: string
  ) =>
    request<CinodeConsultantCv>(`/cinode/credentials/${credential_id}/consultants/${encodeURIComponent(consultant_id)}/cv`, {
      method: "POST",
      body: JSON.stringify({ resume_id, cinode_token_override }),
    }),
  consultantCvDocxUrl: (credential_id: string, consultant_id: string, resume_id?: number, token_override?: string) => {
    const params = new URLSearchParams();
    if (typeof resume_id === "number") {
      params.set("resume_id", String(resume_id));
    }
    if (token_override && token_override.trim()) {
      params.set("token_override", token_override.trim());
    }
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return `${API_BASE}/cinode/credentials/${encodeURIComponent(credential_id)}/consultants/${encodeURIComponent(consultant_id)}/cv.docx${suffix}`;
  },
  consultantCvBrowserPdfUrl: (
    credential_id: string,
    consultant_id: string,
    resume_id?: number,
    prefer_public_url = true,
    token_override?: string
  ) => {
    const params = new URLSearchParams();
    if (typeof resume_id === "number") {
      params.set("resume_id", String(resume_id));
    }
    params.set("prefer_public_url", prefer_public_url ? "true" : "false");
    if (token_override && token_override.trim()) {
      params.set("token_override", token_override.trim());
    }
    return `${API_BASE}/cinode/credentials/${encodeURIComponent(credential_id)}/consultants/${encodeURIComponent(consultant_id)}/cv.browser-pdf?${params.toString()}`;
  },
  getConsultantPublicStatus: (
    credential_id: string,
    consultant_id: string,
    resume_id?: number,
    token_override?: string
  ) => {
    const params = new URLSearchParams();
    if (typeof resume_id === "number") {
      params.set("resume_id", String(resume_id));
    }
    if (token_override && token_override.trim()) {
      params.set("token_override", token_override.trim());
    }
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return request<CinodePublicStatus>(
      `/cinode/credentials/${encodeURIComponent(credential_id)}/consultants/${encodeURIComponent(consultant_id)}/public-status${suffix}`
    );
  },
  enrichConsultant: (credential_id: string, consultant_id: string, resume_id?: number, token_override?: string) => {
    const params = new URLSearchParams();
    if (typeof resume_id === "number") {
      params.set("resume_id", String(resume_id));
    }
    if (token_override && token_override.trim()) {
      params.set("token_override", token_override.trim());
    }
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return request<CinodeEnrichment>(
      `/cinode/credentials/${encodeURIComponent(credential_id)}/consultants/${encodeURIComponent(consultant_id)}/enrich${suffix}`
    );
  },
  createConsultantCvViaBrowser: (
    credential_id: string,
    consultant_id: string,
    payload: {
      variant_id: string;
      resume_id?: number;
      title_override?: string;
      company_slug?: CompanySlug;
      presentation_text_override?: string;
      apply_keywords?: boolean;
      clean_new_cv_content?: boolean;
      enforce_selected_resume_source?: boolean;
      strict_deterministic_mode?: boolean;
      cinode_token_override?: string;
    }
  ) =>
    request<CinodeBrowserCreateCvResult>(
      `/cinode/credentials/${encodeURIComponent(credential_id)}/consultants/${encodeURIComponent(consultant_id)}/create-cv-browser`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    ),
  loginCinodeBrowserProfile: (credential_id: string, company_slug?: CompanySlug) =>
    request<CinodeBrowserLoginResult>(
      `/cinode/credentials/${encodeURIComponent(credential_id)}/browser-login${
        company_slug ? `?company_slug=${encodeURIComponent(company_slug)}` : ""
      }`,
      {
      method: "POST",
      }
    ),
  preflightConsultantBrowserAccess: (
    credential_id: string,
    consultant_id: string,
    payload: {
      resume_id?: number;
      company_slug?: CompanySlug;
      cinode_token_override?: string;
    }
  ) =>
    request<CinodeBrowserPreflightResult>(
      `/cinode/credentials/${encodeURIComponent(credential_id)}/consultants/${encodeURIComponent(
        consultant_id
      )}/preflight-browser-access`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    ),
};
