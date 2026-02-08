// ---- Domain Types (mirrors app/models.py) ----

export interface User {
  id: string;
  username: string;
  balance: number;
  is_active: boolean;
  organization: string | null;
  created_at: string;
}

export interface APIKeyInfo {
  id: string;
  key_prefix: string;
  name: string;
  username: string;
  is_active: boolean;
  rate_limit: string | null;
}

export interface GeneratedKey {
  key: string;
  key_prefix: string;
  name: string;
  rate_limit: string | null;
  user: string;
}

export interface LLMEndpoint {
  id: number;
  name: string;
  base_url: string;
  has_api_key: boolean;
  rpm_limit: number | null;
  day_limit: number | null;
  is_active: boolean;
}

export interface LLMModel {
  id: string;
  litellm_name: string;
  capability: string;
  billing_unit: string;
  price_in: number;
  price_out: number;
  is_active: boolean;
  endpoint_id: number | null;
  endpoint_name: string | null;
  rpm_limit: number | null;
  day_limit: number | null;
}

export interface RequestLogEntry {
  id: string;
  username: string;
  model_used: string;
  input_units: number;
  output_units: number;
  total_cost: number;
  timestamp: string;
}

export interface PaginatedLogs {
  total: number;
  offset: number;
  limit: number;
  items: RequestLogEntry[];
}

export interface Stats {
  users: number;
  api_keys: number;
  models: number;
  endpoints: number;
  total_requests: number;
  total_revenue: number;
  total_input_tokens: number;
  total_output_tokens: number;
}

export interface HealthStatus {
  status: string;
  system: string;
  target_inference_engine: string;
}
