import unittest
from lifemate_graph.evaluation import aggregate, call_metrics, distribution, f1, repeated_reads,arguments_match_proxy,fuzzy_call_metrics


class EvaluationTests(unittest.TestCase):
    def test_fuzzy_is_strictly_above_threshold_and_only_on_marked_fields(self):
        self.assertFalse(arguments_match_proxy('a b c','a b d e f g h',{'x-match':'fuzzy'}))
        self.assertFalse(arguments_match_proxy('a b c','a b c d e f g',{'x-match':'fuzzy'}))  # exactly .6
        self.assertFalse(arguments_match_proxy('Title','title',{'type':'string'}))
        self.assertTrue(arguments_match_proxy('TITLE','title',{'x-match':'fuzzy'}))
        self.assertFalse(arguments_match_proxy({'body':'hi','query':'Mai'},{'body':'HI','query':'mai'},
            {'properties':{'body':{'x-match':'fuzzy'},'query':{'type':'string'}}}))
    def test_maximum_matching_recovers_non_greedy_fuzzy_pairing(self):
        def call(text):return {'name':'t','arguments':{'body':text}}
        result=fuzzy_call_metrics([call('a b c'),call('a b')],[call('a b'),call('a c')],
            {'t':{'parameters':{'properties':{'body':{'x-match':'fuzzy'}}}}})
        self.assertEqual(result['fuzzy_call_f1_proxy'],1)
    def test_exact_f1_counts_duplicate_calls(self):
        self.assertEqual(f1(['a','a'],['a']),2/3)
        self.assertEqual(f1([],[]),1)
    def test_tool_name_f1_separates_wrong_arguments(self):
        r=call_metrics([{'name':'t','arguments':{'id':'wrong'}}],[{'name':'t','arguments':{'id':'right'}}])
        self.assertEqual(r['tool_name_f1'],1);self.assertEqual(r['exact_call_f1'],0)
    def test_latency_percentiles(self):
        self.assertEqual(distribution([1,2,3,4])['p50'],2)
        self.assertEqual(distribution([1,2,3,4])['p95'],4)
        self.assertIsNone(distribution([])['mean'])
    def test_repeated_read_counter_respects_write_boundary(self):
        r={'name':'read','arguments':{}};w={'name':'write','arguments':{}}
        self.assertEqual(repeated_reads([r,r,w,r],{'read':{'kind':'read'},'write':{'kind':'write'}}),1)
    def test_absent_eval_does_not_claim_accuracy(self):
        self.assertEqual(aggregate([])['rollout_decision'],'INSUFFICIENT_EVIDENCE')


if __name__=='__main__':unittest.main()
