export type Role = "owner" | "engineer" | "viewer";

export interface User {
  id: number;
  email: string;
  full_name: string;
  is_admin: boolean;
  is_active: boolean;
  locale: "fr" | "en";
  created_at: string;
  last_login_at: string | null;
}

export interface Project {
  id: number;
  name: string;
  description: string;
  display_tz: "UTC" | "Africa/Casablanca";
  created_at: string;
  role: Role;
  site_count: number;
  archive_enabled: boolean;
  archive_models: string[];
  archive_max_lead_h: number;
}

export interface Member {
  user_id: number;
  email: string;
  full_name: string;
  role: Role;
}

export interface Site {
  id: number;
  project_id: number;
  name: string;
  lat: number;
  lon: number;
  input_crs: string;
  input_x: number | null;
  input_y: number | null;
  dem_elevation_m: number | null;
  created_at: string;
}

export interface LambertRep {
  crs: string;
  name: string;
  key: string;
  x: number;
  y: number;
  transformation: string;
  accuracy_m: number | null;
  warnings: string[];
}

export interface Representations {
  input: { crs: string; transformation?: string; accuracy_m?: number | null; warnings: string[] };
  wgs84: { lat: number; lon: number };
  dms: { lat: string; lon: string };
  utm: { zone: number; hemisphere: string; crs: string; x: number; y: number; warnings: string[] };
  lambert: LambertRep[];
}

export interface CrsItem {
  crs: string;
  name: string;
  kind: "geographic" | "utm" | "lambert_merchich";
  deprecated: boolean;
}

export interface ModelInfo {
  code: string;
  name: string;
  provider: string;
  grid_type: "regular" | "icosahedral" | "reduced_gaussian";
  resolution: string;
  color: string;
  regional: boolean;
  max_lead_h: number;
  members: number;
  milestone: number;
  notes: string;
  domain?: { lat_min: number; lat_max: number; lon_min: number; lon_max: number };
  invariants_status: { ready: boolean; unavailable?: boolean; prepared_at?: string; source?: unknown };
}

export interface Availability {
  model: string;
  available: boolean;
  message_code: string;
  params: Record<string, unknown>;
}

export interface GridPoint {
  grid_point_id: number;
  model: string;
  native_index: number;
  i: number | null;
  j: number | null;
  lat: number;
  lon: number;
  rank: number;
  method: string;
  distance_m: number;
  azimuth_deg: number;
  model_elevation_m: number | null;
  dem_elevation_m: number | null;
  dem_cell_mean_m: number | null;
  elevation_diff_m: number | null;
  elevation_alert: boolean;
  land_fraction: number | null;
  is_land: boolean | null;
  land_sea_mismatch: boolean;
  contains_site: boolean;
  selected: boolean;
}

export interface GridPointsResponse {
  site: { id: number; lat: number; lon: number; dem_elevation_m: number | null };
  elevation_alert_m: number;
  points: GridPoint[];
}

export interface Task {
  id: number;
  kind: string;
  status: "pending" | "running" | "success" | "failure";
  progress: number;
  message: string;
  result: Record<string, any>;
  started_at: string;
  finished_at: string | null;
}

export interface ImportRowError {
  row: number;
  field: string;
  code: string;
  message: string;
  severity: "error" | "warning";
  params: Record<string, unknown>;
}

export interface ImportReport {
  ok: boolean;
  sites: { row: number; name: string; lat: number; lon: number; input_crs: string }[];
  errors: ImportRowError[];
  created: number[];
}

export interface ForecastParams {
  models: string[];
  run: string;
  max_lead_h: number;
  wind_heights_m: number[];
  pressure_levels_hpa: number[];
  surface: string[];
  source: string;
  members: number[] | null;
  accept_paid: boolean;
}

export interface ErrorPayload {
  code: string;
  message?: string;
  params?: Record<string, unknown>;
}

export interface EstimateInfo {
  n_requests: number;
  bytes: number | null;
  megabytes: number | null;
  n_steps: number;
  first_lead_h: number | null;
  last_lead_h: number | null;
  available_variables: string[];
  missing_variables: string[];
  members: number;
  notes: string[];
}

export interface Extract {
  id: number;
  model: string;
  status: "pending" | "running" | "success" | "failure";
  source: string;
  paid: boolean;
  run: string | null;
  n_members: number;
  n_times: number;
  variables: string[];
  missing_variables: string[];
  grid_point_ids: number[];
  estimate: Partial<EstimateInfo>;
  error: ErrorPayload | Record<string, never>;
  attempts: (ErrorPayload & { source: string })[];
}

export interface ForecastJob {
  id: number;
  site_id: number;
  kind: "manual" | "archive";
  status: "pending" | "running" | "success" | "partial" | "failure";
  params: ForecastParams;
  task_id: number | null;
  created_at: string;
  extracts: Extract[];
}

export interface EstimateResponse {
  limit_mb: number;
  models: {
    model: string;
    points: number;
    error?: ErrorPayload;
    candidates: { source: string; run?: string; licence?: string; cost?: string; estimate?: EstimateInfo; over_limit?: boolean; error?: ErrorPayload }[];
  }[];
}

export interface SeriesVar {
  unit: string;
  values?: (number | null)[];
  p10?: (number | null)[];
  p25?: (number | null)[];
  p50?: (number | null)[];
  p75?: (number | null)[];
  p90?: (number | null)[];
  min?: (number | null)[];
  max?: (number | null)[];
  mean?: (number | null)[];
}

export interface SeriesModel {
  model: string;
  run: string;
  source: string;
  members: number;
  times: string[];
  times_local: string[];
  flags: ("native" | "aggregated" | "interpolated")[];
  points: { grid_point_id: number; lat: number; lon: number; variables: Record<string, SeriesVar> }[];
  derived_heights_m: string;
  missing_variables: string;
}

export interface SeriesResponse {
  step: string;
  method: string;
  speed_method: string;
  models: SeriesModel[];
}

export interface WindRose {
  sectors_deg: number[];
  speed_bins: string[];
  frequency_pct: number[][];
  n: number;
  mean_speed: number;
}

export interface Comparison {
  height_m: number;
  step: string;
  times: string[];
  models: string[];
  values: Record<string, (number | null)[]>;
  multi_model_mean: (number | null)[];
  stats: { model: string; mean: number; std: number; bias_vs_mmm: number; rmsd_vs_mmm: number; corr_vs_mmm: number | null }[];
  n_common_times: number;
}

export interface DataSourceInfo {
  code: string;
  name: string;
  usage_licence: "open" | "non_commercial" | "contract";
  cost: "free" | "paid";
  enabled: boolean;
  implemented: boolean;
  mode: string;
  terms_url: string;
  notes: string;
  blocked_by_deployment: boolean;
}

export interface DataSources {
  deployment_usage: "internal" | "commercial";
  open_meteo_mode: string;
  max_download_mb: number;
  sources: DataSourceInfo[];
}
