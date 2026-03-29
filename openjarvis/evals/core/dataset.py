"""Abstract base class for dataset providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from contextlib import AbstractContextManager

from openjarvis.evals.core.types import EvalRecord


class DatasetProvider(ABC):
    """Base class for all evaluation dataset providers."""

    dataset_id: str
    dataset_name: str

    @abstractmethod
    def load(
        self,
        *,
        max_samples: int | None = None,
        split: str | None = None,
        seed: int | None = None,
    ) -> None:
        """Load the dataset (possibly downloading from HuggingFace)."""

    @abstractmethod
    def iter_records(self) -> Iterable[EvalRecord]:
        """Iterate over loaded records."""

    @abstractmethod
    def size(self) -> int:
        """Return the number of loaded records."""

    def create_task_env(
        self, record: EvalRecord,
    ) -> AbstractContextManager | None:
        """Return a task environment context manager, or None."""
        return None

    def verify_requirements(self) -> list[str]:
        """Return list of unsatisfied requirements, or empty list."""
        return []

    def iter_episodes(self) -> Iterable[list[EvalRecord]]:
        """Iterate over episodes (groups of sequential records).

        Default: each record is its own single-record episode.
        Override for benchmarks requiring sequential processing
        with shared agent state within an episode.
        """
        for record in self.iter_records():
            yield [record]


__all__ = ["DatasetProvider"]
