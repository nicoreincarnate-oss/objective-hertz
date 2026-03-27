"""DatasetProvider adapter for personal benchmarks."""

from __future__ import annotations

from collections.abc import Iterable

from openjarvis.evals.core.dataset import DatasetProvider
from openjarvis.evals.core.types import EvalRecord
from openjarvis.learning.optimize.personal.synthesizer import PersonalBenchmark


class PersonalBenchmarkDataset(DatasetProvider):
    """Wraps a PersonalBenchmark as a DatasetProvider for EvalRunner."""

    dataset_id: str = "personal"
    dataset_name: str = "Personal Benchmark"

    def __init__(self, benchmark: PersonalBenchmark) -> None:
        self._benchmark = benchmark
        self._records: list[EvalRecord] = []

    def load(
        self,
        *,
        max_samples: int | None = None,
        split: str | None = None,
        seed: int | None = None,
    ) -> None:
        """Convert :class:`PersonalBenchmarkSample` instances to :class:`EvalRecord`."""
        samples = self._benchmark.samples
        if max_samples is not None:
            samples = samples[:max_samples]
        self._records = [
            EvalRecord(
                record_id=s.trace_id,
                problem=s.query,
                reference=s.reference_answer,
                category=s.category,
                subject=s.agent or "general",
                metadata=s.metadata,
            )
            for s in samples
        ]

    def iter_records(self) -> Iterable[EvalRecord]:
        """Iterate over loaded records."""
        return iter(self._records)

    def size(self) -> int:
        """Return the number of loaded records."""
        return len(self._records)


__all__ = ["PersonalBenchmarkDataset"]
