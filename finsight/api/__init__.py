"""Thin HTTP layer over the workflow. No financial logic lives here: handlers validate
input, start background jobs through the orchestrator, and reshape stored results."""
