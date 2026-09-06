"""Reusable synthetic conversations and Ollama context allocation probes.

Quality is reviewed from transcripts, never inferred from keyword success alone.
"""
from __future__ import annotations

import argparse
import hashlib
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import statistics
import subprocess
import time

from eval_support import fixtures, hestia

CASES = [
    {"id": "explanation", "turns": [
        "Why does a metal spoon feel colder than a wooden one in the same room?",
        "So the metal must actually be at a lower temperature, right?",
        "Explain it again using a simple analogy, in two sentences."],
     "rubric": "Explain heat transfer, politely correct the temperature misconception, then give a sound concise analogy."},
    {"id": "tradeoffs", "turns": [
        "I'm deciding between an open shelf and a cabinet for a small entryway. Help me think it through; don't shop for anything.",
        "Actually, shoes are the main clutter, and wet shoes need to dry. Does that change your advice?",
        "My partner hates seeing the shoes. Suggest a compromise that respects both concerns."],
     "rubric": "Discuss practical tradeoffs, adapt to wet shoes, reconcile ventilation and concealment without buying or inventing household facts."},
    {"id": "knowledge", "turns": [
        "How did the printing press change who could participate in public debate? Keep it conversational.",
        "Was that an immediate change everywhere?",
        "What is one useful comparison with the internet, and one way that comparison breaks down?"],
     "rubric": "Give accurate historical explanation, acknowledge uneven gradual adoption, and offer a nuanced analogy without invented specifics."},
    {"id": "correction", "turns": [
        "Let's discuss a fictional reading club. Mira chose the novel, Theo booked the room, and June invited the guests. Who handled the venue?",
        "Correction: June booked the room; Theo invited the guests. Mira's role is unchanged. Who did what now?",
        "Who should I ask about the room, and why? This is still just our fictional example."],
     "rubric": "Initially identify Theo, then consistently use corrected roles: June room, Theo guests, Mira novel. Do not save fictional facts."},
    {"id": "uncertainty", "turns": [
        "A friend says putting a spoon in the neck of an open sparkling-water bottle keeps it fizzy. Is there a good reason to believe that?",
        "They tried it once and it worked. Doesn't that prove it?",
        "Describe a simple fair comparison we could do, without claiming you already tested it."],
     "rubric": "Avoid endorsing unsupported folklore, distinguish anecdote from evidence, suggest comparable sealed/unsealed or spoon/no-spoon controls and consistent temperature/time."},
    {"id": "conversation", "turns": [
        "I spent the weekend reorganizing a workshop and somehow it feels messier. I'm mostly venting, not asking for a productivity system.",
        "Exactly. I found three half-finished projects and now they're all competing for attention.",
        "Okay, give me just one small next step. No list."],
     "rubric": "Respond naturally to venting, maintain the thread without forced advice, then offer exactly one modest next step."},
]
CONTROL = "You are a helpful conversational assistant. Be direct, warm and concise. Explain stable general knowledge from what you know, correct mistaken assumptions politely, and admit uncertainty. Do not claim access to tools or household information."


def context_input(chars):
    line = 'The fictional archive contains ordinary blue folders.\n'
    filler = (line * (chars // len(line) + 1))[:chars]
    cut = len(filler) // 2
    return ('First code: LARK-731.\n' + filler[:cut] +
            '\nMiddle code: MOSS-284.\n' + filler[cut:] +
            '\nLast code: REED-956.\nReply with the first, middle and last codes only, separated by commas.')


def turn_checks(response, metrics, calls):
    """Mechanical review flags only; none establishes answer quality."""
    return {'nonempty': bool(response.strip()),
            'backend_failure': bool(metrics.get('failure')),
            'tool_calls_to_review': len(calls),
            'output_limit_reached': metrics.get('done_reason') == 'length'}


def summarize(rows):
    times = sorted(t['seconds'] for r in rows for t in r['turns'])
    return {'turns': len(times), 'median_seconds': statistics.median(times),
            'p95_seconds': times[min(len(times)-1, int(len(times)*.95))]}


def gpu():
    try:
        return subprocess.check_output(['nvidia-smi', '--query-compute-apps=gpu_uuid,used_gpu_memory', '--format=csv,noheader'], text=True).splitlines()
    except (OSError, subprocess.CalledProcessError):
        return []


async def chat(model, messages, context, *, output=768):
    response = await hestia.client.post('/api/chat', json={'model': model,
        'messages': messages, 'stream': False, 'think': False, 'keep_alive': -1,
        'options': {'num_ctx': context, 'num_predict': output, 'temperature': .3}})
    response.raise_for_status()
    return response.json()


async def warm(model, context):
    await chat(model, [{'role': 'user', 'content': 'Say ready.'}], context, output=8)
    response = await hestia.client.get('/api/ps')
    response.raise_for_status()
    return next(m for m in response.json()['models'] if m['name'] == model)


def save(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2))
    temp.replace(path)


def transcript(path, data):
    lines = ['# Conversation evaluation', '',
             'Synthetic prompts. Rubrics require human review; latency is not a quality score.', '']
    for row in data['conversations']:
        lines += [f"## {row['model']} / {row['mode']} / {row['case']}", '', row['rubric'], '']
        for turn in row['turns']:
            lines += [f"User: {turn['prompt']}", '', f"Assistant: {turn['response']}", '',
                      f"Elapsed: {turn['seconds']:.2f}s. Tools: {json.dumps(turn['calls'])}", '']
    path.write_text('\n'.join(lines))


async def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    data = {'schema_version': 1, 'corpus_sha256': hashlib.sha256(json.dumps(CASES, sort_keys=True).encode()).hexdigest(),
            'settings': {'context': args.context, 'temperature': .3, 'think': False,
                         'output_tokens': 768, 'fill_chars': args.fill_chars},
            'conversations': [], 'contexts': [], 'metadata': [], 'errors': [], 'restored': False}
    path = args.output / 'results.json'
    try:
        version = await hestia.client.get('/api/version')
        version.raise_for_status()
        data['ollama_version'] = version.json()
        for model in args.models:
            info = await warm(model, args.context)
            data['metadata'].append(info)
            if info['size_vram'] < .95 * info['size']:
                raise RuntimeError(f'{model} is not fully GPU resident at conversation context')
            if not args.context_only:
                for mode in args.modes:
                    for case in CASES:
                        row = {'model': model, 'mode': mode, 'case': case['id'],
                               'rubric': case['rubric'], 'turns': []}
                        with fixtures() as state:
                            messages = []
                            for prompt in case['turns']:
                                messages.append({'role': 'user', 'content': prompt})
                                start, before = time.monotonic(), len(state['calls'])
                                if mode == 'hestia':
                                    trace = hestia.TurnTrace(model=model, think=False)
                                    response = await hestia.run_agent(messages, trace=trace)
                                    metrics = asdict(trace)
                                else:
                                    payload = await chat(model, [{'role': 'system', 'content': CONTROL}, *messages], args.context)
                                    response = payload.get('message', {}).get('content', '')
                                    metrics = {k:v for k,v in payload.items() if k != 'message'}
                                row['turns'].append({'prompt': prompt, 'response': response,
                                    'seconds': time.monotonic()-start, 'metrics': metrics,
                                    'calls': state['calls'][before:],
                                    'checks': turn_checks(response, metrics, state['calls'][before:])})
                                messages.append({'role': 'assistant', 'content': response})
                        data['conversations'].append(row)
                        save(path, data)
                        transcript(args.output / 'transcripts.md', data)
                        print(f'{model} {mode} {case["id"]} complete', flush=True)
            for context in args.probe_contexts:
                start = time.monotonic()
                try:
                    info = await warm(model, context)
                    probe = {'model':model, 'requested_context':context, 'loaded':info,
                             'gpu_processes':gpu(), 'warm_seconds':time.monotonic()-start,
                             'fully_gpu_resident':info['size_vram'] >= .95*info['size']}
                    # Optional filled-input probe is separate from the production context trimmer.
                    if args.fill_chars and probe['fully_gpu_resident']:
                        payload = await chat(model, [{'role':'user','content':context_input(args.fill_chars)}], context, output=64)
                        probe['filled_probe'] = payload
                        probe['gpu_processes_after_fill'] = gpu()
                        answer = payload.get('message', {}).get('content', '')
                        probe['all_codes_present'] = all(code in answer for code in ('LARK-731', 'MOSS-284', 'REED-956'))
                    data['contexts'].append(probe)
                    save(path, data)
                    print(f'{model} context={context} gpu={probe["fully_gpu_resident"]} allocated={info["size_vram"]/2**30:.2f} GiB', flush=True)
                    if not probe['fully_gpu_resident']:
                        break
                except Exception as exc:
                    data['errors'].append({'model':model, 'context':context, 'error':str(exc)})
                    save(path, data)
                    break
    except Exception as exc:
        data['errors'].append({'phase': 'run', 'error': str(exc)})
        raise
    finally:
        try:
            data['restored_model'] = await warm(args.restore, args.restore_context)
            data['restored'] = True
        finally:
            save(path, data)
            await hestia.client.aclose()
    data['summaries'] = [{'model': model, 'mode': mode, **summarize(group)}
        for model in args.models for mode in args.modes
        if (group := [r for r in data['conversations'] if r['model'] == model and r['mode'] == mode])]
    save(path, data)
    print(f'Restored {args.restore}; results: {path}', flush=True)
    return not data['errors']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('models', nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--restore', default='qwen3:14b')
    parser.add_argument('--context', type=int, default=32768)
    parser.add_argument('--restore-context', type=int, default=32768)
    parser.add_argument('--modes', nargs='+', choices=['hestia', 'control'], default=['hestia','control'])
    parser.add_argument('--probe-contexts', nargs='*', type=int, default=[])
    parser.add_argument('--context-only', action='store_true')
    parser.add_argument('--fill-chars', type=int, default=0)
    args = parser.parse_args()
    if args.context < 1024 or args.restore_context < 1024 or any(c < 1024 for c in args.probe_contexts) or args.fill_chars < 0:
        parser.error('contexts must be >=1024 and fill-chars must be nonnegative')
    hestia.NUM_CTX = args.context
    hestia.OUTPUT_TOKENS = 768
    if not asyncio.run(run(args)):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
