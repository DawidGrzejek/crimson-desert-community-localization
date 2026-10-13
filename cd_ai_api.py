"""Run an AI task folder directly through an API, without an agent such as Claude Code.

Every part of the task (data/part_NNN.jsonl) is sent to the model together
with INSTRUCTIONS.md and the glossary names that occur in that part. The
answer is checked with the same rules as check.py (tags, allowed characters),
invalid lines are dropped, and the rest is written to results/part_NNN.jsonl.
Parts that already have a results file are skipped, so a run can be resumed.
"""

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import cd_core as core
from i18n import tr

PROVIDERS = {
    "anthropic": {"label": "Anthropic (Claude)", "url": "https://api.anthropic.com/v1/messages",
                  "model": "claude-sonnet-5-5", "env": "ANTHROPIC_API_KEY"},
    "openai": {"label": "OpenAI-compatible (OpenAI, DeepSeek, Mistral, Ollama, vLLM…)",
               "url": "https://api.openai.com/v1/chat/completions", "model": "", "env": "OPENAI_API_KEY"},
}
OLLAMA_URL = "http://localhost:11434/v1/chat/completions"
MAX_TOKENS = 16000
TIMEOUT = 600


def _post(url, headers, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}") from e


def complete(provider, model, api_key, system, prompt, url=None):
    """Send one prompt and return the model's text answer."""
    if provider == "anthropic":
        data = _post(url or PROVIDERS["anthropic"]["url"],
                     {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                     {"model": model, "max_tokens": MAX_TOKENS, "system": system,
                      "messages": [{"role": "user", "content": prompt}]})
        return "".join(block.get("text", "") for block in data.get("content", []))
    headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
    data = _post(url or PROVIDERS["openai"]["url"], headers,
                 {"model": model, "messages": [{"role": "system", "content": system},
                                               {"role": "user", "content": prompt}]})
    return data["choices"][0]["message"]["content"]


def api_key_for(provider, cfg):
    return cfg.get("api_keys", {}).get(provider) or os.environ.get(PROVIDERS[provider]["env"], "")


def _glossary_for(task_dir, layout, records):
    """Glossary rows whose original name occurs in the given records (keeps prompts short)."""
    try:
        rows = (Path(task_dir) / layout["glossary"]).read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    picked = [row for row in rows[1:] if len(row.split("\t")) > 2 and row.split("\t")[1] in text]
    return "\n".join(rows[:1] + picked)


def parse_answer(answer, inputs, task, charset):
    """Valid result records from a model answer, and the number of rejected lines."""
    src, tgt = task["source_lang"], task["target_lang"]
    results, rejected = [], 0
    for line in answer.splitlines():
        line = line.strip().strip(",")
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
            rid, text = rec["id"], rec[tgt]
        except (ValueError, KeyError, TypeError):
            rejected += 1
            continue
        source = inputs.get(rid)
        if source is None or not isinstance(text, str) or text == source[tgt]:
            rejected += source is None
            continue
        errors, _ = core.check_entry(source[src], text, "en")
        if errors or core.odd_chars(text, charset):
            rejected += 1
            continue
        results.append(rec)
    return results, rejected


def run_task(task_dir, provider, model, api_key, url=None, progress=None, stop=None, log=print):
    """Process every part without results. Returns (parts done, corrections, rejected lines)."""
    task_dir = Path(task_dir)
    L = core.task_layout(task_dir)
    task = json.loads((task_dir / L["meta"]).read_text(encoding="utf-8"))
    instructions = (task_dir / L["instructions"]).read_text(encoding="utf-8")
    charset = (task_dir / L["charset"]).read_text(encoding="utf-8")
    system = (instructions + "\n\n## Answer format for this conversation\n\n"
              "You receive one part of the data in the message. Reply ONLY with the result lines "
              "(one JSON object per line, as described above) – no commentary, no code fences. "
              "If nothing needs fixing, reply with an empty message.")
    todo = [p for p in task["parts"] if not (task_dir / L["results"] / p).exists()]
    done = fixes = rejected = 0
    for n, part in enumerate(todo):
        if stop and stop():
            break
        if progress:
            progress(n, len(todo), part)
        lines = (task_dir / L["data"] / part).read_text(encoding="utf-8").splitlines()
        records = [json.loads(x) for x in lines if x.strip()]
        inputs = {r["id"]: r for r in records}
        prompt = (f"Glossary for this part:\n{_glossary_for(task_dir, L, records)}\n\n"
                  f"Data part {part}:\n" + "\n".join(lines))
        answer = complete(provider, model, api_key, system, prompt, url)
        results, bad = parse_answer(answer, inputs, task, charset)
        with open(task_dir / L["results"] / part, "w", encoding="utf-8") as f:
            for rec in results:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        done += 1
        fixes += len(results)
        rejected += bad
        log(tr("api.part_done", part=part, n=len(results), bad=bad))
    if progress:
        progress(len(todo), len(todo), "")
    return done, fixes, rejected


def model_default(provider):
    return PROVIDERS[provider]["model"]


def is_local(url):
    return bool(url) and re.match(r"https?://(localhost|127\.0\.0\.1)", url) is not None
