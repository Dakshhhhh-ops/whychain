"""Deterministic analysis engine.

Agents decide WHAT to test and explain the results; this package computes them. Every number it produces comes from
one federated query (the dataset's order-level fact table, pulled through the data harness) and documented,
unit-tested calculations, so the same question gives the same numbers on every run.
"""
from whychain.analysis.engine import Engine, Result, Scope, get_engine
from whychain.analysis.model import FactModel, fact_model

__all__ = ["Engine", "Result", "Scope", "get_engine", "FactModel", "fact_model"]
