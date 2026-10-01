# Recovery Desk

An agent that runs an auto insurer's subrogation pipeline end to end. Drop in a closed claim file (PDF). The agent reads it, decides whether someone else owes the money, checks the law, deadline, carrier and value, builds and sends the demand, negotiates and follows up, prepares arbitration or litigation when talks fail, and reconciles the payment when it lands.

**You only see what needs you:**

| You see | When |
| --- | --- |
| **Settle?** | The carrier's best offer is below your settlement threshold (slider on the home screen, default 85%) |
| **Requires arbitration** | Talks failed and Arbitration Forums applies. The agent has drafted the filing; you review and file |
| **Requires litigation** | No insurer, carrier outside AF, over the $100k AF limit, or the deadline is close. The agent has drafted a referral memo for counsel |
| **Carrier needs something** | The carrier asked for something that is not in the file (photos, a statement). Upload it or say you can't |
| **Approve demand** | Only for demands above your auto-send limit (default $25,000; 0 = always ask) |
| **Check** | The agent could not finish a step (model error, unreadable file). Retry |

Everything else runs on its own and shows in the pipeline board.

## The seven stages

| Stage | Agent (model) | Code (guardrails, never the model) | Live or simulated in v1 |
| --- | --- | --- | --- |
| 0 Intake | Labels pages, extracts facts with page citations | Required fields, pages exist, line items add up to the ledger | Live |
| 1 Detect | Liable third party? Quotes the evidence | Each quote must appear in the file word for word | Live |
| 2 Validate | Fault share, likelihood, pursue / park / litigation | State fault rule, deadline, insurer on file, value floor; can refuse the model | Live |
| 3 Build | Fault argument, exhibits | Every amount from the ledger; letter PDF, evidence bundle, hub fields | Live |
| 4 Send | — | E-Subro Hub for AF members, email otherwise; auto-send under the limit | Simulated channel |
| 5 Negotiate | Reads each carrier reply or silence, picks one action: accept, counter, rebut, follow up, send pages, ask you, escalate | Settlement threshold, offers it cannot exceed, capped concessions, arbitration only where available | Agent live; carrier simulated |
| 6 Arbitrate / litigate | Drafts the arbitration filing (contentions, evidence index, defenses) or the litigation memo | Damages and deadline from code; you decide to file | Agent live; forum simulated |
| 7 Collect | Chases short payments | Matches bank payments to claims, computes the deductible refund by state rule, books the recovery | Bank feed simulated |

The simulated carrier replies in plain text the agent has to read (accept, counter, dispute rental days, ask for photos, deny, stay silent, short-pay). The agent never sees the script. Each demo sample exercises one path:

| Sample | What happens |
| --- | --- |
| Rear End California | Carrier asks for photos not in the file → you upload → paid → reconciled |
| Parked Car Georgia | Carrier pays without the deductible → agent chases the balance → paid |
| Rental Dispute New York | Carrier disputes rental days → agent rebuts with the repair invoice → paid |
| Red Light Total Loss California | 60% offer → agent counters → 85% final → settles at your threshold |
| Rear End Florida No Reply | Silence → follow-ups at day 15, 30, 45 → arbitration at day 60 |
| Lane Change Florida | Liability denied → arbitration filing for your review → award → paid |
| Truck Total Loss Texas | Over $100k; 70% final offer → you choose settle or litigation |
| Uninsured Driver Georgia | No insurer → litigation referral straight away |
| Intersection Maryland, Red Light Texas 2023, Hail New York | Parked: contributory negligence, time-barred, no third party |

Use **Load demo book**, then **+30 days** a few times.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python samples/make_samples.py
cp .env.example .env        # set LLM_API_KEY and the model
export $(grep -v '^#' .env | xargs)
uvicorn app.main:app --reload
```

Open http://localhost:8000. Without `DATABASE_URL` it uses a local SQLite file.

## Deploy on Render

1. Push this folder to a GitHub repo.
2. Render → **New → Blueprint** → pick the repo. `render.yaml` creates the web service and a Postgres database.
3. Set `LLM_API_KEY` (and `LLM_PROVIDER`, `LLM_MODEL`, `LLM_BASE_URL` if not Claude). Defaults to Groq + Qwen. Sign-in is off unless you add `APP_PASSWORD` (username `desk`).
4. Open the URL. Health check: `/api/health` (shows provider, model, and whether a key is set).

Free plans sleep when idle; use a paid plan for anything you keep.

## Choose the model

| Provider | `LLM_PROVIDER` | `LLM_BASE_URL` | `LLM_MODEL` example |
| --- | --- | --- | --- |
| Anthropic | `anthropic` | (empty) | `claude-sonnet-5-5` |
| OpenRouter | `openai_compat` | `https://openrouter.ai/api/v1` | a model tagged for tool use, e.g. `qwen/qwen3-235b-a22b` |
| Groq | `openai_compat` | `https://api.groq.com/openai/v1` | e.g. `llama-3.3-70b-versatile` or `openai/gpt-oss-120b` |
| vLLM (self-hosted) | `openai_compat` | `http://<host>:8000/v1` | your served model (start vLLM with tool calling enabled) |
| Ollama | `openai_compat` | `http://localhost:11434/v1` | e.g. `qwen3` |

On OpenRouter, pick a model that supports tools (filter at openrouter.ai/models?supported_parameters=tools). If a model's tool calling is unreliable, set `LLM_TOOL_MODE=json`. Guardrails are code, so a weaker model gets blocked and corrected, not trusted. Tool schemas are simplified automatically for OpenAI-compatible servers.

## Production notes

- **Clock:** the demo moves time with the +7/+30 buttons. In production, run `lifecycle.tick(1)` once a day from a Render cron job and drop `DESK_DATE`.
- **Stage 4:** swap `adapters/hub.py` for the real E-Subro Hub path (AF membership or the claims-system integration) and add an email sender for non-members.
- **Stage 7:** swap `adapters/payments.py` for the bank or finance feed; deductible refunds go to finance for release.
- **Worker:** agent runs happen in the web process. Move them to a Render background worker with a queue when volume grows.
- **States:** six configured (CA, NY, TX, FL, GA, MD) in `app/rules.py`, with sources. Add others before use.

## Code map

```
app/
  main.py         API: home, settings, clock, cases, user decisions, live event stream
  pipeline.py     Stages 0-3
  lifecycle.py    Stages 4-7: send, negotiate, escalate, collect; the daily tick; user decisions
  sim.py          Simulated carrier, bank feed and arbitration forum
  clock.py        Desk date and user settings (settlement threshold, auto-send limit)
  agent/llm.py    Provider layer: Anthropic, OpenAI-compatible (OpenRouter, Groq, vLLM, Ollama), JSON mode
  agent/runner.py Agent loop with guardrails and audit events
  agent/prompts.py, stages.py, negotiate.py   Prompts and tools per stage
  rules.py        State law, deadlines, AF limits, deductible rule, carrier registry
  documents.py    Demand letter, evidence bundle, hub payload, arbitration filing, litigation memo
  adapters/       Stubs for E-Subro Hub and payments
  static/         Frontend
tests/            End-to-end tests with a scripted model (no key needed): pytest -q
```

All sample people, carriers and claims are fictional. Drafts are for human review; not legal advice.
