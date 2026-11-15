"""Shared bounded HF generation for paired GPU evaluation, non-thinking Qwen.

max_time stops after the current generation pass; it cannot preempt a CUDA kernel
or a blocking external tool. Actual >30s samples are reported, never hidden.
"""
import time

def bounded_generate(module, deadline,on_generate=None):
    def generate(model,tokenizer,torch,messages,max_new_tokens=None):
        if time.perf_counter()>=deadline-.25:raise TimeoutError('sample_generation_budget_exhausted')
        rendered=tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True,enable_thinking=False)
        tensors=tokenizer(rendered,return_tensors='pt',add_special_tokens=False)
        ni=int(tensors['input_ids'].shape[-1]);limit=max_new_tokens or module.CONFIG['max_new_tokens']
        if ni>module.CONFIG['max_input_tokens'] or ni+limit>16384:raise RuntimeError(f'prompt_budget_exceeded: {ni}+{limit}')
        tensors={k:v.to('cuda:0') for k,v in tensors.items()}
        torch.cuda.synchronize(0)
        remaining=deadline-time.perf_counter()-.25
        if remaining<=0:raise TimeoutError('sample_generation_budget_exhausted')
        started=time.perf_counter()
        if on_generate:on_generate()
        with torch.inference_mode():
            seq=model.generate(**tensors,max_new_tokens=limit,max_time=remaining,do_sample=False,num_beams=1,
                               pad_token_id=tokenizer.pad_token_id)
        torch.cuda.synchronize(0)
        tokens=seq[0,ni:]
        return tokenizer.decode(tokens,skip_special_tokens=True).strip(),ni,len(tokens),time.perf_counter()-started
    return generate
