# LiteLLM Pre-Call Hook Spike (P1-1 stub)

> **Status:** Placeholder created by audit recovery plan 01-02 (P1-1).

TODO: LiteLLM pre-call hook spike.

Referenced by `litellm/hooks/local_path_guard.py` (also a P1-1 stub).
Will validate that LiteLLM's pre-call hook API can enforce a local-path
policy (block outbound cloud calls when a matching local model is
available) with acceptable overhead.
