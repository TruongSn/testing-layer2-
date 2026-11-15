import copy
import json
import tempfile
import unittest
import os
from pathlib import Path
from lifemate_graph.builder import build, input_inventory
from lifemate_graph.common import read_json, read_csv, sha256, object_sha, schema_errors
from lifemate_graph.compiler import compile_graph, load_compiled
from tests.fixtures import compiled, edge, tool

ROOT=Path(__file__).resolve().parents[1]


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.graph=read_json(ROOT/'artifacts/full_dependency_graph_v3.json')
        self.manifest=read_json(ROOT/'artifacts/graph_manifest.json')
    def test_all_147_tools_audited(self):
        audit=read_json(ROOT/'artifacts/tool_registry_audit.json')
        self.assertEqual((audit['app_count'],audit['tool_count']),(46,147))
        self.assertEqual(set(t['name'] for t in audit['tools']),set(self.graph['tools']))
        self.assertEqual(len(read_csv(ROOT/'artifacts/tool_coverage.csv')),147)
    def test_no_pending_edge_enabled(self):
        self.assertTrue(all(not e['runtime_enabled'] and e['operator_decision']=='PENDING' for e in self.graph['dependencies']))
        self.assertEqual(read_json(ROOT/'artifacts/approved_runtime_graph.json')['dependencies'],[])
    def test_preserved_curated_decisions(self):
        curated=[e['legacy_curated'] for e in self.graph['dependencies'] if e['legacy_curated']]
        self.assertEqual(len(curated),20)
        self.assertTrue(all(e['operator_decision']=='PENDING' and not e['runtime_enabled'] for e in curated))
    def test_manifest_artifact_digests(self):
        for name, digest in self.manifest['artifacts_sha256'].items():
            self.assertEqual(sha256(ROOT/'artifacts'/name),digest,name)
    def test_compiled_loader_checks_actual_catalog(self):
        g=load_compiled(ROOT/'artifacts/compiled_graph_v3.json',ROOT/'artifacts/graph_manifest.json',ROOT/'inputs/train_tools_catalog.json')
        self.assertEqual(g['edges'],{})
    def test_all_node_types_and_proven_field_relations(self):
        types={n['type'] for n in self.graph['nodes']}
        self.assertEqual(types,{'TOOL','ARGUMENT','OUTPUT_FIELD','RESOURCE','ENTITY_TYPE','PRECONDITION'})
        relations={r['type'] for r in self.graph['relations']}
        self.assertTrue({'PRODUCES','CONSUMES','REQUIRES_DATA_FROM','CONDITIONAL_DEPENDENCY','REFERENCES_RESOURCE','ALTERNATIVE_INPUT','INVALIDATES_EVIDENCE'}<=relations)
        nodes={n['id'] for n in self.graph['nodes']}
        self.assertTrue(all(r['from'] in nodes and r['to'] in nodes for r in self.graph['relations']))
    def test_membership_is_not_current_value_copy(self):
        by_key={(e['producer_tool'],e['consumer_tool'],e['target_argument_path']):e for e in self.graph['dependencies']}
        for key,path in [(('appliances.list','appliances.start_cycle','cycle'),'appliances[].cycles[]'),
                         (('household_chores.list_chores','household_chores.assign','assignee'),'members[]'),
                         (('tv.guide','tv.tune','channel'),'channels[]')]:
            self.assertEqual(by_key[key]['source_output_path'],path)
            self.assertEqual(by_key[key]['validation_status'],'NEEDS_REVIEW')
    def test_nested_array_contract_is_retained_for_review(self):
        matches=[e for e in self.graph['dependencies'] if e['producer_tool']=='food_delivery.get_menu' and e['target_argument_path']=='items[].item_id']
        self.assertEqual(len(matches),1);self.assertEqual(matches[0]['validation_status'],'NEEDS_REVIEW')
    def test_music_track_not_nested_playlist_identifier(self):
        e=next(e for e in self.graph['dependencies'] if e['consumer_tool']=='music.play' and e['target_argument_path']=='track_id')
        self.assertEqual(e['source_output_path'],'tracks[].id')
    def test_legacy_counts_not_dependency_count(self):
        self.assertEqual(self.graph['legacy_counts']['structural_edges'],392)
        self.assertEqual(self.graph['legacy_counts']['metadata_candidates'],75)
        legacy=read_json(ROOT/'artifacts/legacy_provenance_v3.json')
        self.assertEqual(len(legacy['manual_review_queue_v2.csv']),161)
    def test_runtime_bundle_has_no_gold_trace_values(self):
        g=read_json(ROOT/'artifacts/compiled_graph_v3.json')
        raw=json.dumps(g)
        self.assertNotIn('sample_ids',raw);self.assertNotIn('gold_calls',raw);self.assertNotIn('final_state',raw)
    def test_bad_catalog_digest_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'catalog.json';p.write_text('{}')
            with self.assertRaises(ValueError):load_compiled(ROOT/'artifacts/compiled_graph_v3.json',catalog_path=p)


@unittest.skipUnless((ROOT/'inputs/train.jsonl').is_file(),'Raw train is intentionally not embedded in the standalone Kaggle notebook')
class SourceAndBuildTests(unittest.TestCase):
    def test_original_manifest_and_data_unchanged(self):
        self.assertEqual(len(input_inventory(ROOT)),13)
    @unittest.skipUnless(os.environ.get('LIFEMATE_RUN_OFFLINE_BUILD')=='1', 'Frozen-graph activation does not rebuild discovery; set LIFEMATE_RUN_OFFLINE_BUILD=1 for the offline build test')
    def test_build_is_byte_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=build(ROOT,Path(tmp)/'a'); b=build(ROOT,Path(tmp)/'b')
            self.assertEqual(a,b)
            expected=read_json(ROOT/'artifacts/graph_manifest.json')
            self.assertEqual(a['graph_content_sha256'],expected['graph_content_sha256'])
    def test_jsonl_schema_audit_is_complete_and_honest(self):
        report=read_json(ROOT/'artifacts/graph_validation_report.json')['data_schema_audit']
        self.assertEqual(report['format'],'JSONL');self.assertEqual(report['samples'],1545)
        self.assertEqual(report['label_calls'],3541)
        self.assertTrue(any(e['sample']=='train_1060' and e['origin']=='history' for e in report['schema_errors']))


class ApprovalTests(unittest.TestCase):
    def test_no_approval_no_execution(self):
        g={'version':'fixture','catalog_digest':'x','tools':{'demo.people':tool('demo.people'),'demo.send':tool('demo.send','write')},'dependencies':[]}
        self.assertEqual(compile_graph(g)[0]['edges'],{})
    def test_missing_reviewer_or_wrong_digest_rejected(self):
        g={'version':'fixture','catalog_digest':'x','tools':{},'dependencies':[]}
        for overlay in [{'graph_content_sha256':'wrong','reviewer':'operator'},{'graph_content_sha256':object_sha(g)}]:
            with self.assertRaises(ValueError):compile_graph(g,overlay)
    def test_approved_fixture_is_scope_isolated(self):
        c=compiled();self.assertEqual(c['runtime_scope'],'SYNTHETIC_ONLY')
        self.assertTrue(all(e['approval']['scope']=='SYNTHETIC_ONLY' for e in c['edges'].values()))
    def test_write_cannot_be_compiled_as_prerequisite(self):
        ts={'demo.people':tool('demo.people','write'),'demo.send':tool('demo.send','write',{'contact_id':{'type':'string'}},['contact_id'])}
        with self.assertRaises(ValueError):compiled(ts,[edge()])
    def test_schema_types_and_nested_required(self):
        schema={'type':'object','properties':{'rows':{'type':'array','items':{'type':'object','properties':{'id':{'type':'string'}},'required':['id']}}},'required':['rows']}
        self.assertTrue(schema_errors({'rows':[{}]},schema))
        self.assertTrue(schema_errors(True,{'type':'integer'}))


if __name__=='__main__':unittest.main()
