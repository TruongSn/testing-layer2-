"""Pre-approval artifact checks only. No graph compilation or tool execution."""
import unittest
from pathlib import Path
from lifemate_graph.common import read_csv, read_json, sha256, object_sha

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless((ROOT/'runtime_review/activation_manifest.json').is_file(), 'Final activation proposal not packaged in this environment')
class RuntimeApprovalReviewTests(unittest.TestCase):
    def test_every_verified_edge_has_one_explicit_review(self):
        g=read_json(ROOT/'artifacts/full_dependency_graph_v3.json')
        rows=read_csv(ROOT/'runtime_approval_review.csv')
        ids=[r['edge_id'] for r in rows]
        self.assertEqual(len(ids),72)
        self.assertEqual(len(set(ids)),72)
        self.assertEqual(set(ids),{e['edge_id'] for e in g['dependencies'] if e['validation_status']=='VERIFIED'})
        self.assertTrue(all(r['reason'] and r['evidence'] and r['required_conditions'] for r in rows))
    def test_proposal_grants_no_operator_authority(self):
        draft=read_json(ROOT/'operator_approvals.proposed.json')
        self.assertIsNone(draft['reviewer'])
        self.assertEqual(len(draft['decisions']),19)
        self.assertTrue(all(d['decision']=='PENDING' for d in draft['decisions']))
        self.assertEqual(draft['graph_content_sha256'],object_sha(read_json(ROOT/'artifacts/full_dependency_graph_v3.json')))
        by_id={e['edge_id']:e for e in read_json(ROOT/'artifacts/full_dependency_graph_v3.json')['dependencies']}
        tools=read_json(ROOT/'artifacts/full_dependency_graph_v3.json')['tools']
        for d in draft['decisions']:
            e=by_id[d['edge_id']]
            self.assertEqual(tools[e['producer_tool']]['kind'],'read')
            self.assertIn(tools[e['consumer_tool']]['kind'],{'read','write'})
            self.assertEqual(d['source_output_path'],e['source_output_path'])
            self.assertLessEqual(set(d['selector_keys']),set(e['subject_binding_rule']['allowed_selector_keys']))
    def test_frozen_graph_unchanged_and_runtime_changes_fingerprinted(self):
        m=read_json(ROOT/'artifacts/graph_manifest.json')
        for name,digest in m['artifacts_sha256'].items():self.assertEqual(sha256(ROOT/'artifacts'/name),digest)
        reviewed=ROOT/'artifacts_final/graph_manifest.json'
        if not reviewed.is_file():reviewed=ROOT/'artifacts_reviewed/graph_manifest.json'
        current=read_json(reviewed if reviewed.is_file() else ROOT/'runtime_review/activation_manifest.json')
        hashes=current.get('implementation_sha256',current.get('runtime_code_sha256'))
        for name,digest in hashes.items():self.assertEqual(sha256(ROOT/'lifemate_graph'/name),digest)
        self.assertEqual(read_json(ROOT/'artifacts/compiled_graph_v3.json')['edges'],{})
    def test_probe_results_and_review_manifest_match(self):
        m=read_json(ROOT/'runtime_review/activation_manifest.json')
        for name,digest in m['files_sha256'].items():self.assertEqual(sha256(ROOT/name),digest)
        self.assertEqual(m['production_approved_edges'],0)
        summary=read_json(ROOT/'runtime_review/review_summary.json')
        self.assertEqual(summary['classification_counts'],{'READY_FOR_PILOT':19,'NEEDS_FIX':14,'NEEDS_REVIEW':39,'NOT_ELIGIBLE':0})
        self.assertEqual(summary['runtime_approved_edges_after'],0)
        self.assertFalse(summary['reviewed_runtime_graph_compiled'])
        self.assertTrue(summary['special_guard_probes']['light_set_matcher_accepts_fan_record_by_name'])
        self.assertTrue(summary['special_guard_probes']['bill_pay_electric_request_guard_problems'])


if __name__=='__main__':unittest.main()
