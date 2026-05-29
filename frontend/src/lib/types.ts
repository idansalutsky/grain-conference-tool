/**
 * Shared API response shapes. Frontend used `any` heavily before this; types
 * here let TanStack Query give us autocomplete + catch obvious typos.
 *
 * Keep loose — backend may add fields without breaking callers.
 */

export interface Conference {
  id: string;
  name: string;
  start_date?: string | null;
  end_date?: string | null;
  city?: string | null;
  country?: string | null;
  region?: string | null;
  vertical?: string | null;
  format?: string | null;
  estimated_attendance?: number | null;
  cost_pass_usd?: number | null;
  cost_booth_usd?: number | null;
  score?: number | null;
  tier?: "A" | "B" | "C" | null;
  themes?: string | null;
  website?: string | null;
  score_breakdown?: ScoreBreakdownData | null;
}

export interface ScoreBreakdownData {
  total: number;
  tier: string;
  factors: Array<{
    key: string;
    raw: number;
    weight: number;
    weighted: number;
    evidence: string;
  }>;
}

export interface Person {
  id: string;
  full_name: string;
  first_name?: string | null;
  last_name?: string | null;
  title?: string | null;
  company_name?: string | null;
  email?: string | null;
  linkedin_url?: string | null;
  vertical?: string | null;
  conference_id?: string | null;
  persona?: PersonaKind | null;
  persona_weight?: number | null;
  icp_score?: number | null;
}

export type PersonaKind =
  | "BUYER"
  | "CHAMPION"
  | "PAIN_OWNER"
  | "GATEKEEPER"
  | "ENTRY_POINT"
  | "INFLUENCER";

export type ArcKind = "warming" | "flat" | "cooling" | "tire_kicker";

export interface Contact {
  id: string;
  primary_name: string;
  primary_email?: string | null;
  primary_company?: string | null;
  primary_title?: string | null;
  arc_verdict?: ArcKind | null;
  arc_confidence?: number | null;
  arc_summary?: string | null;
  nudge_active?: number | null;
  nudge_text?: string | null;
  hubspot_contact_id?: string | null;
  updated_at?: string;
}

export interface Encounter {
  id: string;
  contact_id?: string | null;
  conference_id?: string | null;
  rep_id?: string | null;
  captured_at: string;
  capture_mode?: string;
  sentiment?: number | null;
  meeting_requested?: number | null;
  structured?: {
    name?: string | null;
    title?: string | null;
    company?: string | null;
    vertical?: string | null;
    what_discussed?: string | null;
  };
  soft_signals?: string[];
}

export interface CaptureResult {
  encounter_id: string;
  contact_id?: string | null;
  cascade_status?: "pending" | "complete" | "skipped";
  structured?: {
    name?: string | null;
    title?: string | null;
    company?: string | null;
    vertical?: string | null;
    sentiment?: number | null;
    soft_signals?: string[];
    meeting_requested?: boolean;
    what_discussed?: string | null;
  };
  arc?: {
    kind?: ArcKind;
    confidence?: number;
    summary?: string;
    from_prior_encounters?: boolean;
  } | null;
  nudge?: {
    nudge_active?: boolean;
    nudge_text?: string | null;
    why_suppressed?: string[];
  } | null;
  resolution?: {
    decision?: "created_new" | "auto_merged" | "review_needed" | "reject";
    contact_id?: string;
    candidate?: any;
  };
}

export interface AgentPlan {
  summary?: string;
  priority_order?: Array<{
    person_name: string;
    company: string;
    title?: string;
    priority: number;
    reason: string;
    has_brief?: boolean;
  }>;
  briefs_generated_count?: number;
  skipped_with_reason?: Array<{ person: string; reason: string }>;
  competitor_flags?: string[];
  positioning_notes?: string[];
}

export interface AgentTraceEntry {
  iteration: number;
  name: string;
  args?: Record<string, unknown>;
  result_summary: string;
}

export interface AgentResult {
  ok?: boolean;
  plan?: AgentPlan & { raw_text?: string };
  trace?: {
    iterations: number;
    tool_calls: AgentTraceEntry[];
    final_plan?: AgentPlan;
    conference?: { id: string; name: string };
  };
  error?: string;
}

export interface SearchResult {
  query: string;
  results: {
    conference?: Array<Conference & { score: number }>;
    person?: Array<Person & { score: number }>;
    contact?: Array<Contact & { score: number }>;
  };
}

export interface TodayPayload {
  rep_id: string;
  event: (Conference & {
    is_active_now?: boolean;
    is_explicit_bind?: boolean;
    days_until?: number | null;
  }) | null;
  targets: Array<Person & { has_brief?: boolean; brief_id?: string | null }>;
  nudges: Contact[];
  recent_captures: Encounter[];
  pending_discovery_count: number;
  review_needed_count: number;
}
