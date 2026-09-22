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
  grid_type: "regular" | "icosahedral";
  resolution: string;
  color: string;
  regional: boolean;
  max_lead_h: number;
  members: number;
  milestone: number;
  notes: string;
  domain?: { lat_min: number; lat_max: number; lon_min: number; lon_max: number };
  invariants_status: { ready: boolean; prepared_at?: string; source?: unknown };
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
