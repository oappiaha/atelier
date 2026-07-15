"""Initial schema — TDD §2 verbatim, plus the media.phase sync trigger
(TDD §2.2 note) and the V1 workspace seed.

Revision ID: 0001
Revises:
Create Date: 2026-07-15
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

DDL = r"""
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

-- ── 2.1 Tenancy and identity ─────────────────────────────────────────
CREATE TABLE workspaces (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name          text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email         citext UNIQUE NOT NULL,
  name          text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE memberships (
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role          text NOT NULL DEFAULT 'owner'
                CHECK (role IN ('owner','editor','viewer')),
  PRIMARY KEY (workspace_id, user_id)
);

-- ── 2.2 Archive ──────────────────────────────────────────────────────
CREATE TABLE projects (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name          text NOT NULL,
  kicker        text,
  cover_media_id uuid,
  sort_index    int  NOT NULL DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON projects (workspace_id, sort_index);

CREATE TYPE design_status AS ENUM
  ('developing','in_production','final','archive');

CREATE TABLE designs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  project_id    uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name          text NOT NULL,
  status        design_status NOT NULL DEFAULT 'developing',
  index_no      int  NOT NULL,
  cover_media_id uuid,
  materials     text,
  started_at    date,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, index_no)
);
CREATE INDEX ON designs (workspace_id, project_id, status);

CREATE TYPE phase AS ENUM (
  'moodboard','inspiration','sketch','note','voice',
  'manufacturing','editorial','final','study'
);

CREATE TABLE entries (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  design_id     uuid NOT NULL REFERENCES designs(id) ON DELETE CASCADE,
  phase         phase NOT NULL,
  body          text,
  study_id      uuid,
  occurred_at   timestamptz NOT NULL DEFAULT now(),
  created_at    timestamptz NOT NULL DEFAULT now(),
  is_open       boolean NOT NULL DEFAULT false
);
CREATE INDEX ON entries (design_id, occurred_at DESC);
CREATE INDEX ON entries (design_id, phase, occurred_at DESC);

CREATE TYPE media_kind AS ENUM ('image','audio');

CREATE TABLE media (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  design_id     uuid REFERENCES designs(id) ON DELETE CASCADE,
  entry_id      uuid REFERENCES entries(id) ON DELETE CASCADE,
  kind          media_kind NOT NULL,
  phase         phase,
  r2_key        text NOT NULL,
  thumb_key     text,
  width         int, height int,
  duration_ms   int,
  sha256        char(64) NOT NULL,
  caption       text,
  source_url    text,
  source_app    text,
  sort_index    int NOT NULL DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON media (design_id, phase, created_at DESC);
CREATE INDEX ON media (workspace_id, design_id) WHERE design_id IS NULL;
CREATE UNIQUE INDEX ON media (workspace_id, sha256);

ALTER TABLE projects ADD CONSTRAINT projects_cover_fk
  FOREIGN KEY (cover_media_id) REFERENCES media(id) ON DELETE SET NULL;
ALTER TABLE designs  ADD CONSTRAINT designs_cover_fk
  FOREIGN KEY (cover_media_id) REFERENCES media(id) ON DELETE SET NULL;

-- media.phase is denormalised from its entry: the Media view filters on it
-- and must not join through entries. Trigger keeps it in sync.
CREATE FUNCTION sync_media_phase() RETURNS trigger AS $$
BEGIN
  IF NEW.entry_id IS NOT NULL THEN
    SELECT e.phase, e.design_id INTO NEW.phase, NEW.design_id
    FROM entries e WHERE e.id = NEW.entry_id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER media_phase_sync
  BEFORE INSERT OR UPDATE OF entry_id ON media
  FOR EACH ROW EXECUTE FUNCTION sync_media_phase();

CREATE FUNCTION sync_entry_phase_to_media() RETURNS trigger AS $$
BEGIN
  IF NEW.phase IS DISTINCT FROM OLD.phase THEN
    UPDATE media SET phase = NEW.phase WHERE entry_id = NEW.id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER entry_phase_sync
  AFTER UPDATE OF phase ON entries
  FOR EACH ROW EXECUTE FUNCTION sync_entry_phase_to_media();

CREATE TABLE share_links (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  project_id    uuid REFERENCES projects(id) ON DELETE CASCADE,
  design_id     uuid REFERENCES designs(id) ON DELETE CASCADE,
  slug          text UNIQUE NOT NULL,
  scope         text NOT NULL DEFAULT 'finals'
                CHECK (scope IN ('finals','full')),
  revoked_at    timestamptz,
  view_count    int NOT NULL DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now(),
  CHECK (project_id IS NOT NULL OR design_id IS NOT NULL)
);

-- ── 2.3 Wada: the Sanzo corpus ───────────────────────────────────────
CREATE TABLE sanzo_colors (
  id            int PRIMARY KEY,
  name          text NOT NULL,
  hex           char(7) NOT NULL,
  lab_l         real NOT NULL,
  lab_a         real NOT NULL,
  lab_b         real NOT NULL,
  chroma        real NOT NULL,
  hue_deg       real NOT NULL,
  hue_family    text NOT NULL,
  cmyk          jsonb
);

CREATE TABLE palettes (
  id            text PRIMARY KEY,
  name          text NOT NULL,
  color_count   smallint NOT NULL CHECK (color_count BETWEEN 2 AND 4),
  color_ids     int[] NOT NULL,
  mean_lab_l    real NOT NULL,
  mean_chroma   real NOT NULL,
  temperature   text NOT NULL,
  max_delta_e   real NOT NULL,
  min_delta_e   real NOT NULL,
  volume        smallint, plate smallint
);
CREATE INDEX ON palettes (color_count);
CREATE INDEX ON palettes (temperature, mean_lab_l);
CREATE INDEX ON palettes USING gin (color_ids);

-- ── 2.4 Wada: regions, studies, slots ────────────────────────────────
CREATE TABLE regions (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  media_id      uuid NOT NULL REFERENCES media(id) ON DELETE CASCADE,
  label         text NOT NULL,
  label_source  text NOT NULL DEFAULT 'gemini'
                CHECK (label_source IN ('gemini','user')),
  confidence    real NOT NULL,
  mask_key      text NOT NULL,
  mask_sha256   char(64) NOT NULL,
  area_fraction real NOT NULL,
  bbox          int[] NOT NULL,
  mean_lab      real[] NOT NULL,
  is_cosmetic   boolean GENERATED ALWAYS AS (area_fraction < 0.02) STORED,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON regions (media_id);

CREATE TYPE study_status AS ENUM
  ('draft','estimating','generating','partial','complete','failed','cancelled');

CREATE TABLE studies (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  design_id     uuid NOT NULL REFERENCES designs(id) ON DELETE CASCADE,
  base_media_id uuid NOT NULL REFERENCES media(id),
  palette_id    text NOT NULL REFERENCES palettes(id),
  status        study_status NOT NULL DEFAULT 'draft',
  policy        jsonb NOT NULL,
  perm_total    int,
  perm_planned  int,
  est_cost_cents int,
  actual_cost_cents int NOT NULL DEFAULT 0,
  model_id      text NOT NULL,
  prompt_version text NOT NULL,
  error         text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  completed_at  timestamptz
);
CREATE INDEX ON studies (design_id, created_at DESC);

CREATE TABLE slots (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  study_id      uuid NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
  idx           smallint NOT NULL,
  label         text NOT NULL,
  kind          text NOT NULL DEFAULT 'paint'
                CHECK (kind IN ('paint','lock')),
  region_ids    uuid[] NOT NULL,
  union_mask_key text NOT NULL,
  union_sha256  char(64) NOT NULL,
  area_fraction real NOT NULL,
  anchor_color_id int REFERENCES sanzo_colors(id),
  UNIQUE (study_id, idx)
);
CREATE INDEX ON slots (study_id);

-- entries.study_id FK (deferred until studies exists)
ALTER TABLE entries ADD CONSTRAINT entries_study_fk
  FOREIGN KEY (study_id) REFERENCES studies(id) ON DELETE SET NULL;

-- ── 2.5 Wada: permutations, colorways, generation trie ───────────────
CREATE TYPE colorway_status AS ENUM
  ('planned','generating','ready','pinned','rejected','failed');

CREATE TABLE permutations (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  study_id      uuid NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
  idx           int NOT NULL,
  mapping       jsonb NOT NULL,
  rank_score    real NOT NULL,
  signature     real[] NOT NULL,
  dedupe_group  int,
  is_representative boolean NOT NULL DEFAULT true,
  UNIQUE (study_id, idx)
);
CREATE INDEX ON permutations (study_id, rank_score DESC);

CREATE TABLE colorways (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  study_id      uuid NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
  permutation_id uuid NOT NULL REFERENCES permutations(id) ON DELETE CASCADE,
  status        colorway_status NOT NULL DEFAULT 'planned',
  image_key     text,
  thumb_key     text,
  model_id      text,
  cost_cents    int NOT NULL DEFAULT 0,
  cache_hits    int NOT NULL DEFAULT 0,
  latency_ms    int,
  lock_verified boolean,
  error         text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (permutation_id)
);
CREATE INDEX ON colorways (study_id, status);

CREATE TABLE gen_nodes (
  cache_key     char(64) PRIMARY KEY,
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  parent_key    char(64) REFERENCES gen_nodes(cache_key),
  depth         smallint NOT NULL,
  base_sha256   char(64) NOT NULL,
  step_color_id int NOT NULL REFERENCES sanzo_colors(id),
  step_mask_sha char(64) NOT NULL,
  model_id      text NOT NULL,
  prompt_version text NOT NULL,
  image_key     text NOT NULL,
  cost_cents    int NOT NULL,
  hit_count     int NOT NULL DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_used_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON gen_nodes (parent_key);
CREATE INDEX ON gen_nodes (workspace_id, last_used_at);

-- ── 2.6 Budget ledger ────────────────────────────────────────────────
CREATE TABLE spend_ledger (
  id            bigserial PRIMARY KEY,
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  study_id      uuid REFERENCES studies(id) ON DELETE SET NULL,
  kind          text NOT NULL,
  model_id      text NOT NULL,
  cost_cents    int NOT NULL,
  cache_hit     boolean NOT NULL DEFAULT false,
  occurred_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON spend_ledger (workspace_id, occurred_at);

-- ── V1 seed: exactly one workspace ───────────────────────────────────
INSERT INTO workspaces (id, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'rei');
"""


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    op.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
