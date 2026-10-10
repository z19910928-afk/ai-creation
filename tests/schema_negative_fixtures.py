"""Executable P0 schema regression fixtures.
Run: python tests/schema_negative_fixtures.py
"""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def setup():
    c = sqlite3.connect(':memory:')
    c.execute('PRAGMA foreign_keys=ON')
    c.executescript((ROOT/'docs/schema-v1.sql').read_text())
    c.executescript((ROOT/'docs/schema-v1.1-migration.sql').read_text())
    c.execute("INSERT INTO project VALUES('p1',1,'P1',datetime('now'))")
    c.execute("INSERT INTO project VALUES('p2',1,'P2',datetime('now'))")
    c.execute("INSERT INTO branch VALUES('b1','p1',NULL,NULL,NULL,'active','{}')")
    c.execute("INSERT INTO branch VALUES('b2','p2',NULL,NULL,NULL,'active','{}')")
    c.execute("INSERT INTO branch VALUES('b1x','p1',NULL,NULL,NULL,'active','{}')")
    return c

def expect_fail(label, fn):
    try:
        fn()
    except (sqlite3.IntegrityError, sqlite3.OperationalError):
        return
    raise AssertionError(label + ' unexpectedly passed')

c = setup()
assert c.execute('PRAGMA user_version').fetchone()[0] == 11
assert c.execute('SELECT version FROM migration_ledger').fetchone()[0] == 11
expect_fail('cross-project branch parent insert', lambda: c.execute("INSERT INTO branch VALUES('b3','p1','b2',NULL,NULL,'active','{}')"))
expect_fail('cross-project branch parent update', lambda: c.execute("UPDATE branch SET parent_id='b2' WHERE id='b1'"))
# Nodes must inherit both project and branch scope, including parent links.
expect_fail('document node project/branch mismatch', lambda: c.execute("INSERT INTO document_node VALUES('n_bad','p1','b2',NULL,'chapter','bad',1)"))
c.execute("INSERT INTO document_node VALUES('n1','p1','b1',NULL,'chapter','C1',1)")
c.execute("INSERT INTO document_node VALUES('n1x','p1','b1x',NULL,'chapter','C1x',1)")
c.execute("INSERT INTO actor VALUES('act1','p1','user','Author')")
c.execute("INSERT INTO actor VALUES('act2','p2','user','Other')")
expect_fail('document node parent branch mismatch', lambda: c.execute("INSERT INTO document_node VALUES('n_bad_parent','p1','b1', 'n1x','scene','bad',2)"))
expect_fail('document node cross-project update', lambda: c.execute("UPDATE document_node SET project_id='p2' WHERE id='n1'"))
expect_fail('branch project update with scoped rows', lambda: c.execute("UPDATE branch SET project_id='p2' WHERE id='b1'"))
c.execute("INSERT INTO document_revision VALUES('r1','n1',NULL,'b1','hash',x'61',datetime('now'),'user','create')")
c.execute("INSERT INTO document_revision VALUES('r1x','n1x',NULL,'b1x','hashx',x'62',datetime('now'),'user','create')")
expect_fail('revision node/branch mismatch', lambda: c.execute("INSERT INTO document_revision VALUES('r_bad','n1',NULL,'b1x','hash',x'63',datetime('now'),'user','create')"))
expect_fail('revision parent node/branch mismatch', lambda: c.execute("INSERT INTO document_revision VALUES('r_bad_parent','n1x','r1','b1x','hash',x'64',datetime('now'),'user','create')"))
expect_fail('revision actor project mismatch', lambda: c.execute("INSERT INTO document_revision VALUES('r_bad_actor','n1',NULL,'b1','hash',x'64',datetime('now'),'act2','create')"))
expect_fail('revision update', lambda: c.execute("UPDATE document_revision SET content_hash='x' WHERE id='r1'"))
expect_fail('revision scope update', lambda: c.execute("UPDATE document_revision SET branch_id='b1x' WHERE id='r1'"))
expect_fail('revision replace', lambda: c.execute("INSERT OR REPLACE INTO document_revision VALUES('r1','n1',NULL,'b1','hash2',x'62',datetime('now'),'user','replace')"))
c.execute("INSERT INTO event_log VALUES('e1','p1','b1','r1','create','k1',NULL,'{}','user',datetime('now'))")
expect_fail('event project/branch/revision mismatch', lambda: c.execute("INSERT INTO event_log VALUES('e_bad','p1','b1x','r1','create','k_bad',NULL,'{}','user',datetime('now'))"))
expect_fail('event actor project mismatch', lambda: c.execute("INSERT INTO event_log VALUES('e_bad_actor','p1','b1','r1','create','k_bad_actor',NULL,'{}','act2',datetime('now'))"))
c.execute("INSERT INTO event_log VALUES('e_actor','p1','b1','r1','create','k_actor',NULL,'{}','act1',datetime('now'))")
expect_fail('actor project rehome with references', lambda: c.execute("UPDATE actor SET project_id='p2' WHERE id='act1'"))
expect_fail('event update', lambda: c.execute("UPDATE event_log SET payload_json='{}' WHERE id='e1'"))
expect_fail('event replace', lambda: c.execute("INSERT OR REPLACE INTO event_log VALUES('e1','p1','b1','r1','create','k1',NULL,'{}','user',datetime('now'))"))
# Source spans must match their revision's project and node.
c.execute("INSERT INTO document_node VALUES('n2','p2','b2',NULL,'chapter','C2',1)")
c.execute("INSERT INTO document_revision VALUES('r2','n2',NULL,'b2','hash2',x'65',datetime('now'),'user','create')")
c.execute("INSERT INTO source_span(id,project_id,revision_id,document_node_id,start_codepoint,end_codepoint,start_utf8,end_utf8,quote_hash,quote,anchor_before,anchor_after,status) VALUES('s1','p1','r1','n1',0,1,0,1,'qh','a',NULL,NULL,'valid')")
c.execute("INSERT INTO source_span(id,project_id,revision_id,document_node_id,start_codepoint,end_codepoint,start_utf8,end_utf8,quote_hash,quote,anchor_before,anchor_after,status) VALUES('s2','p2','r2','n2',0,1,0,1,'qh2','b',NULL,NULL,'valid')")
expect_fail('source span cross-project insert', lambda: c.execute("INSERT INTO source_span(id,project_id,revision_id,document_node_id,start_codepoint,end_codepoint,start_utf8,end_utf8,quote_hash,quote,anchor_before,anchor_after,status) VALUES('s_bad','p1','r2','n2',0,1,0,1,'qh','b',NULL,NULL,'valid')"))
expect_fail('source span project update', lambda: c.execute("UPDATE source_span SET project_id='p2' WHERE id='s1'"))
expect_fail('source span node update', lambda: c.execute("UPDATE source_span SET document_node_id='n1x' WHERE id='s1'"))
# Assertion canon requires non-empty evidence array, reviewer and review time.
c.execute("INSERT INTO entity(id,project_id,kind,canonical_name,aliases_json,status) VALUES('ent1','p1','person','E','[]','active')")
c.execute("INSERT INTO entity(id,project_id,kind,canonical_name,aliases_json,status) VALUES('ent2','p2','person','E2','[]','active')")
# Candidate references must stay in the branch/project of their source span.
c.execute("INSERT INTO candidate_memory(id,project_id,branch_id,source_span_id,entity_id,kind,payload_json,status,confidence,created_at,reviewed_at,reviewed_by) VALUES('c1','p1','b1','s1','ent1','fact','{}','candidate',0.5,datetime('now'),NULL,NULL)")
expect_fail('candidate cross-branch source span', lambda: c.execute("INSERT INTO candidate_memory(id,project_id,branch_id,source_span_id,entity_id,kind,payload_json,status,confidence,created_at,reviewed_at,reviewed_by) VALUES('c_bad_branch','p1','b1x','s1','ent1','fact','{}','candidate',0.5,datetime('now'),NULL,NULL)"))
expect_fail('candidate source span project update', lambda: c.execute("UPDATE candidate_memory SET source_span_id='s2' WHERE id='c1'"))
expect_fail('candidate branch/project update', lambda: c.execute("UPDATE candidate_memory SET branch_id='b2',project_id='p2' WHERE id='c1'"))
# Deletion events also bind revision, branch and project.
c.execute("INSERT INTO deletion_event VALUES('d1','p1','b1','r1','r1','cleanup','document','now','user')")
expect_fail('deletion revision branch mismatch', lambda: c.execute("INSERT INTO deletion_event VALUES('d_bad','p1','b1x','r1','r1','cleanup','document','now','user')"))
expect_fail('deletion project update', lambda: c.execute("UPDATE deletion_event SET project_id='p2' WHERE id='d1'"))
expect_fail('audit actor project mismatch', lambda: c.execute("INSERT INTO audit_event VALUES('audit_bad','p1','act2','review','r1',NULL,NULL,NULL,datetime('now'))"))
def insert_assertion(state, evidence, reviewer=None, reviewed_at=None):
    c.execute("INSERT INTO assertion(id,project_id,branch_id,subject_entity_id,predicate,object_json,epistemic_mode,state,confidence,evidence_json,reviewed_by,reviewed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ('tmp','p1','b1','ent1','knows','{}','world_truth',state,0.8,evidence,reviewer,reviewed_at))

expect_fail('canon without evidence/reviewer', lambda: insert_assertion('canon','[]'))
c.execute("INSERT INTO assertion(id,project_id,branch_id,subject_entity_id,predicate,object_json,epistemic_mode,state,confidence,evidence_json,reviewed_by,reviewed_at) VALUES('a1','p1','b1','ent1','knows','{}','world_truth','canon',0.8,'[{\"revision_id\":\"r1\"}]','author',datetime('now'))")
c.execute("INSERT INTO assertion(id,project_id,branch_id,subject_entity_id,predicate,object_json,epistemic_mode,state,confidence,evidence_json,reviewed_by,reviewed_at) VALUES('a2','p2','b2','ent2','knows','{}','world_truth','canon',0.8,'[{\"revision_id\":\"r2\"}]','author',datetime('now'))")
expect_fail('assertion branch update', lambda: c.execute("UPDATE assertion SET branch_id='b2' WHERE id='a1'"))
expect_fail('assertion project update', lambda: c.execute("UPDATE assertion SET project_id='p2' WHERE id='a1'"))
# Remaining projection/provenance tables carry project/branch composites too.
c.execute("INSERT INTO ai_run(id,project_id,branch_id,mode,provider,model,system_prompt_hash,prompt_hash,input_hash,started_at,status,error) VALUES('ar1','p1','b1','write','local','m','s','p','i',datetime('now'),'ok',NULL)")
c.execute("INSERT INTO ai_run(id,project_id,branch_id,mode,provider,model,system_prompt_hash,prompt_hash,input_hash,started_at,status,error) VALUES('ar2','p2','b2','write','local','m','s','p','i',datetime('now'),'ok',NULL)")
expect_fail('ai run branch/project mismatch', lambda: c.execute("INSERT INTO ai_run(id,project_id,branch_id,mode,provider,model,system_prompt_hash,prompt_hash,input_hash,started_at,status,error) VALUES('ar_bad','p1','b2','write','local','m','s','p','i',datetime('now'),'ok',NULL)"))
c.execute("INSERT INTO character_state VALUES('cs1','p1','b1','ent1','mood','{}','e1',NULL,NULL,'{}','a1')")
expect_fail('character state branch/project mismatch', lambda: c.execute("INSERT INTO character_state VALUES('cs_bad','p1','b1x','ent1','mood','{}','e1',NULL,NULL,'{}','a1')"))
expect_fail('character state scope update', lambda: c.execute("UPDATE character_state SET branch_id='b1x' WHERE id='cs1'"))
expect_fail('plot thread branch/project mismatch', lambda: c.execute("INSERT INTO plot_thread VALUES('pt_bad','p1','b2','open','{}','low')"))
c.execute("INSERT INTO plot_thread VALUES('pt1','p1','b1','open','{}','low')")
expect_fail('plot thread scope update', lambda: c.execute("UPDATE plot_thread SET branch_id='b2' WHERE id='pt1'"))
c.execute("INSERT INTO external_source VALUES('src1','p1','https://example.test',NULL,NULL,'{}','trusted')")
c.execute("UPDATE external_source SET uri='https://example.test/v2' WHERE id='src1'")
expect_fail('external source invalid project update', lambda: c.execute("UPDATE external_source SET project_id='p_missing' WHERE id='src1'"))
c.execute("INSERT INTO prov_entity VALUES('pe1','p1','entity','ent1',NULL,datetime('now'))")
expect_fail('provenance entity project mismatch', lambda: c.execute("INSERT INTO prov_entity VALUES('pe_bad','p1','entity','ent2',NULL,datetime('now'))"))
expect_fail('provenance entity scope update', lambda: c.execute("UPDATE prov_entity SET entity_ref='ent2' WHERE id='pe1'"))
expect_fail('provenance revision project mismatch', lambda: c.execute("INSERT INTO prov_entity VALUES('pe_rev_bad','p1','revision','r2',NULL,datetime('now'))"))
expect_fail('provenance assertion project mismatch', lambda: c.execute("INSERT INTO prov_entity VALUES('pe_assert_bad','p1','assertion','a2',NULL,datetime('now'))"))
expect_fail('provenance dangling typed ref', lambda: c.execute("INSERT INTO prov_entity VALUES('pe_missing','p1','actor','missing',NULL,datetime('now'))"))
c.execute("INSERT INTO prov_activity VALUES('pa1','p1','run','ar1','e1',datetime('now'),NULL,NULL)")
expect_fail('provenance activity project mismatch', lambda: c.execute("INSERT INTO prov_activity VALUES('pa_bad','p1','run','ar2','e1',datetime('now'),NULL,NULL)"))
expect_fail('provenance activity scope update', lambda: c.execute("UPDATE prov_activity SET ai_run_id='ar2' WHERE id='pa1'"))
c.execute("INSERT INTO prov_agent VALUES('agent1','p1','model','local','1')")
c.execute("UPDATE prov_agent SET version='2' WHERE id='agent1'")
expect_fail('provenance agent invalid project update', lambda: c.execute("UPDATE prov_agent SET project_id='p_missing' WHERE id='agent1'"))
c.execute("INSERT INTO prov_edge VALUES('edge1','p1','ent1','related','ent1','{}',0)")
# Generic PROV references must resolve to the same project.
c.execute("INSERT INTO external_source VALUES('src2','p2','https://other.test',NULL,NULL,'{}','trusted')")
expect_fail('provenance generic external source mismatch', lambda: c.execute("INSERT INTO prov_edge VALUES('edge_bad_src','p1','src2','related','ent1','{}',0)"))
expect_fail('provenance edge project mismatch', lambda: c.execute("INSERT INTO prov_edge VALUES('edge_bad','p1','ent1','related','ent2','{}',0)"))
expect_fail('provenance edge scope update', lambda: c.execute("UPDATE prov_edge SET object_id='ent2' WHERE id='edge1'"))
c.execute("INSERT INTO index_checkpoint VALUES('idx1','p1','r1','v1','m','current',NULL,datetime('now'))")
expect_fail('index checkpoint project mismatch', lambda: c.execute("INSERT INTO index_checkpoint VALUES('idx_bad','p1','r2','v1','m','current',NULL,datetime('now'))"))
expect_fail('index checkpoint scope update', lambda: c.execute("UPDATE index_checkpoint SET source_revision='r2' WHERE index_name='idx1'"))
c.execute("INSERT INTO merge_conflict VALUES('mc1','p1','b1','r1','r1','r1','content','{}','open',NULL,NULL)")
expect_fail('merge conflict branch/project mismatch', lambda: c.execute("INSERT INTO merge_conflict VALUES('mc_bad','p1','b1x','r1','r1','r1','content','{}','open',NULL,NULL)"))
expect_fail('merge conflict scope update', lambda: c.execute("UPDATE merge_conflict SET branch_id='b1x' WHERE id='mc1'"))
expect_fail('export job branch/project mismatch', lambda: c.execute("INSERT INTO export_job VALUES('x_bad','p1','b2','md','queued',NULL,'{}','u1',NULL,datetime('now'),NULL)"))
c.execute("INSERT INTO run_manifest VALUES('rm1','ar1','p1',NULL,'[]',NULL,NULL,NULL,'{}','[]','[]','{}',NULL,'strict','mh')")
expect_fail('run manifest ai-run project mismatch', lambda: c.execute("INSERT INTO run_manifest VALUES('rm_bad','ar2','p1',NULL,'[]',NULL,NULL,NULL,'{}','[]','[]','{}',NULL,'strict','mh')"))
expect_fail('run manifest scope update', lambda: c.execute("UPDATE run_manifest SET project_id='p2' WHERE id='rm1'"))
# Export must be explicitly confirmed from awaiting_confirmation.
c.execute("INSERT INTO export_job VALUES('x1','p1','b1','md','awaiting_confirmation',NULL,'{}','u1',NULL,datetime('now'),NULL)")
expect_fail('export success without confirmer', lambda: c.execute("UPDATE export_job SET status='success' WHERE id='x1'"))
expect_fail('export job scope update', lambda: c.execute("UPDATE export_job SET branch_id='b2' WHERE id='x1'"))
c.execute("UPDATE export_job SET status='success', confirmed_by='author', completed_at=datetime('now') WHERE id='x1'")
print('schema regression fixtures passed: scope, append-only, canon evidence, export confirmation, migration ledger')
