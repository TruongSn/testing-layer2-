import time
import unittest
from types import SimpleNamespace
from lifemate_graph.inference import bounded_generate

class BudgetTests(unittest.TestCase):
    def test_expired_budget_never_reaches_model_or_tokenizer(self):
        call=bounded_generate(SimpleNamespace(CONFIG={}),time.perf_counter()-1)
        with self.assertRaises(TimeoutError):call(None,None,None,[])
    def test_context_cap_and_non_thinking_template(self):
        class Tokenizer:
            def apply_chat_template(self,*a,**kw):
                assert kw['enable_thinking'] is False
                return 'prompt'
            def __call__(self,*a,**kw):return {'input_ids':SimpleNamespace(shape=(1,16200))}
        call=bounded_generate(SimpleNamespace(CONFIG={'max_new_tokens':256,'max_input_tokens':16384}),time.perf_counter()+10)
        with self.assertRaisesRegex(RuntimeError,'prompt_budget'):call(None,Tokenizer(),None,[])
