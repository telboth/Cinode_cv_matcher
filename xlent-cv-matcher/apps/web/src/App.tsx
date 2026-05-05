import { useEffect, useMemo, useState } from "react";

import {
  API_BASE,
  api,
  type CompanySlug,
  type CinodeConsultant,
  type CinodeConsultantCv,
  type CinodeCredential,
  type CinodeEnrichment,
  type CinodePublicStatus,
  type Requirement,
  type Suggestion,
} from "./lib/api";

const DEFAULT_SUGGESTION_PROMPT = `Du er en senior tilbudsrådgiver som tilpasser CV-tekst til konsulentoppdrag.

Mål:
- Tilpass CV-teksten slik at den matcher kravene i utlysningen.
- Behold teksten sannferdig og dokumenterbar.
- Bevar mest mulig av original ordlyd og tone.

Regler:
- Behold originalt innhold der det er relevant; ikke skriv alt på nytt.
- Du kan korte ned eksisterende tekst med opptil ca. 5 % når det gir bedre klarhet og fokus.
- Legg til kravrelevant tekst naturlig der den passer best.
- Ikke finn opp arbeidsgivere, prosjekter, roller, sertifiseringer, datoer, publikasjoner eller ferdigheter.
- Hvis berikelsesdata finnes (LinkedIn/Scholar/kandidatfakta), bruk det kun som støtte for det som faktisk kan dokumenteres.
- Hold språket konsist og profesjonelt, og skriv på norsk med mindre input tydelig tilsier et annet språk.
- Vurder all tilgjengelig kandidatdata:
  eksisterende CV-payload, strukturerte CV-seksjoner, LinkedIn/Scholar-berikelse og kandidatfakta.
- Vurder utlysningstittel + utlysningstekst + normalisert kravliste.
- Foreslå oppdatert tekst for relevante CV-seksjoner.

Outputformat:
- Returner KUN gyldig JSON.
- Toppnivå skal være en array.
- Hvert element må inneholde:
  section_type, original_text, suggested_text, rationale, evidence_json
- evidence_json bør referere til kravtekst og eventuelle støttende profil-/berikelsesfakta som er brukt.`;

function consultantKey(row: CinodeConsultant): string {
  return `${row.external_id || ""}|${row.full_name}`;
}

function consultantIdFromKey(key: string): string {
  return key.split("|")[0] || "";
}

function extractSummary(cv: CinodeConsultantCv | null): string {
  const blocks = (cv?.resume?.resume as { blocks?: unknown[] } | undefined)?.blocks;
  if (!Array.isArray(blocks)) return "";
  for (const block of blocks) {
    if (typeof block !== "object" || !block) continue;
    const typed = block as Record<string, unknown>;
    if (typed.blockType === 9 && typeof typed.description === "string" && typed.description.trim()) {
      return typed.description.trim();
    }
  }
  return "";
}

function extractSkills(cv: CinodeConsultantCv | null): string[] {
  const blocks = (cv?.resume?.resume as { blocks?: unknown[] } | undefined)?.blocks;
  if (!Array.isArray(blocks)) return [];

  const skills: string[] = [];
  for (const block of blocks) {
    if (typeof block !== "object" || !block) continue;
    const typed = block as Record<string, unknown>;
    if (typed.blockType !== 12 || !Array.isArray(typed.data)) continue;
    for (const category of typed.data) {
      if (typeof category !== "object" || !category) continue;
      const cat = category as Record<string, unknown>;
      if (!Array.isArray(cat.skills)) continue;
      for (const entry of cat.skills) {
        if (typeof entry !== "object" || !entry) continue;
        const name = (entry as Record<string, unknown>).name;
        if (typeof name === "string" && name.trim()) {
          skills.push(name.trim());
        }
      }
    }
  }

  return Array.from(new Set(skills)).slice(0, 80);
}

function buildCinodePayloadFromCv(cv: CinodeConsultantCv): Record<string, unknown> {
  const summary = extractSummary(cv);
  return {
    name: cv.full_name,
    title: cv.title || "Konsulent",
    location: cv.location || "",
    summary: summary || `${cv.full_name} (${cv.title || "Konsulent"})`,
    skills: [],
    source_profile: {
      companyId: (cv.profile as Record<string, unknown> | undefined)?.companyId,
      id: (cv.profile as Record<string, unknown> | undefined)?.id,
      firstName: (cv.profile as Record<string, unknown> | undefined)?.firstName,
      lastName: (cv.profile as Record<string, unknown> | undefined)?.lastName,
      title: (cv.profile as Record<string, unknown> | undefined)?.title,
      locationName: (cv.profile as Record<string, unknown> | undefined)?.locationName,
      companyUserEmail: (cv.profile as Record<string, unknown> | undefined)?.companyUserEmail,
    },
    source_resume: {
      resume: {
        blocks: [
          {
            blockType: 9,
            title: "Presentation",
            description: summary || "",
          },
        ],
      },
    },
    enrichment: {},
  };
}

function buildEnrichmentSummary(enriched: CinodeEnrichment): string[] {
  const candidateFacts = Array.isArray(enriched.candidate_facts) ? enriched.candidate_facts : [];
  const scholarPublications = Array.isArray(enriched.scholar_publications) ? enriched.scholar_publications : [];
  const sources = Array.isArray(enriched.sources) ? enriched.sources : [];
  const externalFindings = Array.isArray(enriched.external_findings) ? enriched.external_findings : [];
  const parts: string[] = [];
  if (enriched.linkedin_url) {
    parts.push(`LinkedIn-profil funnet`);
  }
  if (enriched.linkedin_profile_text) {
    parts.push(`LinkedIn-profiltekst lastet`);
  }
  if (enriched.github_url) {
    parts.push(`GitHub-profil funnet`);
  }
  if (enriched.orcid_url) {
    parts.push(`ORCID-profil funnet`);
  }
  if (enriched.researchgate_url) {
    parts.push(`ResearchGate-profil funnet`);
  }
  if (enriched.scholar_url) {
    parts.push(`Google Scholar-profil funnet`);
  }
  if (candidateFacts.length > 0) {
    parts.push(`${candidateFacts.length} kandidatfakta lastet`);
  }
  if (scholarPublications.length > 0) {
    parts.push(`${scholarPublications.length} publikasjoner lastet`);
  }
  if (sources.length > 0) {
    parts.push(`${sources.length} kilder brukt`);
  }
  if (externalFindings.length > 0) {
    parts.push(`${externalFindings.length} eksterne funn`);
  }
  if (parts.length === 0) {
    parts.push("Ingen ekstra kandidatdata funnet");
  }
  return parts;
}

function normalizeEnrichment(enriched: CinodeEnrichment): CinodeEnrichment {
  return {
    ...enriched,
    scholar_publications: Array.isArray(enriched.scholar_publications) ? enriched.scholar_publications : [],
    candidate_facts: Array.isArray(enriched.candidate_facts) ? enriched.candidate_facts : [],
    external_findings: Array.isArray(enriched.external_findings) ? enriched.external_findings : [],
    sources: Array.isArray(enriched.sources) ? enriched.sources : [],
    warnings: Array.isArray(enriched.warnings) ? enriched.warnings : [],
  };
}

function suggestionStatusLabel(status: "pending" | "accepted" | "rejected"): string {
  if (status === "accepted") return "Godkjent";
  if (status === "rejected") return "Avvist";
  return "Avventer";
}

function buildPublishPresentationText(
  suggestions: Suggestion[],
  editing: Record<string, string>
): string {
  const nonPresentationSections = new Set([
    "keyword",
    "skills",
    "skill",
    "tools",
    "tool",
    "techniques",
    "technique",
    "teknikker",
    "teknikk",
    "verktøy",
    "verktoy",
    "kompetanse",
  ]);
  const textSuggestions = suggestions.filter((item) => {
    const section = String(item.section_type || "").trim().toLowerCase();
    return !nonPresentationSections.has(section);
  });
  if (textSuggestions.length === 0) return "";

  const allowed = textSuggestions.filter((item) => item.status !== "rejected");
  const source = allowed.length > 0 ? allowed : textSuggestions;

  // Simplified publishing flow:
  // update only the top introduction block (before project experience).
  const summary = source.find((item) => String(item.section_type || "").trim().toLowerCase() === "summary");
  if (summary) {
    return String(editing[summary.id] ?? summary.suggested_text ?? "").trim();
  }
  const first = source[0];
  return first ? String(editing[first.id] ?? first.suggested_text ?? "").trim() : "";
}

function normalizeErrorMessage(error: unknown): string {
  const fallback = "ukjent feil";
  if (!(error instanceof Error)) return fallback;

  let message = error.message || fallback;
  const trimmed = message.trim();
  if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
    try {
      const parsed = JSON.parse(trimmed) as { detail?: unknown };
      if (typeof parsed.detail === "string" && parsed.detail.trim()) {
        message = parsed.detail.trim();
      }
    } catch {
      // keep original message
    }
  }

  const replacements: Array<[RegExp, string]> = [
    [/cv has already been imported for this user/gi, "CV er allerede importert for denne brukeren"],
    [/already exists/gi, "finnes allerede"],
    [/not found/gi, "ble ikke funnet"],
    [/invalid/gi, "ugyldig"],
    [/failed to fetch/gi, "kunne ikke kontakte API"],
    [/credential/gi, "tilgang"],
    [/consultant/gi, "konsulent"],
    [/public-status/gi, "offentlig-status"],
    [/payload/gi, "datauttrekk"],
  ];

  let out = message;
  for (const [pattern, replacement] of replacements) {
    out = out.replace(pattern, replacement);
  }
  return out;
}

export function App() {
  const MODEL_STORAGE_KEY = "xlent_selected_openai_model";
  const PROMPT_STORAGE_KEY = "xlent_suggestion_prompt";
  const TESTMODE_STORAGE_KEY = "xlent_testmode_heuristic_only";
  const OPENAI_API_KEY_OVERRIDE_STORAGE_KEY = "xlent_openai_api_key_override";
  const CINODE_TOKEN_OVERRIDE_STORAGE_KEY = "xlent_cinode_token_override";
  const [jobTitle, setJobTitle] = useState("Senior Konsulent - Integrasjon");
  const [jobText, setJobText] = useState(
    "- Må ha erfaring med Claude-code\n- Bør ha erfaring med Codex\n- Bør ha minst 10 års programmeringserfaring"
  );
  const [opportunityFile, setOpportunityFile] = useState<File | null>(null);
  const [opportunityFileWarnings, setOpportunityFileWarnings] = useState<string[]>([]);
  const [uploadingOpportunityFile, setUploadingOpportunityFile] = useState(false);

  const [variantId, setVariantId] = useState("");

  const [requirements, setRequirements] = useState<Requirement[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [editing, setEditing] = useState<Record<string, string>>({});
  const [savingSuggestion, setSavingSuggestion] = useState<{ id: string; status: "pending" | "accepted" | "rejected" } | null>(null);
  const [suggestionFeedback, setSuggestionFeedback] = useState<Record<string, string>>({});
  const [status, setStatus] = useState("Klar");

  const [settingsExpanded, setSettingsExpanded] = useState(false);
  const [allowedModels, setAllowedModels] = useState<string[]>(["gpt-4.1-mini"]);
  const [selectedModel, setSelectedModel] = useState<string>(() => localStorage.getItem(MODEL_STORAGE_KEY) || "gpt-4.1-mini");
  const [suggestionPrompt, setSuggestionPrompt] = useState<string>(
    () => localStorage.getItem(PROMPT_STORAGE_KEY) || DEFAULT_SUGGESTION_PROMPT
  );
  const [suggestionMode, setSuggestionMode] = useState<"llm" | "heuristic" | string>("heuristic");
  const [suggestionModeReason, setSuggestionModeReason] = useState<string>("Ikke lastet");
  const [testModeHeuristicOnly, setTestModeHeuristicOnly] = useState<boolean>(false);
  const [openaiApiKeyOverride, setOpenaiApiKeyOverride] = useState<string>(
    () => localStorage.getItem(OPENAI_API_KEY_OVERRIDE_STORAGE_KEY) || ""
  );

  const [cinodeCredentials, setCinodeCredentials] = useState<CinodeCredential[]>([]);
  const [selectedCredentialId, setSelectedCredentialId] = useState<string>("");
  const [consultants, setConsultants] = useState<CinodeConsultant[]>([]);
  const [consultantSearch, setConsultantSearch] = useState("");
  const [quickConsultantKey, setQuickConsultantKey] = useState("");
  const [consultantCv, setConsultantCv] = useState<CinodeConsultantCv | null>(null);
  const [consultantEnrichment, setConsultantEnrichment] = useState<CinodeEnrichment | null>(null);
  const [enrichmentSummary, setEnrichmentSummary] = useState<string[]>([]);
  const [consultantsLoading, setConsultantsLoading] = useState(false);
  const [consultantFetchStatus, setConsultantFetchStatus] = useState("Ikke lastet");
  const [cinodeTokenBootstrapLoading, setCinodeTokenBootstrapLoading] = useState(false);
  const [cinodeTokenBootstrapStatus, setCinodeTokenBootstrapStatus] = useState("");
  const [publicStatus, setPublicStatus] = useState<CinodePublicStatus | null>(null);
  const [checkingPublicStatus, setCheckingPublicStatus] = useState(false);
  const [selectedResumeId, setSelectedResumeId] = useState<string>("");
  const [selectedCompanySlug, setSelectedCompanySlug] = useState<CompanySlug>("xlent");
  const [cinodeTokenOverride, setCinodeTokenOverride] = useState<string>(
    () => localStorage.getItem(CINODE_TOKEN_OVERRIDE_STORAGE_KEY) || ""
  );

  const [automatingBrowserPublish, setAutomatingBrowserPublish] = useState(false);
  const [openingCinodeLogin, setOpeningCinodeLogin] = useState(false);
  const [browserPublishResponse, setBrowserPublishResponse] = useState<Record<string, unknown> | null>(null);
  const [publishUiMessage, setPublishUiMessage] = useState("");
  const [lastPublished, setLastPublished] = useState<{
    variantId: string;
    summaryText: string;
    createdResumeUrl?: string;
    publishedAt: string;
  } | null>(null);
  const showGeneratingSpinner = status.trim().toLowerCase() === "genererer forslag...".toLowerCase();

  const sortedConsultants = useMemo(() => {
    const rows = [...consultants];
    rows.sort((a, b) => {
      const aName = a.full_name.trim();
      const bName = b.full_name.trim();
      const aThomas = aName.toLowerCase() === "thomas elboth";
      const bThomas = bName.toLowerCase() === "thomas elboth";
      if (aThomas && !bThomas) return -1;
      if (!aThomas && bThomas) return 1;
      return aName.localeCompare(bName, "nb");
    });
    return rows;
  }, [consultants]);

  const filteredConsultants = useMemo(() => {
    const term = consultantSearch.trim().toLowerCase();
    if (!term) return sortedConsultants;
    return sortedConsultants.filter((row) => {
      const haystack = `${row.full_name} ${row.email || ""} ${row.location || ""}`.toLowerCase();
      return haystack.includes(term);
    });
  }, [sortedConsultants, consultantSearch]);

  const cvSummary = useMemo(() => extractSummary(consultantCv), [consultantCv]);
  const cvSkills = useMemo(() => extractSkills(consultantCv), [consultantCv]);
  const availableResumes = useMemo(() => consultantCv?.resumes || [], [consultantCv]);
  const selectedResumeMeta = useMemo(() => {
    if (!consultantCv || availableResumes.length === 0) return null;
    const selectedId = selectedResumeId ? Number(selectedResumeId) : consultantCv.selected_resume_id;
    for (const resume of availableResumes) {
      const typed = resume as { id?: number; viewUrl?: string; publicViewUrl?: string; title?: string };
      if (typeof typed.id === "number" && typeof selectedId === "number" && typed.id === selectedId) {
        return typed;
      }
    }
    return (availableResumes[0] as { id?: number; viewUrl?: string; publicViewUrl?: string; title?: string }) || null;
  }, [consultantCv, availableResumes, selectedResumeId]);
  const keywordSuggestions = useMemo(
    () => suggestions.filter((item) => String(item.section_type || "").trim().toLowerCase() === "keyword"),
    [suggestions]
  );
  const textSuggestions = useMemo(
    () => suggestions.filter((item) => String(item.section_type || "").trim().toLowerCase() !== "keyword"),
    [suggestions]
  );
  const summarySuggestionText = useMemo(() => {
    const summary = suggestions.find((item) => String(item.section_type || "").trim().toLowerCase() === "summary");
    if (!summary) return "";
    return (editing[summary.id] ?? summary.suggested_text ?? "").trim();
  }, [suggestions, editing]);
  const publishPresentationText = useMemo(
    () => buildPublishPresentationText(suggestions, editing),
    [suggestions, editing]
  );
  const selectedCredential = useMemo(
    () => cinodeCredentials.find((row) => row.id === selectedCredentialId) || null,
    [cinodeCredentials, selectedCredentialId]
  );
  const lastAutomationOk = useMemo(() => {
    const okValue = (browserPublishResponse as { ok?: unknown } | null)?.ok;
    return typeof okValue === "boolean" ? okValue : null;
  }, [browserPublishResponse]);
  const openaiApiKeyOverrideValue = useMemo(() => {
    const value = openaiApiKeyOverride.trim();
    return value ? value : undefined;
  }, [openaiApiKeyOverride]);
  const cinodeTokenOverrideValue = useMemo(() => {
    const value = cinodeTokenOverride.trim();
    return value ? value : undefined;
  }, [cinodeTokenOverride]);

  const refreshSuggestions = async (currentVariantId: string) => {
    const sug = await api.listSuggestions(currentVariantId);
    setSuggestions(sug);
    setEditing(Object.fromEntries(sug.map((item) => [item.id, item.suggested_text])));
  };

  const setErrorStatus = (error: unknown) => {
    setStatus(`Feil: ${normalizeErrorMessage(error)}`);
  };

  const autoSizeSuggestionEditor = (element: HTMLTextAreaElement) => {
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight}px`;
  };

  const uploadOpportunityTextFile = async () => {
    if (!opportunityFile) {
      setStatus("Feil: velg en fil først (.txt, .docx eller .pdf)");
      return;
    }

    try {
      setUploadingOpportunityFile(true);
      setStatus("Laster opp og henter tekst fra fil...");
      const extracted = await api.extractOpportunityText(opportunityFile);
      setJobText(extracted.text || "");
      const warnings = Array.isArray(extracted.warnings) ? extracted.warnings : [];
      setOpportunityFileWarnings(warnings);
      if (warnings.length > 0) {
        setStatus(
          `Fil importert (${extracted.detected_type}) med merknader: ${warnings.join(" | ")}`
        );
      } else {
        setStatus(`Fil importert (${extracted.detected_type}).`);
      }
    } catch (error) {
      setErrorStatus(error);
    } finally {
      setUploadingOpportunityFile(false);
    }
  };

  const refreshCinodeCredentials = async () => {
    try {
      const rows = await api.listCinodeCredentials();
      setCinodeCredentials(rows);

      if (rows.length === 0) {
        setSelectedCredentialId("");
        setConsultantFetchStatus("Ingen Cinode-tilganger funnet");
        return;
      }

      // Keep existing selection when still valid.
      if (selectedCredentialId && rows.some((row) => row.id === selectedCredentialId)) {
        return;
      }

      const defaultCredential = rows.find((row) => row.is_default);
      if (defaultCredential) {
        setSelectedCredentialId(defaultCredential.id);
      } else {
        setSelectedCredentialId(rows[0].id);
      }
    } catch (error) {
      setCinodeCredentials([]);
      setSelectedCredentialId("");
      const msg = normalizeErrorMessage(error);
      setConsultantFetchStatus(`Feil ved lasting av Cinode-tilgang: ${msg}`);
      setStatus(`Feil: ${msg}`);
    }
  };

  const fetchConsultants = async () => {
    if (!selectedCredentialId) {
      return;
    }

    try {
      setConsultantsLoading(true);
      setConsultantFetchStatus("Henter konsulenter fra Cinode...");
      const result = await api.fetchCinodeConsultants(
        selectedCredentialId,
        false,
        500,
        undefined,
        cinodeTokenOverrideValue
      );
      setConsultants(result.consultants);

      const keys = new Set(result.consultants.map((row) => consultantKey(row)));
      if (quickConsultantKey && !keys.has(quickConsultantKey)) {
        setQuickConsultantKey("");
        setConsultantCv(null);
        setConsultantEnrichment(null);
        setEnrichmentSummary([]);
        setPublicStatus(null);
        setSelectedResumeId("");
      }

      if (result.restricted_to_self) {
        const who = (result.current_user_name || "").trim() || (result.current_user_id || "").trim() || "innlogget bruker";
        setConsultantFetchStatus(`Begrenset tilgang: viser kun ${who}.`);
      } else {
        setConsultantFetchStatus(`Fant ${result.total} konsulenter`);
      }
    } catch (error) {
      setConsultantFetchStatus(`Feil: ${normalizeErrorMessage(error)}`);
      setErrorStatus(error);
    } finally {
      setConsultantsLoading(false);
    }
  };

  const bootstrapCinodeTokenFromBrowser = async () => {
    try {
      setCinodeTokenBootstrapLoading(true);
      setCinodeTokenBootstrapStatus("Oppretter Cinode API-token via nettleser...");
      setStatus("Starter Cinode token-bootstrap...");

      const result = await api.bootstrapCinodeTokenViaBrowser({
        company_slug: selectedCompanySlug,
        api_account_name: "Cinode_key",
        credential_label: "Cinode (browser bootstrap)",
        set_default: true,
        timeout_ms: 180000,
      });

      await refreshCinodeCredentials();
      if (result.credential_id) {
        setSelectedCredentialId(result.credential_id);
      }

      // Fill the UI token field with the newly created token.
      const bootstrapToken = (result.authorization_value || "").trim();
      if (bootstrapToken) {
        setCinodeTokenOverride(bootstrapToken);
        localStorage.setItem(CINODE_TOKEN_OVERRIDE_STORAGE_KEY, bootstrapToken);
      } else {
        setCinodeTokenOverride("");
        localStorage.setItem(CINODE_TOKEN_OVERRIDE_STORAGE_KEY, "");
      }

      if (result.ok) {
        setCinodeTokenBootstrapStatus(`OK: ${result.detail}`);
        setStatus("Cinode-token opprettet og lagret i tilgangslisten.");
      } else {
        setCinodeTokenBootstrapStatus(`Feil: ${result.detail}`);
        setStatus(`Feil: ${result.detail}`);
      }
    } catch (error) {
      const msg = normalizeErrorMessage(error);
      setCinodeTokenBootstrapStatus(`Feil: ${msg}`);
      setErrorStatus(error);
    } finally {
      setCinodeTokenBootstrapLoading(false);
    }
  };

  useEffect(() => {
    const loadModels = async () => {
      const maxAttempts = 3;
      for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
        try {
          const response = await api.getOpenAIModels();
          const models = response.allowed_models.length > 0 ? response.allowed_models : ["gpt-4.1-mini"];
          setAllowedModels(models);
          setSuggestionMode(response.suggestion_mode || "heuristic");
          setSuggestionModeReason(response.suggestion_mode_reason || "");

          const fromStorage = localStorage.getItem(MODEL_STORAGE_KEY);
          if (fromStorage && models.includes(fromStorage)) {
            setSelectedModel(fromStorage);
            return;
          }

          const fallback = models.includes(response.default_model) ? response.default_model : models[0];
          setSelectedModel(fallback);
          localStorage.setItem(MODEL_STORAGE_KEY, fallback);
          return;
        } catch {
          if (attempt < maxAttempts) {
            await new Promise((resolve) => window.setTimeout(resolve, 1000));
            continue;
          }
          // Keep fallback model in local state if config endpoint fails.
          setSuggestionMode("heuristic");
          setSuggestionModeReason("Kunne ikke laste config fra API");
        }
      }
    };

    void loadModels();
    void refreshCinodeCredentials();
  }, []);

  useEffect(() => {
    const maybeLegacy = suggestionPrompt.trim();
    if (maybeLegacy.startsWith("You are a senior bid consultant")) {
      setSuggestionPrompt(DEFAULT_SUGGESTION_PROMPT);
      localStorage.setItem(PROMPT_STORAGE_KEY, DEFAULT_SUGGESTION_PROMPT);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedCredentialId) {
      setConsultants([]);
      setQuickConsultantKey("");
      setConsultantCv(null);
      setConsultantEnrichment(null);
      setEnrichmentSummary([]);
      setPublicStatus(null);
      setSelectedResumeId("");
      setConsultantFetchStatus("Ingen aktiv tilgang");
      return;
    }

    const timer = window.setTimeout(() => {
      void fetchConsultants();
    }, 500);

    return () => window.clearTimeout(timer);
  }, [selectedCredentialId, cinodeTokenOverrideValue]);

  useEffect(() => {
    const editors = document.querySelectorAll<HTMLTextAreaElement>(".suggestion-editor");
    editors.forEach((editor) => autoSizeSuggestionEditor(editor));
  }, [suggestions, editing]);

  const runFlow = async () => {
    try {
      const selectedResumeIdForRun = selectedResumeId ? Number(selectedResumeId) : consultantCv?.selected_resume_id;
      if (!quickConsultantKey || !consultantCv || !selectedResumeIdForRun) {
        setStatus("Feil: velg en konsulent og en utgangspunkt-CV først");
        return;
      }

      const consultantName = String(consultantCv.full_name || "").trim();
      const consultantEmail = String(consultantCv.email || "").trim();
      if (!consultantName || !consultantEmail) {
        setStatus("Feil: valgt konsulent mangler navn eller e-post");
        return;
      }

      setStatus("Oppretter ansatt...");
      let employee;
      try {
        employee = await api.createEmployee(consultantName, consultantEmail);
      } catch (error) {
        const message = error instanceof Error ? error.message : "";
        if (message.includes("already exists")) {
          const matches = await api.findEmployeeByEmail(consultantEmail);
          if (matches.length === 0) {
            throw error;
          }
          employee = matches[0];
          setStatus("Fant eksisterende ansatt, fortsetter...");
        } else {
          throw error;
        }
      }

      setStatus("Importer profil (Cinode-datauttrekk)...");
      const payload = buildCinodePayloadFromCv(consultantCv);
      const snapshot = await api.importCinode(employee.id, payload);

      setStatus("Oppretter utlysning...");
      const opportunity = await api.createOpportunity(jobTitle, jobText);

      setStatus("Analyserer utlysning...");
      await api.analyzeOpportunityWithModel(opportunity.id, selectedModel, openaiApiKeyOverrideValue);
      const reqs = await api.listRequirements(opportunity.id);
      setRequirements(reqs);

      setStatus("Oppretter CV-variant...");
      const variantTitle = `${consultantName} - ${jobTitle}`.trim();
      const variant = await api.createVariant(employee.id, opportunity.id, snapshot.id, variantTitle);
      setVariantId(variant.id);

      setStatus(testModeHeuristicOnly ? "Genererer forslag (testmodus: heuristikk)..." : "Genererer forslag...");
      await api.generateSuggestionsWithModel(
        variant.id,
        selectedModel,
        suggestionPrompt,
        testModeHeuristicOnly,
        openaiApiKeyOverrideValue
      );
      await refreshSuggestions(variant.id);

      setBrowserPublishResponse(null);
      setPublishUiMessage("Forslag er klare. Du kan nå opprette ny CV i Cinode.");
      setLastPublished(null);
      setStatus("Ferdig");
    } catch (error) {
      setErrorStatus(error);
    }
  };

  const updateSuggestion = async (suggestionId: string, statusValue: "pending" | "accepted" | "rejected") => {
    if (!variantId) return;

    try {
      setSavingSuggestion({ id: suggestionId, status: statusValue });
      setSuggestionFeedback((prev) => ({ ...prev, [suggestionId]: "Lagrer endring..." }));
      setSuggestions((prev) =>
        prev.map((item) =>
          item.id === suggestionId
            ? { ...item, status: statusValue, suggested_text: editing[suggestionId] ?? item.suggested_text }
            : item
        )
      );
      setStatus(`Oppdaterer forslag (${suggestionStatusLabel(statusValue).toLowerCase()})...`);
      await api.updateSuggestion(variantId, suggestionId, statusValue, editing[suggestionId]);
      await refreshSuggestions(variantId);
      const now = new Date();
      const time = now.toLocaleTimeString("nb-NO", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      setSuggestionFeedback((prev) => ({
        ...prev,
        [suggestionId]: `Lagret: ${suggestionStatusLabel(statusValue)} kl. ${time}`,
      }));
      setStatus("Forslag oppdatert");
    } catch (error) {
      setSuggestionFeedback((prev) => ({ ...prev, [suggestionId]: `Kunne ikke lagre: ${normalizeErrorMessage(error)}` }));
      setErrorStatus(error);
    } finally {
      setSavingSuggestion(null);
    }
  };

  const publishViaBrowserAutomation = async () => {
    if (!variantId) {
      setPublishUiMessage("Kjør Generer oppdatert CV text først.");
      setStatus("Feil: kjør Generer oppdatert CV text først");
      return;
    }
    if (!selectedCredentialId || !quickConsultantKey) {
      setPublishUiMessage("Velg Cinode-tilgang og konsulent før oppretting.");
      setStatus("Feil: velg både Cinode-tilgang og konsulent først");
      return;
    }
    const consultantId = consultantIdFromKey(quickConsultantKey);
    if (!consultantId) {
      setPublishUiMessage("Fant ikke konsulent-id. Velg konsulent på nytt.");
      setStatus("Feil: mangler konsulent-id");
      return;
    }

    const resumeId = selectedResumeId ? Number(selectedResumeId) : consultantCv?.selected_resume_id ?? undefined;

    try {
      setAutomatingBrowserPublish(true);
      setOpeningCinodeLogin(true);
      setPublishUiMessage("Starter innlogging og automasjon i Cinode...");
      setStatus("Starter Cinode-login for automasjon...");
      setBrowserPublishResponse(null);
      try {
        const loginResult = await api.loginCinodeBrowserProfile(selectedCredentialId, selectedCompanySlug);
        if (!loginResult.ok) {
          setPublishUiMessage(`Innlogging ikke fullført (${loginResult.detail}). Fortsetter...`);
          setStatus(`Innlogging ikke fullført (${loginResult.detail}). Fortsetter med CV-oppretting...`);
        } else {
          setPublishUiMessage("Innlogging OK. Oppretter ny CV...");
          setStatus("Innlogging OK. Starter CV-oppretting...");
        }
      } catch (loginError) {
        const msg = normalizeErrorMessage(loginError);
        setPublishUiMessage(`Login-sjekk feilet (${msg}). Fortsetter...`);
        setStatus(`Login-sjekk feilet (${msg}). Fortsetter med CV-oppretting...`);
      }

      setPublishUiMessage("Kjører preflight-sjekk for tilgang til valgt konsulent-CV...");
      setStatus("Kjører preflight-sjekk (tilgang/scope)...");
      const preflight = await api.preflightConsultantBrowserAccess(selectedCredentialId, consultantId, {
        resume_id: resumeId,
        company_slug: selectedCompanySlug,
        cinode_token_override: cinodeTokenOverrideValue,
      });
      if (!preflight.ok) {
        setBrowserPublishResponse(preflight as unknown as Record<string, unknown>);
        setPublishUiMessage(`Preflight feilet: ${preflight.detail}`);
        setStatus(`Preflight feilet: ${preflight.detail}`);
        return;
      }

      setPublishUiMessage("Oppretter ny CV i Cinode...");
      setStatus("Kjører full automasjon i Cinode (oppretter ny CV via nettleser)...");
      if (!publishPresentationText) {
        setPublishUiMessage("Fant ikke publiseringstekst i forslagene. Bruker variant-sammendrag fra backend.");
      }
      const result = await api.createConsultantCvViaBrowser(selectedCredentialId, consultantId, {
        variant_id: variantId,
        resume_id: resumeId,
        cinode_token_override: cinodeTokenOverrideValue,
        title_override: jobTitle.trim() || undefined,
        company_slug: selectedCompanySlug,
        presentation_text_override: publishPresentationText || undefined,
        apply_keywords: true,
        clean_new_cv_content: false,
        enforce_selected_resume_source: true,
        strict_deterministic_mode: true,
      });
      setBrowserPublishResponse(result as unknown as Record<string, unknown>);
      if (result.ok) {
        setLastPublished({
          variantId,
          summaryText: publishPresentationText || "",
          createdResumeUrl: result.created_resume_url || undefined,
          publishedAt: new Date().toISOString(),
        });
        if (quickConsultantKey) {
          await loadConsultantCv(quickConsultantKey);
        }
        setPublishUiMessage("Programmet er kjørt, og ny CV eksisterer nå i Cinode.");
        setStatus(
          `Automasjon fullført: ${result.detail}${result.created_resume_url ? ` (Ny side: ${result.created_resume_url})` : ""}`
        );
      } else {
        setPublishUiMessage(`Automasjon ikke fullført: ${result.detail}`);
        setStatus(`Automasjon ikke fullført: ${result.detail}`);
      }
    } catch (error) {
      setBrowserPublishResponse({ ok: false, detail: normalizeErrorMessage(error) });
      setPublishUiMessage(`Feil: ${normalizeErrorMessage(error)}`);
      setErrorStatus(error);
    } finally {
      setOpeningCinodeLogin(false);
      setAutomatingBrowserPublish(false);
    }
  };

  const loadConsultantCv = async (consultantKeyValue: string, resumeId?: number) => {
    if (!selectedCredentialId) {
      setStatus("Velg tilgang først");
      return;
    }
    const consultantId = consultantIdFromKey(consultantKeyValue);
    if (!consultantId) {
      setStatus("Feil: mangler konsulent-id");
      return;
    }

    try {
      setStatus("Henter CV-data fra Cinode...");
      const result = await api.fetchCinodeConsultantCv(
        selectedCredentialId,
        consultantId,
        resumeId,
        cinodeTokenOverrideValue
      );
      setConsultantCv(result);
      setConsultantEnrichment(null);
      setEnrichmentSummary([]);
      setSelectedResumeId(result.selected_resume_id ? String(result.selected_resume_id) : "");
      setCheckingPublicStatus(true);
      const statusResult = await api.getConsultantPublicStatus(
        selectedCredentialId,
        consultantId,
        result.selected_resume_id ?? undefined,
        cinodeTokenOverrideValue
      );
      setPublicStatus(statusResult);
      const resumeInfo = result.selected_resume_id ? ` (CV-id: ${result.selected_resume_id})` : "";
      setStatus(`CV hentet for ${result.full_name}${resumeInfo}`);
    } catch (error) {
      setConsultantCv(null);
      setConsultantEnrichment(null);
      setEnrichmentSummary([]);
      setPublicStatus(null);
      setErrorStatus(error);
    } finally {
      setCheckingPublicStatus(false);
    }
  };

  const downloadSelectedCv = () => {
    if (!selectedCredentialId || !quickConsultantKey) return;
    const consultantId = consultantIdFromKey(quickConsultantKey);
    if (!consultantId) return;
    const resumeId = selectedResumeId ? Number(selectedResumeId) : undefined;
    const url = api.consultantCvDocxUrl(selectedCredentialId, consultantId, resumeId, cinodeTokenOverrideValue);
    window.open(url, "_blank");
  };

  const refreshPublicStatus = async () => {
    if (!selectedCredentialId || !quickConsultantKey) return;
    const consultantId = consultantIdFromKey(quickConsultantKey);
    if (!consultantId) return;

    const resumeId = selectedResumeId ? Number(selectedResumeId) : consultantCv?.selected_resume_id ?? undefined;
    try {
      setCheckingPublicStatus(true);
      const statusResult = await api.getConsultantPublicStatus(
        selectedCredentialId,
        consultantId,
        resumeId,
        cinodeTokenOverrideValue
      );
      setPublicStatus(statusResult);
      setStatus(
        statusResult.public_ready
          ? "Offentlig-status: OK"
          : `Offentlig-status: ikke klar${statusResult.status_code ? ` (HTTP ${statusResult.status_code})` : ""}`
      );
    } catch (error) {
      setErrorStatus(error);
    } finally {
      setCheckingPublicStatus(false);
    }
  };

  const downloadCinodeLayoutPdf = (preferPublicUrl = true) => {
    if (!selectedCredentialId || !quickConsultantKey) return;
    const consultantId = consultantIdFromKey(quickConsultantKey);
    if (!consultantId) return;
    const resumeId = selectedResumeId ? Number(selectedResumeId) : undefined;
    const url = api.consultantCvBrowserPdfUrl(
      selectedCredentialId,
      consultantId,
      resumeId,
      preferPublicUrl,
      cinodeTokenOverrideValue
    );
    window.open(url, "_blank");
  };

  return (
    <main className="shell">
      <section className="card card-results">
        <div className="brand-row">
          <div className="brand-logo-wrap">
            <img src="/logo-white.svg" alt="XLENT" className="brand-logo" />
          </div>
          <h1>Cinode CV matcher</h1>
        </div>
        <details className="settings-panel" open={settingsExpanded} onToggle={(e) => setSettingsExpanded((e.target as HTMLDetailsElement).open)}>
          <summary>Innstillinger</summary>
          <p>
            API-base: <code>{API_BASE}</code>
          </p>
          <p>
            <strong>Forslagsmotor:</strong>{" "}
            {testModeHeuristicOnly ? "Heuristikk (testmodus)" : suggestionMode === "llm" ? "LLM (OpenAI)" : "Heuristikk"}{" "}
            <span className="muted">({suggestionModeReason})</span>
          </p>
          <label>
            OpenAI-modell
            <select
              value={selectedModel}
              onChange={(e) => {
                setSelectedModel(e.target.value);
                localStorage.setItem(MODEL_STORAGE_KEY, e.target.value);
              }}
            >
              {allowedModels.map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
            </select>
          </label>
          <label>
            OpenAI API key (overstyring, valgfri)
            <input
              type="password"
              value={openaiApiKeyOverride}
              placeholder="sk-..."
              onChange={(e) => {
                const value = e.target.value;
                setOpenaiApiKeyOverride(value);
                localStorage.setItem(OPENAI_API_KEY_OVERRIDE_STORAGE_KEY, value);
              }}
            />
          </label>
          <p className="muted">
            Tomt felt bruker OPENAI_API_KEY fra <code>.env</code>. Utfylt felt overstyrer nøkkelen for analyse og
            forslag.
          </p>
          <label>
            Prompt for LLM-forslag
            <textarea
              rows={12}
              value={suggestionPrompt}
              onChange={(e) => {
                setSuggestionPrompt(e.target.value);
                localStorage.setItem(PROMPT_STORAGE_KEY, e.target.value);
              }}
            />
          </label>
          <div className="actions">
            <button
              className="ghost"
              onClick={() => {
                setSuggestionPrompt(DEFAULT_SUGGESTION_PROMPT);
                localStorage.setItem(PROMPT_STORAGE_KEY, DEFAULT_SUGGESTION_PROMPT);
              }}
            >
              Reset prompt til standard
            </button>
          </div>
          <label>
            <input
              type="checkbox"
              checked={testModeHeuristicOnly}
              onChange={(e) => {
                const checked = e.target.checked;
                setTestModeHeuristicOnly(checked);
                localStorage.setItem(TESTMODE_STORAGE_KEY, checked ? "true" : "false");
              }}
            />{" "}
            Testmodus: hopp over LLM og bruk kun heuristikk
          </label>

          <div className="separator" />
          <h3>Cinode-tilgang</h3>
          <p className="muted">
            Standard-tilgang hentes fra valgt credential (typisk <code>.env</code>). Hvis feltet under er utfylt,
            overstyres tokenet i alle Cinode-kall fra appen.
          </p>
          <label>
            Cinode API-token (overstyring, valgfri)
            <input
              type="password"
              value={cinodeTokenOverride}
              placeholder="Bearer ... eller Basic ..."
              onChange={(e) => {
                const value = e.target.value;
                setCinodeTokenOverride(value);
                localStorage.setItem(CINODE_TOKEN_OVERRIDE_STORAGE_KEY, value);
              }}
            />
          </label>
          <div className="actions">
            <button
              className="ghost"
              type="button"
              onClick={() => void bootstrapCinodeTokenFromBrowser()}
              disabled={cinodeTokenBootstrapLoading}
            >
              {cinodeTokenBootstrapLoading
                ? "Oppretter Cinode-token..."
                : "Opprett Cinode-token via nettleser"}
            </button>
          </div>
          {cinodeTokenBootstrapStatus && <p className="muted">{cinodeTokenBootstrapStatus}</p>}
          {cinodeCredentials.length > 1 && (
            <label>
              Aktiv Cinode-tilgang
              <select value={selectedCredentialId} onChange={(e) => setSelectedCredentialId(e.target.value)}>
                {cinodeCredentials.map((cred) => (
                  <option key={cred.id} value={cred.id}>
                    {cred.label} {cred.is_default ? "(standard)" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          <p className="muted">
            Konsulentlisten oppdateres automatisk når tilgang endres.
            {consultantsLoading ? " Laster..." : ""}
          </p>
          <p className="muted">
            <strong>Konsulentstatus:</strong> {consultantFetchStatus}
          </p>
          <details>
            <summary>Driftsstatus</summary>
            <p className="muted">
              <strong>Forslagsmotor:</strong>{" "}
              {testModeHeuristicOnly ? "Heuristikk (testmodus)" : suggestionMode === "llm" ? "LLM (OpenAI)" : "Heuristikk"}{" "}
              <span className="muted">({suggestionModeReason})</span>
            </p>
            <p className="muted">
              <strong>Cinode credential:</strong>{" "}
              {selectedCredential ? (
                <>
                  <code>{selectedCredential.label}</code>
                  {selectedCredential.is_default ? " (standard)" : ""}
                </>
              ) : (
                "Ikke valgt"
              )}
            </p>
            <p className="muted">
              <strong>Cinode token override aktiv:</strong> {cinodeTokenOverrideValue ? "Ja" : "Nei"}
            </p>
            <p className="muted">
              <strong>Credential test:</strong>{" "}
              {selectedCredential?.last_test_status
                ? `${selectedCredential.last_test_status}${selectedCredential.last_test_message ? ` - ${selectedCredential.last_test_message}` : ""}`
                : "Ikke testet"}
            </p>
            <p className="muted">
              <strong>Konsulenter lastet:</strong> {consultants.length}
              {consultantsLoading ? " (laster...)" : ""}
            </p>
            <p className="muted">
              <strong>Siste automasjon:</strong>{" "}
              {lastAutomationOk === null ? "Ikke kjørt" : lastAutomationOk ? "OK" : "Feilet"}
            </p>
          </details>
        </details>

        <div className="field-row field-row-three">
          <label>
            Konsulent fra Cinode
            <select
              value={quickConsultantKey}
              onChange={(e) => {
                const key = e.target.value;
                setQuickConsultantKey(key);
                if (key) {
                  void loadConsultantCv(key);
                } else {
                  setConsultantCv(null);
                  setConsultantEnrichment(null);
                  setPublicStatus(null);
                  setSelectedResumeId("");
                }
              }}
            >
              <option value="">-- velg konsulent --</option>
              {filteredConsultants.map((row) => {
                const key = consultantKey(row);
                const desc = row.location ? `${row.full_name} (${row.location})` : row.full_name;
                return (
                  <option key={key} value={key}>
                    {desc}
                  </option>
                );
              })}
            </select>
          </label>
          <label>
            Søk i konsulenter
            <input
              value={consultantSearch}
              onChange={(e) => setConsultantSearch(e.target.value)}
              placeholder="Søk på navn, e-post eller lokasjon"
            />
          </label>

          <label>
            Organisasjon (for Cinode-mal)
            <select value={selectedCompanySlug} onChange={(e) => setSelectedCompanySlug(e.target.value as CompanySlug)}>
              <option value="xlent">XLENT</option>
              <option value="differ">Differ</option>
              <option value="folden">Folden</option>
            </select>
          </label>
        </div>
        {consultantSearch.trim() && (
          <p className="muted">
            Viser {filteredConsultants.length} av {sortedConsultants.length} konsulenter.
          </p>
        )}
        {sortedConsultants.length === 0 && (
          <p className="muted">{consultantsLoading ? "Henter konsulenter..." : "Ingen konsulenter funnet for valgt tilgang."}</p>
        )}

        {availableResumes.length > 0 && (
          <div className="resume-row">
            <label className="resume-label">
              Utgangspunkt-CV fra Cinode
              <select
                value={selectedResumeId}
                onChange={(e) => {
                  const value = e.target.value;
                  setSelectedResumeId(value);
                  if (quickConsultantKey && value) {
                    setStatus("Laster valgt utgangspunkt-CV...");
                    void loadConsultantCv(quickConsultantKey, Number(value));
                  }
                }}
              >
                {availableResumes.map((resume) => {
                  const id = String((resume as { id?: number }).id || "");
                  const title = String((resume as { title?: string }).title || `CV ${id}`);
                  return (
                    <option key={id} value={id}>
                      {title}
                    </option>
                  );
                })}
              </select>
            </label>
          </div>
        )}
        {consultantCv && (
          <>
            <details className="settings-panel">
              <summary>Nedlasting og Cinode-lenker</summary>
              <p>
                <strong>Klar for offentlig eksport:</strong>{" "}
                {checkingPublicStatus
                  ? "Sjekker..."
                  : publicStatus?.public_ready
                    ? "Ja - offentlig lenke er tilgjengelig"
                    : `Nei${publicStatus?.status_code ? ` (HTTP ${publicStatus.status_code})` : ""}${
                        publicStatus?.detail ? `: ${publicStatus.detail}` : ""
                      }`}
              </p>
              <div className="actions">
                <button className="ghost" onClick={() => void refreshPublicStatus()} disabled={checkingPublicStatus}>
                  Sjekk offentlig-status
                </button>
                <button className="ghost" onClick={downloadSelectedCv}>
                  Last ned CV (.docx)
                </button>
                <button className="ghost" onClick={() => downloadCinodeLayoutPdf(true)}>
                  Last ned Cinode-layout (PDF)
                </button>
                <button
                  className="ghost"
                  onClick={() => {
                    const publicUrl = selectedResumeMeta?.publicViewUrl;
                    if (publicUrl) {
                      window.open(publicUrl, "_blank");
                    }
                  }}
                  disabled={!selectedResumeMeta?.publicViewUrl}
                >
                  Åpne offentlig CV i Cinode
                </button>
                <button
                  className="ghost"
                  onClick={() => {
                    const viewUrl = selectedResumeMeta?.viewUrl;
                    if (viewUrl) {
                      window.open(viewUrl, "_blank");
                    }
                  }}
                  disabled={!selectedResumeMeta?.viewUrl}
                >
                  Åpne original CV i Cinode
                </button>
              </div>
              {enrichmentSummary.length > 0 && (
                <>
                  <p>
                    <strong>Lastet inn ved berikelse:</strong>
                  </p>
                  <ul>
                    {enrichmentSummary.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </>
              )}
              <div className="separator" />
              <h3>Valgt CV</h3>
              {!consultantCv && <p>Velg en konsulent for å hente og vise CV.</p>}
              {consultantCv && (
                <>
                  <p>
                    <strong>Navn:</strong> {consultantCv.full_name}
                  </p>
                  <p>
                    <strong>Tittel:</strong> {consultantCv.title || "-"}
                  </p>
                  <p>
                    <strong>Lokasjon:</strong> {consultantCv.location || "-"}
                  </p>
                  <p>
                    <strong>Kilde:</strong> <code>{consultantCv.source_path}</code>
                  </p>
                  {selectedResumeMeta?.viewUrl && (
                    <p>
                      <strong>URL for original CV:</strong> <code>{selectedResumeMeta.viewUrl}</code>
                    </p>
                  )}
                  {selectedResumeMeta?.publicViewUrl && (
                    <p>
                      <strong>URL for offentlig CV:</strong> <code>{selectedResumeMeta.publicViewUrl}</code>
                    </p>
                  )}
                  <h4>Sammendrag</h4>
                  <p>{cvSummary || "Ingen sammendrag funnet i valgt resume."}</p>
                  <h4>Kompetanse</h4>
                  <ul>
                    {cvSkills.map((skill) => (
                      <li key={skill}>{skill}</li>
                    ))}
                  </ul>
                  {consultantEnrichment && (
                    <>
                      <h4>Beriket kandidatdata</h4>
                      {consultantEnrichment.linkedin_url && (
                        <p>
                          <strong>LinkedIn:</strong> <code>{consultantEnrichment.linkedin_url}</code>
                        </p>
                      )}
                      {consultantEnrichment.scholar_url && (
                        <p>
                          <strong>Google Scholar:</strong> <code>{consultantEnrichment.scholar_url}</code>
                        </p>
                      )}
                      {consultantEnrichment.github_url && (
                        <p>
                          <strong>GitHub:</strong> <code>{consultantEnrichment.github_url}</code>
                        </p>
                      )}
                      {consultantEnrichment.orcid_url && (
                        <p>
                          <strong>ORCID:</strong> <code>{consultantEnrichment.orcid_url}</code>
                        </p>
                      )}
                      {consultantEnrichment.researchgate_url && (
                        <p>
                          <strong>ResearchGate:</strong> <code>{consultantEnrichment.researchgate_url}</code>
                        </p>
                      )}
                      {consultantEnrichment.external_findings.length > 0 && (
                        <>
                          <h4>Eksterne funn</h4>
                          <ul>
                            {consultantEnrichment.external_findings.map((finding, index) => {
                              const source = String(finding.source_type || "").trim() || "kilde";
                              const confidence =
                                typeof finding.confidence === "number"
                                  ? ` (score ${finding.confidence.toFixed(2)})`
                                  : "";
                              const firstFact =
                                Array.isArray(finding.facts) && finding.facts.length > 0
                                  ? String(finding.facts[0] || "").trim()
                                  : "";
                              const url = String(finding.url || "").trim();
                              const detail = [firstFact, url].filter(Boolean).join(" | ");
                              return (
                                <li key={`${source}-${index}`}>
                                  {source}
                                  {confidence}
                                  {detail ? `: ${detail}` : ""}
                                </li>
                              );
                            })}
                          </ul>
                        </>
                      )}
                      {consultantEnrichment.candidate_facts.length > 0 && (
                        <>
                          <h4>Fakta til LLM-input</h4>
                          <ul>
                            {consultantEnrichment.candidate_facts.map((fact) => (
                              <li key={fact}>{fact}</li>
                            ))}
                          </ul>
                        </>
                      )}
                      {consultantEnrichment.scholar_publications.length > 0 && (
                        <>
                          <h4>Utvalgte publikasjoner</h4>
                          <ul>
                            {consultantEnrichment.scholar_publications.map((pub) => (
                              <li key={pub}>{pub}</li>
                            ))}
                          </ul>
                        </>
                      )}
                      {consultantEnrichment.warnings.length > 0 && (
                        <>
                          <h4>Merknader</h4>
                          <ul>
                            {consultantEnrichment.warnings.map((warning) => (
                              <li key={warning}>{warning}</li>
                            ))}
                          </ul>
                        </>
                      )}
                    </>
                  )}
                  <details>
                    <summary>Vis rå CV-data (JSON)</summary>
                    <pre>{JSON.stringify(consultantCv.resume || consultantCv.profile, null, 2)}</pre>
                  </details>
                </>
              )}
            </details>
          </>
        )}

        <section className="job-brief-section">
          <h3>Utlysningsgrunnlag</h3>
          <p className="muted">Lim inn utlysningstittel og utlysningstekst her.</p>
          <label>
            Utlysningstittel / CV-navn ved publisering
            <input value={jobTitle} onChange={(e) => setJobTitle(e.target.value)} />
          </label>
          <div className="job-text-row">
            <label>
              Utlysningstekst
              <textarea rows={7} value={jobText} onChange={(e) => setJobText(e.target.value)} />
            </label>
            <div className="job-upload-panel">
              <label>
                Last opp utlysning (.txt/.docx/.pdf)
                <input
                  type="file"
                  accept=".txt,.docx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain"
                  onChange={(e) => {
                    const file = e.target.files?.[0] || null;
                    setOpportunityFile(file);
                    setOpportunityFileWarnings([]);
                  }}
                />
              </label>
              <button
                className="ghost"
                type="button"
                onClick={() => void uploadOpportunityTextFile()}
                disabled={!opportunityFile || uploadingOpportunityFile}
              >
                {uploadingOpportunityFile ? "Laster opp..." : "Hent tekst fra fil"}
              </button>
              {opportunityFile && (
                <p className="muted">
                  Valgt fil: <code>{opportunityFile.name}</code>
                </p>
              )}
              {opportunityFileWarnings.length > 0 && (
                <>
                  <p className="muted">
                    <strong>Merknader:</strong>
                  </p>
                  <ul>
                    {opportunityFileWarnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          </div>
        </section>

        <div className="actions">
          <button onClick={runFlow}>Generer oppdatert CV text</button>
          <p className="muted inline-status">
            <strong>Status:</strong> {status}
            {showGeneratingSpinner && <span className="status-spinner" aria-label="Laster" />}
          </p>
        </div>
      </section>

      {variantId && (
      <section className="card card-results">
        <h2>Resultater</h2>

        <h3>Krav</h3>
        <ul>
          {requirements.map((req) => (
            <li key={req.id}>
              [{req.category}] {req.text} ({req.weight.toFixed(2)})
            </li>
          ))}
        </ul>

        <h3>Nøkkelord fra krav (Teknikker/Verktøy)</h3>
        {keywordSuggestions.length === 0 && <p className="muted">Ingen nøkkelord foreslått ennå.</p>}
        {keywordSuggestions.map((item) => {
          const proposedText = editing[item.id] ?? item.suggested_text;
          return (
            <article key={item.id} className="suggestion suggestion-card">
              <header className="suggestion-head">
                <div className="suggestion-meta">
                  <span className="chip">keyword</span>
                  <span className={`chip ${item.status === "accepted" ? "chip-ok" : item.status === "rejected" ? "chip-reject" : "chip-muted"}`}>
                    {suggestionStatusLabel(item.status)}
                  </span>
                </div>
              </header>
              <p>
                <strong>Foreslått nøkkelord:</strong> <code>{proposedText}</code>
              </p>
              {item.original_text && (
                <p className="muted">
                  <strong>Kildekrav:</strong> {item.original_text}
                </p>
              )}
              <div className="actions suggestion-actions">
                <button onClick={() => updateSuggestion(item.id, "accepted")} disabled={savingSuggestion?.id === item.id}>
                  {savingSuggestion?.id === item.id && savingSuggestion.status === "accepted" ? "Lagrer..." : "Godta"}
                </button>
                <button className="ghost" onClick={() => updateSuggestion(item.id, "rejected")} disabled={savingSuggestion?.id === item.id}>
                  {savingSuggestion?.id === item.id && savingSuggestion.status === "rejected" ? "Lagrer..." : "Avvis"}
                </button>
              </div>
              {suggestionFeedback[item.id] && <p className="muted suggestion-save-feedback">{suggestionFeedback[item.id]}</p>}
            </article>
          );
        })}

        <h3>Forslag (gjennomgang)</h3>
        {textSuggestions.map((item) => {
          const originalText = item.original_text || "";
          const proposedText = editing[item.id] ?? item.suggested_text;
          const hasChanged = proposedText.trim() !== originalText.trim();
          const delta = proposedText.length - originalText.length;

          return (
            <article key={item.id} className="suggestion suggestion-card">
              <header className="suggestion-head">
                <div className="suggestion-meta">
                  <span className="chip">{item.section_type}</span>
                  <span className={`chip ${item.status === "accepted" ? "chip-ok" : item.status === "rejected" ? "chip-reject" : "chip-muted"}`}>
                    {suggestionStatusLabel(item.status)}
                  </span>
                  <span className={`chip ${hasChanged ? "chip-ok" : "chip-muted"}`}>{hasChanged ? "Endret" : "Uendret"}</span>
                  <span className="chip chip-muted">Tegn: {delta >= 0 ? `+${delta}` : `${delta}`}</span>
                </div>
              </header>

              <details className="suggestion-rationale">
                <summary>Hvorfor dette forslaget</summary>
                <p className="muted">{item.rationale || "-"}</p>
              </details>

              <div className="suggestion-grid">
                <section className="suggestion-pane">
                  <h4 className="suggestion-pane-title">Original tekst</h4>
                  <div className="suggestion-text suggestion-text-original">{originalText || "-"}</div>
                </section>

                <section className="suggestion-pane">
                  <h4 className="suggestion-pane-title">Foreslått tekst (redigerbar)</h4>
                  <textarea
                    className="suggestion-editor"
                    rows={12}
                    value={proposedText}
                    onChange={(e) => {
                      setEditing((prev) => ({ ...prev, [item.id]: e.target.value }));
                      autoSizeSuggestionEditor(e.target);
                    }}
                  />
                </section>
              </div>

              <div className="actions suggestion-actions">
                <button
                  onClick={() => updateSuggestion(item.id, "accepted")}
                  disabled={savingSuggestion?.id === item.id}
                >
                  {savingSuggestion?.id === item.id && savingSuggestion.status === "accepted" ? "Lagrer..." : "Godta"}
                </button>
                <button
                  className="ghost"
                  onClick={() => updateSuggestion(item.id, "rejected")}
                  disabled={savingSuggestion?.id === item.id}
                >
                  {savingSuggestion?.id === item.id && savingSuggestion.status === "rejected" ? "Lagrer..." : "Avvis"}
                </button>
              </div>
              {suggestionFeedback[item.id] && <p className="muted suggestion-save-feedback">{suggestionFeedback[item.id]}</p>}
            </article>
          );
        })}

        <div className="separator" />
        <div className="actions">
          <button className="ghost" onClick={publishViaBrowserAutomation} disabled={automatingBrowserPublish}>
            {openingCinodeLogin
              ? "Åpner login-vindu..."
              : automatingBrowserPublish
                ? "Kjører automasjon..."
                : "Opprett ny CV i Cinode"}
          </button>
          {automatingBrowserPublish && <span className="status-spinner" aria-label="Automasjon kjører" />}
        </div>
        {publishUiMessage && <p className="muted">{publishUiMessage}</p>}
        {lastPublished && (
          <div className="muted">
            <p>
              <strong>Sist publisert variant:</strong> <code>{lastPublished.variantId}</code>
            </p>
            {lastPublished.createdResumeUrl && (
              <p>
                <strong>Ny CV-side:</strong> <code>{lastPublished.createdResumeUrl}</code>
              </p>
            )}
            <div className="actions">
              <button
                className="ghost"
                onClick={() => {
                  if (lastPublished.createdResumeUrl) {
                    window.open(lastPublished.createdResumeUrl, "_blank");
                  }
                }}
                disabled={!lastPublished.createdResumeUrl}
              >
                Åpne sist publiserte CV
              </button>
            </div>
            {(variantId !== lastPublished.variantId || (summarySuggestionText || "").trim() !== (lastPublished.summaryText || "").trim()) && (
              <p>
                <strong>Merk:</strong> Nåværende forslag i appen er endret etter siste publisering, og er ikke publisert ennå.
              </p>
            )}
          </div>
        )}

        {browserPublishResponse && (
          <details>
            <summary>Automasjonsrespons</summary>
            <pre>{JSON.stringify(browserPublishResponse, null, 2)}</pre>
          </details>
        )}

      </section>
      )}
    </main>
  );
}
