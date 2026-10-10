-- Migration v1.1: close audit gaps. Apply after schema-v1.sql.
--
-- Keep the migration atomic. The ledger row is written first, so a failed
-- migration rolls it back together with the DDL and a caller can retry from
-- the v1 schema. `user_version` is the SQLite-level version marker; the
-- ledger retains the human-readable migration identity.
PRAGMA foreign_keys = ON;
PRAGMA recursive_triggers = ON;
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS migration_ledger(
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  checksum TEXT NOT NULL,
  applied_at TEXT NOT NULL
);
INSERT INTO migration_ledger(version,name,checksum,applied_at)
  VALUES(11,'schema-v1.1','schema-v1.1-migration',datetime('now'));

ALTER TABLE candidate_memory ADD COLUMN created_by TEXT;
ALTER TABLE source_span ADD COLUMN external_uri TEXT;
ALTER TABLE source_span ADD COLUMN snapshot_hash TEXT;
ALTER TABLE source_span ADD COLUMN snapshot_at TEXT;
ALTER TABLE ai_run ADD COLUMN context_item_ids_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE ai_run ADD COLUMN token_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE ai_run ADD COLUMN latency_ms INTEGER;
ALTER TABLE ai_run ADD COLUMN cost_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE ai_run ADD COLUMN author_decision TEXT CHECK(author_decision IN ('pending','accepted','rejected','partial'));
ALTER TABLE ai_run ADD COLUMN consent_json TEXT NOT NULL DEFAULT '{}';
CREATE TABLE actor(id TEXT PRIMARY KEY, project_id TEXT REFERENCES project(id), kind TEXT NOT NULL, name TEXT NOT NULL);
CREATE TABLE assertion(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), branch_id TEXT NOT NULL REFERENCES branch(id), subject_entity_id TEXT NOT NULL REFERENCES entity(id), predicate TEXT NOT NULL, object_json TEXT NOT NULL, epistemic_mode TEXT NOT NULL CHECK(epistemic_mode IN ('world_truth','character_belief','narrator','inference','author_plan')), state TEXT NOT NULL CHECK(state IN ('candidate','canon','contradicted','superseded','retracted')), valid_start TEXT, valid_end TEXT, confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1), supersedes_id TEXT REFERENCES assertion(id), evidence_json TEXT NOT NULL DEFAULT '[]', reviewed_by TEXT, reviewed_at TEXT, CHECK(json_valid(evidence_json)));
CREATE TABLE character_state(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), branch_id TEXT NOT NULL REFERENCES branch(id), entity_id TEXT NOT NULL REFERENCES entity(id), attribute TEXT NOT NULL, value_json TEXT NOT NULL, event_id TEXT, valid_start TEXT, valid_end TEXT, knowledge_scope_json TEXT NOT NULL DEFAULT '{}', source_assertion_id TEXT REFERENCES assertion(id));
CREATE TABLE plot_thread(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), branch_id TEXT NOT NULL REFERENCES branch(id), status TEXT NOT NULL CHECK(status IN ('open','paid_off','abandoned','invalidated')), payload_json TEXT NOT NULL, severity TEXT NOT NULL);
CREATE TABLE external_source(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), uri TEXT NOT NULL, snapshot_hash TEXT, fetched_at TEXT, license_json TEXT NOT NULL DEFAULT '{}', trust_level TEXT NOT NULL);
CREATE TABLE prov_entity(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), entity_type TEXT NOT NULL, entity_ref TEXT NOT NULL, content_hash TEXT, created_at TEXT NOT NULL);
CREATE TABLE prov_activity(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), activity_type TEXT NOT NULL, ai_run_id TEXT REFERENCES ai_run(id), event_id TEXT REFERENCES event_log(id), started_at TEXT NOT NULL, ended_at TEXT, parameters_hash TEXT);
CREATE TABLE prov_agent(id TEXT PRIMARY KEY, project_id TEXT REFERENCES project(id), agent_type TEXT NOT NULL, provider TEXT, version TEXT);
CREATE TABLE prov_edge(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), subject_id TEXT NOT NULL, predicate TEXT NOT NULL, object_id TEXT NOT NULL, qualified_json TEXT NOT NULL DEFAULT '{}', is_application_extension INTEGER NOT NULL DEFAULT 0 CHECK(is_application_extension IN (0,1)));
CREATE TABLE index_checkpoint(index_name TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), source_revision TEXT, index_version TEXT NOT NULL, model TEXT, status TEXT NOT NULL CHECK(status IN ('current','stale','rebuilding','failed')), last_error TEXT, updated_at TEXT NOT NULL);
CREATE TABLE export_job(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), branch_id TEXT NOT NULL REFERENCES branch(id), format TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('queued','running','awaiting_confirmation','success','failed','cancelled')), manifest_hash TEXT, loss_manifest_json TEXT NOT NULL DEFAULT '{}', requested_by TEXT NOT NULL, confirmed_by TEXT, created_at TEXT NOT NULL, completed_at TEXT);

CREATE TRIGGER prevent_revision_update BEFORE UPDATE ON document_revision BEGIN SELECT RAISE(ABORT,'append-only revision'); END;
CREATE TRIGGER prevent_revision_delete BEFORE DELETE ON document_revision BEGIN SELECT RAISE(ABORT,'append-only revision'); END;
-- A BEFORE INSERT guard catches INSERT OR REPLACE even when a connection has
-- recursive_triggers disabled.
CREATE TRIGGER prevent_revision_replace BEFORE INSERT ON document_revision WHEN EXISTS (SELECT 1 FROM document_revision p WHERE p.id=NEW.id) BEGIN SELECT RAISE(ABORT,'append-only revision'); END;
CREATE TRIGGER prevent_event_update BEFORE UPDATE ON event_log BEGIN SELECT RAISE(ABORT,'append-only event'); END;
CREATE TRIGGER prevent_event_delete BEFORE DELETE ON event_log BEGIN SELECT RAISE(ABORT,'append-only event'); END;
-- Include event_log's second UNIQUE conflict target (idempotency_key).
CREATE TRIGGER prevent_event_replace BEFORE INSERT ON event_log WHEN EXISTS (SELECT 1 FROM event_log p WHERE p.id=NEW.id OR p.idempotency_key=NEW.idempotency_key) BEGIN SELECT RAISE(ABORT,'append-only event'); END;
CREATE TRIGGER prevent_deletion_event_update BEFORE UPDATE ON deletion_event BEGIN SELECT RAISE(ABORT,'append-only deletion event'); END;
CREATE TRIGGER prevent_deletion_event_delete BEFORE DELETE ON deletion_event BEGIN SELECT RAISE(ABORT,'append-only deletion event'); END;
CREATE TRIGGER prevent_deletion_event_replace BEFORE INSERT ON deletion_event WHEN EXISTS (SELECT 1 FROM deletion_event p WHERE p.id=NEW.id) BEGIN SELECT RAISE(ABORT,'append-only deletion event'); END;
CREATE TRIGGER prevent_audit_event_update BEFORE UPDATE ON audit_event BEGIN SELECT RAISE(ABORT,'append-only audit event'); END;
CREATE TRIGGER prevent_audit_event_delete BEFORE DELETE ON audit_event BEGIN SELECT RAISE(ABORT,'append-only audit event'); END;
CREATE TRIGGER prevent_audit_event_replace BEFORE INSERT ON audit_event WHEN EXISTS (SELECT 1 FROM audit_event p WHERE p.id=NEW.id) BEGIN SELECT RAISE(ABORT,'append-only audit event'); END;
CREATE TRIGGER check_revision_scope BEFORE INSERT ON document_revision WHEN EXISTS (SELECT 1 FROM document_revision p WHERE p.id=NEW.parent_id AND (p.branch_id<>NEW.branch_id OR p.document_node_id<>NEW.document_node_id)) BEGIN SELECT RAISE(ABORT,'revision scope mismatch'); END;
CREATE TRIGGER check_span_range BEFORE INSERT ON source_span WHEN NEW.start_codepoint<0 OR NEW.end_codepoint<NEW.start_codepoint BEGIN SELECT RAISE(ABORT,'invalid span range'); END;
CREATE TRIGGER check_branch_scope BEFORE INSERT ON branch WHEN NEW.parent_id IS NOT NULL AND EXISTS (SELECT 1 FROM branch p WHERE p.id=NEW.parent_id AND p.project_id<>NEW.project_id) BEGIN SELECT RAISE(ABORT,'branch scope mismatch'); END;
-- Export success is reachable only from the explicit confirmation state and
-- carries the confirmer identity. Direct INSERT of a terminal row is also
-- rejected, including INSERT OR REPLACE.
CREATE TRIGGER check_export_confirmation_insert BEFORE INSERT ON export_job WHEN NEW.status='success' BEGIN SELECT RAISE(ABORT,'export confirmation required'); END;
CREATE TRIGGER check_export_confirmation BEFORE UPDATE OF status ON export_job WHEN NEW.status='success' AND OLD.status<>'awaiting_confirmation' BEGIN SELECT RAISE(ABORT,'export confirmation required'); END;
CREATE TRIGGER check_export_confirmed_by BEFORE UPDATE OF status,confirmed_by ON export_job WHEN NEW.status='success' AND (OLD.status<>'awaiting_confirmation' OR NEW.confirmed_by IS NULL OR length(trim(NEW.confirmed_by))=0) BEGIN SELECT RAISE(ABORT,'export confirmer required'); END;
CREATE TRIGGER check_span_project BEFORE INSERT ON source_span WHEN EXISTS (SELECT 1 FROM document_revision r JOIN document_node n ON n.id=r.document_node_id WHERE r.id=NEW.revision_id AND (n.project_id<>NEW.project_id OR n.id<>NEW.document_node_id)) BEGIN SELECT RAISE(ABORT,'source span project mismatch'); END;
CREATE TRIGGER check_candidate_scope BEFORE INSERT ON candidate_memory WHEN (NEW.entity_id IS NOT NULL AND EXISTS (SELECT 1 FROM entity e WHERE e.id=NEW.entity_id AND e.project_id<>NEW.project_id)) OR (NEW.source_span_id IS NOT NULL AND EXISTS (SELECT 1 FROM source_span s WHERE s.id=NEW.source_span_id AND s.project_id<>NEW.project_id)) BEGIN SELECT RAISE(ABORT,'candidate project mismatch'); END;
CREATE TRIGGER check_deletion_scope BEFORE INSERT ON deletion_event WHEN NEW.revision_id IS NOT NULL AND EXISTS (SELECT 1 FROM document_revision r JOIN document_node n ON n.id=(SELECT document_node_id FROM document_revision WHERE id=NEW.revision_id) WHERE r.id=NEW.revision_id AND n.project_id<>NEW.project_id) BEGIN SELECT RAISE(ABORT,'deletion project mismatch'); END;
CREATE TRIGGER check_assertion_scope BEFORE INSERT ON assertion WHEN EXISTS (SELECT 1 FROM entity e WHERE e.id=NEW.subject_entity_id AND e.project_id<>NEW.project_id) BEGIN SELECT RAISE(ABORT,'assertion project mismatch'); END;
-- Canon is a reviewed projection, never an unqualified direct write. The
-- evidence payload must be a non-empty JSON array and reviewer identity and
-- review timestamp are required for INSERT and state transitions.
CREATE TRIGGER check_assertion_canon_insert BEFORE INSERT ON assertion WHEN NEW.state='canon' AND (json_valid(NEW.evidence_json)=0 OR json_type(NEW.evidence_json)<>'array' OR json_array_length(NEW.evidence_json)=0 OR NEW.reviewed_by IS NULL OR length(trim(NEW.reviewed_by))=0 OR NEW.reviewed_at IS NULL OR length(trim(NEW.reviewed_at))=0) BEGIN SELECT RAISE(ABORT,'canon evidence and reviewer required'); END;
CREATE TRIGGER check_assertion_canon_update BEFORE UPDATE OF state,evidence_json,reviewed_by,reviewed_at ON assertion WHEN NEW.state='canon' AND (json_valid(NEW.evidence_json)=0 OR json_type(NEW.evidence_json)<>'array' OR json_array_length(NEW.evidence_json)=0 OR NEW.reviewed_by IS NULL OR length(trim(NEW.reviewed_by))=0 OR NEW.reviewed_at IS NULL OR length(trim(NEW.reviewed_at))=0) BEGIN SELECT RAISE(ABORT,'canon evidence and reviewer required'); END;

-- Cross-project/branch scope guards.  Single-column foreign keys only prove
-- that a referenced id exists; these guards keep all composite relationships
-- on the same project and branch for both inserts and updates.
CREATE TRIGGER check_branch_scope_update
BEFORE UPDATE ON branch
WHEN NEW.parent_id IS NOT NULL
 AND EXISTS (SELECT 1 FROM branch p
             WHERE p.id=NEW.parent_id AND p.project_id<>NEW.project_id)
BEGIN SELECT RAISE(ABORT,'branch scope mismatch'); END;

CREATE TRIGGER check_branch_project_scope_update
BEFORE UPDATE OF project_id ON branch
WHEN NEW.project_id<>OLD.project_id
 AND (EXISTS (SELECT 1 FROM branch child
              WHERE child.parent_id=OLD.id AND child.project_id<>NEW.project_id)
      OR EXISTS (SELECT 1 FROM document_node n
                 WHERE n.branch_id=OLD.id AND n.project_id<>NEW.project_id)
      OR EXISTS (SELECT 1 FROM event_log e
                 WHERE e.branch_id=OLD.id AND e.project_id<>NEW.project_id)
      OR EXISTS (SELECT 1 FROM candidate_memory cm
                 WHERE cm.branch_id=OLD.id AND cm.project_id<>NEW.project_id)
      OR EXISTS (SELECT 1 FROM deletion_event d
                 WHERE d.branch_id=OLD.id AND d.project_id<>NEW.project_id)
      OR EXISTS (SELECT 1 FROM assertion a
                 WHERE a.branch_id=OLD.id AND a.project_id<>NEW.project_id))
BEGIN SELECT RAISE(ABORT,'branch project scope mismatch'); END;

CREATE TRIGGER check_document_node_scope
BEFORE INSERT ON document_node
WHEN EXISTS (SELECT 1 FROM branch b
             WHERE b.id=NEW.branch_id AND b.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM document_node p
             WHERE p.id=NEW.parent_id
               AND (p.project_id<>NEW.project_id OR p.branch_id<>NEW.branch_id))
BEGIN SELECT RAISE(ABORT,'document node scope mismatch'); END;
CREATE TRIGGER check_document_node_scope_update
BEFORE UPDATE ON document_node
WHEN EXISTS (SELECT 1 FROM branch b
             WHERE b.id=NEW.branch_id AND b.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM document_node p
             WHERE p.id=NEW.parent_id
               AND (p.project_id<>NEW.project_id OR p.branch_id<>NEW.branch_id))
  OR EXISTS (SELECT 1 FROM document_node child
             WHERE child.parent_id=OLD.id
               AND (child.project_id<>NEW.project_id OR child.branch_id<>NEW.branch_id))
  OR EXISTS (SELECT 1 FROM document_revision r
             WHERE r.document_node_id=OLD.id
               AND r.branch_id<>NEW.branch_id)
BEGIN SELECT RAISE(ABORT,'document node scope mismatch'); END;

CREATE TRIGGER check_document_revision_scope
BEFORE INSERT ON document_revision
WHEN EXISTS (
       SELECT 1
       FROM document_node n JOIN branch b ON b.id=NEW.branch_id
       WHERE n.id=NEW.document_node_id
         AND (n.project_id<>b.project_id OR n.branch_id<>NEW.branch_id)
     )
  OR EXISTS (
       SELECT 1
       FROM document_revision p JOIN document_node n ON n.id=p.document_node_id
       WHERE p.id=NEW.parent_id
         AND (p.branch_id<>NEW.branch_id OR p.document_node_id<>NEW.document_node_id
              OR n.branch_id<>NEW.branch_id)
     )
BEGIN SELECT RAISE(ABORT,'document revision scope mismatch'); END;
CREATE TRIGGER check_document_revision_scope_update
BEFORE UPDATE ON document_revision
WHEN EXISTS (
       SELECT 1
       FROM document_node n JOIN branch b ON b.id=NEW.branch_id
       WHERE n.id=NEW.document_node_id
         AND (n.project_id<>b.project_id OR n.branch_id<>NEW.branch_id)
     )
  OR EXISTS (
       SELECT 1
       FROM document_revision p JOIN document_node n ON n.id=p.document_node_id
       WHERE p.id=NEW.parent_id
         AND (p.branch_id<>NEW.branch_id OR p.document_node_id<>NEW.document_node_id
              OR n.branch_id<>NEW.branch_id)
     )
BEGIN SELECT RAISE(ABORT,'document revision scope mismatch'); END;

CREATE TRIGGER check_event_scope
BEFORE INSERT ON event_log
WHEN EXISTS (SELECT 1 FROM branch b
             WHERE b.id=NEW.branch_id AND b.project_id<>NEW.project_id)
  OR EXISTS (
       SELECT 1
       FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
            JOIN branch b ON b.id=r.branch_id
       WHERE r.id=NEW.aggregate_id
         AND (r.branch_id<>NEW.branch_id OR n.project_id<>NEW.project_id
              OR b.project_id<>NEW.project_id)
     )
  OR EXISTS (
       SELECT 1
       FROM document_node n JOIN branch b ON b.id=n.branch_id
       WHERE n.id=NEW.aggregate_id
         AND (n.branch_id<>NEW.branch_id OR n.project_id<>NEW.project_id
              OR b.project_id<>NEW.project_id)
     )
BEGIN SELECT RAISE(ABORT,'event scope mismatch'); END;
CREATE TRIGGER check_event_scope_update
BEFORE UPDATE ON event_log
WHEN EXISTS (SELECT 1 FROM branch b
             WHERE b.id=NEW.branch_id AND b.project_id<>NEW.project_id)
  OR EXISTS (
       SELECT 1
       FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
            JOIN branch b ON b.id=r.branch_id
       WHERE r.id=NEW.aggregate_id
         AND (r.branch_id<>NEW.branch_id OR n.project_id<>NEW.project_id
              OR b.project_id<>NEW.project_id)
     )
  OR EXISTS (
       SELECT 1
       FROM document_node n JOIN branch b ON b.id=n.branch_id
       WHERE n.id=NEW.aggregate_id
         AND (n.branch_id<>NEW.branch_id OR n.project_id<>NEW.project_id
              OR b.project_id<>NEW.project_id)
     )
BEGIN SELECT RAISE(ABORT,'event scope mismatch'); END;

CREATE TRIGGER check_assertion_scope_update
BEFORE UPDATE ON assertion
WHEN EXISTS (SELECT 1 FROM branch b
             WHERE b.id=NEW.branch_id AND b.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM entity e
             WHERE e.id=NEW.subject_entity_id AND e.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM assertion a
             WHERE a.id=NEW.supersedes_id
               AND (a.project_id<>NEW.project_id OR a.branch_id<>NEW.branch_id))
BEGIN SELECT RAISE(ABORT,'assertion scope mismatch'); END;
-- Extend the existing INSERT guard to cover branch and supersession scope.
CREATE TRIGGER check_assertion_branch_scope
BEFORE INSERT ON assertion
WHEN EXISTS (SELECT 1 FROM branch b
             WHERE b.id=NEW.branch_id AND b.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM assertion a
             WHERE a.id=NEW.supersedes_id
               AND (a.project_id<>NEW.project_id OR a.branch_id<>NEW.branch_id))
BEGIN SELECT RAISE(ABORT,'assertion scope mismatch'); END;

CREATE TRIGGER check_source_span_scope
BEFORE INSERT ON source_span
WHEN EXISTS (
       SELECT 1
       FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
            JOIN branch b ON b.id=r.branch_id
       WHERE r.id=NEW.revision_id
         AND (r.document_node_id<>NEW.document_node_id
              OR n.project_id<>NEW.project_id OR b.project_id<>NEW.project_id)
     )
BEGIN SELECT RAISE(ABORT,'source span scope mismatch'); END;
CREATE TRIGGER check_source_span_scope_update
BEFORE UPDATE ON source_span
WHEN EXISTS (
       SELECT 1
       FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
            JOIN branch b ON b.id=r.branch_id
       WHERE r.id=NEW.revision_id
         AND (r.document_node_id<>NEW.document_node_id
              OR n.project_id<>NEW.project_id OR b.project_id<>NEW.project_id)
     )
BEGIN SELECT RAISE(ABORT,'source span scope mismatch'); END;

CREATE TRIGGER check_candidate_scope_update
BEFORE UPDATE ON candidate_memory
WHEN EXISTS (SELECT 1 FROM branch b
             WHERE b.id=NEW.branch_id AND b.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM entity e
             WHERE e.id=NEW.entity_id AND e.project_id<>NEW.project_id)
  OR EXISTS (
       SELECT 1
       FROM source_span s
            JOIN document_revision r ON r.id=s.revision_id
            JOIN document_node n ON n.id=r.document_node_id
            JOIN branch b ON b.id=r.branch_id
       WHERE s.id=NEW.source_span_id
         AND (s.project_id<>NEW.project_id OR r.branch_id<>NEW.branch_id
              OR n.project_id<>NEW.project_id OR b.project_id<>NEW.project_id)
     )
BEGIN SELECT RAISE(ABORT,'candidate scope mismatch'); END;
CREATE TRIGGER check_candidate_branch_scope
BEFORE INSERT ON candidate_memory
WHEN EXISTS (SELECT 1 FROM branch b
             WHERE b.id=NEW.branch_id AND b.project_id<>NEW.project_id)
  OR EXISTS (
       SELECT 1
       FROM source_span s
            JOIN document_revision r ON r.id=s.revision_id
            JOIN document_node n ON n.id=r.document_node_id
            JOIN branch b ON b.id=r.branch_id
       WHERE s.id=NEW.source_span_id
         AND (s.project_id<>NEW.project_id OR r.branch_id<>NEW.branch_id
              OR n.project_id<>NEW.project_id OR b.project_id<>NEW.project_id)
     )
BEGIN SELECT RAISE(ABORT,'candidate scope mismatch'); END;

CREATE TRIGGER check_deletion_scope_update
BEFORE UPDATE ON deletion_event
WHEN EXISTS (SELECT 1 FROM branch b
             WHERE b.id=NEW.branch_id AND b.project_id<>NEW.project_id)
  OR EXISTS (
       SELECT 1
       FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
            JOIN branch b ON b.id=r.branch_id
       WHERE r.id=NEW.revision_id
         AND (r.branch_id<>NEW.branch_id OR n.project_id<>NEW.project_id
              OR b.project_id<>NEW.project_id)
     )
  OR EXISTS (
       SELECT 1
       FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
            JOIN branch b ON b.id=r.branch_id
       WHERE r.id=NEW.aggregate_id
         AND (r.branch_id<>NEW.branch_id OR n.project_id<>NEW.project_id
              OR b.project_id<>NEW.project_id)
     )
  OR EXISTS (
       SELECT 1 FROM document_node n JOIN branch b ON b.id=n.branch_id
       WHERE n.id=NEW.aggregate_id
         AND (n.branch_id<>NEW.branch_id OR n.project_id<>NEW.project_id
              OR b.project_id<>NEW.project_id)
     )
BEGIN SELECT RAISE(ABORT,'deletion scope mismatch'); END;
CREATE TRIGGER check_deletion_branch_scope
BEFORE INSERT ON deletion_event
WHEN EXISTS (SELECT 1 FROM branch b
             WHERE b.id=NEW.branch_id AND b.project_id<>NEW.project_id)
  OR EXISTS (
       SELECT 1
       FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
            JOIN branch b ON b.id=r.branch_id
       WHERE r.id=NEW.revision_id
         AND (r.branch_id<>NEW.branch_id OR n.project_id<>NEW.project_id
              OR b.project_id<>NEW.project_id)
     )
  OR EXISTS (
       SELECT 1
       FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
            JOIN branch b ON b.id=r.branch_id
       WHERE r.id=NEW.aggregate_id
         AND (r.branch_id<>NEW.branch_id OR n.project_id<>NEW.project_id
              OR b.project_id<>NEW.project_id)
     )
  OR EXISTS (
       SELECT 1 FROM document_node n JOIN branch b ON b.id=n.branch_id
       WHERE n.id=NEW.aggregate_id
         AND (n.branch_id<>NEW.branch_id OR n.project_id<>NEW.project_id
              OR b.project_id<>NEW.project_id)
     )
BEGIN SELECT RAISE(ABORT,'deletion scope mismatch'); END;

-- Remaining projection and provenance tables carry the same composite scope
-- in denormalized form.  Keep their project/branch and cross-table references
-- aligned on INSERT and UPDATE as well.
CREATE TRIGGER check_ai_run_scope
BEFORE INSERT ON ai_run
WHEN NEW.branch_id IS NOT NULL
 AND EXISTS (SELECT 1 FROM branch b
             WHERE b.id=NEW.branch_id AND b.project_id<>NEW.project_id)
BEGIN SELECT RAISE(ABORT,'ai run scope mismatch'); END;
CREATE TRIGGER check_ai_run_scope_update
BEFORE UPDATE ON ai_run
WHEN NEW.branch_id IS NOT NULL
 AND EXISTS (SELECT 1 FROM branch b
             WHERE b.id=NEW.branch_id AND b.project_id<>NEW.project_id)
BEGIN SELECT RAISE(ABORT,'ai run scope mismatch'); END;

CREATE TRIGGER check_character_state_scope
BEFORE INSERT ON character_state
WHEN EXISTS (SELECT 1 FROM branch b
             WHERE b.id=NEW.branch_id AND b.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM entity e
             WHERE e.id=NEW.entity_id AND e.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM event_log ev
             WHERE ev.id=NEW.event_id
               AND (ev.project_id<>NEW.project_id OR ev.branch_id<>NEW.branch_id))
  OR EXISTS (SELECT 1 FROM assertion a
             WHERE a.id=NEW.source_assertion_id
               AND (a.project_id<>NEW.project_id OR a.branch_id<>NEW.branch_id))
BEGIN SELECT RAISE(ABORT,'character state scope mismatch'); END;
CREATE TRIGGER check_character_state_scope_update
BEFORE UPDATE ON character_state
WHEN EXISTS (SELECT 1 FROM branch b
             WHERE b.id=NEW.branch_id AND b.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM entity e
             WHERE e.id=NEW.entity_id AND e.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM event_log ev
             WHERE ev.id=NEW.event_id
               AND (ev.project_id<>NEW.project_id OR ev.branch_id<>NEW.branch_id))
  OR EXISTS (SELECT 1 FROM assertion a
             WHERE a.id=NEW.source_assertion_id
               AND (a.project_id<>NEW.project_id OR a.branch_id<>NEW.branch_id))
BEGIN SELECT RAISE(ABORT,'character state scope mismatch'); END;

CREATE TRIGGER check_plot_thread_scope
BEFORE INSERT ON plot_thread
WHEN EXISTS (SELECT 1 FROM branch b
             WHERE b.id=NEW.branch_id AND b.project_id<>NEW.project_id)
BEGIN SELECT RAISE(ABORT,'plot thread scope mismatch'); END;
CREATE TRIGGER check_plot_thread_scope_update
BEFORE UPDATE ON plot_thread
WHEN EXISTS (SELECT 1 FROM branch b
             WHERE b.id=NEW.branch_id AND b.project_id<>NEW.project_id)
BEGIN SELECT RAISE(ABORT,'plot thread scope mismatch'); END;

CREATE TRIGGER check_external_source_scope
BEFORE INSERT ON external_source
WHEN NOT EXISTS (SELECT 1 FROM project p WHERE p.id=NEW.project_id)
BEGIN SELECT RAISE(ABORT,'external source project mismatch'); END;
CREATE TRIGGER check_external_source_scope_update
BEFORE UPDATE ON external_source
WHEN NOT EXISTS (SELECT 1 FROM project p WHERE p.id=NEW.project_id)
BEGIN SELECT RAISE(ABORT,'external source project mismatch'); END;

CREATE TRIGGER check_prov_entity_scope
BEFORE INSERT ON prov_entity
WHEN NEW.entity_type NOT IN ('entity','document_node','revision','source_span','assertion','character_state','event','ai_run','actor','external_source','prov_entity','prov_activity','prov_agent')
  OR (NEW.entity_type='entity' AND NOT EXISTS (SELECT 1 FROM entity e WHERE e.id=NEW.entity_ref))
  OR (NEW.entity_type='document_node' AND NOT EXISTS (SELECT 1 FROM document_node n WHERE n.id=NEW.entity_ref))
  OR (NEW.entity_type='revision' AND NOT EXISTS (SELECT 1 FROM document_revision r WHERE r.id=NEW.entity_ref))
  OR (NEW.entity_type='source_span' AND NOT EXISTS (SELECT 1 FROM source_span x WHERE x.id=NEW.entity_ref))
  OR (NEW.entity_type='assertion' AND NOT EXISTS (SELECT 1 FROM assertion a WHERE a.id=NEW.entity_ref))
  OR (NEW.entity_type='character_state' AND NOT EXISTS (SELECT 1 FROM character_state c WHERE c.id=NEW.entity_ref))
  OR (NEW.entity_type='event' AND NOT EXISTS (SELECT 1 FROM event_log e WHERE e.id=NEW.entity_ref))
  OR (NEW.entity_type='ai_run' AND NOT EXISTS (SELECT 1 FROM ai_run r WHERE r.id=NEW.entity_ref))
  OR (NEW.entity_type='actor' AND NOT EXISTS (SELECT 1 FROM actor a WHERE a.id=NEW.entity_ref))
  OR (NEW.entity_type='external_source' AND NOT EXISTS (SELECT 1 FROM external_source x WHERE x.id=NEW.entity_ref))
  OR (NEW.entity_type='prov_entity' AND NOT EXISTS (SELECT 1 FROM prov_entity p WHERE p.id=NEW.entity_ref))
  OR (NEW.entity_type='prov_activity' AND NOT EXISTS (SELECT 1 FROM prov_activity p WHERE p.id=NEW.entity_ref))
  OR (NEW.entity_type='prov_agent' AND NOT EXISTS (SELECT 1 FROM prov_agent p WHERE p.id=NEW.entity_ref))
  OR EXISTS (SELECT 1 FROM entity e
             WHERE e.id=NEW.entity_ref AND e.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM document_node n
             WHERE n.id=NEW.entity_ref AND n.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
             WHERE NEW.entity_type='revision' AND r.id=NEW.entity_ref AND n.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM source_span s
             WHERE NEW.entity_type='source_span' AND s.id=NEW.entity_ref AND s.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM assertion a
             WHERE NEW.entity_type='assertion' AND a.id=NEW.entity_ref AND a.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM character_state cs
             WHERE NEW.entity_type='character_state' AND cs.id=NEW.entity_ref AND cs.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM event_log ev
             WHERE NEW.entity_type='event' AND ev.id=NEW.entity_ref AND ev.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM ai_run ar
             WHERE NEW.entity_type='ai_run' AND ar.id=NEW.entity_ref AND ar.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM actor ac
             WHERE NEW.entity_type='actor' AND ac.id=NEW.entity_ref AND ac.project_id IS NOT NULL AND ac.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM external_source xs
             WHERE NEW.entity_type='external_source' AND xs.id=NEW.entity_ref AND xs.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM prov_entity pe
             WHERE NEW.entity_type='prov_entity' AND pe.id=NEW.entity_ref AND pe.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM prov_activity pa
             WHERE NEW.entity_type='prov_activity' AND pa.id=NEW.entity_ref AND pa.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM prov_agent pg
             WHERE NEW.entity_type='prov_agent' AND pg.id=NEW.entity_ref AND pg.project_id IS NOT NULL AND pg.project_id<>NEW.project_id)
BEGIN SELECT RAISE(ABORT,'provenance entity scope mismatch'); END;
CREATE TRIGGER check_prov_entity_scope_update
BEFORE UPDATE ON prov_entity
WHEN NEW.entity_type NOT IN ('entity','document_node','revision','source_span','assertion','character_state','event','ai_run','actor','external_source','prov_entity','prov_activity','prov_agent')
  OR (NEW.entity_type='entity' AND NOT EXISTS (SELECT 1 FROM entity e WHERE e.id=NEW.entity_ref))
  OR (NEW.entity_type='document_node' AND NOT EXISTS (SELECT 1 FROM document_node n WHERE n.id=NEW.entity_ref))
  OR (NEW.entity_type='revision' AND NOT EXISTS (SELECT 1 FROM document_revision r WHERE r.id=NEW.entity_ref))
  OR (NEW.entity_type='source_span' AND NOT EXISTS (SELECT 1 FROM source_span x WHERE x.id=NEW.entity_ref))
  OR (NEW.entity_type='assertion' AND NOT EXISTS (SELECT 1 FROM assertion a WHERE a.id=NEW.entity_ref))
  OR (NEW.entity_type='character_state' AND NOT EXISTS (SELECT 1 FROM character_state c WHERE c.id=NEW.entity_ref))
  OR (NEW.entity_type='event' AND NOT EXISTS (SELECT 1 FROM event_log e WHERE e.id=NEW.entity_ref))
  OR (NEW.entity_type='ai_run' AND NOT EXISTS (SELECT 1 FROM ai_run r WHERE r.id=NEW.entity_ref))
  OR (NEW.entity_type='actor' AND NOT EXISTS (SELECT 1 FROM actor a WHERE a.id=NEW.entity_ref))
  OR (NEW.entity_type='external_source' AND NOT EXISTS (SELECT 1 FROM external_source x WHERE x.id=NEW.entity_ref))
  OR (NEW.entity_type='prov_entity' AND NOT EXISTS (SELECT 1 FROM prov_entity p WHERE p.id=NEW.entity_ref))
  OR (NEW.entity_type='prov_activity' AND NOT EXISTS (SELECT 1 FROM prov_activity p WHERE p.id=NEW.entity_ref))
  OR (NEW.entity_type='prov_agent' AND NOT EXISTS (SELECT 1 FROM prov_agent p WHERE p.id=NEW.entity_ref))
  OR EXISTS (SELECT 1 FROM entity e
             WHERE e.id=NEW.entity_ref AND e.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM document_node n
             WHERE n.id=NEW.entity_ref AND n.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
             WHERE NEW.entity_type='revision' AND r.id=NEW.entity_ref AND n.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM source_span s
             WHERE NEW.entity_type='source_span' AND s.id=NEW.entity_ref AND s.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM assertion a
             WHERE NEW.entity_type='assertion' AND a.id=NEW.entity_ref AND a.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM character_state cs
             WHERE NEW.entity_type='character_state' AND cs.id=NEW.entity_ref AND cs.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM event_log ev
             WHERE NEW.entity_type='event' AND ev.id=NEW.entity_ref AND ev.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM ai_run ar
             WHERE NEW.entity_type='ai_run' AND ar.id=NEW.entity_ref AND ar.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM actor ac
             WHERE NEW.entity_type='actor' AND ac.id=NEW.entity_ref AND ac.project_id IS NOT NULL AND ac.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM external_source xs
             WHERE NEW.entity_type='external_source' AND xs.id=NEW.entity_ref AND xs.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM prov_entity pe
             WHERE NEW.entity_type='prov_entity' AND pe.id=NEW.entity_ref AND pe.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM prov_activity pa
             WHERE NEW.entity_type='prov_activity' AND pa.id=NEW.entity_ref AND pa.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM prov_agent pg
             WHERE NEW.entity_type='prov_agent' AND pg.id=NEW.entity_ref AND pg.project_id IS NOT NULL AND pg.project_id<>NEW.project_id)
BEGIN SELECT RAISE(ABORT,'provenance entity scope mismatch'); END;

CREATE TRIGGER check_prov_activity_scope
BEFORE INSERT ON prov_activity
WHEN EXISTS (SELECT 1 FROM ai_run r
             WHERE r.id=NEW.ai_run_id AND r.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM event_log ev
             WHERE ev.id=NEW.event_id AND ev.project_id<>NEW.project_id)
BEGIN SELECT RAISE(ABORT,'provenance activity scope mismatch'); END;
CREATE TRIGGER check_prov_activity_scope_update
BEFORE UPDATE ON prov_activity
WHEN EXISTS (SELECT 1 FROM ai_run r
             WHERE r.id=NEW.ai_run_id AND r.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM event_log ev
             WHERE ev.id=NEW.event_id AND ev.project_id<>NEW.project_id)
BEGIN SELECT RAISE(ABORT,'provenance activity scope mismatch'); END;

CREATE TRIGGER check_prov_agent_scope
BEFORE INSERT ON prov_agent
WHEN NEW.project_id IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM project p WHERE p.id=NEW.project_id)
BEGIN SELECT RAISE(ABORT,'provenance agent project mismatch'); END;
CREATE TRIGGER check_prov_agent_scope_update
BEFORE UPDATE ON prov_agent
WHEN NEW.project_id IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM project p WHERE p.id=NEW.project_id)
BEGIN SELECT RAISE(ABORT,'provenance agent project mismatch'); END;

CREATE TRIGGER check_prov_edge_scope
BEFORE INSERT ON prov_edge
WHEN EXISTS (SELECT 1 FROM entity e
             WHERE e.id=NEW.subject_id AND e.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM entity e
             WHERE e.id=NEW.object_id AND e.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM document_node n
             WHERE n.id=NEW.subject_id AND n.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM document_node n
             WHERE n.id=NEW.object_id AND n.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
             WHERE r.id=NEW.subject_id AND n.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
             WHERE r.id=NEW.object_id AND n.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM event_log e
             WHERE e.id=NEW.subject_id AND e.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM event_log e
             WHERE e.id=NEW.object_id AND e.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM ai_run r
             WHERE r.id=NEW.subject_id AND r.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM ai_run r
             WHERE r.id=NEW.object_id AND r.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM source_span s
             WHERE s.id=NEW.subject_id AND s.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM source_span s
             WHERE s.id=NEW.object_id AND s.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM assertion a
             WHERE a.id=NEW.subject_id AND a.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM assertion a
             WHERE a.id=NEW.object_id AND a.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM external_source x
             WHERE x.id IN (NEW.subject_id,NEW.object_id) AND x.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM actor a
             WHERE a.id IN (NEW.subject_id,NEW.object_id) AND a.project_id IS NOT NULL AND a.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM prov_entity pe
             WHERE pe.id IN (NEW.subject_id,NEW.object_id) AND pe.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM prov_activity pa
             WHERE pa.id IN (NEW.subject_id,NEW.object_id) AND pa.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM prov_agent pg
             WHERE pg.id IN (NEW.subject_id,NEW.object_id) AND pg.project_id IS NOT NULL AND pg.project_id<>NEW.project_id)
BEGIN SELECT RAISE(ABORT,'provenance edge scope mismatch'); END;
CREATE TRIGGER check_prov_edge_scope_update
BEFORE UPDATE ON prov_edge
WHEN EXISTS (SELECT 1 FROM entity e
             WHERE e.id=NEW.subject_id AND e.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM entity e
             WHERE e.id=NEW.object_id AND e.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM document_node n
             WHERE n.id=NEW.subject_id AND n.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM document_node n
             WHERE n.id=NEW.object_id AND n.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
             WHERE r.id=NEW.subject_id AND n.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
             WHERE r.id=NEW.object_id AND n.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM event_log e
             WHERE e.id=NEW.subject_id AND e.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM event_log e
             WHERE e.id=NEW.object_id AND e.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM ai_run r
             WHERE r.id=NEW.subject_id AND r.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM ai_run r
             WHERE r.id=NEW.object_id AND r.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM source_span s
             WHERE s.id=NEW.subject_id AND s.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM source_span s
             WHERE s.id=NEW.object_id AND s.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM assertion a
             WHERE a.id=NEW.subject_id AND a.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM assertion a
             WHERE a.id=NEW.object_id AND a.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM external_source x
             WHERE x.id IN (NEW.subject_id,NEW.object_id) AND x.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM actor a
             WHERE a.id IN (NEW.subject_id,NEW.object_id) AND a.project_id IS NOT NULL AND a.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM prov_entity pe
             WHERE pe.id IN (NEW.subject_id,NEW.object_id) AND pe.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM prov_activity pa
             WHERE pa.id IN (NEW.subject_id,NEW.object_id) AND pa.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM prov_agent pg
             WHERE pg.id IN (NEW.subject_id,NEW.object_id) AND pg.project_id IS NOT NULL AND pg.project_id<>NEW.project_id)
BEGIN SELECT RAISE(ABORT,'provenance edge scope mismatch'); END;

CREATE TRIGGER check_index_checkpoint_scope
BEFORE INSERT ON index_checkpoint
WHEN EXISTS (SELECT 1 FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
             WHERE r.id=NEW.source_revision AND n.project_id<>NEW.project_id)
BEGIN SELECT RAISE(ABORT,'index checkpoint scope mismatch'); END;
CREATE TRIGGER check_index_checkpoint_scope_update
BEFORE UPDATE ON index_checkpoint
WHEN EXISTS (SELECT 1 FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
             WHERE r.id=NEW.source_revision AND n.project_id<>NEW.project_id)
BEGIN SELECT RAISE(ABORT,'index checkpoint scope mismatch'); END;

CREATE TRIGGER check_export_job_scope
BEFORE INSERT ON export_job
WHEN EXISTS (SELECT 1 FROM branch b
             WHERE b.id=NEW.branch_id AND b.project_id<>NEW.project_id)
BEGIN SELECT RAISE(ABORT,'export job scope mismatch'); END;
CREATE TRIGGER check_export_job_scope_update
BEFORE UPDATE ON export_job
WHEN EXISTS (SELECT 1 FROM branch b
             WHERE b.id=NEW.branch_id AND b.project_id<>NEW.project_id)
BEGIN SELECT RAISE(ABORT,'export job scope mismatch'); END;

CREATE TRIGGER check_merge_conflict_scope
BEFORE INSERT ON merge_conflict
WHEN EXISTS (SELECT 1 FROM branch b
             WHERE b.id=NEW.branch_id AND b.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
             WHERE r.id=NEW.merge_base
               AND (r.branch_id<>NEW.branch_id OR n.project_id<>NEW.project_id))
  OR EXISTS (SELECT 1 FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
             WHERE r.id=NEW.left_revision
               AND (r.branch_id<>NEW.branch_id OR n.project_id<>NEW.project_id))
  OR EXISTS (SELECT 1 FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
             WHERE r.id=NEW.right_revision
               AND (r.branch_id<>NEW.branch_id OR n.project_id<>NEW.project_id))
BEGIN SELECT RAISE(ABORT,'merge conflict scope mismatch'); END;
CREATE TRIGGER check_merge_conflict_scope_update
BEFORE UPDATE ON merge_conflict
WHEN EXISTS (SELECT 1 FROM branch b
             WHERE b.id=NEW.branch_id AND b.project_id<>NEW.project_id)
  OR EXISTS (SELECT 1 FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
             WHERE r.id=NEW.merge_base
               AND (r.branch_id<>NEW.branch_id OR n.project_id<>NEW.project_id))
  OR EXISTS (SELECT 1 FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
             WHERE r.id=NEW.left_revision
               AND (r.branch_id<>NEW.branch_id OR n.project_id<>NEW.project_id))
  OR EXISTS (SELECT 1 FROM document_revision r JOIN document_node n ON n.id=r.document_node_id
             WHERE r.id=NEW.right_revision
               AND (r.branch_id<>NEW.branch_id OR n.project_id<>NEW.project_id))
BEGIN SELECT RAISE(ABORT,'merge conflict scope mismatch'); END;

CREATE TRIGGER check_actor_rehome
BEFORE UPDATE ON actor
WHEN NEW.project_id<>OLD.project_id
 AND (EXISTS (SELECT 1 FROM document_revision r WHERE r.actor_id=OLD.id)
      OR EXISTS (SELECT 1 FROM event_log e WHERE e.actor_id=OLD.id)
      OR EXISTS (SELECT 1 FROM deletion_event d WHERE d.actor_id=OLD.id)
      OR EXISTS (SELECT 1 FROM audit_event a WHERE a.actor_id=OLD.id))
BEGIN SELECT RAISE(ABORT,'actor project scope mismatch'); END;
CREATE TRIGGER check_actor_scope
BEFORE INSERT ON actor
WHEN NEW.project_id IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM project p WHERE p.id=NEW.project_id)
BEGIN SELECT RAISE(ABORT,'actor project scope mismatch'); END;
CREATE TRIGGER check_actor_scope_update
BEFORE UPDATE ON actor
WHEN NEW.project_id IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM project p WHERE p.id=NEW.project_id)
BEGIN SELECT RAISE(ABORT,'actor project scope mismatch'); END;
CREATE TRIGGER check_revision_actor_scope
BEFORE INSERT ON document_revision
WHEN EXISTS (SELECT 1 FROM actor a
             WHERE a.id=NEW.actor_id AND a.project_id IS NOT NULL
               AND EXISTS (SELECT 1 FROM document_node n
                           JOIN branch b ON b.id=NEW.branch_id
                           WHERE n.id=NEW.document_node_id
                             AND a.project_id<>n.project_id))
BEGIN SELECT RAISE(ABORT,'revision actor scope mismatch'); END;
CREATE TRIGGER check_event_actor_scope
BEFORE INSERT ON event_log
WHEN EXISTS (SELECT 1 FROM actor a
             WHERE a.id=NEW.actor_id AND a.project_id IS NOT NULL
               AND a.project_id<>NEW.project_id)
BEGIN SELECT RAISE(ABORT,'event actor scope mismatch'); END;
CREATE TRIGGER check_deletion_actor_scope
BEFORE INSERT ON deletion_event
WHEN EXISTS (SELECT 1 FROM actor a
             WHERE a.id=NEW.actor_id AND a.project_id IS NOT NULL
               AND a.project_id<>NEW.project_id)
BEGIN SELECT RAISE(ABORT,'deletion actor scope mismatch'); END;
CREATE TRIGGER check_audit_actor_scope
BEFORE INSERT ON audit_event
WHEN EXISTS (SELECT 1 FROM actor a
             WHERE a.id=NEW.actor_id AND a.project_id IS NOT NULL
               AND a.project_id<>NEW.project_id)
BEGIN SELECT RAISE(ABORT,'audit actor scope mismatch'); END;

CREATE TABLE run_manifest(id TEXT PRIMARY KEY, ai_run_id TEXT NOT NULL REFERENCES ai_run(id), project_id TEXT NOT NULL REFERENCES project(id), sanitized_prompt TEXT, context_refs_json TEXT NOT NULL DEFAULT '[]', provider_endpoint TEXT, api_version TEXT, model_snapshot TEXT, sampling_json TEXT NOT NULL DEFAULT '{}', tool_results_json TEXT NOT NULL DEFAULT '[]', author_actions_json TEXT NOT NULL DEFAULT '[]', consent_json TEXT NOT NULL DEFAULT '{}', retention_until TEXT, redaction_policy TEXT NOT NULL, manifest_hash TEXT NOT NULL);
CREATE TRIGGER check_run_manifest_scope
BEFORE INSERT ON run_manifest
WHEN EXISTS (SELECT 1 FROM ai_run r
             WHERE r.id=NEW.ai_run_id AND r.project_id<>NEW.project_id)
BEGIN SELECT RAISE(ABORT,'run manifest scope mismatch'); END;
CREATE TRIGGER check_run_manifest_scope_update
BEFORE UPDATE ON run_manifest
WHEN EXISTS (SELECT 1 FROM ai_run r
             WHERE r.id=NEW.ai_run_id AND r.project_id<>NEW.project_id)
BEGIN SELECT RAISE(ABORT,'run manifest scope mismatch'); END;
CREATE TRIGGER prevent_run_manifest_update BEFORE UPDATE ON run_manifest BEGIN SELECT RAISE(ABORT,'immutable run manifest'); END;
CREATE TRIGGER prevent_run_manifest_delete BEFORE DELETE ON run_manifest BEGIN SELECT RAISE(ABORT,'immutable run manifest'); END;
CREATE TRIGGER prevent_run_manifest_replace BEFORE INSERT ON run_manifest WHEN EXISTS (SELECT 1 FROM run_manifest p WHERE p.id=NEW.id) BEGIN SELECT RAISE(ABORT,'immutable run manifest'); END;

-- Keep existing projects honest about the migration level while preserving
-- any project that is already on a newer schema.
UPDATE project SET schema_version=11 WHERE schema_version<11;
PRAGMA user_version = 11;
COMMIT;

-- Direct SQL execution is intentionally one-shot because SQLite cannot
-- conditionally skip ALTER TABLE statements. Use scripts/apply_migration.py
-- for idempotent/retry-safe application; it checks migration_ledger(version).
