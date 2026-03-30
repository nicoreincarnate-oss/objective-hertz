# Quantum Computing Access — Reference

**Cloud Platforms:**
- Amazon Braket: https://aws.amazon.com/braket/
- IBM Quantum: https://quantum.ibm.com/
- Azure Quantum: https://azure.microsoft.com/en-us/products/quantum

**Hardware Companies:**
- IonQ (public, trapped-ion): https://www.ionq.com/
- Equal1 (silicon, rack-mountable): https://www.equal1.com/
- Rigetti (superconducting): https://www.rigetti.com/

---

## Why This Matters (and When)

### For Objective Hertz: Almost irrelevant now
OH sends cold emails, builds websites, processes payments. None of this benefits from quantum. The exception is post-quantum cryptography for Conway's wallets (see `intel/post-quantum-crypto/`).

### For Trading Project: Relevant at scale
When the trading system hits Layer 4+ (momentum rotation across 20-50 assets), portfolio optimization becomes a genuine quantum use case. Combinatorial optimization of correlated asset allocations is one of quantum computing's strongest near-term applications.

### Timeline
- **Now:** Post-quantum crypto only (see PQC reference)
- **Month 3:** Amazon Braket free tier, quantum simulator experiments
- **Month 6:** Build quantum portfolio optimizer on simulator
- **Year 1:** Test on real QPU when 256-qubit systems available
- **Year 2+:** Evaluate quantum ML and quantum sensing

---

## Amazon Braket (Recommended Starting Point)

### What It Is
AWS managed service providing access to quantum hardware from multiple providers through a single API.

### Free Tier
- 1 hour of quantum simulator time per month
- Enough to learn and prototype

### Pricing: Pay-As-You-Go

**Per-Task Cost:** $0.30 (fixed, all providers)

**Per-Shot Costs:**

| Provider | Device | Type | Per-Shot |
|----------|--------|------|----------|
| Rigetti | Ankaa | Superconducting | $0.00090 |
| IQM | Garnet | Superconducting | $0.00145 |
| IQM | Emerald | Superconducting | $0.00160 |
| QuEra | Aquila | Neutral atom | $0.01000 |
| AQT | IBEX-Q1 | Trapped ion | $0.02350 |
| IonQ | Forte | Trapped ion | $0.08000 |

**Simulator Pricing:**
- SV1: $0.075/minute
- DM1: $0.075/minute

**Example Cost Calculation:**
```
Portfolio optimization experiment:
- 1 task × $0.30 = $0.30
- 1000 shots on IonQ Forte × $0.08 = $80.00
- Total: $80.30

Same experiment on Rigetti Ankaa:
- 1 task × $0.30 = $0.30
- 1000 shots × $0.00090 = $0.90
- Total: $1.20
```

**Dedicated Access (Braket Direct):**
- Reserved hourly rates: $2,500-$7,000/hour
- For serious production workloads

### Braket SDK (Python)
```python
# Install
pip install amazon-braket-sdk

# Basic circuit example
from braket.circuits import Circuit
from braket.aws import AwsDevice

# Create a simple quantum circuit
circuit = Circuit().h(0).cnot(0, 1)

# Run on simulator (free tier)
device = AwsDevice("arn:aws:braket:::device/quantum-simulator/amazon/sv1")
task = device.run(circuit, shots=1000)
result = task.result()
print(result.measurement_counts)

# Run on real quantum hardware
ionq = AwsDevice("arn:aws:braket:us-east-1::device/qpu/ionq/Forte")
task = ionq.run(circuit, shots=100)
result = task.result()
```

---

## Quantum Portfolio Optimization (Trading Project Use Case)

### The Problem
Given N assets with expected returns, covariances, and constraints, find the optimal allocation that maximizes return for a given risk level. Classical algorithms scale as O(N³). Quantum algorithms can potentially solve this in O(√N).

### QAOA (Quantum Approximate Optimization Algorithm)
```python
from braket.circuits import Circuit
from braket.aws import AwsDevice
import numpy as np

def portfolio_qaoa(returns, covariance, risk_tolerance, num_assets):
    """
    Quantum Approximate Optimization for portfolio allocation.
    Encodes the optimization problem into a quantum circuit.
    """
    # Number of qubits = number of assets
    n_qubits = num_assets

    # Create QAOA circuit
    circuit = Circuit()

    # Initial superposition
    for i in range(n_qubits):
        circuit.h(i)

    # Problem Hamiltonian (encodes returns and covariance)
    # Cost layer
    for i in range(n_qubits):
        for j in range(i+1, n_qubits):
            circuit.zz(i, j, covariance[i][j] * risk_tolerance)

    # Mixer layer
    for i in range(n_qubits):
        circuit.rx(i, returns[i])

    return circuit

# Example: 4 assets (BTC, ETH, SOL, + cash)
returns = [0.15, 0.12, 0.20, 0.03]
covariance = np.array([
    [1.0, 0.7, 0.5, 0.0],
    [0.7, 1.0, 0.6, 0.0],
    [0.5, 0.6, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0]
])

circuit = portfolio_qaoa(returns, covariance, risk_tolerance=0.5, num_assets=4)

# Run on simulator first
simulator = AwsDevice("arn:aws:braket:::device/quantum-simulator/amazon/sv1")
task = simulator.run(circuit, shots=1000)
result = task.result()

# Interpret: most frequent measurement = optimal allocation
# |1100⟩ = allocate to assets 0 and 1 (BTC + ETH)
# |1010⟩ = allocate to assets 0 and 2 (BTC + SOL)
```

### When This Becomes Practical
- **4-8 assets:** Classical is fine, quantum adds no value
- **20-50 assets:** Quantum starts showing advantage (Layer 4 of trading project)
- **100+ assets:** Quantum significantly outperforms classical optimization
- **With constraints (sector limits, liquidity):** Quantum advantage increases

---

## Hardware Landscape (2026)

### IonQ (Most Relevant for Finance)
- **Public company**, first to cross $100M annual revenue in quantum
- **256-qubit system** planned for 2026 (6th generation)
- **$151B DoD SHIELD IDIQ** contract
- **Revenue:** $225-245M expected in 2026
- Available on Amazon Braket, Azure Quantum, Google Cloud
- Best for: optimization, chemistry simulation, ML

### Equal1 (Novel but Early)
- Irish startup, $60M raised (total $85M)
- **Bell-1:** Silicon-based, fits in standard server racks
- Operates at 0.3 Kelvin with closed-cycle cryo-cooler
- **Only 6 qubits** currently — proof of concept, not production
- Interesting for: data center integration path

### Rigetti (Superconducting)
- Available on Braket at lowest per-shot cost ($0.00090)
- Ankaa processor
- Good for: high-shot-count experiments on a budget

### QuEra (Neutral Atom)
- Aquila processor on Braket
- Rydberg atom approach
- Good for: optimization, simulation

---

## Quantum Teleportation / Quantum Internet (Context Only)

### Su Xiaolong (Shanxi University) — 5 Simultaneous States
- Published in Science Bulletin (2026)
- Teleported 5 sideband qumodes simultaneously within 24 MHz bandwidth
- ~70% fidelity
- Breaks the one-state-at-a-time bottleneck
- Opens door to scalable quantum networks

### What This Means for You
Nothing practical for 5-10 years minimum. When quantum internet exists, it could enable perfectly secure communication between distributed agents. But that's science fiction for your current projects.

---

## Getting Started Checklist

### Month 1: Post-Quantum Crypto (do this now)
```bash
pip install liboqs-python
# See intel/post-quantum-crypto/ for Conway wallet integration
```

### Month 3: Braket Free Tier
```bash
pip install amazon-braket-sdk
# AWS account with Braket enabled
# Run first circuit on SV1 simulator (free)
# Tutorial: https://docs.aws.amazon.com/braket/latest/developerguide/braket-get-started.html
```

### Month 6: Portfolio Optimizer Prototype
```bash
# Build QAOA circuit for 4-asset portfolio
# Test on simulator with historical crypto data
# Compare results to classical scipy.optimize
```

### Year 1: Real QPU Testing
```bash
# When 256-qubit IonQ system available on Braket:
# Run portfolio optimizer on real hardware
# Compare quantum vs classical: speed, quality, cost
```

---

## Sources

- [Amazon Braket](https://aws.amazon.com/braket/)
- [Braket Pricing](https://aws.amazon.com/braket/pricing/)
- [Braket SDK GitHub](https://github.com/amazon-braket/amazon-braket-sdk-python)
- [IonQ Roadmap](https://www.ionq.com/roadmap)
- [Equal1 Bell-1](https://www.equal1.com/bell-1)
- [SandboxAQ](https://www.sandboxaq.com/)
- [Quantum Teleportation Breakthrough](https://scitechdaily.com/quantum-teleportation-breakthrough-sends-5-states-at-once/)
- [QAOA for Portfolio Optimization](https://arxiv.org/abs/1907.05415)
