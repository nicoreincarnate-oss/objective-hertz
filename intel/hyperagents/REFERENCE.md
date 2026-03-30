# Meta HyperAgents — Reference

**Paper:** "Hyperagents" (arXiv:2603.19461, March 2026)
**Authors:** Jenny Zhang, Bingchen Zhao, Wannan Yang, Jakob Foerster, Jeff Clune, Minqi Jiang, Sam Devlin, Tatiana Shavrina
**Affiliations:** UBC, Vector Institute, Edinburgh, NYU, FAIR at Meta, Meta Superintelligence Labs
**Implementation:** https://github.com/facebookresearch/Hyperagents
**License:** CC BY-NC-SA 4.0 (non-commercial research only)

---

## Why This Matters for Objective Hertz

OH already has self-improvement infrastructure: `titan/expansion.py` (revenue expansion with shadow rollouts), `shared/magma.py` (MAGMA learning engine), `titan/training.py` (LoRA fine-tuning), `perseus/backprop.py` + `cell_division.py` + `sleep_cycle.py`. HyperAgents provides the theoretical framework and tested patterns for making these components genuinely self-referential — where the improvement mechanism itself improves.

**Direct application:** When Titan's expansion engine evaluates new lead discovery skills via shadow rollouts, HyperAgents' pattern of metacognitive self-modification could make the evaluation criteria themselves evolve based on outcomes.

---

## Core Innovation: Metacognitive Self-Modification

### What Makes It Different

| Aspect | Prior Self-Improving AI | DGM-H (HyperAgents) |
|--------|------------------------|------|
| Improvement Focus | Direct task performance | Task + improvement mechanism |
| Meta-Level Mechanism | Fixed, hand-crafted | Editable, self-modifiable |
| Domain Specificity | Requires task-specific tuning | Domain-agnostic |
| Improvement Velocity | Limited by fixed meta rules | Self-accelerating |
| Transferability | Limited cross-domain | Meta improvements transfer broadly |

### The DGM-Hyperagent Framework

**Architecture Components:**
- **Meta Agent** — Modifies both the task agent's code AND its own meta-level code
- **Task Agent** — Executes target objectives, continuously refined across generations
- **Single Unified Editable Program** — Both agents coexist in one Python repository
- **Population-Based Exploration** — Archive of prior agent variants as "stepping stones"
- **Isolated Execution** — Docker containers for reproducibility and safety

### Three Levels of Improvement
1. **First-Order** — Direct task-solving enhancements (better code, better prompts)
2. **Second-Order** — Improving the mechanism that generates improvements
3. **Metacognitive Loop** — The meta agent modifies how the meta agent generates future modifications

---

## Iteration Process

```
1. Select parent agent from performance archive (top performer)
2. Copy repository into isolated Docker container
3. Run meta-agent to generate agent variants (code modifications)
4. Evaluate variants independently on task benchmarks
5. Update archive with performance metrics
6. Repeat for N iterations (self-accelerating progress)
```

---

## Emergent Behaviors (Discovered Without Explicit Specification)

The system spontaneously developed:
- Persistent memory and logging utilities
- Performance tracking and analysis tools
- Prompt template systems
- Compute-aware strategic planning
- Self-diagnosis and error correction mechanisms
- Agent ensemble techniques
- Enhanced search strategies

**Critical finding:** These meta-level improvements are generalizable — optimization discovered in paper review transfers to robotics task performance.

---

## Benchmark Results

### Coding (Python/Mixed Languages)
- Training: Pass@1 improved 0.140 → 0.340
- Test: Pass@1 improved 0.084 → 0.267

### Paper Review (Scientific Evaluation)
- Classical agents: 0.0
- HyperAgents: 0.710
- Beats AI-Scientist-v2 (current best AI system for automated research)
- Discovered multi-stage evaluation pipelines with explicit checklists

### Olympiad-Level Math Grading
- Cross-domain transfer: imp@50 of 0.630
- Human-customized runs FAILED; only full DGM-H generalized

### Six-Domain Testing Suite
BALROG (game navigation), Genesis (physics sim), IMO (math), Paper Review, Polyglot (multi-language code), Search Arena (real-time search)

---

## Ablation Findings

**Both components required:**
- Removing metacognitive self-modification → stagnation
- Removing open-ended exploration → stagnation
- Fixed hand-crafted meta mechanisms (all prior systems) → inferior

---

## Implementation Details

### Technical Stack
- Python 3.12
- API keys: OpenAI, Anthropic, Google Gemini
- Docker containerization for code execution
- graphviz, cmake, ninja-build

### Code Structure
```
hyperagents/
├── agent/              # Foundation model interactions
├── domains/            # Task-specific implementations
├── meta_agent.py       # Meta-agent logic for code generation
├── task_agent.py       # Task execution and refinement
└── generate_loop.py    # Primary entry point (orchestrates improvement cycles)
```

### Installation
```bash
python3.12 -m venv venv_nat
source venv_nat/bin/activate
pip install -r requirements.txt
pip install -r requirements_dev.txt
python generate_loop.py --domains <domain_name>
```

---

## Integration Pattern for Objective Hertz

### Current OH Self-Improvement Architecture
```
titan/expansion.py     → Detects revenue bottlenecks, proposes capabilities
titan/memory.py        → Daily/weekly learning extraction
shared/magma.py        → MAGMA learning engine
perseus/backprop.py    → Self-improvement (dormant until Phase 4)
perseus/cell_division.py → Daemon spawning (dormant)
perseus/sleep_cycle.py  → Reflection cycle (dormant)
```

### HyperAgents Enhancement Pattern
```
Current: Titan evaluates skills → fixed criteria → keep/reject
Enhanced: Titan evaluates skills → criteria that evolve based on outcomes
                                    ↑
                            Meta-agent modifies evaluation logic
                            based on which kept skills actually produced revenue
```

### Concrete Implementation
```python
# In titan/expansion.py, instead of fixed shadow rollout criteria:
class ExpansionEngine:
    def evaluate_skill(self, skill, metrics):
        # Current: fixed threshold
        if metrics.conversion_rate > 0.05:
            return "adopt"

        # HyperAgents pattern: criteria evolves
        # Meta-agent reviews past adopt/reject decisions
        # Discovers that conversion_rate alone misses skills that
        # produce fewer but higher-value leads
        # Self-modifies to include: avg_deal_value, lead_quality_score
```

---

## Constraints & Safety Notes

- **Executes untrusted, model-generated code** — Docker isolation is mandatory
- **CC BY-NC-SA 4.0** — Non-commercial research only
- **Human-defined goals required** — System optimizes "how to achieve goals," not "what goals matter"
- **Computational cost** — Iterative generation/evaluation requires significant compute
- **Codebases must be explicitly editable** — Black-box systems can't serve as improvement targets

---

## Sources

- [ArXiv Paper](https://arxiv.org/abs/2603.19461)
- [GitHub Repository](https://github.com/facebookresearch/Hyperagents)
- [Meta AI Research Page](https://ai.meta.com/research/publications/hyperagents/)
- [MarkTechPost Breakdown](https://www.marktechpost.com/2026/03/23/meta-ais-new-hyperagents-dont-just-solve-tasks-they-rewrite-the-rules-of-how-they-learn/)
- [HuggingFace Paper Page](https://huggingface.co/papers/2603.19461)
