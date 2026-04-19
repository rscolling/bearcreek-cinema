# From Personal Project to Client Engagement

A translation of Bear Creek Cinema's design patterns into the shape of
a client-facing consulting offering. Useful for sales conversations,
proposals, and the Agentic Agent landing page.

---

## What the personal project is

A ~6-8 weekend personal project: a self-hosted home cinema that curates
public-domain films and classic TV from the Internet Archive, learning
taste from Jellyfin playback and delivering recommendations through a
custom Roku app.

## What the equivalent client engagement looks like

The same architectural patterns scaled to enterprise data and
operational requirements.

### Engagement shape

**Duration:** 4-6 weeks for discovery + MVP, 2-3 months for production
deployment.

**Team:** 1-2 engineers plus a technical stakeholder on the client side.

**Deliverables:**

- Architecture document with ADRs for each major choice
- Frozen interface contracts (schemas, APIs, CLI)
- Reference implementation running in a staging environment
- Deployment runbook and operational documentation
- Training session for the client team who will maintain it

### Where the patterns translate directly

**"Bear Creek learns what the household watches on Jellyfin"**
→ "The agent learns from signals already in your existing systems —
CRM events, support tickets, product telemetry, whatever you've
already instrumented. You don't retrain on new data you don't have."

**"Ollama by default, Claude optional"**
→ "Local inference by default, cloud augmentation as opt-in, with
identical interfaces behind both. Your data stays in your boundary.
Cloud is available for workloads where quality matters more than
privacy, selected per workflow, not as a silent fallback."

**"Two-stage ranking: TF-IDF prefilter, LLM rerank"**
→ "Narrow the LLM's job to something it's actually good at. Use
ordinary code for what ordinary code does well. Smaller prompts,
faster responses, smaller bills, more observable behavior."

**"Graceful degradation to TF-IDF fallback"**
→ "The system never silently fails. If the LLM is unavailable or
returning malformed output, the pipeline continues on a simpler path.
Users may notice the system is degraded; they don't experience an
outage."

**"Every LLM call audited to a database table"**
→ "You can answer 'is our model behavior regressing?' and 'what did
our model say to that customer last week?' from SQL, not from prayer.
Observability is cheap to build in and expensive to retrofit."

**"Frozen contracts and ADRs"**
→ "When your team wants to change how the system works six months from
now, they can read what the system does and why, and make a decision
grounded in that history. Without these, every refactor is a
re-excavation."

**"Disk-budget-aware librarian"**
→ "Your AI system lives inside real operational constraints — storage,
cost, latency, rate limits. Build those constraints into the system
itself, don't rely on human discipline."

### Where the scaling is different

**Data volume.** Enterprise candidate pools are often larger — think
O(10⁵)-O(10⁶) items. The TF-IDF in-memory approach that's perfect for
Bear Creek Cinema's ~10⁴ candidates gives way to a real vector store
(pgvector, Qdrant) at higher volumes. But the principle — "use the
smallest tool that fits" — stays the same; the sizing changes.

**Multi-user.** Bear Creek Cinema has one Jellyfin account; a client
system usually has thousands of users. The taste-profile mechanism
generalizes cleanly, but the storage layer changes, and per-user
ranking parallelism becomes a real concern.

**Regulatory surface.** Personal film history is low-stakes. HIPAA,
SOC2, GDPR, and sector-specific regulations change what "local-first
with cloud optional" means in practice. The architecture supports this
well — the providers are swappable — but the compliance work is
genuine.

**SLA expectations.** Bear Creek Cinema being down for an evening is
fine. Client systems often have real uptime commitments. This mostly
shapes ops (monitoring, on-call, rollback procedures) rather than
architecture.

### What the engagement isn't

To preempt the predictable question:

**Not a Netflix-clone or Spotify-clone for your data.** We build the
agent layer that ranks and surfaces things to humans; we don't build
the media storage, the player, the search UI, the notification system,
or any of the other moving parts that already exist. This engagement
specifically targets the decision-making layer.

**Not a "chat with your data" bot.** Those exist; they're often the
wrong tool. The Bear Creek pattern builds an agent that does useful
work in the background on behalf of your users, surfacing results in
the tools they already use. "Chat with your data" is sometimes the
right follow-up; rarely the right starting point.

**Not a 12-month AI transformation.** This is a 1-3 month focused
engagement on one real workflow. Scope comes first; scope stays tight.

---

## Specific engagement templates

### Template 1: "Internal knowledge recommender"

A version of Bear Creek Cinema for internal documents, learning from
which docs employees actually read-through vs. skim-and-abandon.
Surfaces relevant documentation when employees start a new project
or get assigned to a new team.

Signal source: existing Confluence / Google Drive / SharePoint
analytics.
Candidate pool: the internal knowledge base.
Delivery surface: Slack bot, a browser extension, or an internal
web UI.

### Template 2: "Support ticket triage agent"

A version for incoming support tickets: the agent learns from how
senior agents route tickets and resolve them, and produces routing +
initial-response recommendations for new incoming tickets.

Signal source: ticket history with resolution paths and times.
Candidate pool: incoming unassigned tickets.
Delivery surface: the existing ticketing system, with recommendations
appearing as suggestions the first-line support agent can accept or
override.

### Template 3: "Lead-quality scorer"

A version for sales teams: the agent learns which inbound leads
salespeople actually work and close, and scores new leads against
that learned profile.

Signal source: CRM activity and outcomes.
Candidate pool: incoming leads.
Delivery surface: CRM integration, lead lists sorted and annotated
with ranking reasoning.

---

## Pricing framing

Per Rob's consulting rate of $200-250/hr:

- **Discovery + architecture (2 weeks):** ~$12-15K
- **MVP implementation (4 weeks):** ~$25-30K
- **Production deployment + documentation (2-3 weeks):** ~$12-20K
- **Optional retainer for the first 3 months post-deployment:**
  ~$5-8K/month

Total range for a full engagement: $50-80K depending on client-specific
complexity (auth integration, data pipelines, compliance work).

Bear Creek Cinema itself, as a reference, represents roughly ~60-80
hours of Rob's time over 6-8 weekends, which maps to about $15K at
consulting rates. That's a useful reference point in conversations:
"the personal project was 60ish hours; your production equivalent is
significantly more because of [data volume | multi-user | compliance |
SLA]."

---

## Positioning one-liners

- "We build the agent that does useful work in the background, not the
  chatbot that demands user attention."
- "Local-first AI, production-shaped, honest about what it can and
  can't do."
- "Small systems, explicit decisions, observable behavior. Nothing
  you'll need to rebuild in eighteen months."

Avoid:

- "AI-powered" (vague, overused)
- "Intelligent" (meaningless)
- "Revolutionary" (you're not)
- "Next-generation" (every generation)
- Anything with "synergy," "ecosystem," or "paradigm"
